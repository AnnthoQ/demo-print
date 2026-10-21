"""Pruebas del parser y del cálculo de estado / papel con los volcados reales de la Fase 0."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lector  # noqa: E402
import mibs  # noqa: E402
import volcado  # noqa: E402
from adaptadores import obtener_adaptador  # noqa: E402
from simulador import aplicar_escenario  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
EN_USO = {"canon": {"1.1": False, "1.2": True, "1.3": False, "1.4": True, "1.5": False},
          "ricoh": {"1.1": True, "1.2": False, "1.3": True, "1.4": False},
          "konica": {"1.1": False, "1.2": True, "1.3": False, "1.5": False}}
ENTRADA = "1.3.6.1.2.1.43.8.2.1"


def filas(marca, escenario="normal", cambios=None):
    base = dict(volcado.cargar(FIX / f"{marca}.txt"))
    datos = aplicar_escenario(base, marca, escenario)
    datos.update(cambios or {})
    return list(datos.items())


def normalizar(marca, escenario="normal", cambios=None, cfg=None):
    crudo = lector.crudo_desde_filas(filas(marca, escenario, cambios))
    ad = obtener_adaptador(marca)
    bandejas = lector.construir_bandejas(crudo, ad, EN_USO[marca] if cfg is None else cfg)
    return lector.normalizar({"id": marca, "name": marca.title(), "ip": "0.0.0.0"}, crudo, ad, bandejas)


def bandeja(p, idx):
    return next(t for t in p["trays"] if t["id"] == idx)


class Parser(unittest.TestCase):
    def test_volcados_completos(self):
        self.assertEqual(len(volcado.cargar(FIX / "canon.txt")), 586)
        self.assertEqual(len(volcado.cargar(FIX / "ricoh.txt")), 503)
        self.assertEqual(len(volcado.cargar(FIX / "konica.txt")), 242)

    def test_bitmask_msb_primero(self):
        self.assertEqual(mibs.decodificar_errores(bytes.fromhex("a000")), ["lowPaper", "lowToner"])
        self.assertEqual(mibs.decodificar_errores(bytes.fromhex("0400")), ["jammed"])
        self.assertEqual(mibs.decodificar_errores(bytes.fromhex("0a")), ["doorOpen", "offline"])
        self.assertEqual(mibs.decodificar_errores(bytes.fromhex("0080")), ["inputTrayMissing"])
        self.assertEqual(mibs.decodificar_errores(b""), [])

    def test_texto_latin1_de_ricoh(self):
        p = normalizar("ricoh")
        self.assertIn("Tóner", [s["name"] for s in p["supplies"]])


class Identificacion(unittest.TestCase):
    def test_modelos(self):
        self.assertEqual(normalizar("canon")["model"], "Canon iR-ADV C7570 /P")
        self.assertTrue(normalizar("ricoh")["model"].startswith("RICOH Aficio MP 7502"))
        self.assertEqual(normalizar("konica")["model"], "KONICA MINOLTA bizhub PRO 920")

    def test_nombres_y_tamanos_de_bandeja(self):
        canon, konica = normalizar("canon"), normalizar("konica")
        self.assertEqual((bandeja(canon, "1.2")["name"], bandeja(canon, "1.2")["paper"]), ("Drawer 1", "A4"))
        self.assertEqual(bandeja(canon, "1.4")["paper"], "A3")
        # La Konica no reporta nombres: se derivan del índice
        self.assertEqual(bandeja(konica, "1.2")["name"], "Bandeja 2")

    def test_bypass_detectado_y_desactivado_por_defecto(self):
        canon = normalizar("canon", cfg={})
        ricoh = normalizar("ricoh", cfg={})
        self.assertTrue(bandeja(canon, "1.1")["isBypass"])
        self.assertFalse(bandeja(canon, "1.1")["enabled"])
        self.assertTrue(bandeja(ricoh, "1.4")["isBypass"])   # "Bypass Tray" aunque su tipo no sea manual
        self.assertFalse(bandeja(ricoh, "1.4")["enabled"])
        self.assertTrue(bandeja(ricoh, "1.1")["enabled"])    # las demás, activas


class Papel(unittest.TestCase):
    def test_porcentaje_y_hojas(self):
        t = bandeja(normalizar("canon"), "1.2")
        self.assertEqual((t["percent"], t["sheets"], t["capacity"]), (10, 110, 1100))
        self.assertEqual(t["levelType"], "pasos")
        self.assertEqual(t["label"], "≈ 110 hojas · 10 %")

    def test_vacia(self):
        t = bandeja(normalizar("canon"), "1.3")
        self.assertEqual((t["percent"], t["label"]), (0, "Vacía"))

    def test_desconocido_menos_2(self):
        t = bandeja(normalizar("canon", cambios={f"{ENTRADA}.10.1.2": ("INTEGER", -2)}), "1.2")
        self.assertEqual((t["percent"], t["levelType"], t["label"]), (None, "desconocido", "Desconocido"))

    def test_binario_menos_3(self):
        t = bandeja(normalizar("canon", cambios={f"{ENTRADA}.10.1.2": ("INTEGER", -3)}), "1.2")
        self.assertEqual((t["percent"], t["levelType"], t["label"]), (None, "binario", "Con papel"))

    def test_limitado_a_100(self):
        t = bandeja(normalizar("canon", cambios={f"{ENTRADA}.10.1.2": ("INTEGER", 5000)}), "1.2")
        self.assertEqual(t["percent"], 100)

    def test_bandeja_abierta_no_es_vacia(self):
        p = normalizar("canon", "bandeja_abierta")
        t = bandeja(p, "1.2")
        self.assertTrue(t["open"])
        self.assertEqual(t["label"], "Bandeja abierta")
        self.assertFalse(p["errors"]["noPaper"])

    def test_bandeja_inactiva_vacia_no_avisa(self):
        p = normalizar("ricoh")  # Tray 2 está vacía pero no se usa
        self.assertFalse(p["errors"]["noPaper"])
        self.assertNotIn("Sin papel en Tray 2", p["warnings"])

    def test_sin_papel_en_bandeja_activa(self):
        p = normalizar("ricoh", "sin_papel")
        self.assertTrue(p["errors"]["noPaper"])
        self.assertIn("Sin papel en Tray 1", p["warnings"])

    def test_papel_bajo(self):
        p = normalizar("konica", "papel_bajo")
        self.assertTrue(p["errors"]["lowPaper"])

    def test_estabilizacion_de_vacia(self):
        anterior = normalizar("canon")["trays"]
        pendientes = set()
        crudo = lector.crudo_desde_filas(filas("canon", cambios={f"{ENTRADA}.10.1.2": ("INTEGER", 0)}))
        nuevas = lector.construir_bandejas(crudo, obtener_adaptador("canon"), EN_USO["canon"])
        primera = lector.estabilizar(nuevas, anterior, pendientes)
        self.assertEqual(bandeja({"trays": primera}, "1.2")["sheets"], 110)   # se mantiene una lectura
        segunda = lector.estabilizar(nuevas, primera, pendientes)
        self.assertEqual(bandeja({"trays": segunda}, "1.2")["label"], "Vacía")  # y luego se confirma


class Estado(unittest.TestCase):
    def test_disponibles_con_avisos_permanentes(self):
        # lowPaper/lowToner (Canon) y serviceRequested (Konica) no son errores
        for marca in ("canon", "ricoh", "konica"):
            self.assertEqual(normalizar(marca)["state"], "disponible", marca)
        self.assertNotIn("Servicio solicitado", normalizar("konica")["warnings"])
        self.assertIn("Tóner bajo", normalizar("canon")["warnings"])

    def test_ocupada_y_calentando(self):
        self.assertEqual(normalizar("ricoh", "ocupada")["state"], "ocupada")
        self.assertEqual(normalizar("ricoh", "calentando")["state"], "calentando")

    def test_atasco_por_bitmask(self):
        p = normalizar("canon", "atasco")
        self.assertEqual(p["state"], "error")
        self.assertTrue(p["errors"]["jammed"])
        self.assertEqual(p["reasons"][0], "Papel atascado")

    def test_atasco_solo_por_alerta(self):
        cambios = {f"1.3.6.1.2.1.43.18.1.1.{c}.1.77": ("INTEGER", v) for c, v in ((2, 3), (4, 13), (5, 1), (7, 8))}
        p = normalizar("konica", cambios=cambios)
        self.assertTrue(p["errors"]["jammed"])
        self.assertEqual(p["state"], "error")

    def test_tapa_abierta(self):
        p = normalizar("ricoh", "tapa_abierta")
        self.assertEqual(p["state"], "error")
        self.assertTrue(p["errors"]["doorOpen"])
        self.assertTrue(any(c["open"] for c in p["covers"]))

    def test_reposo_no_es_error(self):
        # Caso real de la Canon en ahorro de energía (21-10-2026)
        p = normalizar("canon", "reposo")
        self.assertEqual((p["state"], p["stateLabel"], p["sleeping"]), ("disponible", "En reposo", True))
        self.assertEqual(p["reasons"], [])
        t = bandeja(p, "1.2")
        self.assertFalse(t["open"])                       # cajón "no disponible" no es cajón abierto
        self.assertEqual(t["label"], "≈ 110 hojas · 10 %")  # conserva el nivel
        self.assertNotIn("Drawer 1 abierta", p["warnings"])

    def test_detenida_sin_bits(self):
        p = normalizar("konica", "detenida")
        self.assertEqual(p["state"], "error")
        self.assertIn("Operation Error", p["reasons"][0])

    def test_sin_respuesta(self):
        anterior = normalizar("canon")
        p = lector.fuera_de_linea({"id": "canon", "name": "Canon", "ip": "0.0.0.0"}, "timeout", anterior)
        self.assertEqual(p["state"], "fuera_de_linea")
        self.assertTrue(p["errors"]["offline"])
        self.assertEqual(p["model"], anterior["model"])
        self.assertTrue(all(t["label"] == "Sin datos" for t in p["trays"]))


if __name__ == "__main__":
    unittest.main()

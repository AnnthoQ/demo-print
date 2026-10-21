"""Pruebas de punta a punta del servidor contra el simulador SNMP (UDP real en 127.0.0.1)."""
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lector  # noqa: E402
from configuracion import ConfigBandejas  # noqa: E402
from servidor import crear_servidor  # noqa: E402
from simulador import AgenteSimulado  # noqa: E402
from snmp_min import Sesion, SnmpTimeout  # noqa: E402

TOKEN = "token-de-prueba-1234567890"
ORIGEN = "chrome-extension://abcdefghijklmnop"


class Simulador(unittest.TestCase):
    def test_canon_solo_v1(self):
        a = AgenteSimulado("canon", 0, versiones=("1",)).iniciar()
        try:
            self.assertEqual(len(Sesion("127.0.0.1", "public", "1", puerto=a.puerto).walk("1.3.6.1.2.1.43")), 567)
            with self.assertRaises(SnmpTimeout):
                Sesion("127.0.0.1", "public", "2c", timeout=0.3, reintentos=0, puerto=a.puerto).get("1.3.6.1.2.1.1.1.0")
        finally:
            a.detener()

    def test_comunidad_incorrecta_no_responde(self):
        a = AgenteSimulado("ricoh", 0).iniciar()
        try:
            with self.assertRaises(SnmpTimeout):
                Sesion("127.0.0.1", "otra", "2c", timeout=0.3, reintentos=0, puerto=a.puerto).get("1.3.6.1.2.1.1.1.0")
        finally:
            a.detener()

    def test_lectura_completa_por_bulk(self):
        a = AgenteSimulado("ricoh", 0).iniciar()
        try:
            crudo = lector.crudo_desde_filas(lector.leer_filas(Sesion("127.0.0.1", "public", "2c", puerto=a.puerto)))
            self.assertEqual(len(crudo["entradas"]), 4)
            a.cambiar("atasco")
            crudo = lector.crudo_desde_filas(lector.leer_filas(Sesion("127.0.0.1", "public", "2c", puerto=a.puerto)))
            self.assertIn(8, [x["codigo"] for x in crudo["alertas"]])
        finally:
            a.detener()


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agentes = {"canon": AgenteSimulado("canon", 0, versiones=("1",)).iniciar(),
                       "ricoh": AgenteSimulado("ricoh", 0).iniciar(),
                       "konica": AgenteSimulado("konica", 0).iniciar()}
        impresoras = [{"id": m, "name": m.title(), "brand": m, "ip": "127.0.0.1", "port": a.puerto,
                       "snmpVersion": "1" if m == "canon" else "2c", "community": "public"}
                      for m, a in cls.agentes.items()]
        cls.dir = Path(tempfile.mkdtemp())
        shutil.copy(Path(__file__).resolve().parents[2] / "config" / "trays.json", cls.dir / "trays.json")
        cls.httpd, cls.sondeo = crear_servidor(impresoras, ConfigBandejas(cls.dir / "trays.json"), TOKEN, [ORIGEN],
                                               host="127.0.0.1", puerto=0, intervalo=0.5, timeout=0.5, reintentos=0)
        cls.sondeo.iniciar()
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls._esperar(lambda: len(cls.sondeo.impresoras_estado()) == 3)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for a in cls.agentes.values():
            a.detener()
        shutil.rmtree(cls.dir, ignore_errors=True)

    @staticmethod
    def _esperar(condicion, limite=8.0):
        fin = time.time() + limite
        while time.time() < fin:
            if condicion():
                return True
            time.sleep(0.1)
        raise AssertionError("condición no alcanzada a tiempo")

    def pedir(self, ruta, metodo="GET", cuerpo=None, token=TOKEN, origen=None):
        req = urllib.request.Request(self.base + ruta, method=metodo,
                                     data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        if origen:
            req.add_header("Origin", origen)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"null"), dict(r.headers)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"null"), dict(e.headers)

    def test_health_sin_token(self):
        codigo, datos, _ = self.pedir("/health", token=None)
        self.assertEqual((codigo, datos["printers"]), (200, 3))

    def test_rutas_tolerantes(self):
        # Lo que suele escribirse a mano en el navegador
        self.assertEqual(self.pedir("/health/", token=None)[0], 200)
        codigo, datos, _ = self.pedir("/", token=None)
        self.assertEqual((codigo, datos["salud"]), (200, "/health"))
        self.assertEqual(self.pedir("/api/printers/")[0], 200)
        self.assertEqual(self.pedir("/otra", token=None)[0], 404)

    def test_requiere_token(self):
        self.assertEqual(self.pedir("/api/printers", token=None)[0], 401)
        self.assertEqual(self.pedir("/api/printers", token="incorrecto-xxxxxxxx")[0], 401)

    def test_lista_e_impresora(self):
        codigo, lista, _ = self.pedir("/api/printers")
        self.assertEqual(codigo, 200)
        self.assertEqual([p["id"] for p in lista], ["canon", "ricoh", "konica"])
        self.assertEqual(self.pedir("/api/printers/canon")[1]["model"], "Canon iR-ADV C7570 /P")
        self.assertEqual(self.pedir("/api/printers/nada")[0], 404)

    def test_cors(self):
        _, _, cab = self.pedir("/api/printers", origen=ORIGEN)
        self.assertEqual(cab.get("Access-Control-Allow-Origin"), ORIGEN)
        _, _, cab = self.pedir("/api/printers", origen="https://otro.sitio")
        self.assertNotIn("Access-Control-Allow-Origin", cab)
        _, _, cab = self.pedir("/api/printers", origen="chrome-extension://otra")
        self.assertNotIn("Access-Control-Allow-Origin", cab)  # sin comodín configurado

    def test_cambiar_bandeja_se_persiste(self):
        codigo, _, _ = self.pedir("/api/trays/konica/1.5", "PUT", {"enabled": True})
        self.assertEqual(codigo, 200)
        self.assertTrue(json.loads((self.dir / "trays.json").read_text())["konica"]["1.5"])
        bandejas = {t["id"]: t["enabled"] for t in self.pedir("/api/trays")[1]["konica"]}
        self.assertTrue(bandejas["1.5"])
        self.pedir("/api/trays/konica/1.5", "PUT", {"enabled": False})
        self.assertEqual(self.pedir("/api/trays/konica/9.9", "PUT", {"enabled": True})[0], 404)
        self.assertEqual(self.pedir("/api/trays/konica/1.5", "PUT", {"enabled": "si"})[0], 400)

    def test_atasco_genera_evento_y_se_resuelve(self):
        q = self.sondeo.suscribir()
        try:
            self.agentes["ricoh"].cambiar("atasco")
            self._esperar(lambda: self.sondeo.impresora("ricoh")["errors"]["jammed"])
            self.agentes["ricoh"].cambiar("normal")
            self._esperar(lambda: not self.sondeo.impresora("ricoh")["errors"]["jammed"])
            alertas = []
            while not q.empty():
                tipo, datos = q.get_nowait()
                if tipo == "alerta" and datos["type"] == "atasco":
                    alertas.append(datos["active"])
            self.assertEqual(alertas, [True, False])
        finally:
            self.sondeo.desuscribir(q)

    def test_fuera_de_linea(self):
        self.agentes["konica"].cambiar("fuera_de_linea")
        try:
            self._esperar(lambda: self.sondeo.impresora("konica")["state"] == "fuera_de_linea")
            # Cambiar una bandeja sin lectura actual también debe verse en /api/trays
            self.pedir("/api/trays/konica/1.3", "PUT", {"enabled": True})
            self.assertTrue({t["id"]: t["enabled"] for t in self.pedir("/api/trays")[1]["konica"]}["1.3"])
            self.pedir("/api/trays/konica/1.3", "PUT", {"enabled": False})
        finally:
            self.agentes["konica"].cambiar("normal")
        self._esperar(lambda: self.sondeo.impresora("konica")["state"] == "disponible")

    def test_sse_envia_snapshot(self):
        req = urllib.request.Request(f"{self.base}/api/events?token={TOKEN}")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.headers["Content-Type"].split(";")[0], "text/event-stream")
            self.assertEqual(r.readline().decode().strip(), "event: snapshot")
            datos = json.loads(r.readline().decode()[len("data: "):])
            self.assertEqual(len(datos), 3)


if __name__ == "__main__":
    unittest.main()

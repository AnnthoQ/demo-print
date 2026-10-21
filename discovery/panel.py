"""Panel local de diagnóstico (Fase 0): muestra en vivo todo lo que reportan las
impresoras por SNMP. Solo lectura sobre las impresoras.

Uso:
    python discovery/panel.py      ->  http://127.0.0.1:8765

Las bandejas activas se guardan en bandejas_en_uso.json (no contiene IPs).
"""
import json
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent / "server"))  # módulos SNMP compartidos

import mibs  # noqa: E402
from snmp_min import Sesion, SnmpError, SnmpTimeout, oid_tuple  # noqa: E402

PUERTO = 8765
INTERVALO = 5.0
UMBRAL_BAJO = 20  # % para avisar "papel bajo"
PRT_SUPPLIES = "1.3.6.1.2.1.43.11.1.1"
ARCHIVO_BANDEJAS = AQUI / "bandejas_en_uso.json"

# Bits de hrPrinterDetectedErrorState que ponen la impresora en "error"
ERRORES = {"jammed": "Papel atascado", "doorOpen": "Tapa abierta", "offline": "Fuera de línea",
           "noPaper": "Sin papel", "noToner": "Sin tóner", "outputFull": "Bandeja de salida llena"}
# Bits que solo son avisos (algunos están siempre activos: no deben pintar de rojo)
AVISOS = {"lowPaper": "Papel bajo (según la impresora)", "lowToner": "Tóner bajo",
          "serviceRequested": "Servicio solicitado", "overduePreventMaint": "Mantenimiento pendiente",
          "outputNearFull": "Salida casi llena", "markerSupplyMissing": "Falta un consumible",
          "inputTrayMissing": "Bandeja abierta o ausente", "inputTrayEmpty": "Bandeja vacía",
          "outputTrayMissing": "Falta bandeja de salida"}
ESTADO_TXT = {"disponible": "Disponible", "ocupada": "Ocupada", "calentando": "Calentando",
              "error": "Con error", "fuera_de_linea": "Fuera de línea"}

TAMANOS = {(210, 297): "A4", (297, 420): "A3", (148, 210): "A5", (216, 279): "Carta",
           (216, 356): "Oficio", (279, 432): "11×17", (305, 457): "12×18", (320, 450): "SRA3"}

_lock = threading.Lock()
_estado = {}      # id -> datos de la impresora
_eventos = []     # [{hora, impresora, texto}], más reciente al final


def cargar_en_uso():
    try:
        return json.loads(ARCHIVO_BANDEJAS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_en_uso(datos):
    ARCHIVO_BANDEJAS.write_text(json.dumps(datos, indent=2) + "\n", encoding="utf-8")


def columna(s, oid):
    return {o[len(oid) + 1:]: v for o, (t, v) in s.walk(oid)}


def tamano_papel(c):
    f, x = c.get(6, -1), c.get(7, -1)
    if not (isinstance(f, int) and isinstance(x, int) and f > 0 and x > 0):
        f, x = c.get(4, -1), c.get(5, -1)
    factor = {3: 25.4 / 10000, 4: 1 / 1000}.get(c.get(3))
    if not (factor and isinstance(f, int) and isinstance(x, int) and f > 0 and x > 0):
        return None
    a, b = sorted((f * factor, x * factor))
    for (w, h), nombre in TAMANOS.items():
        if abs(a - w) <= 3 and abs(b - h) <= 3:
            return nombre
    return f"{a:.0f}×{b:.0f} mm"


def color_consumible(desc):
    d = (desc or "").lower()
    for clave, color in (("black", "negro"), ("negro", "negro"), ("cyan", "cian"), ("cian", "cian"),
                         ("magenta", "magenta"), ("yellow", "amarillo"), ("amarillo", "amarillo")):
        if clave in d:
            return color
    return "otro"


def leer(imp, s, info, en_uso):
    """Una lectura completa, ya interpretada para el panel."""
    pst = columna(s, mibs.HR_PRINTER_STATUS)
    dev_idx, codigo = next(iter(pst.items())) if pst else ("1", 2)
    errs = columna(s, mibs.HR_PRINTER_ERRORS).get(dev_idx, b"")
    errs = errs if isinstance(errs, bytes) else b""
    devst = columna(s, mibs.HR_DEVICE_STATUS)
    entradas = mibs.tabla(s.walk(mibs.PRT_INPUT), mibs.PRT_INPUT)
    alertas = mibs.alertas(s.walk(mibs.PRT_ALERT))
    cubiertas = mibs.cubiertas(s.walk(mibs.PRT_COVER))
    pantalla = [mibs.texto(v) for o, (t, v) in s.walk(mibs.PRT_CONSOLE) if mibs.texto(v)]
    suministros = mibs.tabla(s.walk(PRT_SUPPLIES), PRT_SUPPLIES)

    bits = mibs.decodificar_errores(errs)
    activas = set(en_uso.get(imp["id"], []))

    bandejas = []
    for idx, c in sorted(entradas.items(), key=lambda kv: oid_tuple(kv[0])):
        n_entrada = idx.split(".")[-1]
        nombre = mibs.texto(c.get(13)) or f"Bandeja {n_entrada}"
        nivel, maximo, est = c.get(10), c.get(9), c.get(11)
        abierta = (isinstance(est, int) and est >= 0 and est & 7 == 5) or any(
            a["codigo"] == 801 and str(a["grupo_indice"]) == n_entrada for a in alertas)
        pct = None
        if abierta:
            etiqueta = "Bandeja abierta"
        elif nivel == -3:
            etiqueta = "Con papel"
        elif not isinstance(nivel, int) or nivel < 0:
            etiqueta = "Desconocido"
        elif nivel == 0:
            etiqueta, pct = "Vacía", 0
        else:
            unidad = "hojas" if c.get(8) == 8 else "unidades"
            etiqueta = f"≈ {nivel} {unidad}"
            if isinstance(maximo, int) and maximo > 0:
                pct = max(0, min(100, round(nivel / maximo * 100)))
        bandejas.append({
            "indice": idx, "nombre": nombre, "enUso": idx in activas,
            "bypass": mibs.es_bypass(c.get(2), nombre, c.get(18)),
            "tipo": mibs.TIPOS_ENTRADA.get(c.get(2), c.get(2)), "papel": tamano_papel(c),
            "nivel": nivel, "max": maximo, "pct": pct, "etiqueta": etiqueta, "abierta": abierta,
            "estadoSnmp": mibs.decodificar_estado_subunidad(est),
        })

    consumibles = []
    for idx, c in sorted(suministros.items(), key=lambda kv: oid_tuple(kv[0])):
        desc, maxc, lvl = mibs.texto(c.get(6)), c.get(8), c.get(9)
        pct = round(lvl / maxc * 100) if isinstance(maxc, int) and maxc > 0 and isinstance(lvl, int) and lvl >= 0 else None
        consumibles.append({
            "descripcion": desc, "pct": pct, "nivel": lvl, "max": maxc,
            # receptáculo que se llena (tóner residual); Ricoh no marca la clase, solo el nombre
            "residuo": c.get(4) == 4 or any(p in (desc or "").lower() for p in ("residual", "waste")),
            "color": color_consumible(desc),
            "etiqueta": f"{pct} %" if pct is not None else {-3: "Con existencia", -2: "Desconocido"}.get(lvl, "—"),
        })

    # --- Estado normalizado
    motivos = [ERRORES[b] for b in bits if b in ERRORES]
    atasco = "jammed" in bits or any(a["codigo"] == 8 for a in alertas)
    if atasco and "Papel atascado" not in motivos:
        motivos.insert(0, "Papel atascado")
    detenida = devst.get(dev_idx) == 5 or codigo == 1
    if detenida and not motivos:
        # Detenida sin ningún bit de error (p. ej. "Operation Error" de la Konica)
        otras = [a["descripcion"] for a in alertas if a["codigo"] not in (808, 807, 1104, 801)]
        motivos.append("Detenida: " + (", ".join(filter(None, otras)) or " / ".join(pantalla) or "motivo desconocido"))
    avisos = [AVISOS[b] for b in bits if b in AVISOS]
    for b in bandejas:
        if not b["enUso"]:
            continue
        if b["abierta"]:
            avisos.append(f"{b['nombre']} abierta")
        elif b["pct"] == 0:
            avisos.append(f"Sin papel en {b['nombre']}")
        elif b["pct"] is not None and b["pct"] < UMBRAL_BAJO:
            avisos.append(f"Papel bajo en {b['nombre']} ({b['etiqueta']})")

    if motivos:
        estado = "error"
    elif codigo == 4:
        estado = "ocupada"
    elif codigo == 5:
        estado = "calentando"
    else:
        estado = "disponible"

    return {
        **info, "online": True, "estado": estado, "estadoTxt": ESTADO_TXT[estado],
        "atasco": atasco, "motivos": motivos, "avisos": avisos,
        "hrPrinterStatus": f"{codigo} {mibs.HR_PRINTER_STATUS_NOMBRES.get(codigo, '?')}",
        "hrDeviceStatus": {i: f"{v} {mibs.HR_DEVICE_STATUS_NOMBRES.get(v, '?')}" for i, v in devst.items()},
        "erroresHex": errs.hex(), "erroresBits": bits,
        "bandejas": bandejas, "consumibles": consumibles, "cubiertas": cubiertas,
        "alertas": alertas, "pantalla": pantalla, "actualizado": datetime.now().strftime("%H:%M:%S"),
    }


def plano(p):
    """Resumen comparable para generar eventos legibles."""
    if not p.get("online"):
        return {"Estado": "Fuera de línea"}
    d = {"Estado": p["estadoTxt"]}
    for b in p["bandejas"]:
        d[f"{b['nombre']}"] = b["etiqueta"] + (f" ({b['pct']} %)" if b["pct"] not in (None, 0) else "")
    for c in p["cubiertas"]:
        d[f"Tapa «{c['descripcion']}»"] = {"doorOpen": "abierta", "doorClosed": "cerrada"}.get(c["estado"], c["estado"])
    for a in p["alertas"]:
        d[f"alerta:{a['descripcion'] or a['codigo_txt']}"] = "activa"
    return d


def registrar(nombre, antes, ahora):
    for k in sorted(set(antes) | set(ahora)):
        a, b = antes.get(k), ahora.get(k)
        if a == b:
            continue
        if k.startswith("alerta:"):
            texto = ("Nueva alerta: " if b else "Alerta resuelta: ") + k[7:]
        else:
            texto = f"{k}: {a} → {b}" if a is not None and b is not None else f"{k}: {b or 'ya no aparece'}"
        _eventos.append({"hora": datetime.now().strftime("%H:%M:%S"), "impresora": nombre, "texto": texto})
    del _eventos[:-300]


def vigilar(imp):
    base = {"id": imp["id"], "nombre": imp["name"], "ip": imp["ip"]}
    sesion, info, anterior = None, None, None
    while True:
        t0 = time.time()
        try:
            if sesion is None:
                version = mibs.detectar_version(imp["ip"], imp["community"], imp.get("snmpVersion", ["2c", "1"]))
                if not version:
                    raise SnmpTimeout("sin respuesta SNMP v2c ni v1")
                sesion = Sesion(imp["ip"], imp["community"], version, timeout=2.0, reintentos=1)
                sis = {o: mibs.texto(v) for o, (t, v) in sesion.walk("1.3.6.1.2.1.1")}
                info = {**base, "snmpVersion": version, "modelo": sis.get(mibs.SYS_DESCR + ".0"),
                        "sysName": sis.get(mibs.SYS_NAME + ".0")}
            datos = leer(imp, sesion, info, cargar_en_uso())
        except (SnmpTimeout, SnmpError, OSError) as e:
            sesion = None
            datos = {**(info or base), "online": False, "estado": "fuera_de_linea",
                     "estadoTxt": ESTADO_TXT["fuera_de_linea"], "error": str(e),
                     "actualizado": datetime.now().strftime("%H:%M:%S")}
        except Exception as e:  # noqa: BLE001 — un dato inesperado no debe detener el hilo
            traceback.print_exc()
            datos = {**(info or base), "online": False, "estado": "fuera_de_linea",
                     "estadoTxt": ESTADO_TXT["fuera_de_linea"], "error": f"error interno: {e}",
                     "actualizado": datetime.now().strftime("%H:%M:%S")}
        datos["msLectura"] = round((time.time() - t0) * 1000)
        with _lock:
            ahora = plano(datos)
            if anterior is not None:
                registrar(imp["name"], anterior, ahora)
            anterior = ahora
            _estado[imp["id"]] = datos
        time.sleep(max(0.5, INTERVALO - (time.time() - t0)))


class Manejador(BaseHTTPRequestHandler):
    def _json(self, obj, codigo=200):
        cuerpo = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            cuerpo = (AQUI / "panel.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)
        elif self.path == "/api/estado":
            with _lock:
                self._json({"impresoras": [_estado[i] for i in ORDEN if i in _estado],
                            "eventos": list(reversed(_eventos[-100:])), "umbral": UMBRAL_BAJO,
                            "intervalo": INTERVALO})
        else:
            self._json({"error": "no encontrado"}, 404)

    def do_POST(self):
        if self.path != "/api/bandejas":
            return self._json({"error": "no encontrado"}, 404)
        try:
            datos = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            imp, bandeja, activa = datos["impresora"], str(datos["bandeja"]), bool(datos["enUso"])
        except (ValueError, KeyError):
            return self._json({"error": "petición inválida"}, 400)
        if imp not in ORDEN:
            return self._json({"error": "impresora desconocida"}, 400)
        with _lock:
            en_uso = cargar_en_uso()
            lista = [b for b in en_uso.get(imp, []) if b != bandeja] + ([bandeja] if activa else [])
            en_uso[imp] = sorted(lista, key=oid_tuple)
            guardar_en_uso(en_uso)
        self._json({"ok": True, "enUso": en_uso})

    def log_message(self, *args):
        pass  # sin ruido en consola


ORDEN = []

if __name__ == "__main__":
    impresoras = json.loads((AQUI / "printers.local.json").read_text(encoding="utf-8"))
    ORDEN.extend(p["id"] for p in impresoras)
    for p in impresoras:
        threading.Thread(target=vigilar, args=(p,), daemon=True).start()
    print(f"Panel en http://127.0.0.1:{PUERTO}  (Ctrl+C para salir)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PUERTO), Manejador).serve_forever()

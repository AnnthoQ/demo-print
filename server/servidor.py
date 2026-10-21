"""FASE 1 — Servidor de monitoreo de impresoras (solo lectura sobre las impresoras).

Uso:
    python server/servidor.py

Configuración: .env (token, comunidades, puerto), config/printers.json y config/trays.json.
API:
    GET  /health                         (sin token)
    GET  /api/printers                   lista con estado completo
    GET  /api/printers/<id>
    GET  /api/trays                      bandejas por impresora (panel de configuración)
    PUT  /api/trays/<impresora>/<bandeja>   {"enabled": true|false}
    GET  /api/events                     Server-Sent Events (token también por ?token=)
"""
import hmac
import json
import queue
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import configuracion  # noqa: E402
from sondeo import Sondeo  # noqa: E402

VERSION = "1.0.0"


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


class Manejador(BaseHTTPRequestHandler):
    server_version = f"MonitorImpresoras/{VERSION}"
    # Atributos inyectados por crear_servidor(): sondeo, token, origenes, inicio

    # ------------------------------------------------------------ utilidades
    def _cors(self):
        origen = self.headers.get("Origin")
        # "chrome-extension://*" permite cualquier extensión (el token sigue siendo obligatorio)
        comodin = origen and origen.startswith("chrome-extension://") and "chrome-extension://*" in self.origenes
        if origen and (origen in self.origenes or "*" in self.origenes or comodin):
            self.send_header("Access-Control-Allow-Origin", origen)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
            self.send_header("Access-Control-Max-Age", "600")

    def _json(self, obj, codigo=200):
        cuerpo = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(cuerpo)))
        self._cors()
        self.end_headers()
        self.wfile.write(cuerpo)

    def _autorizado(self, url):
        cabecera = self.headers.get("Authorization", "")
        token = cabecera[7:] if cabecera.startswith("Bearer ") else ""
        if not token and url.path.rstrip("/") == "/api/events":
            # EventSource no permite cabeceras: se acepta el token en la URL solo aquí
            token = parse_qs(url.query).get("token", [""])[0]
        return bool(token) and hmac.compare_digest(token.encode(), self.token.encode())

    def log_message(self, formato, *args):
        pass  # sin registro de cada petición; los errores se registran aparte

    # ------------------------------------------------------------ rutas
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        partes = [unquote(p) for p in url.path.strip("/").split("/") if p]
        if partes == ["health"]:  # también acepta "/health/"
            return self._json({"ok": True, "version": VERSION, "printers": len(self.sondeo.impresoras),
                               "uptimeSeconds": round(time.time() - self.inicio)})
        if not partes:
            # Raíz: útil para comprobar desde un navegador que el servidor responde
            return self._json({"ok": True, "servicio": "Monitor de impresoras", "version": VERSION,
                               "salud": "/health", "api": "/api/printers (requiere token)"})
        if partes[0] != "api":
            return self._json({"error": "no encontrado"}, 404)
        if not self._autorizado(url):
            return self._json({"error": "token inválido o ausente"}, 401)
        if partes == ["api", "events"]:
            return self._eventos()

        if partes == ["api", "printers"]:
            return self._json(self.sondeo.impresoras_estado())
        if len(partes) == 3 and partes[:2] == ["api", "printers"]:
            p = self.sondeo.impresora(partes[2])
            return self._json(p) if p else self._json({"error": "impresora desconocida"}, 404)
        if partes == ["api", "trays"]:
            return self._json({
                p["id"]: [{k: t[k] for k in ("id", "name", "isBypass", "enabled", "paper", "capacity")}
                          for t in p["trays"]]
                for p in self.sondeo.impresoras_estado()})
        return self._json({"error": "no encontrado"}, 404)

    def do_PUT(self):
        url = urlparse(self.path)
        partes = [unquote(p) for p in url.path.strip("/").split("/") if p]
        if not self._autorizado(url):
            return self._json({"error": "token inválido o ausente"}, 401)
        if len(partes) != 4 or partes[:2] != ["api", "trays"]:
            return self._json({"error": "no encontrado"}, 404)
        impresora, bandeja = partes[2], partes[3]
        try:
            largo = int(self.headers.get("Content-Length", 0))
            cuerpo = json.loads(self.rfile.read(largo) or b"{}")
            activa = cuerpo["enabled"]
            if not isinstance(activa, bool):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self._json({"error": 'se espera {"enabled": true|false}'}, 400)
        if not self.sondeo.existe(impresora):
            return self._json({"error": "impresora desconocida"}, 404)
        actual = self.sondeo.impresora(impresora)
        if actual and actual["trays"] and bandeja not in {t["id"] for t in actual["trays"]}:
            return self._json({"error": "bandeja desconocida"}, 404)
        self.sondeo.bandejas.fijar(impresora, bandeja, activa)
        self.sondeo.reevaluar(impresora)
        log(f"Bandeja {impresora}/{bandeja} -> {'activa' if activa else 'inactiva'}")
        return self._json({"printerId": impresora, "trayId": bandeja, "enabled": activa})

    def _eventos(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        q = self.sondeo.suscribir()
        try:
            self._sse("snapshot", self.sondeo.impresoras_estado())
            while True:
                try:
                    tipo, datos = q.get(timeout=15)
                    self._sse(tipo, datos)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")  # mantiene viva la conexión
                    self.wfile.flush()
        except OSError:
            pass  # el cliente cerró la conexión
        finally:
            self.sondeo.desuscribir(q)

    def _sse(self, tipo, datos):
        self.wfile.write(f"event: {tipo}\ndata: {json.dumps(datos, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()


def crear_servidor(impresoras, bandejas, token, origenes=(), host="0.0.0.0", puerto=8787,
                   intervalo=5.0, umbral=20, timeout=2.0, reintentos=1):
    sondeo = Sondeo(impresoras, bandejas, intervalo, umbral, timeout, reintentos)
    manejador = type("ManejadorConfigurado", (Manejador,), {
        "sondeo": sondeo, "token": token, "origenes": set(origenes), "inicio": time.time()})
    httpd = ThreadingHTTPServer((host, puerto), manejador)
    httpd.daemon_threads = True
    return httpd, sondeo


def redirigir_registro(ruta):
    """Sin consola (pythonw.exe) no hay dónde escribir: los mensajes van a un archivo.
    Al superar 5 MB se guarda una copia .old y se empieza de nuevo."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if ruta.exists() and ruta.stat().st_size > 5 * 1024 * 1024:
        ruta.replace(ruta.with_suffix(ruta.suffix + ".old"))
    archivo = open(ruta, "a", encoding="utf-8", buffering=1)  # se mantiene abierto mientras viva el servidor
    sys.stdout = sys.stderr = archivo


def main():
    configuracion.cargar_env()
    e = configuracion.entorno
    if e("LOG_FILE"):
        redirigir_registro(e("LOG_FILE"))
    token = e("API_TOKEN")
    if not token or len(token) < 16:
        sys.exit("Falta API_TOKEN en .env (mínimo 16 caracteres). Ver .env.example")
    raiz = configuracion.RAIZ
    impresoras = configuracion.cargar_impresoras(raiz / e("PRINTERS_FILE", "config/printers.json"))
    bandejas = configuracion.ConfigBandejas(raiz / e("TRAYS_FILE", "config/trays.json"))
    origenes = [o.strip() for o in e("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    host, puerto = e("HOST", "0.0.0.0"), e("PORT", 8787, int)
    httpd, sondeo = crear_servidor(
        impresoras, bandejas, token, origenes, host, puerto,
        intervalo=e("POLL_INTERVAL", 5.0, float), umbral=e("LOW_PAPER_THRESHOLD", 20, int),
        timeout=e("SNMP_TIMEOUT", 2.0, float), reintentos=e("SNMP_RETRIES", 1, int))
    sondeo.iniciar()
    log(f"Monitor de impresoras {VERSION} en http://{host}:{puerto}  "
        f"({len(impresoras)} impresoras, sondeo cada {sondeo.intervalo:g} s)")
    if not origenes:
        log("Aviso: ALLOWED_ORIGINS vacío; solo la extensión (con permiso de host) podrá consultar la API")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("Servidor detenido")


if __name__ == "__main__":
    main()

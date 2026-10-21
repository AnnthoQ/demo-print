"""Simulador SNMP de las impresoras, a partir de los volcados reales de la Fase 0.

Sirve para probar el servidor y la extensión sin depender del hardware y para
forzar escenarios (atasco, sin papel, etc.) que no se deben provocar en la realidad.

Uso:
    python server/simulador.py
        canon  -> 127.0.0.1:16101 (solo v1, como la real)
        ricoh  -> 127.0.0.1:16102
        konica -> 127.0.0.1:16103
        control HTTP -> http://127.0.0.1:16100

    Cambiar escenario en vivo:
        curl "http://127.0.0.1:16100/escenario?impresora=canon&nombre=atasco"
    Escenarios: normal, ocupada, calentando, atasco, tapa_abierta, sin_papel,
                papel_bajo, bandeja_abierta, detenida, fuera_de_linea
"""
import bisect
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import volcado  # noqa: E402
from snmp_min import (END_OF_MIB, GET, GETBULK, GETNEXT, NO_SUCH_OBJECT,  # noqa: E402
                      construir_respuesta, oid_tuple, parsear_peticion)

FIXTURES = Path(__file__).resolve().parent / "pruebas" / "fixtures"
ESCENARIOS = ("normal", "reposo", "ocupada", "calentando", "atasco", "tapa_abierta", "sin_papel",
              "papel_bajo", "bandeja_abierta", "detenida", "fuera_de_linea")
# Bandeja principal de cada impresora (la que usan en la oficina)
BANDEJA_PRINCIPAL = {"canon": "1.2", "ricoh": "1.1", "konica": "1.2"}

HR_STATUS = "1.3.6.1.2.1.25.3.5.1.1.1"
HR_ERRORS = "1.3.6.1.2.1.25.3.5.1.2.1"
HR_DEVICE = "1.3.6.1.2.1.25.3.2.1.5.1"
ALERTA = "1.3.6.1.2.1.43.18.1.1"
COVER_STATUS = "1.3.6.1.2.1.43.6.1.1.3.1.1"
CONSOLA = "1.3.6.1.2.1.43.16.5.1.2.1.1"


def _bits(errores, *numeros):
    b = bytearray(errores.ljust(2, b"\x00"))
    for n in numeros:
        b[n // 8] |= 0x80 >> (n % 8)
    return bytes(b)


def _alerta(datos, idx, severidad, grupo, grupo_idx, codigo, texto):
    for col, valor in ((2, severidad), (3, 4), (4, grupo), (5, grupo_idx), (6, -1), (7, codigo)):
        datos[f"{ALERTA}.{col}.1.{idx}"] = ("INTEGER", valor)
    datos[f"{ALERTA}.8.1.{idx}"] = ("OCTETS", texto.encode())


def aplicar_escenario(base, marca, escenario):
    """Devuelve una copia de las filas base modificadas según el escenario."""
    d = dict(base)
    errores = d.get(HR_ERRORS, ("OCTETS", b"\x00\x00"))[1]
    bandeja = BANDEJA_PRINCIPAL.get(marca, "1.1")
    n = bandeja.split(".")[-1]

    def detener(texto):
        d[HR_STATUS] = ("INTEGER", 1)
        d[HR_DEVICE] = ("INTEGER", 5)
        d[CONSOLA] = ("OCTETS", texto.encode())

    if escenario == "reposo":
        # Como la Canon real en ahorro de energía: "other" + "warning" y cajones
        # "no disponibles" que conservan su nivel
        d[HR_STATUS] = ("INTEGER", 1)
        d[HR_DEVICE] = ("INTEGER", 3)
        for oid in [o for o in d if o.startswith("1.3.6.1.2.1.43.8.2.1.11.")]:
            d[oid] = ("INTEGER", 5)
    elif escenario == "ocupada":
        d[HR_STATUS] = ("INTEGER", 4)
    elif escenario == "calentando":
        d[HR_STATUS] = ("INTEGER", 5)
    elif escenario == "atasco":
        d[HR_ERRORS] = ("OCTETS", _bits(errores, 5))
        _alerta(d, 900, 3, 13, 1, 8, "Atasco de papel (simulado)")
        detener("Atasco de papel")
    elif escenario == "tapa_abierta":
        d[HR_ERRORS] = ("OCTETS", _bits(errores, 4))
        d[COVER_STATUS] = ("INTEGER", 3)
        _alerta(d, 901, 3, 6, 1, 3, "Tapa abierta (simulado)")
        detener("Tapa abierta")
    elif escenario == "sin_papel":
        d[f"1.3.6.1.2.1.43.8.2.1.10.{bandeja}"] = ("INTEGER", 0)
        _alerta(d, 902, 4, 8, int(n), 808, f"Sin papel bandeja {n} (simulado)")
    elif escenario == "papel_bajo":
        maximo = d.get(f"1.3.6.1.2.1.43.8.2.1.9.{bandeja}", ("INTEGER", 500))[1]
        d[f"1.3.6.1.2.1.43.8.2.1.10.{bandeja}"] = ("INTEGER", maximo // 10)
    elif escenario == "bandeja_abierta":
        d[f"1.3.6.1.2.1.43.8.2.1.10.{bandeja}"] = ("INTEGER", 0)
        d[f"1.3.6.1.2.1.43.8.2.1.11.{bandeja}"] = ("INTEGER", 5)
        _alerta(d, 903, 4, 8, int(n), 801, f"Bandeja {n} abierta (simulado)")
    elif escenario == "detenida":
        _alerta(d, 904, 3, 1, -1, 1, "Operation Error (simulado)")
        detener("Operation Error")
    return d


class AgenteSimulado:
    def __init__(self, marca, puerto, versiones=("1", "2c"), community=b"public", host="127.0.0.1"):
        self.marca, self.versiones, self.community = marca, tuple(versiones), community
        self.base = dict(volcado.cargar(FIXTURES / f"{marca}.txt"))
        self.escenario = "normal"
        self._fijar_datos()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, puerto))
        self.puerto = self.sock.getsockname()[1]
        self._vivo = True

    def _fijar_datos(self):
        datos = aplicar_escenario(self.base, self.marca, self.escenario)
        orden = sorted(datos, key=oid_tuple)
        self._datos, self._orden, self._claves = datos, orden, [oid_tuple(o) for o in orden]

    def cambiar(self, escenario):
        if escenario not in ESCENARIOS:
            raise ValueError(f"escenario desconocido: {escenario}")
        self.escenario = escenario
        self._fijar_datos()

    def _siguiente(self, oid):
        i = bisect.bisect_right(self._claves, oid_tuple(oid))
        return (self._orden[i], self._datos[self._orden[i]]) if i < len(self._orden) else None

    def responder(self, paquete):
        ver, comm, tag, reqid, a, b, oids = parsear_peticion(paquete)
        version = {0: "1", 1: "2c"}.get(ver)
        if version not in self.versiones or comm != self.community or self.escenario == "fuera_de_linea":
            return None  # como un agente real: comunidad o versión incorrecta = silencio
        vbs, error = [], 0
        if tag == GET:
            for o in oids:
                if o in self._datos:
                    vbs.append((o, self._datos[o]))
                elif ver == 0:
                    error = 2
                    vbs.append((o, ("NULL", None)))
                else:
                    vbs.append((o, (NO_SUCH_OBJECT, None)))
        elif tag == GETNEXT or (tag == GETBULK and ver == 1):
            repeticiones = max(1, b) if tag == GETBULK else 1
            for o in oids:
                actual = o
                for _ in range(repeticiones):
                    sig = self._siguiente(actual)
                    if sig is None:
                        if ver == 0:
                            error = 2
                        vbs.append((actual, (END_OF_MIB, None)))
                        break
                    vbs.append(sig)
                    actual = sig[0]
        else:
            return None
        return construir_respuesta(ver, comm, reqid, vbs, error, 1 if error else 0)

    def servir(self):
        while self._vivo:
            try:
                paquete, origen = self.sock.recvfrom(65535)
            except OSError:
                break
            try:
                resp = self.responder(paquete)
            except Exception:  # noqa: BLE001 — paquete mal formado: se ignora
                continue
            if resp:
                self.sock.sendto(resp, origen)

    def iniciar(self):
        threading.Thread(target=self.servir, name=f"sim-{self.marca}", daemon=True).start()
        return self

    def detener(self):
        self._vivo = False
        self.sock.close()


def crear_control(agentes, puerto=16100, host="127.0.0.1"):
    """Pequeño HTTP para cambiar escenarios en vivo."""
    class Control(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            codigo, cuerpo = 200, None
            if url.path == "/escenario":
                try:
                    agentes[q["impresora"]].cambiar(q.get("nombre", "normal"))
                except (KeyError, ValueError) as e:
                    codigo, cuerpo = 400, {"error": str(e), "escenarios": ESCENARIOS}
            if cuerpo is None:
                cuerpo = {m: {"puerto": a.puerto, "escenario": a.escenario} for m, a in agentes.items()}
            datos = json.dumps(cuerpo, ensure_ascii=False).encode()
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(datos)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer((host, puerto), Control)
    httpd.daemon_threads = True
    return httpd


def main():
    args = sys.argv[1:]
    agentes = {
        "canon": AgenteSimulado("canon", 16101, versiones=("1",)).iniciar(),
        "ricoh": AgenteSimulado("ricoh", 16102).iniciar(),
        "konica": AgenteSimulado("konica", 16103).iniciar(),
    }
    for a in args:  # --escenario canon=atasco
        if "=" in a:
            m, e = a.split("=", 1)
            agentes[m].cambiar(e)
    control = crear_control(agentes)
    print("Simulador SNMP: canon 16101 (v1), ricoh 16102, konica 16103; control http://127.0.0.1:16100", flush=True)
    try:
        control.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

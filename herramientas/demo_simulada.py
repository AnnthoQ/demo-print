"""Demo sin hardware: simulador SNMP de las 3 impresoras + servidor de monitoreo.

Uso:
    python herramientas/demo_simulada.py
        API:      http://127.0.0.1:8788   (token: token-demo-simulada-0000)
        Control:  http://127.0.0.1:16100/escenario?impresora=canon&nombre=atasco

El token es solo para esta demo local; no es el del servidor real.
"""
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "server"))

from configuracion import ConfigBandejas  # noqa: E402
from servidor import crear_servidor, log  # noqa: E402
from simulador import AgenteSimulado, crear_control  # noqa: E402

TOKEN = "token-demo-simulada-0000"
PUERTO = 8788
# Orígenes de la vista previa local de la extensión (herramientas/vista_extension)
ORIGENES = ["http://localhost:8790", "http://127.0.0.1:8790"]


def main():
    agentes = {"canon": AgenteSimulado("canon", 16101, versiones=("1",)).iniciar(),
               "ricoh": AgenteSimulado("ricoh", 16102).iniciar(),
               "konica": AgenteSimulado("konica", 16103).iniciar()}
    impresoras = [{"id": m, "name": n, "brand": m, "ip": "127.0.0.1", "port": agentes[m].puerto,
                   "snmpVersion": "1" if m == "canon" else "2c", "community": "public"}
                  for m, n in (("canon", "Canon"), ("ricoh", "Ricoh"), ("konica", "Konica Minolta"))]
    # Copia temporal de trays.json para no tocar la configuración real
    carpeta = Path(tempfile.mkdtemp(prefix="demo-impresoras-"))
    shutil.copy(RAIZ / "config" / "trays.json", carpeta / "trays.json")
    httpd, sondeo = crear_servidor(impresoras, ConfigBandejas(carpeta / "trays.json"), TOKEN, ORIGENES,
                                   host="127.0.0.1", puerto=PUERTO, intervalo=3)
    sondeo.iniciar()
    control = crear_control(agentes)
    threading.Thread(target=control.serve_forever, daemon=True).start()
    log(f"Demo simulada: API http://127.0.0.1:{PUERTO} · control http://127.0.0.1:16100 · "
        f"escenarios {json.dumps(list(agentes))}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


if __name__ == "__main__":
    main()

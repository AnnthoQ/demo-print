"""FASE 0.3 — Monitoreo en bucle para las pruebas físicas guiadas (solo lectura).

Uso:
    python monitor.py                     # todas las impresoras, cada 3 s
    python monitor.py ricoh --intervalo 3
    python monitor.py canon konica --duracion 600

Registra solo los CAMBIOS (con marca de tiempo) en consola y en
logs/monitor_<fecha>.log. La primera lectura de cada impresora se registra completa.
"""
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))  # módulos SNMP compartidos

import mibs  # noqa: E402
from snmp_min import Sesion, SnmpError, SnmpTimeout

AQUI = Path(__file__).parent
LOGS = AQUI / "logs"
_lock = threading.Lock()
_archivo = None


def log(linea):
    marca = datetime.now().strftime("%H:%M:%S")
    with _lock:
        print(f"{marca} {linea}", flush=True)
        _archivo.write(f"{marca} {linea}\n")
        _archivo.flush()


def columna(s, oid):
    """Walk de una columna -> {indice: valor}."""
    return {o[len(oid) + 1:]: v for o, (t, v) in s.walk(oid)}


def leer(s, fijos):
    """Una lectura de estado, aplanada en {clave: texto} para comparar."""
    foto = {}
    for idx, v in columna(s, mibs.HR_PRINTER_STATUS).items():
        foto[f"hrPrinterStatus.{idx}"] = f"{v} {mibs.HR_PRINTER_STATUS_NOMBRES.get(v, '?')}"
    for idx, v in columna(s, mibs.HR_PRINTER_ERRORS).items():
        bits = mibs.decodificar_errores(v) if isinstance(v, bytes) else []
        foto[f"errores.{idx}"] = f"{v.hex() if isinstance(v, bytes) else v} [{','.join(bits) or '-'}]"
    for idx, v in columna(s, mibs.HR_DEVICE_STATUS).items():
        foto[f"hrDeviceStatus.{idx}"] = f"{v} {mibs.HR_DEVICE_STATUS_NOMBRES.get(v, '?')}"
    for idx, v in columna(s, mibs.PRT_COVER + ".3").items():
        foto[f"cubierta.{idx} {fijos['cubiertas'].get(idx, '')}"] = mibs.ESTADO_CUBIERTA.get(v, v)
    niveles = columna(s, mibs.PRT_INPUT + ".10")
    estados = columna(s, mibs.PRT_INPUT + ".11")
    for idx, nivel in niveles.items():
        nombre, maximo = fijos["bandejas"].get(idx, ("?", None))
        pct = f" ({round(nivel / maximo * 100)} %)" if maximo and maximo > 0 and nivel >= 0 else ""
        foto[f"bandeja.{idx} {nombre}"] = (f"{nivel}/{maximo}{pct} "
                                          f"{mibs.decodificar_estado_subunidad(estados.get(idx))}")
    codigos = columna(s, mibs.PRT_ALERT + ".7")
    descs = columna(s, mibs.PRT_ALERT + ".8")
    grupos = columna(s, mibs.PRT_ALERT + ".4")
    gidx = columna(s, mibs.PRT_ALERT + ".5")
    foto["alertas"] = " | ".join(sorted(
        f"{mibs.CODIGO_ALERTA.get(c, c)}({c}) {mibs.GRUPO_ALERTA.get(grupos.get(i), grupos.get(i))}"
        f"#{gidx.get(i)}: {mibs.texto(descs.get(i))}" for i, c in codigos.items())) or "-"
    foto["panel"] = " / ".join(mibs.texto(v) for v in columna(s, mibs.PRT_CONSOLE).values()) or "-"
    return foto


def vigilar(imp, intervalo, fin):
    nombre = imp["id"].upper()
    version = mibs.detectar_version(imp["ip"], imp["community"], imp.get("snmpVersion", ["2c", "1"]))
    if not version:
        log(f"[{nombre}] SIN RESPUESTA SNMP")
        return
    s = Sesion(imp["ip"], imp["community"], version, timeout=2.0, reintentos=1)
    maximos = columna(s, mibs.PRT_INPUT + ".9")
    nombres = columna(s, mibs.PRT_INPUT + ".13")
    fijos = {
        "bandejas": {i: (mibs.texto(nombres.get(i)) or f"(índice {i})", m) for i, m in maximos.items()},
        "cubiertas": {i: mibs.texto(v) for i, v in columna(s, mibs.PRT_COVER + ".2").items()},
    }
    anterior, lecturas, fallos = None, 0, 0
    while time.time() < fin:
        t0 = time.time()
        try:
            foto = leer(s, fijos)
        except (SnmpTimeout, SnmpError) as e:
            fallos += 1
            if anterior != "caida":
                log(f"[{nombre}] SIN RESPUESTA ({e})")
            anterior = "caida"
        else:
            lecturas += 1
            if anterior is None or anterior == "caida":
                log(f"[{nombre}] lectura completa ({time.time() - t0:.1f}s):")
                for k, v in foto.items():
                    log(f"[{nombre}]    {k} = {v}")
            else:
                for k in sorted(set(foto) | set(anterior)):
                    if foto.get(k) != anterior.get(k):
                        log(f"[{nombre}] CAMBIO {k}: {anterior.get(k)}  ->  {foto.get(k)}")
            anterior = foto
        time.sleep(max(0.0, intervalo - (time.time() - t0)))
    log(f"[{nombre}] fin: {lecturas} lecturas, {fallos} sin respuesta")


def main():
    global _archivo
    args = sys.argv[1:]
    intervalo = float(args[args.index("--intervalo") + 1]) if "--intervalo" in args else 3.0
    duracion = float(args[args.index("--duracion") + 1]) if "--duracion" in args else 4 * 3600
    ids = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or not args[i - 1].startswith("--"))]
    impresoras = [p for p in json.loads((AQUI / "printers.local.json").read_text(encoding="utf-8"))
                  if not ids or p["id"] in ids]
    LOGS.mkdir(exist_ok=True)
    _archivo = open(LOGS / f"monitor_{datetime.now():%Y%m%d_%H%M%S}.log", "a", encoding="utf-8")
    log(f"Monitor iniciado: {[p['id'] for p in impresoras]} cada {intervalo:.0f} s")
    fin = time.time() + duracion
    hilos = [threading.Thread(target=vigilar, args=(p, intervalo, fin), daemon=True) for p in impresoras]
    for h in hilos:
        h.start()
    try:
        for h in hilos:
            h.join()
    except KeyboardInterrupt:
        log("Monitor detenido por el usuario")


if __name__ == "__main__":
    main()

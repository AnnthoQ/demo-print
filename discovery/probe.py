"""FASE 0 — Descubrimiento de lo que reporta cada impresora (solo lectura).

Uso:
    python probe.py                 # todas las impresoras de printers.local.json
    python probe.py ricoh konica    # solo las indicadas
    python probe.py --sin-privadas  # omite los walks de MIB privada (más rápido)

Guarda los volcados crudos en dumps/<marca>_<ip>_<oid>.txt y un resumen en
dumps/<marca>_<ip>_resumen.json.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))  # módulos SNMP compartidos

import mibs  # noqa: E402
from ipp_min import ESTADOS_IMPRESORA, get_printer_attributes
from snmp_min import Sesion, SnmpError, SnmpTimeout, fmt_valor

AQUI = Path(__file__).parent
DUMPS = AQUI / "dumps"

# (descripción, OID raíz). Todo se recorre con walk, así funcionan los índices
# que use cada fabricante (hrDeviceIndex, prtInputIndex, etc.).
CONSULTAS = [
    ("system", "1.3.6.1.2.1.1"),
    ("hrDeviceDescr", mibs.HR_DEVICE_DESCR),
    ("hrDeviceStatus", mibs.HR_DEVICE_STATUS),
    ("hrPrinterStatus", mibs.HR_PRINTER_STATUS),
    ("hrPrinterDetectedErrorState", mibs.HR_PRINTER_ERRORS),
    ("prtCoverTable", mibs.PRT_COVER),
    ("prtInputTable", mibs.PRT_INPUT),
    ("prtAlertTable", mibs.PRT_ALERT),
    ("prtConsoleDisplayBufferText", mibs.PRT_CONSOLE),
    ("Printer-MIB completo", mibs.PRINTER_MIB),
]

RUTAS_IPP = [(631, False, "/ipp/print"), (631, False, "/ipp"), (631, False, "/printer"),
             (631, False, "/"), (443, True, "/ipp/print"), (443, True, "/ipp"), (80, False, "/ipp/print")]


def guardar(marca, ip, nombre, lineas):
    DUMPS.mkdir(exist_ok=True)
    ruta = DUMPS / f"{marca}_{ip}_{nombre}.txt"
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ruta


def volcar(sesion, marca, ip, oid, max_filas=20000):
    t0 = time.time()
    try:
        filas = sesion.walk(oid, max_filas=max_filas)
        err = None
    except (SnmpTimeout, SnmpError) as e:
        filas, err = [], str(e)
    lineas = [f"# walk {oid}  v{sesion.version}  {len(filas)} filas  {time.time() - t0:.1f}s"]
    if err:
        lineas.append(f"# ERROR: {err}")
    lineas += [f"{o} = {fmt_valor(*tv)}" for o, tv in filas]
    guardar(marca, ip, oid, lineas)
    return filas, err


def probar_ipp(marca, ip):
    intentos = []
    for puerto, tls, ruta in RUTAS_IPP:
        try:
            uri, status, attrs = get_printer_attributes(ip, ruta, puerto, tls)
        except Exception as e:  # noqa: BLE001 — registramos cualquier fallo de red/protocolo
            intentos.append(f"# {'ipps' if tls else 'ipp'}://{ip}:{puerto}{ruta} -> {type(e).__name__}: {e}")
            continue
        intentos.append(f"# {uri} -> status-code 0x{status:04x}, {len(attrs)} atributos")
        if status < 0x0100 and attrs.get("printer-state"):
            lineas = intentos + [f"{k} = {v if len(v) > 1 else v[0]}" for k, v in sorted(attrs.items())]
            guardar(marca, ip, "ipp", lineas)
            return {"uri": uri, "attrs": attrs}
    guardar(marca, ip, "ipp", intentos)
    return None


def probar(imp, con_privadas=True):
    marca, ip = imp["marca"], imp["ip"]
    print(f"\n=== {imp['name']} ({ip}) ===")
    version = mibs.detectar_version(ip, imp["community"], imp.get("snmpVersion", ["2c", "1"]))
    resumen = {"id": imp["id"], "marca": marca, "ip": ip, "snmp_version": version}
    if not version:
        print("  SNMP: sin respuesta en v2c ni v1 -> hay que activar SNMP en la impresora")
    else:
        print(f"  SNMP v{version} OK")
        s = Sesion(ip, imp["community"], version, timeout=3.0, reintentos=2)
        datos = {}
        for nombre, oid in CONSULTAS:
            filas, err = volcar(s, marca, ip, oid)
            datos[oid] = filas
            print(f"  {nombre:30s} {len(filas):5d} filas" + (f"  ({err})" if err else ""))

        def sistema(oid):
            return next((v for o, (t, v) in datos["1.3.6.1.2.1.1"] if o == oid + ".0"), None)

        sysdescr = mibs.texto(sistema(mibs.SYS_DESCR))
        resumen.update({
            "sysDescr": sysdescr,
            "sysName": mibs.texto(sistema(mibs.SYS_NAME)),
            "sysObjectID": sistema(mibs.SYS_OBJECT_ID),
            "hrDeviceDescr": [mibs.texto(v) for o, (t, v) in datos[mibs.HR_DEVICE_DESCR]],
            "hrDeviceStatus": {o.rsplit(".", 1)[1]: mibs.HR_DEVICE_STATUS_NOMBRES.get(v, v)
                               for o, (t, v) in datos[mibs.HR_DEVICE_STATUS]},
            "hrPrinterStatus": [mibs.HR_PRINTER_STATUS_NOMBRES.get(v, v) for o, (t, v) in datos[mibs.HR_PRINTER_STATUS]],
            "errorState": [{"hex": v.hex() if isinstance(v, bytes) else v,
                            "bits": mibs.decodificar_errores(v) if isinstance(v, bytes) else None}
                           for o, (t, v) in datos[mibs.HR_PRINTER_ERRORS]],
            "cubiertas": mibs.cubiertas(datos[mibs.PRT_COVER]),
            "bandejas": mibs.bandejas(datos[mibs.PRT_INPUT]),
            "alertas": mibs.alertas(datos[mibs.PRT_ALERT]),
            "panel": [mibs.texto(v) for o, (t, v) in datos[mibs.PRT_CONSOLE]],
            "filas_printer_mib": len(datos[mibs.PRINTER_MIB]),
        })
        print(f"  sysDescr: {sysdescr}")
        print(f"  hrPrinterStatus: {resumen['hrPrinterStatus']}  errores: {resumen['errorState']}")
        for b in resumen["bandejas"]:
            print(f"    bandeja {b['indice']:6s} {str(b['nombre']):25s} tipo={b['tipo']} bypass={b['bypass']} "
                  f"nivel={b['nivel']}/{b['max']} (unidad {b['unidad']}) estado={b['estado_txt']} medio={b['medio']}")
        for a in resumen["alertas"]:
            print(f"    alerta {a['severidad']} {a['grupo']} {a['codigo_txt']}: {a['descripcion']}")
        if resumen["panel"]:
            print(f"  panel: {resumen['panel']}")

        if con_privadas and marca in mibs.ENTERPRISE:
            oid = mibs.ENTERPRISE[marca]
            filas, err = volcar(s, marca, ip, oid, max_filas=50000)
            resumen["filas_mib_privada"] = len(filas)
            print(f"  MIB privada {oid}: {len(filas)} filas" + (f"  ({err})" if err else ""))

    ipp = probar_ipp(marca, ip)
    if ipp:
        a = ipp["attrs"]
        resumen["ipp"] = {
            "uri": ipp["uri"],
            "printer-make-and-model": a.get("printer-make-and-model"),
            "printer-state": [ESTADOS_IMPRESORA.get(x, x) for x in a.get("printer-state", [])],
            "printer-state-reasons": a.get("printer-state-reasons"),
            "printer-input-tray": a.get("printer-input-tray"),
            "ipp-versions-supported": a.get("ipp-versions-supported"),
        }
        print(f"  IPP OK en {ipp['uri']}: {resumen['ipp']['printer-state']} {resumen['ipp']['printer-state-reasons']}")
    else:
        resumen["ipp"] = None
        print("  IPP: no responde en las rutas probadas")

    (DUMPS / f"{marca}_{ip}_resumen.json").write_text(
        json.dumps(resumen, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return resumen


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    impresoras = json.loads((AQUI / "printers.local.json").read_text(encoding="utf-8"))
    for imp in impresoras:
        if not args or imp["id"] in args:
            probar(imp, con_privadas="--sin-privadas" not in sys.argv)


if __name__ == "__main__":
    main()

"""Lectura SNMP de una impresora y normalización al modelo común de la API.

El flujo es:  filas SNMP -> crudo_desde_filas() -> construir_bandejas() -> normalizar()
Las mismas funciones sirven para la red real, el simulador y los fixtures.
"""
from datetime import datetime, timezone

import mibs
from snmp_min import bajo, oid_tuple

PRT_SUPPLIES = "1.3.6.1.2.1.43.11.1.1"
SUBARBOLES = ["1.3.6.1.2.1.1", mibs.HR_DEVICE_STATUS, mibs.HR_PRINTER_STATUS, mibs.HR_PRINTER_ERRORS,
              mibs.PRT_COVER, mibs.PRT_INPUT, PRT_SUPPLIES, mibs.PRT_CONSOLE, mibs.PRT_ALERT]

ESTADO_TXT = {"disponible": "Disponible", "ocupada": "Ocupada", "calentando": "Calentando",
              "error": "Con error", "fuera_de_linea": "Fuera de línea"}

# Bits que ponen la impresora en "error"
BITS_ERROR = {"jammed": "Papel atascado", "doorOpen": "Tapa abierta", "offline": "Fuera de línea",
              "noPaper": "Sin papel", "noToner": "Sin tóner", "outputFull": "Bandeja de salida llena"}
# Bits que solo son avisos
BITS_AVISO = {"lowPaper": "Papel bajo (según la impresora)", "lowToner": "Tóner bajo",
              "serviceRequested": "Servicio solicitado", "overduePreventMaint": "Mantenimiento pendiente",
              "outputNearFull": "Salida casi llena", "markerSupplyMissing": "Falta un consumible",
              "inputTrayMissing": "Bandeja abierta o ausente", "outputTrayMissing": "Falta bandeja de salida"}

# Alertas que ya se reflejan en otros campos y no explican una detención
ALERTAS_INFORMATIVAS = {801, 807, 808, 1104}

TAMANOS = {(210, 297): "A4", (297, 420): "A3", (148, 210): "A5", (216, 279): "Carta",
           (216, 356): "Oficio", (279, 432): "11×17", (305, 457): "12×18", (320, 450): "SRA3"}


def ahora_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def leer_filas(sesion):
    filas = []
    for raiz in SUBARBOLES:
        filas += sesion.walk(raiz)
    return filas


def _sub(filas, raiz):
    return [(o, tv) for o, tv in filas if bajo(o, raiz)]


def _columna(filas, raiz):
    return {o[len(raiz) + 1:]: v for o, (t, v) in filas if bajo(o, raiz)}


def crudo_desde_filas(filas):
    sistema = _columna(filas, "1.3.6.1.2.1.1")
    pst = sorted(_columna(filas, mibs.HR_PRINTER_STATUS).items(), key=lambda kv: oid_tuple(kv[0]))
    dev_idx, estado = pst[0] if pst else ("1", 2)
    errores = _columna(filas, mibs.HR_PRINTER_ERRORS).get(dev_idx, b"")
    return {
        "sysDescr": mibs.texto(sistema.get("1.0")),
        "sysName": mibs.texto(sistema.get("5.0")),
        "hrPrinterStatus": estado,
        "hrDeviceStatus": _columna(filas, mibs.HR_DEVICE_STATUS).get(dev_idx),
        "errores": errores if isinstance(errores, bytes) else b"",
        "entradas": mibs.tabla(_sub(filas, mibs.PRT_INPUT), mibs.PRT_INPUT),
        "alertas": mibs.alertas(_sub(filas, mibs.PRT_ALERT)),
        "cubiertas": mibs.cubiertas(_sub(filas, mibs.PRT_COVER)),
        "pantalla": [t for t in (mibs.texto(v) for o, (tp, v) in _sub(filas, mibs.PRT_CONSOLE)) if t],
        "suministros": mibs.tabla(_sub(filas, PRT_SUPPLIES), PRT_SUPPLIES),
    }


def _entero_positivo(*valores):
    return all(isinstance(v, int) and v > 0 for v in valores)


def tamano_papel(c):
    f, x = c.get(6), c.get(7)
    if not _entero_positivo(f, x):
        f, x = c.get(4), c.get(5)
    factor = {3: 25.4 / 10000, 4: 1 / 1000}.get(c.get(3))
    if not (factor and _entero_positivo(f, x)):
        return None
    a, b = sorted((f * factor, x * factor))
    for (w, h), nombre in TAMANOS.items():
        if abs(a - w) <= 3 and abs(b - h) <= 3:
            return nombre
    return f"{a:.0f}×{b:.0f} mm"


def construir_bandejas(crudo, adaptador, cfg_bandejas):
    """cfg_bandejas: {id_bandeja: bool}. Sin configuración: activas salvo las bypass."""
    bandejas = []
    for idx, c in sorted(crudo["entradas"].items(), key=lambda kv: oid_tuple(kv[0])):
        n = idx.split(".")[-1]
        descripcion = mibs.texto(c.get(18))
        nombre = adaptador.nombre_bandeja(idx, mibs.texto(c.get(13)), descripcion)
        nivel, maximo, est = c.get(10), c.get(9), c.get(11)
        bypass = adaptador.es_bypass(idx, c.get(2), nombre, descripcion, maximo)
        # Una bandeja abierta reporta 0: no es lo mismo que vacía. Solo la alerta 801 lo
        # confirma: en reposo la Canon marca todos los cajones "no disponibles" (estado 5)
        # pero conserva sus niveles.
        abierta = any(a["codigo"] == 801 and str(a["grupo_indice"]) == n for a in crudo["alertas"])
        capacidad = maximo if _entero_positivo(maximo) else None
        porcentaje, tipo = None, adaptador.tipo_nivel
        if not isinstance(nivel, int) or nivel in (-1, -2):
            tipo, etiqueta = "desconocido", "Desconocido"
        elif nivel == -3:
            tipo, etiqueta = "binario", "Con papel"
        elif nivel < 0:
            tipo, etiqueta = "desconocido", "Desconocido"
        elif capacidad:
            porcentaje = max(0, min(100, round(nivel / capacidad * 100)))
            etiqueta = "Vacía" if nivel == 0 else f"≈ {nivel} hojas · {porcentaje} %"
        else:
            etiqueta = "Vacía" if nivel == 0 else f"≈ {nivel} hojas"
        if abierta:
            etiqueta, porcentaje = "Bandeja abierta", None
        bandejas.append({
            "id": idx, "name": nombre, "isBypass": bypass,
            "enabled": bool(cfg_bandejas.get(idx, not bypass)),
            "levelType": tipo, "percent": porcentaje,
            "sheets": nivel if isinstance(nivel, int) and nivel >= 0 and not abierta else None,
            "capacity": capacidad, "paper": tamano_papel(c), "open": abierta, "label": etiqueta,
        })
    return bandejas


def estabilizar(bandejas, anteriores, pendientes):
    """Evita falsas alarmas de "Vacía": al abrir un cajón algunas impresoras
    reportan 0 durante una lectura antes de marcarlo como abierto. Una bandeja
    que pasa de tener papel a 0 se confirma recién en la lectura siguiente.
    `pendientes` es un set que se conserva entre lecturas."""
    previas = {b["id"]: b for b in anteriores or []}
    salida = []
    for b in bandejas:
        prev = previas.get(b["id"])
        cayo = b["label"] == "Vacía" and prev and (prev["sheets"] or 0) > 0
        if cayo and b["id"] not in pendientes:
            pendientes.add(b["id"])
            salida.append({**prev, "enabled": b["enabled"]})
            continue
        if b["label"] != "Vacía":
            pendientes.discard(b["id"])
        salida.append(b)
    return salida


def _color(desc):
    d = (desc or "").lower()
    for clave, color in (("black", "negro"), ("negro", "negro"), ("cyan", "cian"), ("cian", "cian"),
                         ("magenta", "magenta"), ("yellow", "amarillo"), ("amarillo", "amarillo")):
        if clave in d:
            return color
    return "otro"


def _consumibles(crudo):
    out = []
    for idx, c in sorted(crudo["suministros"].items(), key=lambda kv: oid_tuple(kv[0])):
        desc, maximo, nivel = mibs.texto(c.get(6)), c.get(8), c.get(9)
        pct = round(nivel / maximo * 100) if _entero_positivo(maximo) and isinstance(nivel, int) and nivel >= 0 else None
        out.append({
            "name": desc, "percent": pct, "color": _color(desc),
            # receptáculo que se llena (tóner residual); Ricoh no marca la clase, solo el nombre
            "waste": c.get(4) == 4 or any(p in (desc or "").lower() for p in ("residual", "waste")),
            "label": f"{pct} %" if pct is not None else {-3: "Con existencia"}.get(nivel, "Desconocido"),
        })
    return out


def normalizar(imp, crudo, adaptador, bandejas, umbral=20, version=None):
    """Modelo normalizado de la API a partir de una lectura y sus bandejas ya construidas."""
    bits = mibs.decodificar_errores(crudo["errores"])
    alertas = crudo["alertas"]
    activas = [b for b in bandejas if b["enabled"]]

    tapa_abierta = "doorOpen" in bits or any(c["estado"] in ("doorOpen", "interlockOpen") for c in crudo["cubiertas"])
    atasco = "jammed" in bits or any(a["codigo"] == 8 for a in alertas)

    motivos = [BITS_ERROR[b] for b in bits if b in BITS_ERROR]
    if atasco and "Papel atascado" not in motivos:
        motivos.insert(0, "Papel atascado")
    if tapa_abierta and "Tapa abierta" not in motivos:
        motivos.append("Tapa abierta")
    # Solo hrDeviceStatus=down indica detención: hrPrinterStatus=other sin "down" es el
    # modo reposo (ahorro de energía) de la Canon
    detenida = crudo["hrDeviceStatus"] == 5
    if detenida and not motivos:
        # Detenida sin ningún bit de error (p. ej. "Operation Error" de la Konica)
        otras = [a["descripcion"] for a in alertas if a["codigo"] not in ALERTAS_INFORMATIVAS and a["descripcion"]]
        motivos.append("Detenida: " + (", ".join(otras) or " / ".join(crudo["pantalla"]) or "motivo desconocido"))

    avisos = [BITS_AVISO[b] for b in bits if b in BITS_AVISO and b not in adaptador.bits_ignorados]
    for b in activas:
        if b["open"]:
            avisos.append(f"{b['name']} abierta")
        elif b["label"] == "Vacía":
            avisos.append(f"Sin papel en {b['name']}")
        elif b["percent"] is not None and b["percent"] < umbral:
            avisos.append(f"Papel bajo en {b['name']} (≈ {b['sheets']} hojas)")

    if motivos:
        estado = "error"
    elif crudo["hrPrinterStatus"] == 4:
        estado = "ocupada"
    elif crudo["hrPrinterStatus"] == 5:
        estado = "calentando"
    else:
        estado = "disponible"
    en_reposo = estado == "disponible" and crudo["hrPrinterStatus"] == 1

    return {
        "id": imp["id"], "name": imp["name"], "model": crudo["sysDescr"] or "", "ip": imp["ip"],
        "online": True, "snmpVersion": version, "state": estado,
        "stateLabel": "En reposo" if en_reposo else ESTADO_TXT[estado], "sleeping": en_reposo,
        "errors": {
            "jammed": atasco,
            "noPaper": "noPaper" in bits or any(b["label"] == "Vacía" for b in activas),
            "doorOpen": tapa_abierta,
            "lowPaper": any(b["percent"] is not None and 0 < b["percent"] < umbral for b in activas),
            "offline": "offline" in bits,
        },
        "reasons": motivos, "warnings": avisos,
        "panelText": " / ".join(crudo["pantalla"]) or None,
        "trays": bandejas,
        "supplies": _consumibles(crudo),
        "covers": [{"name": c["descripcion"], "open": c["estado"] in ("doorOpen", "interlockOpen")}
                   for c in crudo["cubiertas"]],
        "alerts": [{"severity": a["severidad"], "group": a["grupo"], "code": a["codigo"],
                    "codeName": a["codigo_txt"], "description": a["descripcion"]} for a in alertas],
        "raw": {"hrPrinterStatus": crudo["hrPrinterStatus"], "hrDeviceStatus": crudo["hrDeviceStatus"],
                "errorState": crudo["errores"].hex(), "errorBits": bits},
        "lastUpdate": ahora_iso(),
    }


def fuera_de_linea(imp, error, anterior=None):
    """Impresora sin respuesta SNMP. Conserva modelo y bandejas de la última lectura."""
    return {
        "id": imp["id"], "name": imp["name"], "model": (anterior or {}).get("model", ""), "ip": imp["ip"],
        "online": False, "snmpVersion": None, "state": "fuera_de_linea",
        "stateLabel": ESTADO_TXT["fuera_de_linea"], "sleeping": False,
        "errors": {"jammed": False, "noPaper": False, "doorOpen": False, "lowPaper": False, "offline": True},
        "reasons": [f"Sin respuesta SNMP: {error}"], "warnings": [], "panelText": None,
        "trays": [{**t, "label": "Sin datos", "percent": None, "sheets": None}
                  for t in (anterior or {}).get("trays", [])],
        "supplies": [], "covers": [], "alerts": [], "raw": {}, "lastUpdate": ahora_iso(),
    }

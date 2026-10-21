"""Constantes y decodificadores de Host Resources MIB y Printer-MIB (RFC 3805)."""
from snmp_min import Sesion, SnmpError, SnmpTimeout, NO_SUCH_INSTANCE, NO_SUCH_OBJECT

SYS_DESCR = "1.3.6.1.2.1.1.1"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2"
SYS_NAME = "1.3.6.1.2.1.1.5"
HR_DEVICE_DESCR = "1.3.6.1.2.1.25.3.2.1.3"
HR_DEVICE_STATUS = "1.3.6.1.2.1.25.3.2.1.5"
HR_PRINTER_STATUS = "1.3.6.1.2.1.25.3.5.1.1"
HR_PRINTER_ERRORS = "1.3.6.1.2.1.25.3.5.1.2"
PRT_COVER = "1.3.6.1.2.1.43.6.1.1"
PRT_INPUT = "1.3.6.1.2.1.43.8.2.1"
PRT_ALERT = "1.3.6.1.2.1.43.18.1.1"
PRT_CONSOLE = "1.3.6.1.2.1.43.16.5.1.2"
PRINTER_MIB = "1.3.6.1.2.1.43"

ENTERPRISE = {"canon": "1.3.6.1.4.1.1602", "ricoh": "1.3.6.1.4.1.367", "konica": "1.3.6.1.4.1.18334"}

HR_PRINTER_STATUS_NOMBRES = {1: "other", 2: "unknown", 3: "idle", 4: "printing", 5: "warmup"}
HR_DEVICE_STATUS_NOMBRES = {1: "unknown", 2: "running", 3: "warning", 4: "testing", 5: "down"}

BITS_ERROR = ["lowPaper", "noPaper", "lowToner", "noToner", "doorOpen", "jammed", "offline",
              "serviceRequested", "inputTrayMissing", "outputTrayMissing", "markerSupplyMissing",
              "outputNearFull", "outputFull", "inputTrayEmpty", "overduePreventMaint"]

TIPOS_ENTRADA = {1: "other", 2: "unknown", 3: "sheetFeedAutoRemovableTray",
                 4: "sheetFeedAutoNonRemovableTray", 5: "sheetFeedManual",
                 6: "continuousRoll", 7: "continuousFanFold"}

ESTADO_CUBIERTA = {1: "other", 3: "doorOpen", 4: "doorClosed", 5: "interlockOpen", 6: "interlockClosed"}

SEVERIDAD_ALERTA = {1: "other", 3: "critical", 4: "warning", 5: "warningBinaryChangeEvent"}
GRUPO_ALERTA = {1: "other", 3: "storage", 4: "hrDevice", 5: "generalPrinter", 6: "cover",
                7: "localization", 8: "input", 9: "output", 10: "marker", 11: "markerSupplies",
                12: "markerColorant", 13: "mediaPath", 14: "channel", 15: "interpreter",
                16: "consoleDisplay", 17: "consoleLights", 18: "alert", 30: "finDevice",
                31: "finSupply", 32: "finSupplyMediaInput", 33: "finAttribute"}
CODIGO_ALERTA = {1: "other", 2: "unknown", 3: "coverOpen", 4: "coverClosed", 5: "interlockOpen",
                 6: "interlockClosed", 7: "configurationChange", 8: "jam", 9: "subunitMissing",
                 12: "subunitAlmostEmpty", 13: "subunitEmpty", 14: "subunitAlmostFull",
                 15: "subunitFull", 18: "subunitOpened", 19: "subunitClosed", 22: "subunitOffline",
                 23: "subunitPowerSaver", 24: "subunitWarmingUp", 30: "subunitUnrecoverableFailure",
                 501: "doorOpen", 502: "doorClosed", 503: "powerUp", 504: "powerDown",
                 507: "printerReadyToPrint", 801: "inputMediaTrayMissing",
                 802: "inputMediaSizeChange", 807: "inputMediaSupplyLow",
                 808: "inputMediaSupplyEmpty", 809: "inputManualInputRequest",
                 901: "outputMediaTrayMissing", 902: "outputMediaTrayAlmostFull",
                 903: "outputMediaTrayFull", 1101: "markerTonerEmpty", 1104: "markerTonerAlmostEmpty",
                 1107: "markerWasteTonerAlmostFull", 1109: "markerWasteTonerFull",
                 1301: "mediaPathMediaTrayMissing", 1302: "mediaPathMediaTrayAlmostFull",
                 1303: "mediaPathMediaTrayFull"}

PALABRAS_BYPASS = ("bypass", "manual", "multi", "mpt", "mp tray", "mptray", "handfeed")


def decodificar_errores(octetos):
    """Bitmask hrPrinterDetectedErrorState: el bit 0 es el MSB del primer octeto."""
    activos = []
    for n, nombre in enumerate(BITS_ERROR):
        b = n // 8
        if b < len(octetos) and octetos[b] & (0x80 >> (n % 8)):
            activos.append(nombre)
    return activos


def decodificar_estado_subunidad(v):
    """PrtSubUnitStatusTC (prtInputStatus, etc.)."""
    if not isinstance(v, int) or v < 0:
        return str(v)
    disp = {0: "disponible-inactiva", 1: "disponible-en-espera", 2: "disponible-activa",
            3: "disponible-ocupada", 4: "no-disponible-a-pedido", 5: "no-disponible-averiada",
            6: "desconocido"}.get(v & 7, f"?{v & 7}")
    extra = [n for bit, n in ((8, "alerta-no-critica"), (16, "alerta-critica"),
                              (32, "fuera-de-linea"), (64, "en-transicion")) if v & bit]
    return "+".join([disp] + extra)


def texto(valor):
    if isinstance(valor, bytes):
        try:
            t = valor.decode("utf-8")
        except UnicodeDecodeError:
            t = valor.decode("cp1252", "replace")  # p. ej. Ricoh envía "Tóner" en Latin-1
        return t.strip("\x00").strip()
    return valor


def tabla(filas, base):
    """Convierte un walk de una tabla en {indice: {columna: valor}}."""
    largo = len(base.split("."))
    out = {}
    for oid, (tipo, valor) in filas:
        partes = oid.split(".")
        col, idx = int(partes[largo]), ".".join(partes[largo + 1:])
        out.setdefault(idx, {})[col] = valor
    return out


def es_bypass(tipo, *nombres):
    if tipo == 5:
        return True
    t = " ".join(str(texto(n) or "") for n in nombres).lower()
    return any(p in t for p in PALABRAS_BYPASS)


def bandejas(filas):
    out = []
    for idx, c in tabla(filas, PRT_INPUT).items():
        nombre, desc = texto(c.get(13)), texto(c.get(18))
        out.append({
            "indice": idx, "nombre": nombre, "descripcion": desc,
            "tipo": TIPOS_ENTRADA.get(c.get(2), c.get(2)),
            "bypass": es_bypass(c.get(2), nombre, desc),
            "unidad": c.get(8), "max": c.get(9), "nivel": c.get(10),
            "estado": c.get(11), "estado_txt": decodificar_estado_subunidad(c.get(11)),
            "medio": texto(c.get(12)),
        })
    return out


def alertas(filas):
    out = []
    for idx, c in tabla(filas, PRT_ALERT).items():
        out.append({
            "indice": idx,
            "severidad": SEVERIDAD_ALERTA.get(c.get(2), c.get(2)),
            "grupo": GRUPO_ALERTA.get(c.get(4), c.get(4)),
            "grupo_indice": c.get(5), "ubicacion": c.get(6),
            "codigo": c.get(7), "codigo_txt": CODIGO_ALERTA.get(c.get(7), c.get(7)),
            "descripcion": texto(c.get(8)),
        })
    return out


def cubiertas(filas):
    return [{"indice": i, "descripcion": texto(c.get(2)),
             "estado": ESTADO_CUBIERTA.get(c.get(3), c.get(3))}
            for i, c in tabla(filas, PRT_COVER).items()]


def detectar_version(ip, community, preferidas=("2c", "1"), timeout=2.0):
    for v in preferidas:
        try:
            Sesion(ip, community, v, timeout=timeout, reintentos=1).get(SYS_DESCR + ".0")
            return v
        except (SnmpTimeout, SnmpError):
            continue
    return None

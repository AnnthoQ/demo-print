"""Cliente IPP mínimo de SOLO LECTURA: únicamente Get-Printer-Attributes (0x000B).

No implementa ninguna operación que modifique la impresora.
"""
import http.client
import ssl
import struct

GET_PRINTER_ATTRIBUTES = 0x000B

ESTADOS_IMPRESORA = {3: "idle", 4: "processing", 5: "stopped"}


def _attr(tag, nombre, valor):
    n, v = nombre.encode(), valor.encode()
    return struct.pack(">BH", tag, len(n)) + n + struct.pack(">H", len(v)) + v


def _peticion(uri, atributos):
    cuerpo = struct.pack(">BBHI", 1, 1, GET_PRINTER_ATTRIBUTES, 1) + b"\x01"
    cuerpo += _attr(0x47, "attributes-charset", "utf-8")
    cuerpo += _attr(0x48, "attributes-natural-language", "en")
    cuerpo += _attr(0x45, "printer-uri", uri)
    for i, a in enumerate(atributos):
        cuerpo += _attr(0x44, "requested-attributes" if i == 0 else "", a)
    return cuerpo + b"\x03"


def _valor(tag, v):
    if tag in (0x21, 0x23) and len(v) == 4:
        return struct.unpack(">i", v)[0]
    if tag == 0x22:
        return bool(v[0]) if v else None
    if tag == 0x33 and len(v) == 8:
        return "%d-%d" % struct.unpack(">ii", v)
    if tag == 0x32 and len(v) == 9:
        x, y, u = struct.unpack(">iiB", v)
        return f"{x}x{y}{'dpi' if u == 3 else 'dpcm'}"
    if tag in (0x35, 0x36):  # text/nameWithLanguage
        ln = struct.unpack(">H", v[:2])[0]
        tl = struct.unpack(">H", v[2 + ln:4 + ln])[0]
        return v[4 + ln:4 + ln + tl].decode("utf-8", "replace")
    if tag == 0x30 or 0x41 <= tag <= 0x49:
        return v.decode("utf-8", "replace")
    if tag in (0x10, 0x12, 0x13):
        return {0x10: "<unsupported>", 0x12: "<unknown>", 0x13: "<no-value>"}[tag]
    return v.hex()


def _parsear(data):
    """Devuelve (status_code, {nombre: [valores]}). Las colecciones se aplanan
    como 'atributo.miembro'."""
    status = struct.unpack(">H", data[2:4])[0]
    attrs, i, nombre, pila, miembro = {}, 8, None, [], None
    while i < len(data):
        tag = data[i]
        i += 1
        if tag == 0x03:
            break
        if tag < 0x10:
            continue  # delimitador de grupo
        nl = struct.unpack(">H", data[i:i + 2])[0]
        n = data[i + 2:i + 2 + nl].decode("utf-8", "replace")
        i += 2 + nl
        vl = struct.unpack(">H", data[i:i + 2])[0]
        v = data[i + 2:i + 2 + vl]
        i += 2 + vl
        if n:
            nombre = n
        if tag == 0x34:  # begCollection
            pila.append(miembro if pila else nombre)
            miembro = None
            continue
        if tag == 0x37:  # endCollection
            pila.pop()
            miembro = None
            continue
        if tag == 0x4A:  # memberAttrName
            miembro = v.decode()
            continue
        clave = ".".join(pila + [miembro or "?"]) if pila else nombre
        attrs.setdefault(clave, []).append(_valor(tag, v))
    return status, attrs


def get_printer_attributes(ip, ruta="/ipp/print", puerto=631, tls=False, timeout=4.0,
                           atributos=("all",)):
    esquema = "ipps" if tls else "ipp"
    uri = f"{esquema}://{ip}:{puerto}{ruta}"
    if tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # certificados autofirmados de impresoras
        # Firmware antiguo (p. ej. Canon iR-ADV) usa certificados con firmas débiles
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        ctx.minimum_version = ssl.TLSVersion.TLSv1
        con = http.client.HTTPSConnection(ip, puerto, timeout=timeout, context=ctx)
    else:
        con = http.client.HTTPConnection(ip, puerto, timeout=timeout)
    try:
        con.request("POST", ruta, body=_peticion(uri, list(atributos)),
                    headers={"Content-Type": "application/ipp"})
        r = con.getresponse()
        cuerpo = r.read()
        if r.status != 200 or len(cuerpo) < 8:
            raise RuntimeError(f"HTTP {r.status} en {uri}")
        status, attrs = _parsear(cuerpo)
        return uri, status, attrs
    finally:
        con.close()

"""Cliente SNMP mínimo (v1 / v2c) de SOLO LECTURA: GET, GETNEXT, GETBULK y walk.

Implementado únicamente con la biblioteca estándar de Python para no tener que
instalar dependencias en el equipo. No existe (ni existirá) soporte para SET.
"""
import random
import socket

VERSIONES = {"1": 0, "2c": 1}

# Etiquetas de PDU
GET, GETNEXT, RESPONSE, GETBULK = 0xA0, 0xA1, 0xA2, 0xA5

# Marcadores de excepción de SNMPv2
NO_SUCH_OBJECT, NO_SUCH_INSTANCE, END_OF_MIB = "noSuchObject", "noSuchInstance", "endOfMibView"


class SnmpTimeout(Exception):
    pass


class SnmpError(Exception):
    def __init__(self, status, index):
        super().__init__(f"error-status={status} error-index={index}")
        self.status, self.index = status, index


# ---------------------------------------------------------------- BER
def _len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag, payload):
    return bytes([tag]) + _len(len(payload)) + payload


def _int(v):
    return _tlv(0x02, v.to_bytes((v.bit_length() + 8) // 8 or 1, "big", signed=True))


def _oid(oid):
    parts = [int(x) for x in oid.strip(".").split(".")]
    body = bytearray()
    for arc in [parts[0] * 40 + parts[1]] + parts[2:]:
        chunk = [arc & 0x7F]
        arc >>= 7
        while arc:
            chunk.append(0x80 | (arc & 0x7F))
            arc >>= 7
        body.extend(reversed(chunk))
    return _tlv(0x06, bytes(body))


def _read(buf, i):
    tag = buf[i]
    l = buf[i + 1]
    i += 2
    if l & 0x80:
        n = l & 0x7F
        l = int.from_bytes(buf[i:i + n], "big")
        i += n
    return tag, buf[i:i + l], i + l


def _dec_oid(b):
    arcs, v = [], 0
    for x in b:
        v = (v << 7) | (x & 0x7F)
        if not x & 0x80:
            arcs.append(v)
            v = 0
    a = min(arcs[0] // 40, 2)
    return ".".join(map(str, [a, arcs[0] - 40 * a] + arcs[1:]))


def _dec_val(tag, v):
    """Devuelve (tipo, valor) con nombres de tipo al estilo de snmpwalk."""
    if tag == 0x02:
        return "INTEGER", int.from_bytes(v, "big", signed=True)
    if tag == 0x04:
        return "OCTETS", bytes(v)
    if tag == 0x05:
        return "NULL", None
    if tag == 0x06:
        return "OID", _dec_oid(v)
    if tag == 0x40:
        return "IpAddress", ".".join(str(x) for x in v)
    if tag in (0x41, 0x42, 0x43, 0x46):
        nombre = {0x41: "Counter32", 0x42: "Gauge32", 0x43: "Timeticks", 0x46: "Counter64"}[tag]
        return nombre, int.from_bytes(v, "big")
    if tag == 0x80:
        return NO_SUCH_OBJECT, None
    if tag == 0x81:
        return NO_SUCH_INSTANCE, None
    if tag == 0x82:
        return END_OF_MIB, None
    return f"tag_0x{tag:02x}", bytes(v)


def oid_tuple(oid):
    return tuple(int(x) for x in oid.strip(".").split("."))


def bajo(oid, raiz):
    """True si `oid` está dentro del subárbol `raiz`."""
    o, r = oid_tuple(oid), oid_tuple(raiz)
    return o[:len(r)] == r and len(o) > len(r)


# ---------------------------------------------------------------- sesión
class Sesion:
    def __init__(self, ip, community="public", version="2c", timeout=2.0, reintentos=1, puerto=161):
        self.ip, self.puerto = ip, puerto
        self.community = community.encode()
        self.version = version
        self._ver = VERSIONES[version]
        self.timeout, self.reintentos = timeout, reintentos

    def _mensaje(self, pdu_tag, reqid, oids, a=0, b=0):
        vbs = b"".join(_tlv(0x30, _oid(o) + b"\x05\x00") for o in oids)
        pdu = _tlv(pdu_tag, _int(reqid) + _int(a) + _int(b) + _tlv(0x30, vbs))
        return _tlv(0x30, _int(self._ver) + _tlv(0x04, self.community) + pdu)

    @staticmethod
    def _respuesta(data):
        _, msg, _ = _read(data, 0)
        _, _, i = _read(msg, 0)        # versión
        _, _, i = _read(msg, i)        # comunidad
        _, pdu, _ = _read(msg, i)
        _, rid, j = _read(pdu, 0)
        _, es, j = _read(pdu, j)
        _, ei, j = _read(pdu, j)
        _, vbl, _ = _read(pdu, j)
        out, k = [], 0
        while k < len(vbl):
            _, vb, k = _read(vbl, k)
            _, ob, m = _read(vb, 0)
            vt, vv, _ = _read(vb, m)
            out.append((_dec_oid(ob), _dec_val(vt, vv)))
        return (int.from_bytes(rid, "big", signed=True), int.from_bytes(es, "big"),
                int.from_bytes(ei, "big"), out)

    def _pedir(self, pdu_tag, oids, a=0, b=0):
        reqid = random.randint(1, 0x7FFFFFFF)
        datos = self._mensaje(pdu_tag, reqid, oids, a, b)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(self.timeout)
            for _ in range(self.reintentos + 1):
                s.sendto(datos, (self.ip, self.puerto))
                try:
                    while True:
                        resp, _ = s.recvfrom(65535)
                        rid, es, ei, vbs = self._respuesta(resp)
                        if rid != reqid:
                            continue  # respuesta tardía de un intento anterior
                        if es:
                            raise SnmpError(es, ei)
                        return vbs
                except socket.timeout:
                    continue
        raise SnmpTimeout(f"{self.ip}: sin respuesta SNMP v{self.version}")

    def get(self, *oids):
        return self._pedir(GET, list(oids))

    def getnext(self, *oids):
        return self._pedir(GETNEXT, list(oids))

    def walk(self, raiz, max_filas=20000, repeticiones=25):
        """Recorre un subárbol. Usa GETBULK en v2c y GETNEXT en v1."""
        filas, actual = [], raiz
        while len(filas) < max_filas:
            try:
                if self._ver == 0:
                    vbs = self.getnext(actual)
                else:
                    vbs = self._pedir(GETBULK, [actual], 0, repeticiones)
            except SnmpError as e:
                if e.status == 2:  # noSuchName en v1 = fin de la MIB
                    break
                raise
            avanzo = False
            for oid, (tipo, valor) in vbs:
                if tipo == END_OF_MIB or not bajo(oid, raiz):
                    return filas
                if oid_tuple(oid) <= oid_tuple(actual):
                    return filas  # el agente no avanza: evitar bucle infinito
                filas.append((oid, (tipo, valor)))
                actual, avanzo = oid, True
            if not avanzo:
                break
        return filas


# ---------------------------------------------------------------- lado agente (solo para el simulador)
_TAGS_SIN_SIGNO = {"Counter32": 0x41, "Gauge32": 0x42, "Timeticks": 0x43, "Counter64": 0x46}
_TAGS_EXCEPCION = {NO_SUCH_OBJECT: 0x80, NO_SUCH_INSTANCE: 0x81, END_OF_MIB: 0x82}


def codificar_valor(tipo, valor):
    if tipo == "INTEGER":
        return _int(valor)
    if tipo == "OCTETS":
        return _tlv(0x04, bytes(valor))
    if tipo == "OID":
        return _oid(valor)
    if tipo == "IpAddress":
        return _tlv(0x40, bytes(int(x) for x in valor.split(".")))
    if tipo in _TAGS_SIN_SIGNO:
        return _tlv(_TAGS_SIN_SIGNO[tipo], valor.to_bytes((valor.bit_length() + 8) // 8 or 1, "big"))
    if tipo in _TAGS_EXCEPCION:
        return bytes([_TAGS_EXCEPCION[tipo], 0])
    return b"\x05\x00"  # NULL


def parsear_peticion(data):
    """Devuelve (version, community, pdu_tag, reqid, a, b, [oids])."""
    _, msg, _ = _read(data, 0)
    _, ver, i = _read(msg, 0)
    _, comm, i = _read(msg, i)
    tag, pdu, _ = _read(msg, i)
    _, rid, j = _read(pdu, 0)
    _, a, j = _read(pdu, j)
    _, b, j = _read(pdu, j)
    _, vbl, _ = _read(pdu, j)
    oids, k = [], 0
    while k < len(vbl):
        _, vb, k = _read(vbl, k)
        _, ob, _ = _read(vb, 0)
        oids.append(_dec_oid(ob))
    entero = lambda x: int.from_bytes(x, "big", signed=True)  # noqa: E731
    return entero(ver), bytes(comm), tag, entero(rid), entero(a), entero(b), oids


def construir_respuesta(version, community, reqid, varbinds, error_status=0, error_index=0):
    vbs = b"".join(_tlv(0x30, _oid(o) + codificar_valor(t, v)) for o, (t, v) in varbinds)
    pdu = _tlv(RESPONSE, _int(reqid) + _int(error_status) + _int(error_index) + _tlv(0x30, vbs))
    return _tlv(0x30, _int(version) + _tlv(0x04, community) + pdu)


# ---------------------------------------------------------------- formato
def fmt_valor(tipo, valor):
    """Formato legible similar al de snmpwalk (STRING / Hex-STRING)."""
    if tipo != "OCTETS":
        return f"{tipo}: {valor}"
    hexa = " ".join(f"{x:02X}" for x in valor)
    try:
        texto = valor.decode("utf-8")
        imprimible = all(c.isprintable() or c in "\r\n\t" for c in texto)
    except UnicodeDecodeError:
        imprimible = False
    if imprimible and texto:
        # En cadenas cortas mostramos también el hex (p. ej. bitmasks como "@")
        return f'STRING: "{texto}"' + (f"  (Hex: {hexa})" if len(valor) <= 8 else "")
    return f"Hex-STRING: {hexa}" if valor else 'STRING: ""'

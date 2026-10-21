"""Lectura de volcados de texto (formato de discovery/dumps) a filas SNMP.

Lo usan las pruebas unitarias (fixtures) y el simulador.
"""
import re
from pathlib import Path

_FILA = re.compile(r"^(\d+(?:\.\d+)+) = (.*)$")
_STRING = re.compile(r'^STRING: "(.*)"(?:  \(Hex: ([0-9A-F ]+)\))?$', re.S)


def _valor(texto):
    m = _STRING.match(texto)
    if m:
        return "OCTETS", bytes.fromhex(m.group(2)) if m.group(2) else m.group(1).encode("utf-8")
    tipo, _, resto = texto.partition(": ")
    if tipo == "Hex-STRING":
        return "OCTETS", bytes.fromhex(resto)
    if tipo in ("INTEGER", "Counter32", "Gauge32", "Timeticks", "Counter64"):
        return tipo, int(resto)
    if tipo in ("OID", "IpAddress"):
        return tipo, resto
    return "NULL", None


def parsear(texto):
    """Texto de volcado -> [(oid, (tipo, valor))]. Ignora comentarios '#'."""
    filas, actual = [], None
    for linea in texto.splitlines():
        if linea.startswith("#"):
            continue
        m = _FILA.match(linea)
        if m:
            if actual:
                filas.append((actual[0], _valor(actual[1])))
            actual = [m.group(1), m.group(2)]
        elif actual:
            actual[1] += "\n" + linea  # cadena con saltos de línea
    if actual:
        filas.append((actual[0], _valor(actual[1])))
    return filas


def cargar(ruta):
    return parsear(Path(ruta).read_text(encoding="utf-8"))

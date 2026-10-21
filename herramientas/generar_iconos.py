"""Genera los íconos PNG de la extensión (16, 32, 48, 128 px) sin dependencias.

Silueta de impresora en gris claro sobre fondo transparente, del mismo tono que los
íconos de la barra de Chrome en tema oscuro.

Uso:  python herramientas/generar_iconos.py
"""
import struct
import zlib
from pathlib import Path

DESTINO = Path(__file__).resolve().parent.parent / "extension" / "icons"
GRIS = (199, 199, 199)  # ~ #C7C7C7, como el ícono de extensiones de Chrome
HUECO = None            # recorta (deja transparente)


def rect(x0, y0, x1, y1, r=0.0):
    def dentro(x, y):
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        if r <= 0:
            return True
        cx = min(max(x, x0 + r), x1 - r)
        cy = min(max(y, y0 + r), y1 - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    return dentro


# Silueta en una cuadrícula de 24 unidades; los bordes caen en múltiplos de 1,5 para que
# a 16 px coincidan con píxeles enteros y se vean nítidos. Se aplican en orden.
FORMAS = [
    (rect(6, 3, 18, 6), GRIS),            # hoja de entrada
    (rect(1.5, 7.5, 22.5, 16.5), GRIS),   # cuerpo
    (rect(6, 12, 18, 21), GRIS),          # marco de la hoja impresa
    (rect(7.5, 12, 16.5, 19.5), HUECO),   # hoja impresa (hueco)
    (rect(18, 9, 19.5, 10.5), HUECO),     # luz de estado (hueco cuadrado)
]


def pixel(px, py, tam, muestras=6):
    cubiertos = 0
    for sy in range(muestras):
        for sx in range(muestras):
            x = (px + (sx + 0.5) / muestras) / tam * 24
            y = (py + (sy + 0.5) / muestras) / tam * 24
            color = HUECO
            for dentro, c in FORMAS:
                if dentro(x, y):
                    color = c
            cubiertos += color is not HUECO
    return (*GRIS, round(255 * cubiertos / muestras ** 2))


def png(tam):
    filas = b"".join(b"\x00" + bytes(c for x in range(tam) for c in pixel(x, y, tam)) for y in range(tam))

    def bloque(tipo, datos):
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + bloque(b"IHDR", struct.pack(">IIBBBBB", tam, tam, 8, 6, 0, 0, 0))
            + bloque(b"IDAT", zlib.compress(filas, 9)) + bloque(b"IEND", b""))


if __name__ == "__main__":
    DESTINO.mkdir(parents=True, exist_ok=True)
    for tam in (16, 32, 48, 128):
        (DESTINO / f"{tam}.png").write_bytes(png(tam))
        print(f"icons/{tam}.png")

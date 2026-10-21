from .base import Adaptador
from .canon import Canon
from .konica import Konica
from .ricoh import Ricoh

_POR_MARCA = {a.marca: a for a in (Canon, Ricoh, Konica)}


def obtener_adaptador(marca):
    return _POR_MARCA.get((marca or "").lower(), Adaptador)()

"""Ricoh Aficio MP (probado con MP 7502). Al abrir la tapa activa doorOpen y offline."""
from .base import Adaptador


class Ricoh(Adaptador):
    marca = "ricoh"
    tipo_nivel = "pasos"

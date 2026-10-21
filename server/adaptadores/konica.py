"""Konica Minolta bizhub PRO (probado con bizhub PRO 920 / IC-203)."""
from .base import Adaptador


class Konica(Adaptador):
    marca = "konica"
    # Escalones de 25 % observados: 100 -> 75 -> 50
    tipo_nivel = "pasos"
    # serviceRequested está siempre activo en este equipo
    bits_ignorados = frozenset({"serviceRequested"})

    def nombre_bandeja(self, indice, nombre, descripcion):
        # No reporta nombres; sus alertas usan "Tray1", "Tray3"... con el mismo índice
        return nombre or f"Bandeja {indice.split('.')[-1]}"

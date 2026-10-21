"""Canon iR-ADV (probado con iR-ADV C7570, SNMP solo v1)."""
from .base import Adaptador


class Canon(Adaptador):
    marca = "canon"
    # Reporta el nivel por escalones (10 % = casi vacía)
    tipo_nivel = "pasos"
    # lowPaper queda activo si cualquier cajón está vacío, aunque no se use;
    # el papel bajo se calcula por bandeja activa
    bits_ignorados = frozenset({"lowPaper"})

"""Adaptador estándar: Host Resources MIB + Printer-MIB (RFC 3805).

En la Fase 0 las tres impresoras reportaron todo lo necesario por las MIB
estándar; las MIB privadas no aportaron nada (la de Ricoh repite la tabla de
bandejas estándar). Por eso los adaptadores solo ajustan detalles de cada marca.
"""
import mibs


class Adaptador:
    marca = "generico"
    # Cómo mide el papel la impresora cuando el nivel es >= 0
    tipo_nivel = "exacto"
    # Bits de hrPrinterDetectedErrorState que el modelo deja siempre activos y
    # que no deben mostrarse como aviso
    bits_ignorados = frozenset()

    def nombre_bandeja(self, indice, nombre, descripcion):
        return nombre or descripcion or f"Bandeja {indice.split('.')[-1]}"

    def es_bypass(self, indice, tipo, nombre, descripcion, maximo):
        return mibs.es_bypass(tipo, nombre, descripcion)

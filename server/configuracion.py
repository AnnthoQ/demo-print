"""Configuración del servidor: variables de entorno (.env), impresoras y bandejas."""
import json
import os
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def cargar_env(ruta=RAIZ / ".env"):
    """Carga KEY=VALOR de .env sin pisar variables ya definidas en el entorno."""
    if not Path(ruta).exists():
        return
    for linea in Path(ruta).read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


def entorno(clave, defecto=None, tipo=str):
    valor = os.environ.get(clave)
    return defecto if valor in (None, "") else tipo(valor)


def _resolver(valor, contexto):
    if isinstance(valor, str) and valor.startswith("env:"):
        nombre = valor[4:]
        if nombre not in os.environ:
            raise ValueError(f"{contexto}: falta la variable de entorno {nombre} (defínela en .env)")
        return os.environ[nombre]
    return valor


def cargar_impresoras(ruta):
    datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    impresoras = []
    for p in datos:
        for campo in ("id", "name", "ip"):
            if not p.get(campo):
                raise ValueError(f"{ruta}: cada impresora necesita '{campo}'")
        impresoras.append({
            "id": p["id"], "name": p["name"], "brand": p.get("brand", p["id"]), "ip": p["ip"],
            "port": int(p.get("port", 161)), "snmpVersion": str(p.get("snmpVersion", "2c")),
            "community": _resolver(p.get("community", "public"), f"impresora {p['id']}"),
        })
    return impresoras


class ConfigBandejas:
    """Bandejas activas/inactivas, compartidas por todos los usuarios: {impresora: {bandeja: bool}}."""

    def __init__(self, ruta):
        self.ruta = Path(ruta)
        self._lock = threading.Lock()
        self._datos = json.loads(self.ruta.read_text(encoding="utf-8")) if self.ruta.exists() else {}

    def de(self, impresora):
        with self._lock:
            return dict(self._datos.get(impresora, {}))

    def todas(self):
        with self._lock:
            return json.loads(json.dumps(self._datos))

    def fijar(self, impresora, bandeja, activa):
        with self._lock:
            self._datos.setdefault(impresora, {})[bandeja] = bool(activa)
            tmp = self.ruta.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, self.ruta)  # escritura atómica

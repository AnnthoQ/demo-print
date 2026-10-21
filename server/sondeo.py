"""Sondeo periódico de las impresoras (un hilo por impresora) y generación de eventos."""
import itertools
import queue
import threading
import time
import traceback
from collections import deque

import lector
from adaptadores import obtener_adaptador
from snmp_min import Sesion, SnmpError, SnmpTimeout


def condiciones(p, umbral):
    """Problemas activos de una impresora: {clave: mensaje}. Cada clave genera un
    evento al aparecer y otro al resolverse."""
    c = {}
    nombre = p["name"]
    if p["state"] == "fuera_de_linea":
        c["fuera_de_linea"] = f"{nombre} no responde"
        return c
    if p["errors"]["jammed"]:
        c["atasco"] = f"Papel atascado en {nombre}"
    if p["errors"]["doorOpen"]:
        c["tapa_abierta"] = f"Tapa abierta en {nombre}"
    if p["state"] == "error" and not (p["errors"]["jammed"] or p["errors"]["doorOpen"]):
        c["error"] = f"{nombre}: " + "; ".join(p["reasons"])
    for t in p["trays"]:
        if not t["enabled"]:
            continue
        if t["label"] == "Vacía":
            c[f"sin_papel:{t['id']}"] = f"Sin papel en {nombre} · {t['name']}"
        elif t["percent"] is not None and t["percent"] < umbral:
            c[f"papel_bajo:{t['id']}"] = f"Papel bajo en {nombre} · {t['name']} ({t['label']})"
    return c


class Sondeo:
    def __init__(self, impresoras, bandejas, intervalo=5.0, umbral=20, timeout=2.0, reintentos=1):
        self.impresoras = impresoras
        self.bandejas = bandejas
        self.intervalo, self.umbral = intervalo, umbral
        self.timeout, self.reintentos = timeout, reintentos
        self._lock = threading.Lock()
        self._estado = {}          # id -> impresora normalizada
        self._crudo = {}           # id -> (crudo, versión) de la última lectura
        self._pendientes = {p["id"]: set() for p in impresoras}
        self._condiciones = {}     # id -> {clave: mensaje}
        self.eventos = deque(maxlen=200)
        self._seq = itertools.count(1)
        self._suscriptores = set()

    # ------------------------------------------------------------ consulta
    def impresoras_estado(self):
        with self._lock:
            return [self._estado[p["id"]] for p in self.impresoras if p["id"] in self._estado]

    def impresora(self, id_):
        with self._lock:
            return self._estado.get(id_)

    def existe(self, id_):
        return any(p["id"] == id_ for p in self.impresoras)

    # ------------------------------------------------------------ SSE
    def suscribir(self):
        q = queue.Queue(maxsize=500)
        with self._lock:
            self._suscriptores.add(q)
        return q

    def desuscribir(self, q):
        with self._lock:
            self._suscriptores.discard(q)

    def _publicar(self, tipo, datos):
        for q in list(self._suscriptores):
            try:
                q.put_nowait((tipo, datos))
            except queue.Full:
                pass  # cliente demasiado lento: se pierde el mensaje, no el servidor

    # ------------------------------------------------------------ ciclo
    def iniciar(self):
        for p in self.impresoras:
            threading.Thread(target=self._vigilar, args=(p,), name=f"sondeo-{p['id']}", daemon=True).start()

    def _vigilar(self, imp):
        sesion, fallos = None, 0
        adaptador = obtener_adaptador(imp["brand"])
        while True:
            t0 = time.time()
            try:
                if sesion is None:
                    sesion = Sesion(imp["ip"], imp["community"], imp["snmpVersion"],
                                    timeout=self.timeout, reintentos=self.reintentos, puerto=imp["port"])
                crudo = lector.crudo_desde_filas(lector.leer_filas(sesion))
                fallos = 0
                with self._lock:
                    self._crudo[imp["id"]] = (crudo, imp["snmpVersion"])
                self._actualizar(imp, adaptador)
            except (SnmpTimeout, SnmpError, OSError) as e:
                sesion, fallos = None, fallos + 1
                # Un paquete UDP perdido no es una impresora apagada: se exigen 2 fallos seguidos
                if fallos < 2 and self.impresora(imp["id"]):
                    time.sleep(1)
                    continue
                with self._lock:
                    self._crudo.pop(imp["id"], None)
                    nuevo = lector.fuera_de_linea(imp, e, self._estado.get(imp["id"]))
                self._registrar(imp, nuevo)
            except Exception:  # noqa: BLE001 — un dato inesperado no debe detener el hilo
                traceback.print_exc()
            time.sleep(max(0.5, self.intervalo - (time.time() - t0)))

    def _actualizar(self, imp, adaptador=None):
        """Recalcula el modelo desde la última lectura (también tras cambiar bandejas)."""
        adaptador = adaptador or obtener_adaptador(imp["brand"])
        with self._lock:
            if imp["id"] not in self._crudo:
                # Sin lectura actual (fuera de línea): solo se reflejan los cambios de bandejas
                actual = self._estado.get(imp["id"])
                if actual:
                    cfg = self.bandejas.de(imp["id"])
                    actual["trays"] = [{**t, "enabled": cfg.get(t["id"], t["enabled"])} for t in actual["trays"]]
                return
            crudo, version = self._crudo[imp["id"]]
            anterior = self._estado.get(imp["id"])
            bandejas = lector.construir_bandejas(crudo, adaptador, self.bandejas.de(imp["id"]))
            bandejas = lector.estabilizar(bandejas, (anterior or {}).get("trays"), self._pendientes[imp["id"]])
            nuevo = lector.normalizar(imp, crudo, adaptador, bandejas, self.umbral, version)
        self._registrar(imp, nuevo)

    def reevaluar(self, id_):
        imp = next(p for p in self.impresoras if p["id"] == id_)
        self._actualizar(imp)

    def _registrar(self, imp, nuevo):
        with self._lock:
            anterior = self._estado.get(imp["id"])
            self._estado[imp["id"]] = nuevo
            antes = self._condiciones.get(imp["id"])
            ahora = condiciones(nuevo, self.umbral)
            self._condiciones[imp["id"]] = ahora
        eventos = []
        if anterior and anterior["state"] != nuevo["state"]:
            eventos.append(self._evento(nuevo, "estado", None, True,
                                        f"{nuevo['name']}: {anterior['stateLabel']} → {nuevo['stateLabel']}"))
        if antes is not None:  # la primera lectura no genera avisos de "apareció"
            for clave in ahora.keys() - antes.keys():
                tipo, _, bandeja = clave.partition(":")
                eventos.append(self._evento(nuevo, tipo, bandeja or None, True, ahora[clave]))
            for clave in antes.keys() - ahora.keys():
                tipo, _, bandeja = clave.partition(":")
                eventos.append(self._evento(nuevo, tipo, bandeja or None, False, "Resuelto: " + antes[clave]))
        for e in eventos:
            self.eventos.append(e)
            self._publicar("alerta", e)
        if anterior is None or _sin_hora(anterior) != _sin_hora(nuevo):
            self._publicar("printer", nuevo)

    def _evento(self, p, tipo, bandeja, activo, mensaje):
        return {"id": next(self._seq), "time": lector.ahora_iso(), "printerId": p["id"], "printerName": p["name"],
                "type": tipo, "trayId": bandeja, "active": activo, "message": mensaje}


def _sin_hora(p):
    return {k: v for k, v in p.items() if k != "lastUpdate"}

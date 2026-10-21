# Informe de la Fase 0: descubrimiento de impresoras

Fecha: 21-10-2026. Todo se hizo por SNMP de **solo lectura** (GET/GETNEXT/GETBULK) e IPP `Get-Printer-Attributes`.
No se envió ningún trabajo ni se cambió nada en las impresoras. Las impresiones de prueba las envió el usuario.

Herramientas: cliente SNMP/IPP propio en Python (`server/snmp_min.py`, `server/ipp_min.py`), sin instalar Net-SNMP.
Volcados crudos en `discovery/dumps/` y registros del monitoreo en `discovery/logs/` (ambos fuera del repositorio).

## Por impresora

| | Canon | Ricoh | Konica Minolta |
|---|---|---|---|
| Modelo (sysDescr) | Canon iR-ADV C7570 /P | RICOH Aficio MP 7502 (Gestetner MP 7502 en Windows) | KONICA MINOLTA bizhub PRO 920 |
| Firmware | — (no lo reporta) | 1.04 | Controladora IC-203 v1.30 |
| sysObjectID | 1.3.6.1.4.1.1602.4.7 | 1.3.6.1.4.1.367.1.1 | 1.3.6.1.4.1.18334.1.1.1.2.1.22.2 |
| SNMP que funciona | **solo v1** (v2c no responde) | v1 y v2c | v1 y v2c |
| Comunidad | public | public | public |
| IPP | ❌ 631 cerrado; 443 TLS antiguo y 404 | ✅ `ipp://…:631/ipp` (IPP 1.0–2.0) | ✅ `ipp://…:631/ipp` (IPP 1.1) |
| Texto del panel | ✅ (en inglés) | ✅ (en español) | ❌ |

## Matriz de capacidades

| | Ocupada | Atasco | Sin papel | Tapa abierta | Nivel de papel |
|---|---|---|---|---|---|
| Canon | ✅ `hrPrinterStatus=4` | 🟡 bit `jammed` + alerta 8 (estándar; no observado en real) | ✅ nivel 0 + alerta 808 | ✅ bit `doorOpen` + `prtCoverTable` + alerta 3 | 🟡 por pasos |
| Ricoh | ✅ `=4`; además `warmup (5)` | 🟡 ídem | ✅ nivel 0 + alerta 808 + panel | ✅ `doorOpen` (+ `offline`) + cubierta + alerta | 🟡 por pasos |
| Konica | ✅ `=4` (dura pocos segundos: 92 ppm) | 🟡 ídem | ✅ nivel 0 + alerta 808 | ✅ `doorOpen` + cubierta + alerta | 🟡 por pasos |

Fuente: MIB estándar (Host Resources + Printer-MIB, RFC 3805) en todos los casos. **Atasco**: las tres implementan
los objetos estándar donde se reporta (bitmask y `prtAlertTable`), pero no se provocó un atasco real a propósito.
Se valida con el simulador; si ocurre uno real, revisar el registro para confirmarlo.

## Bandejas

| Impresora | Índice | Nombre | Tipo | Papel | Capacidad | Uso | Escalones observados |
|---|---|---|---|---|---|---|---|
| Canon | 1.1 | Stack Bypass | **bypass** (manual) | — | 250 | ✗ | 0 |
| Canon | 1.2 | Drawer 1 | fija | A4 | 1100 | ✅ | 0 · 10 · 50 · 100 % |
| Canon | 1.3 | Drawer 2 | fija | A4 | 1100 | ✗ | 0 |
| Canon | 1.4 | Drawer 3 | extraíble | A3 | 550 | ✅ | 0 · 10 % |
| Canon | 1.5 | Drawer 4 | extraíble | A4 | 550 | ✗ | 10 % |
| Ricoh | 1.1 | Tray 1 (LCT) | extraíble | A4 | 3100 | ✅ | 15 · 25 % |
| Ricoh | 1.2 | Tray 2 | extraíble | A4 | 550 | ✗ | 0 |
| Ricoh | 1.3 | Tray 3 | extraíble | A3 | 550 | ✅ | 0 · 30 % |
| Ricoh | 1.4 | Bypass Tray | **bypass** (por nombre) | A3 | 100 | ✗ | 0 |
| Konica | 1.1 | (sin nombre) → Bandeja 1 | fija | A3 | 500 | ✗ | 0 |
| Konica | 1.2 | (sin nombre) → Bandeja 2 | fija | A4 | 500 | ✅ | 100 · 75 · 50 · 25 · 10 · 0 % |
| Konica | 1.3 | (sin nombre) → Bandeja 3 | fija | A3 | 1000 | ✗ | 0 |
| Konica | 1.5 | (sin nombre) → Bandeja 5 | fija (¿bypass?) | 11×17 | 150 | ✗ | 0 |

**Tipo de nivel**: las tres reportan un número (`prtInputCurrentLevel` ≥ 0) que es **por pasos**, no exacto. Ninguna
reportó -2 (desconocido) ni -3 (binario) en las bandejas. El nivel es aproximado: se muestra como «≈ N hojas · P %».

## Hallazgos que cambiaron el diseño

1. **Bits siempre activos.** Canon tiene `lowPaper` (por cajones vacíos que no se usan) y `lowToner`; Konica tiene
   `serviceRequested`. Si «cualquier error activo → error», estarían siempre en rojo. Solo cuentan como error:
   `jammed`, `doorOpen`, `offline`, `noPaper`, `noToner`, `outputFull`. El resto son avisos, y el adaptador de cada
   marca ignora los que su firmware deja siempre activos.
2. **El bit `noPaper` no sirve por bandeja.** Ricoh tenía la Bandeja 2 vacía con el bitmask en `00`. El papel se
   evalúa por bandeja activa (nivel 0 y alerta 808).
3. **Detenida sin bits de error.** La Konica mostró «Operation Error» (al pedir una bandeja vacía) solo como
   `hrPrinterStatus=other` + `hrDeviceStatus=down`. Eso también se trata como error, con el texto de la alerta.
4. **Bandeja abierta ≠ vacía.** Al abrir un cajón el nivel cae a 0 hasta cerrarlo (alerta 801 o `prtInputStatus`
   «no disponible»). En la Canon hay una lectura con 0 *antes* de la alerta. Por eso se muestra «Bandeja abierta» y un
   0 nuevo se confirma en la lectura siguiente.
5. **MIB privadas innecesarias.** La de Ricoh repite la tabla estándar; las de Konica y Canon no aportan nada para
   estas funciones. IPP tampoco aporta bandejas (`printer-input-tray` no existe en ninguna).
6. **Textos en Latin-1** (Ricoh: «Tóner») y **Konica sin nombres** de bandeja ni texto de panel.
7. **Stack.** Node.js no estaba instalado y el cliente Python ya estaba validado contra las tres impresoras, así que el
   servidor se hizo en Python sin dependencias (aprobado por el usuario).

## Recomendación aplicada

- Usar solo MIB estándar con un adaptador mínimo por marca (`server/adaptadores/`).
- Bandejas activas por defecto: Canon Drawer 1 y 3, Ricoh Tray 1 y 3, Konica Bandeja 2. El resto, desactivadas.
- Pendiente de confirmar con el uso real: un atasco real (bit `jammed` / alerta 8) y si la Bandeja 5 de la Konica es
  la bandeja manual.

# Monitor de impresoras de red

Extensión de Chrome que muestra el estado de las impresoras de la oficina (disponible, ocupada, calentando, con error,
fuera de línea), el papel de cada bandeja y avisa con una notificación del sistema cuando hay un atasco.

```
Impresoras ──SNMP (UDP 161, solo lectura)──▶ Servidor (Python) ──HTTP + token──▶ Extensión de Chrome
```

Una extensión de Chrome no puede hablar SNMP, por eso un servidor pequeño en la red local consulta las impresoras y
expone una API. **Todo es de solo lectura**: no se usa `SET`, ni operaciones IPP que modifiquen la impresora.

| Impresora | Modelo | SNMP | Notas |
|---|---|---|---|
| Canon | iR-ADV C7570 | v1 (no responde v2c) | Niveles de papel por pasos (10 %, 50 %…) |
| Ricoh | Aficio MP 7502 | v2c | Tóner en Latin-1; al abrir la tapa activa `offline` |
| Konica Minolta | bizhub PRO 920 | v2c | Sin nombres de bandeja ni texto de pantalla |

Detalle completo en [discovery/REPORTE.md](discovery/REPORTE.md).

## Estructura

| Carpeta | Contenido |
|---|---|
| `server/` | Servidor de monitoreo, adaptadores por marca, simulador SNMP y pruebas |
| `extension/` | Extensión de Chrome (Manifest V3) |
| `config/` | `printers.json` (IPs, **no se sube**), `trays.json` (bandejas activas, compartidas) |
| `discovery/` | Herramientas de la Fase 0: `probe.py`, `monitor.py`, `panel.py` y el informe |
| `deploy/` | Servicio de Windows (NSSM) y Linux (systemd) |
| `herramientas/` | Generador de íconos, demo con simulador y vista previa de la extensión |

## 1. Servidor

Requiere **Python 3.10 o superior** y nada más (solo biblioteca estándar).

1. Copia `.env.example` como `.env` y completa:
   - `API_TOKEN`: genera uno con `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
   - `SNMP_COMMUNITY_*`: comunidad de **solo lectura** de cada impresora.
   - `ALLOWED_ORIGINS`: `chrome-extension://<ID>` (el ID aparece en `chrome://extensions` tras instalarla).
2. Copia `config/printers.example.json` como `config/printers.json` y pon las IPs.
3. Ejecuta:

```bash
python server/servidor.py
```

Comprueba `http://<servidor>:8787/health`.

### Cambiar IPs, comunidades o bandejas

- **IP o versión SNMP**: edita `config/printers.json` y reinicia el servidor.
- **Comunidad**: edita `SNMP_COMMUNITY_<MARCA>` en `.env` y reinicia.
- **Bandejas activas**: desde la página de opciones de la extensión (se guarda en `config/trays.json`).
  Una bandeja que no figure en el archivo queda activa, salvo las bypass.

### API

Todas las rutas `/api/*` requieren `Authorization: Bearer <API_TOKEN>`.

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health` | Estado del servidor (sin token) |
| GET | `/api/printers` | Lista con estado completo |
| GET | `/api/printers/:id` | Una impresora |
| GET | `/api/trays` | Bandejas por impresora |
| PUT | `/api/trays/:printerId/:trayId` | `{"enabled": true\|false}` |
| GET | `/api/events` | Server-Sent Events: `snapshot`, `printer`, `alerta` (acepta `?token=`) |

Reglas de estado: sin respuesta SNMP en 2 lecturas seguidas → `fuera_de_linea`; atasco, tapa abierta, `offline`,
`noPaper`, `noToner` o impresora detenida (`hrDeviceStatus=down`) → `error`; `hrPrinterStatus=4` → `ocupada`;
`=5` → `calentando`; si no → `disponible`. Avisos permanentes del firmware (`lowPaper` en Canon, `serviceRequested`
en Konica) no cuentan como error. Una bandeja abierta reporta 0, pero se muestra como «Bandeja abierta», no como vacía.

### Inicio automático sin servicio (`.bat`)

- `iniciar_servidor.bat`: lanza el servidor **en segundo plano y sin ventana** (`pythonw.exe`). Si ya hay un servidor
  en el puerto (por ejemplo, el servicio de Windows), no inicia otro. Registro: `logs\servidor-bat.log`.
- `detener_servidor.bat`: cierra solo el servidor iniciado con el `.bat`; nunca toca el servicio de Windows.
- Para que arranque al iniciar sesión: crea un acceso directo de `iniciar_servidor.bat`, colócalo en la carpeta de
  inicio (Win+R → `shell:startup`) y en sus propiedades elige **Ejecutar: Minimizada**.

Diferencia con el servicio: el `.bat` en la carpeta de inicio arranca cuando **ese usuario inicia sesión**; el
servicio arranca con Windows aunque nadie inicie sesión. Usa uno de los dos (si están ambos, el `.bat` no hace nada).

### Como servicio

- **Windows** (PowerShell como administrador, requiere [NSSM](https://nssm.cc)):
  ```powershell
  powershell -ExecutionPolicy Bypass -File deploy\windows\instalar_servicio.ps1 -Nssm "C:\ruta\nssm.exe" -AbrirFirewall
  ```
  `-AbrirFirewall` crea la regla de entrada TCP 8787 para que otros equipos usen el servidor.
- **Linux**: ver `deploy/linux/monitor-impresoras.service`.
- **Docker** (opcional): ver `Dockerfile`.

## 2. Extensión de Chrome

Requiere **Chrome 109 o superior**, así que funciona también en **Windows 7** (Chrome 109 es la última versión para
ese sistema; compruébalo en `chrome://settings/help`). En Chrome 109 la extensión usa alternativas para las APIs más
nuevas, y la revisión de respaldo pasa de 30 s a 1 min; los avisos en tiempo real funcionan igual.

1. Abre `chrome://extensions`, activa **Modo de desarrollador** → **Cargar descomprimida** → carpeta `extension/`.
2. Se abre la configuración: escribe la URL del servidor (p. ej. `http://10.0.101.51:8787`) y el token, y pulsa
   **Guardar y probar conexión**. Chrome pide permiso solo para esa dirección.
3. Copia el ID de la extensión a `ALLOWED_ORIGINS` en `.env` y reinicia el servidor.

- **Popup**: tarjeta por impresora con estado en color, barras de papel de las bandejas activas y banner rojo si hay
  atasco. Se actualiza cada 5 s mientras está abierto.
- **Tiempo real**: un documento oculto (`offscreen.html`) mantiene una conexión SSE con `/api/events`; cada cambio
  despierta al service worker, que avisa en ~5 s. Una alarma cada 30 s queda como respaldo.
- **Avisos**: cada problema se avisa **una sola vez** cuando aparece (y opcionalmente cuando se resuelve), como:
  - **Aviso flotante sobre la página** (predeterminado): se dibuja encima de la web activa, dentro de un Shadow DOM.
    Los críticos (atasco, tapa, error, sin red) quedan hasta cerrarlos. En páginas internas de Chrome no se puede
    dibujar; ahí, o si Chrome no está en primer plano, se usa la notificación de Windows.
  - **Notificación de Windows**, o **ambos**.
- Ícono: `!` rojo si hay atasco/error/fuera de línea; número de impresoras con avisos; `?` sin servidor.
- **Opciones**: servidor y token; forma de aviso, posición y duración; **matriz impresora × tipo de aviso** (con
  atajos «Todos / Solo críticos / Ninguno»); umbral de papel bajo (20 %) y bandejas activas.

Permisos: `alarms`, `notifications`, `storage`, `offscreen`, `scripting` y acceso de red al servidor configurado.
Los avisos flotantes requieren además permiso para todos los sitios (`http://*/*`, `https://*/*`), que se pide solo
al elegir ese modo: la extensión únicamente dibuja el aviso, no lee las páginas.

Estados: «En reposo» es la impresora en ahorro de energía (`hrPrinterStatus=other` sin `hrDeviceStatus=down`); se
muestra en verde y no es un error.

## 3. Pruebas

```bash
python -m unittest discover -s server/pruebas -v
```

- `test_normalizacion.py`: parser y cálculo de estado/papel con los volcados reales (`server/pruebas/fixtures/`),
  incluidos los casos -2, -3, 0, bitmask de atasco, bandeja abierta e impresora sin respuesta.
- `test_api.py`: servidor completo contra el simulador SNMP por UDP (token, CORS, bandejas, eventos, SSE).

### Simulador y demo sin hardware

```bash
python herramientas/demo_simulada.py
```

Levanta las 3 impresoras simuladas y una API en `http://127.0.0.1:8788` (token `token-demo-simulada-0000`).
Forzar escenarios: `http://127.0.0.1:16100/escenario?impresora=canon&nombre=atasco`
(`normal`, `ocupada`, `calentando`, `atasco`, `tapa_abierta`, `sin_papel`, `papel_bajo`, `bandeja_abierta`,
`detenida`, `fuera_de_linea`). Para ver el popup y las opciones sin instalar la extensión:
`python -m http.server 8790 --bind 127.0.0.1` y abre `http://localhost:8790/herramientas/vista_extension/`.

## Seguridad

- `.env`, `config/printers.json`, `discovery/dumps/` y `discovery/logs/` están en `.gitignore` (IPs, comunidades, token).
- Usa comunidades SNMP de solo lectura. Nunca se envía `snmpset` ni se cambia configuración de las impresoras.

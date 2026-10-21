// Service worker: recibe los cambios en tiempo real (vía offscreen.html) o cada 30 s como
// respaldo, avisa cada problema una sola vez (aviso flotante en la página y/o notificación
// de Windows) y actualiza el ícono.
import { PERMISO_PAGINAS, api, badge, debeNotificar, leerConfig, problemas } from "./lib/comun.js";

const ALARMA = "revisar-impresoras";
const ICONO = "icons/128.png";
const DURACION_RESUELTO = 8; // segundos
let cola = Promise.resolve(); // serializa revisiones para no duplicar avisos
let revisionPendiente = false;

function encolar(tarea) {
  cola = cola.then(tarea).catch((e) => console.error("Monitor de impresoras:", e));
  return cola;
}

/** Agrupa ráfagas de eventos en una sola consulta al servidor. */
function pedirRevision() {
  if (revisionPendiente) return cola;
  revisionPendiente = true;
  return encolar(async () => {
    revisionPendiente = false;
    await revisar();
  });
}

function iniciar() {
  chrome.alarms.create(ALARMA, { periodInMinutes: 0.5 }); // respaldo: 30 s es el mínimo de Chrome
  asegurarConexion();
  pedirRevision();
}

chrome.runtime.onInstalled.addListener(({ reason }) => {
  if (reason === "install") chrome.runtime.openOptionsPage();
  iniciar();
});
chrome.runtime.onStartup.addListener(iniciar);

chrome.alarms.onAlarm.addListener((a) => {
  if (a.name !== ALARMA) return;
  asegurarConexion();
  pedirRevision();
});

chrome.storage.onChanged.addListener((cambios, area) => {
  if (area === "local" && cambios.config) {
    asegurarConexion();
    pedirRevision();
  }
});

chrome.runtime.onMessage.addListener((msg, _remitente, responder) => {
  switch (msg?.tipo) {
    case "cambio": // evento en tiempo real del servidor
      pedirRevision();
      return false;
    case "offscreen_listo":
      asegurarConexion();
      return false;
    case "datos": // el popup ya consultó el servidor
      encolar(async () => procesar(msg.impresoras, await leerConfig())).then(() => responder(true));
      return true;
    case "revisar":
      pedirRevision().then(() => responder(true));
      return true;
    case "cerrar_aviso":
      quitarDePantalla([msg.id]).then(() => responder(true));
      return true;
    case "abrir_popup":
      try {
        chrome.action.openPopup?.()?.catch(() => {}); // no disponible en todas las versiones
      } catch { /* Chrome 109: openPopup restringido */ }
      return false;
    case "probar_sistema":
      notificarSistema({ id: `prueba-${Date.now()}`, titulo: "Monitor de impresoras",
        mensaje: "Las notificaciones de Windows funcionan.", nivel: "info" }).then(responder);
      return true;
    case "reiniciar_avisos":
      // Olvida qué se avisó en esta sesión: los problemas actuales se vuelven a avisar
      encolar(async () => {
        await chrome.storage.session.set({ activos: {}, pantalla: [] });
        await revisar();
      }).then(() => responder(true));
      return true;
    default:
      return false;
  }
});

chrome.notifications.onClicked.addListener((id) => chrome.notifications.clear(id));

// Al cambiar de pestaña o cargar una página, los avisos que siguen vigentes se muestran ahí también
chrome.tabs.onActivated.addListener(({ tabId }) => sincronizarPestanas(tabId));
chrome.tabs.onUpdated.addListener((tabId, cambio, tab) => {
  if (cambio.status === "complete" && tab.active) sincronizarPestanas(tabId);
});

// ------------------------------------------------------------------ conexión en tiempo real
// Compatible con Chrome 109 (el último para Windows 7): ahí no existen hasDocument() ni el
// motivo "WORKERS", así que se usan alternativas.
async function hayOffscreen() {
  if (chrome.offscreen.hasDocument) return chrome.offscreen.hasDocument(); // Chrome 116+
  const url = chrome.runtime.getURL("offscreen.html");
  return (await self.clients.matchAll({ includeUncontrolled: true })).some((c) => c.url === url);
}

async function crearOffscreen() {
  for (const motivo of ["WORKERS", "DOM_SCRAPING"]) { // "WORKERS" no existe en Chrome 109
    try {
      await chrome.offscreen.createDocument({
        url: "offscreen.html",
        reasons: [motivo],
        justification: "Mantener la conexión en tiempo real (SSE) con el servidor de monitoreo de impresoras",
      });
      return;
    } catch (e) {
      if (String(e?.message).includes("single offscreen")) return; // ya existía
      if (motivo === "DOM_SCRAPING") console.warn("Monitor de impresoras: sin conexión en tiempo real:", e);
    }
  }
}

async function asegurarConexion() {
  if (!chrome.offscreen) return; // sin documento oculto: queda la revisión periódica
  const config = await leerConfig();
  const existe = await hayOffscreen();
  if (!config.servidor || !config.token) {
    if (existe) await chrome.offscreen.closeDocument().catch(() => {});
    return;
  }
  if (!existe) await crearOffscreen();
  const url = `${config.servidor}/api/events?token=${encodeURIComponent(config.token)}`;
  chrome.runtime.sendMessage({ destino: "offscreen", tipo: "conectar", url }).catch(() => {});
}

// ------------------------------------------------------------------ revisión
async function revisar() {
  const config = await leerConfig();
  if (!config.servidor || !config.token) {
    await pintarBadge({ texto: "", color: "#5f6368" });
    return;
  }
  try {
    await procesar(await api(config, "/api/printers"), config);
  } catch (e) {
    await servidorCaido(e, config);
  }
}

async function procesar(impresoras, config) {
  const lista = problemas(impresoras, config);
  const { activos = {} } = await chrome.storage.session.get("activos");
  const nuevos = {};
  const avisos = [];
  for (const p of lista) {
    nuevos[p.clave] = { tipo: p.tipo, impresora: p.impresora, titulo: p.titulo, mensaje: p.mensaje };
    if (!activos[p.clave] && debeNotificar(config, p.impresora, p.tipo)) {
      avisos.push({ id: p.clave, titulo: p.titulo, mensaje: p.mensaje, nivel: p.grave ? "grave" : "aviso" });
    }
  }
  const resueltos = Object.keys(activos).filter((clave) => !nuevos[clave]);
  for (const clave of resueltos) {
    const p = activos[clave];
    chrome.notifications.clear(clave);
    const corresponde = clave === "servidor" ? config.avisarServidor : debeNotificar(config, p.impresora, p.tipo);
    if (config.avisarResolucion && corresponde) {
      avisos.push({
        id: `${clave}:resuelto:${Date.now()}`,
        titulo: clave === "servidor" ? "Conexión restablecida" : `Resuelto: ${p.titulo}`,
        mensaje: clave === "servidor" ? "El servidor de monitoreo responde de nuevo." : "El problema ya no aparece.",
        nivel: "resuelto",
      });
    }
  }
  await chrome.storage.session.set({ activos: nuevos, ultimaRevision: Date.now(), ultimoError: null });
  await quitarDePantalla(resueltos, false);
  await avisar(avisos, config);
  await pintarBadge(badge(lista));
}

async function servidorCaido(error, config) {
  // Sin servidor no se sabe el estado de las impresoras: se conservan los problemas conocidos
  const { activos = {} } = await chrome.storage.session.get("activos");
  const nuevo = !activos.servidor;
  activos.servidor = { tipo: "fuera_de_linea", impresora: null, titulo: "Servidor sin conexión", mensaje: error.message };
  await chrome.storage.session.set({ activos, ultimoError: error.message });
  if (nuevo && config.avisarServidor && error.codigo !== "sin_config") {
    await avisar([{ id: "servidor", titulo: "Servidor de monitoreo sin conexión", mensaje: error.message, nivel: "grave" }], config);
  }
  await pintarBadge({ texto: "?", color: "#5f6368" });
}

// ------------------------------------------------------------------ avisos
/** Muestra los avisos según el modo elegido. Devuelve {flotante, sistema}. */
async function avisar(avisos, config) {
  if (!avisos.length) return { flotante: false, sistema: false };
  const ahora = Date.now();
  avisos = avisos.map((a) => ({
    ...a,
    hora: ahora,
    // Los graves quedan hasta cerrarlos; los leves según la duración elegida
    expira: a.nivel === "grave" ? null
      : a.nivel === "resuelto" ? ahora + DURACION_RESUELTO * 1000
      : config.duracion > 0 ? ahora + config.duracion * 1000 : null,
  }));
  let flotante = false;
  if (config.modo !== "sistema") {
    const { pantalla = [] } = await chrome.storage.session.get("pantalla");
    const ids = new Set(avisos.map((a) => a.id));
    await chrome.storage.session.set({ pantalla: [...pantalla.filter((a) => !ids.has(a.id)), ...avisos] });
    flotante = await sincronizarPestanas();
  }
  // Windows: si así se eligió, si no se pudo mostrar en ninguna página (p. ej. chrome://) o si
  // Chrome no está en primer plano (el aviso flotante no se vería)
  const sistema = config.modo !== "flotante" || !flotante || (config.sistemaSiNoEnfocado && !(await chromeEnFoco()));
  if (sistema) for (const a of avisos) await notificarSistema(a);
  return { flotante, sistema };
}

async function avisosVigentes() {
  const { pantalla = [] } = await chrome.storage.session.get("pantalla");
  const vigentes = pantalla.filter((a) => !a.expira || a.expira > Date.now());
  if (vigentes.length !== pantalla.length) await chrome.storage.session.set({ pantalla: vigentes });
  return vigentes;
}

async function quitarDePantalla(ids, sincronizar = true) {
  if (!ids.length) return;
  const { pantalla = [] } = await chrome.storage.session.get("pantalla");
  const quitar = new Set(ids);
  await chrome.storage.session.set({ pantalla: pantalla.filter((a) => !quitar.has(a.id)) });
  if (sincronizar) await sincronizarPestanas();
}

/** Lleva los avisos vigentes a la pestaña activa de cada ventana. true si se mostró en alguna. */
async function sincronizarPestanas(soloTab) {
  const config = await leerConfig();
  if (config.modo === "sistema" || !(await chrome.permissions.contains(PERMISO_PAGINAS))) return false;
  const lista = await avisosVigentes();
  const pestanas = soloTab ? [{ id: soloTab }] : await chrome.tabs.query({ active: true });
  let alguna = false;
  for (const { id } of pestanas) {
    try {
      if (lista.length) await chrome.scripting.executeScript({ target: { tabId: id }, files: ["toast.js"] });
      await chrome.scripting.executeScript({
        target: { tabId: id },
        func: (avisos, prefs) => window.__monitorImpresoras?.sincronizar(avisos, prefs),
        args: [lista, { posicion: config.posicion }],
      });
      alguna = true;
    } catch {
      // Páginas donde Chrome no permite extensiones: chrome://, Web Store, visor de PDF…
    }
  }
  return alguna;
}

async function chromeEnFoco() {
  try {
    return (await chrome.windows.getLastFocused()).focused === true;
  } catch {
    return false;
  }
}

function notificarSistema(a) {
  return new Promise((resolver) => {
    chrome.notifications.create(a.id, {
      type: "basic",
      iconUrl: chrome.runtime.getURL(ICONO),
      title: a.titulo,
      message: a.mensaje,
      priority: a.nivel === "grave" ? 2 : 0,
      requireInteraction: a.nivel === "grave", // los atascos quedan en pantalla hasta que se cierran
    }, () => {
      const error = chrome.runtime.lastError?.message;
      if (error) console.warn(`Monitor de impresoras: no se pudo mostrar la notificación «${a.titulo}»: ${error}`);
      resolver(!error);
    });
  });
}

async function pintarBadge({ texto, color }) {
  await chrome.action.setBadgeText({ text: texto });
  await chrome.action.setBadgeBackgroundColor({ color });
  if (chrome.action.setBadgeTextColor) await chrome.action.setBadgeTextColor({ color: "#ffffff" });
}

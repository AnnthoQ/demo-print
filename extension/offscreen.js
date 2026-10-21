// Conexión en tiempo real (Server-Sent Events) con el servidor de monitoreo.
// El service worker se suspende tras ~30 s sin actividad; este documento oculto no, así que
// aquí vive la conexión y cada cambio despierta al service worker para que avise de inmediato.
let fuente = null;
let urlActual = null;

function avisarCambio() {
  chrome.runtime.sendMessage({ tipo: "cambio" }).catch(() => {});
}

function conectar(url) {
  if (url === urlActual && fuente && fuente.readyState !== EventSource.CLOSED) return;
  fuente?.close();
  urlActual = url;
  fuente = new EventSource(url);
  for (const evento of ["snapshot", "printer", "alerta"]) fuente.addEventListener(evento, avisarCambio);
  // Si el servidor se cae, EventSource reintenta solo; si responde 401 se cierra y el
  // service worker vuelve a pedir la conexión en su próxima revisión
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.destino !== "offscreen") return;
  if (msg.tipo === "conectar") conectar(msg.url);
  if (msg.tipo === "desconectar") {
    fuente?.close();
    fuente = urlActual = null;
  }
});

chrome.runtime.sendMessage({ tipo: "offscreen_listo" }).catch(() => {});

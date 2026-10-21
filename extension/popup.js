// Popup: una tarjeta por impresora, actualizada cada 5 s mientras está abierto.
import { api, esc, hora, leerConfig } from "./lib/comun.js";

const $ = (id) => document.getElementById(id);
let config;

$("ajustes").addEventListener("click", () => chrome.runtime.openOptionsPage());

function mensaje(titulo, detalle, conBoton, esError = false) {
  $("contenido").innerHTML = `<div class="mensaje ${esError ? "error" : ""}">
    <strong>${esc(titulo)}</strong><span>${esc(detalle)}</span>
    ${conBoton ? `<button class="boton primario" id="ir-opciones">Abrir configuración</button>` : ""}</div>`;
  $("ir-opciones")?.addEventListener("click", () => chrome.runtime.openOptionsPage());
}

function bandeja(t) {
  const umbral = config.umbral;
  const clase = t.open ? "abierta" : t.label === "Vacía" ? "vacia" : t.percent !== null && t.percent < umbral ? "bajo" : "";
  // Binario o desconocido: solo etiqueta, sin barra
  const barra = t.percent === null ? "" : `<div class="barra"><div class="relleno ${
    t.percent < umbral ? "bajo" : t.percent < 50 ? "medio" : ""}" style="width:${t.percent}%"></div></div>`;
  return `<div class="bandeja">
    <div class="fila"><span><span class="nom">${esc(t.name)}</span>${t.paper ? `<span class="tag">${esc(t.paper)}</span>` : ""}</span>
    <span class="valor ${clase}">${esc(t.label)}</span></div>${barra}</div>`;
}

function tarjeta(p) {
  const activas = p.trays.filter((t) => t.enabled);
  // Los avisos de papel ya se ven en las barras
  const avisos = p.warnings.filter((w) => !/papel|bandeja|abierta|Tray|Drawer/i.test(w));
  let cuerpo = "";
  if (p.state === "fuera_de_linea") {
    cuerpo = `<div class="offline">No responde por la red · último intento ${esc(hora(p.lastUpdate))}</div>`;
  } else {
    if (p.errors.jammed) cuerpo += `<div class="atasco" role="alert">⚠ PAPEL ATASCADO</div>`;
    const motivos = p.reasons.filter((r) => r !== "Papel atascado");
    if (motivos.length) cuerpo += `<div class="motivo">${motivos.map(esc).join(" · ")}</div>`;
    if (avisos.length) cuerpo += `<div class="avisos">${avisos.map((a) => `<span class="aviso">${esc(a)}</span>`).join("")}</div>`;
    cuerpo += activas.length ? activas.map(bandeja).join("") : `<div class="bandeja vacio">Sin bandejas activas</div>`;
  }
  return `<article class="tarjeta ${esc(p.state)}">
    <div class="fila"><span class="nombre">${esc(p.name)}</span><span class="chip ${esc(p.state)}">${esc(p.stateLabel)}</span></div>
    <div class="modelo" title="${esc(p.model)}">${esc(p.model || "Modelo desconocido")} · ${esc(p.ip)}</div>
    ${cuerpo}</article>`;
}

async function cargar() {
  config = await leerConfig();
  if (!config.servidor || !config.token) {
    $("sub").textContent = "Sin configurar";
    return mensaje("Falta configurar el servidor", "Indica la URL del servidor de monitoreo y el token.", true);
  }
  try {
    const impresoras = await api(config, "/api/printers");
    $("contenido").innerHTML = impresoras.length
      ? impresoras.map(tarjeta).join("") + `<div class="pie">Se actualiza cada 5 s</div>`
      : `<div class="mensaje">El servidor aún no tiene lecturas. Espera unos segundos.</div>`;
    $("sub").textContent = `Actualizado ${hora()}`;
    chrome.runtime.sendMessage({ tipo: "datos", impresoras }).catch(() => {});
  } catch (e) {
    $("sub").textContent = "Sin conexión";
    mensaje("No se pudo obtener el estado", e.message, e.codigo === "token" || e.codigo === "sin_config", true);
  }
}

cargar();
setInterval(cargar, 5000);

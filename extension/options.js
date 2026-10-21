// Página de opciones: servidor y token, forma de aviso, qué avisos recibir y bandejas activas.
import {
  PERMISO_PAGINAS, TIPOS_ALERTA, api, debeNotificar, esc, guardarConfig, leerConfig, normalizarServidor,
} from "./lib/comun.js";

const $ = (id) => document.getElementById(id);
let impresoras = [];

function estado(id, texto, ok) {
  $(id).textContent = texto;
  $(id).className = `estado ${ok === undefined ? "" : ok ? "ok" : "mal"}`;
}

async function iniciar() {
  const config = await leerConfig();
  $("servidor").value = config.servidor;
  $("token").value = config.token;
  $("umbral").value = config.umbral;
  $("resolucion").checked = config.avisarResolucion;
  $("servidor-caido").checked = config.avisarServidor;
  $("posicion").value = config.posicion;
  $("duracion").value = String(config.duracion);
  $("enfocado").checked = config.sistemaSiNoEnfocado;
  document.querySelector(`input[name="modo"][value="${config.modo}"]`).checked = true;
  await mostrarSegunModo(config.modo);
  revisarBloqueoSistema();
  if (config.servidor && config.token) cargarImpresoras(config);
}

// ------------------------------------------------------------------ servidor
$("ver-token").addEventListener("click", () => {
  const visible = $("token").type === "text";
  $("token").type = visible ? "password" : "text";
  $("ver-token").textContent = visible ? "Mostrar" : "Ocultar";
});

$("form-servidor").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  let servidor;
  try {
    servidor = normalizarServidor($("servidor").value);
  } catch (e) {
    return estado("estado-servidor", e.message.startsWith("La URL") ? e.message : "La URL no es válida", false);
  }
  // El permiso debe pedirse dentro del clic, antes de cualquier espera
  const permiso = chrome.permissions.request({ origins: [`${servidor}/*`] });
  estado("estado-servidor", "Probando…");
  if (!(await permiso)) return estado("estado-servidor", "Sin permiso para acceder al servidor", false);
  const config = await guardarConfig({ servidor, token: $("token").value.trim() });
  $("servidor").value = servidor;
  try {
    const lista = await api(config, "/api/printers");
    estado("estado-servidor", `✓ Conectado: ${lista.length} impresoras`, true);
    cargarImpresoras(config, lista);
  } catch (e) {
    estado("estado-servidor", e.message, false);
  }
});

// ------------------------------------------------------------------ cómo avisar
async function mostrarSegunModo(modo) {
  const usaFlotante = modo !== "sistema";
  $("opciones-flotante").classList.toggle("oculto", !usaFlotante);
  $("fila-enfocado").classList.toggle("oculto", modo !== "flotante");
  $("falta-permiso").hidden = !usaFlotante || (await chrome.permissions.contains(PERMISO_PAGINAS));
}

document.querySelectorAll('input[name="modo"]').forEach((radio) => radio.addEventListener("change", async () => {
  const modo = radio.value;
  // Pedir el permiso dentro del clic: sin él no se puede dibujar el aviso en las páginas
  const permiso = modo === "sistema" ? Promise.resolve(true) : chrome.permissions.request(PERMISO_PAGINAS);
  await guardarConfig({ modo });
  const ok = await permiso;
  await mostrarSegunModo(modo);
  estado("estado-modo", ok ? "Guardado" : "Sin permiso: se usará la notificación de Windows", ok);
}));

$("dar-permiso").addEventListener("click", async () => {
  const ok = await chrome.permissions.request(PERMISO_PAGINAS);
  await mostrarSegunModo((await leerConfig()).modo);
  estado("estado-modo", ok ? "Permiso concedido" : "Permiso denegado", ok);
});

$("posicion").addEventListener("change", async (e) => {
  await guardarConfig({ posicion: e.target.value });
  ejemploFlotante();
});
$("duracion").addEventListener("change", async (e) => {
  await guardarConfig({ duracion: Number(e.target.value) });
  estado("estado-modo", "Guardado", true);
});
$("enfocado").addEventListener("change", async (e) => {
  await guardarConfig({ sistemaSiNoEnfocado: e.target.checked });
  estado("estado-modo", "Guardado", true);
});

async function ejemploFlotante() {
  // Se dibuja en esta misma página para ver el aspecto y la posición elegidos
  const config = await leerConfig();
  const ahora = Date.now();
  window.__monitorImpresoras.sincronizar([
    { id: `ej-grave-${ahora}`, nivel: "grave", hora: ahora, expira: ahora + 8000,
      titulo: "Papel atascado — Canon", mensaje: "Hay un atasco de papel. Revisa la impresora." },
    { id: `ej-aviso-${ahora}`, nivel: "aviso", hora: ahora, expira: ahora + 8000,
      titulo: "Papel bajo — Ricoh", mensaje: "Tray 1 (A4): ≈ 465 hojas · 15 %." },
  ], { posicion: config.posicion });
  estado("estado-modo", "Así se verá sobre las páginas web (ejemplo de 8 s)", true);
}
$("probar-flotante").addEventListener("click", ejemploFlotante);

$("probar-sistema").addEventListener("click", async () => {
  if (await revisarBloqueoSistema()) return estado("estado-modo", "Notificaciones de Windows bloqueadas en Chrome", false);
  const ok = await chrome.runtime.sendMessage({ tipo: "probar_sistema" });
  estado("estado-modo", ok
    ? "Enviada. Si no aparece, revisa en Windows: Configuración → Sistema → Notificaciones (Google Chrome) y el modo No molestar."
    : "Chrome rechazó la notificación", ok);
});

async function revisarBloqueoSistema() {
  const nivel = await new Promise((r) => chrome.notifications.getPermissionLevel(r));
  $("bloqueo").hidden = nivel !== "denied";
  return nivel === "denied";
}

// ------------------------------------------------------------------ qué avisos recibir
function pintarMatriz(config) {
  if (!impresoras.length) return;
  $("matriz").innerHTML = `<table>
    <thead><tr><th>Impresora</th>${TIPOS_ALERTA.map(([tipo, corto, largo, critico]) =>
      `<th class="${critico ? "critico" : ""}" title="${esc(largo)}">${esc(corto)}</th>`).join("")}</tr></thead>
    <tbody>${impresoras.map((p) => `<tr><td>${esc(p.name)}</td>${TIPOS_ALERTA.map(([tipo, , largo]) =>
      `<td><input type="checkbox" data-impresora="${esc(p.id)}" data-tipo="${tipo}"
        aria-label="${esc(p.name)}: ${esc(largo)}" ${debeNotificar(config, p.id, tipo) ? "checked" : ""}></td>`).join("")}</tr>`).join("")}
    </tbody></table>`;
}

async function guardarMatriz() {
  const notificar = {};
  document.querySelectorAll("#matriz input[data-tipo]").forEach((i) => {
    (notificar[i.dataset.impresora] ||= {})[i.dataset.tipo] = i.checked;
  });
  await guardarConfig({ notificar });
  estado("estado-alertas", "Guardado", true);
}

$("matriz").addEventListener("change", guardarMatriz);

document.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", async () => {
  const criticos = new Set(TIPOS_ALERTA.filter((t) => t[3]).map((t) => t[0]));
  document.querySelectorAll("#matriz input[data-tipo]").forEach((i) => {
    i.checked = b.dataset.preset === "todo" || (b.dataset.preset === "criticos" && criticos.has(i.dataset.tipo));
  });
  await guardarMatriz();
}));

$("servidor-caido").addEventListener("change", async (e) => {
  await guardarConfig({ avisarServidor: e.target.checked });
  estado("estado-alertas", "Guardado", true);
});
$("resolucion").addEventListener("change", async (e) => {
  await guardarConfig({ avisarResolucion: e.target.checked });
  estado("estado-alertas", "Guardado", true);
});
$("umbral").addEventListener("change", async (e) => {
  const umbral = Math.min(90, Math.max(1, Math.round(Number(e.target.value) || 20)));
  e.target.value = umbral;
  await guardarConfig({ umbral });
  estado("estado-alertas", "Guardado", true);
});
$("reavisar").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ tipo: "reiniciar_avisos" });
  estado("estado-alertas", "Listo: se volvieron a avisar los problemas actuales", true);
});

// ------------------------------------------------------------------ bandejas
async function cargarImpresoras(config, lista) {
  try {
    impresoras = lista || (await api(config, "/api/printers"));
  } catch (e) {
    $("bandejas").innerHTML = `<p class="vacio">${esc(e.message)}</p>`;
    return;
  }
  pintarMatriz(config);
  $("bandejas").innerHTML = impresoras.map((p) => `<div class="impresora">
    <h3>${esc(p.name)} <span class="chip ${esc(p.state)}">${esc(p.stateLabel)}</span></h3>
    ${p.trays.length ? p.trays.map((t) => `<label class="bandeja">
      <span class="nom">${esc(t.name)}${t.paper ? `<span class="tag">${esc(t.paper)}</span>` : ""}${t.isBypass ? `<span class="tag">bypass</span>` : ""}</span>
      <span class="cap">${t.capacity ? `${esc(t.capacity)} hojas` : ""}</span>
      <span class="switch"><input type="checkbox" data-impresora="${esc(p.id)}" data-bandeja="${esc(t.id)}" ${t.enabled ? "checked" : ""}
        aria-label="${esc(p.name)} ${esc(t.name)}"><span></span></span>
    </label>`).join("") : `<p class="vacio">Sin datos de bandejas (¿impresora fuera de línea?)</p>`}
  </div>`).join("");
}

$("bandejas").addEventListener("change", async (ev) => {
  const input = ev.target;
  if (!input.dataset.bandeja) return;
  input.disabled = true;
  const config = await leerConfig();
  try {
    await api(config, `/api/trays/${encodeURIComponent(input.dataset.impresora)}/${encodeURIComponent(input.dataset.bandeja)}`,
      { method: "PUT", body: JSON.stringify({ enabled: input.checked }) });
    chrome.runtime.sendMessage({ tipo: "revisar" }).catch(() => {});
  } catch (e) {
    input.checked = !input.checked; // se revierte si el servidor no lo guardó
    alert(`No se pudo guardar: ${e.message}`);
  } finally {
    input.disabled = false;
  }
});

iniciar();

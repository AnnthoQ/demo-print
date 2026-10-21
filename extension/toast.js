// Avisos flotantes del Monitor de impresoras. El service worker inyecta este archivo en la
// pestaña activa y luego llama a window.__monitorImpresoras.sincronizar(lista, preferencias).
// Va dentro de un Shadow DOM cerrado para no mezclarse con los estilos de la página.
(() => {
  if (window.__monitorImpresoras) return;

  const CSS = `
    :host { all: initial; }
    .pila { position: fixed; z-index: 2147483647; display: flex; flex-direction: column; gap: 10px;
            width: min(360px, calc(100vw - 32px)); pointer-events: none;
            font: 14px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
    .arriba-derecha { top: 16px; right: 16px; }
    .abajo-derecha { bottom: 16px; right: 16px; flex-direction: column-reverse; }
    .abajo-izquierda { bottom: 16px; left: 16px; flex-direction: column-reverse; }
    .aviso { pointer-events: auto; display: grid; grid-template-columns: auto 1fr auto; gap: 10px; align-items: start;
             padding: 12px 12px 12px 14px; background: #000; color: #ededed;
             --c: #5aa0ff; border: 1px solid #333; border-left: 4px solid var(--c);
             box-shadow: 0 8px 24px rgba(0,0,0,.6);
             animation: entrar .2s ease-out; }
    .aviso.saliendo { animation: salir .16s ease-in forwards; }
    .n-grave { --c: #ff5555; border-color: #ff5555; }
    .n-aviso { --c: #f0b545; }
    .n-resuelto { --c: #3fcf73; }
    .icono { width: 20px; height: 20px; color: var(--c); margin-top: 1px; }
    .texto { cursor: pointer; min-width: 0; }
    strong { display: block; font-size: 14px; font-weight: 700; color: #fff; }
    span { display: block; color: #c8c8c8; font-size: 13px; overflow-wrap: anywhere; }
    small { display: block; color: #8c8c8c; font-size: 11.5px; margin-top: 3px; }
    button { all: unset; cursor: pointer; color: #8c8c8c; font-size: 20px; line-height: 1; padding: 0 2px; }
    button:hover { color: #fff; }
    button:focus-visible { outline: 2px solid #5aa0ff; }
    @keyframes entrar { from { opacity: 0; transform: translateY(-6px); } }
    @keyframes salir { to { opacity: 0; } }
    @media (prefers-reduced-motion: reduce) { .aviso, .aviso.saliendo { animation: none; } }`;

  const ICONOS = {
    grave: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2 1 21h22L12 2zm1 15h-2v-2h2v2zm0-4h-2V9h2v4z"/></svg>',
    aviso: '<svg viewBox="0 0 24 24" fill="currentColor" fill-rule="evenodd"><path d="M3 3h18v18H3zm8 4v7h2V7zm0 9v2h2v-2z"/></svg>',
    resuelto: '<svg viewBox="0 0 24 24" fill="currentColor" fill-rule="evenodd"><path d="M3 3h18v18H3zm7 13.4 8-8L16.6 7 10 13.6 7.4 11 6 12.4z"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="currentColor" fill-rule="evenodd"><path d="M3 3h18v18H3zm8 8v6h2v-6zm0-4v2h2V7z"/></svg>',
  };

  const host = document.createElement("div");
  host.id = "monitor-impresoras-avisos";
  const raiz = host.attachShadow({ mode: "closed" });
  try {
    const hoja = new CSSStyleSheet();
    hoja.replaceSync(CSS); // no depende de la política CSP de la página
    raiz.adoptedStyleSheets = [hoja];
  } catch {
    const estilo = document.createElement("style");
    estilo.textContent = CSS;
    raiz.appendChild(estilo);
  }
  const pila = document.createElement("div");
  pila.className = "pila arriba-derecha";
  raiz.appendChild(pila);
  const temporizadores = new Map();

  const enviar = (msg) => {
    try { chrome.runtime.sendMessage(msg).catch(() => {}); } catch { /* extensión recargada */ }
  };

  function quitar(el) {
    clearTimeout(temporizadores.get(el.dataset.id));
    temporizadores.delete(el.dataset.id);
    el.classList.add("saliendo");
    setTimeout(() => el.remove(), 200);
  }

  function crear(a) {
    const el = document.createElement("div");
    el.className = `aviso n-${a.nivel}`;
    el.dataset.id = a.id;
    el.setAttribute("role", a.nivel === "grave" ? "alert" : "status");
    el.innerHTML = `<div class="icono">${ICONOS[a.nivel] || ICONOS.info}</div>
      <div class="texto" title="Abrir el monitor de impresoras"><strong></strong><span></span><small></small></div>
      <button aria-label="Cerrar aviso" title="Cerrar">×</button>`;
    el.querySelector("strong").textContent = a.titulo;
    el.querySelector("span").textContent = a.mensaje;
    el.querySelector("small").textContent =
      `Monitor de impresoras · ${new Date(a.hora).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" })}`;
    el.querySelector("button").addEventListener("click", () => {
      enviar({ tipo: "cerrar_aviso", id: a.id });
      quitar(el);
    });
    el.querySelector(".texto").addEventListener("click", () => enviar({ tipo: "abrir_popup" }));
    return el;
  }

  function sincronizar(lista, prefs = {}) {
    if (!host.isConnected) (document.body || document.documentElement).appendChild(host);
    pila.className = `pila ${prefs.posicion || "arriba-derecha"}`;
    const ids = new Set(lista.map((a) => a.id));
    for (const el of [...pila.children]) if (!ids.has(el.dataset.id) && !el.classList.contains("saliendo")) quitar(el);
    for (const a of lista) {
      if ([...pila.children].some((el) => el.dataset.id === a.id)) continue;
      const restante = a.expira ? a.expira - Date.now() : null;
      if (restante !== null && restante <= 0) continue;
      const el = crear(a);
      pila.appendChild(el);
      if (restante !== null) temporizadores.set(a.id, setTimeout(() => quitar(el), restante));
    }
  }

  window.__monitorImpresoras = { sincronizar };
})();

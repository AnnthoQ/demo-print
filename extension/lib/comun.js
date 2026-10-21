// Funciones compartidas por el popup, la página de opciones y el service worker.

export const CONFIG_PREDETERMINADA = {
  servidor: "",
  token: "",
  umbral: 20,
  modo: "flotante",            // "flotante" (sobre la página) | "sistema" (Windows) | "ambos"
  posicion: "arriba-derecha",  // "arriba-derecha" | "abajo-derecha" | "abajo-izquierda"
  duracion: 10,                // segundos de los avisos leves; 0 = hasta cerrarlos
  sistemaSiNoEnfocado: true,   // si Chrome no está en primer plano, también aviso de Windows
  avisarServidor: true,        // avisar si el servidor de monitoreo no responde
  notificar: {},               // { [impresora]: { [tipo]: bool } }; sin valor = activo
  avisarResolucion: true,
};

// [tipo, nombre corto (columna), descripción, crítico]
export const TIPOS_ALERTA = [
  ["atasco", "Atasco", "Papel atascado", true],
  ["tapa_abierta", "Tapa", "Tapa abierta", true],
  ["error", "Error", "Impresora detenida por otro error", true],
  ["fuera_de_linea", "Sin red", "Impresora fuera de línea", true],
  ["sin_papel", "Sin papel", "Bandeja activa vacía", false],
  ["papel_bajo", "Papel bajo", "Bandeja activa por debajo del umbral", false],
];

export const PERMISO_PAGINAS = { origins: ["http://*/*", "https://*/*"] };

export async function leerConfig() {
  const { config = {} } = await chrome.storage.local.get("config");
  return { ...CONFIG_PREDETERMINADA, ...config, notificar: config.notificar || {} };
}

/** ¿Se avisa este tipo de problema de esta impresora? Lo no configurado está activo. */
export function debeNotificar(config, impresora, tipo) {
  const valor = config.notificar?.[impresora]?.[tipo];
  if (typeof valor === "boolean") return valor;
  const anterior = config.notificar?.[tipo]; // formato de la versión 1.0: { tipo: bool }
  return typeof anterior === "boolean" ? anterior : true;
}

export async function guardarConfig(cambios) {
  const actual = await leerConfig();
  const nueva = { ...actual, ...cambios };
  await chrome.storage.local.set({ config: nueva });
  return nueva;
}

/** "http://10.0.101.51:8787/" -> "http://10.0.101.51:8787". Lanza error si no es una URL válida. */
export function normalizarServidor(texto) {
  const url = new URL(texto.trim());
  if (!/^https?:$/.test(url.protocol)) throw new Error("La URL debe empezar con http:// o https://");
  return url.origin;
}

export class ErrorApi extends Error {
  constructor(mensaje, codigo) {
    super(mensaje);
    this.codigo = codigo;
  }
}

export async function api(config, ruta, opciones = {}) {
  if (!config.servidor) throw new ErrorApi("Falta configurar la URL del servidor", "sin_config");
  let r;
  try {
    r = await fetch(config.servidor + ruta, {
      ...opciones,
      headers: {
        Authorization: `Bearer ${config.token}`,
        ...(opciones.body ? { "Content-Type": "application/json" } : {}),
      },
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
  } catch {
    throw new ErrorApi("No se puede conectar con el servidor de monitoreo", "red");
  }
  if (r.status === 401) throw new ErrorApi("El token no es correcto", "token");
  if (!r.ok) {
    const cuerpo = await r.json().catch(() => ({}));
    throw new ErrorApi(cuerpo.error || `Error del servidor (${r.status})`, "http");
  }
  return r.json();
}

/** Problemas activos según la configuración del usuario. Cada uno tiene una
 *  clave estable para notificar una sola vez cuando aparece. */
export function problemas(impresoras, config) {
  const lista = [];
  const agregar = (p, tipo, sufijo, titulo, mensaje, grave = false) =>
    lista.push({ clave: `${p.id}:${tipo}${sufijo ? ":" + sufijo : ""}`, tipo, impresora: p.id, titulo, mensaje, grave });

  for (const p of impresoras) {
    if (p.state === "fuera_de_linea") {
      agregar(p, "fuera_de_linea", "", `${p.name} fuera de línea`, "La impresora no responde por la red.", true);
      continue;
    }
    if (p.errors.jammed) {
      agregar(p, "atasco", "", `Papel atascado — ${p.name}`, "Hay un atasco de papel. Revisa la impresora.", true);
    }
    if (p.errors.doorOpen) {
      agregar(p, "tapa_abierta", "", `Tapa abierta — ${p.name}`, "Cierra la tapa para seguir imprimiendo.", true);
    }
    if (p.state === "error" && !p.errors.jammed && !p.errors.doorOpen) {
      agregar(p, "error", "", `${p.name} con error`, p.reasons.join(" · ") || "La impresora está detenida.", true);
    }
    for (const t of p.trays) {
      if (!t.enabled || t.open) continue;
      if (t.label === "Vacía") {
        agregar(p, "sin_papel", t.id, `Sin papel — ${p.name}`, `${t.name}${t.paper ? " (" + t.paper + ")" : ""} está vacía.`);
      } else if (t.percent !== null && t.percent > 0 && t.percent < config.umbral) {
        agregar(p, "papel_bajo", t.id, `Papel bajo — ${p.name}`, `${t.name}${t.paper ? " (" + t.paper + ")" : ""}: ${t.label}.`);
      }
    }
  }
  return lista;
}

/** Texto y color del ícono: "!" rojo si hay atasco o error, número de impresoras con problemas. */
export function badge(lista) {
  if (lista.some((p) => p.grave)) return { texto: "!", color: "#d93025" };
  const n = new Set(lista.map((p) => p.impresora)).size;
  return n ? { texto: String(n), color: "#e37400" } : { texto: "", color: "#5f6368" };
}

export function hora(iso) {
  const d = iso ? new Date(iso) : new Date();
  return d.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

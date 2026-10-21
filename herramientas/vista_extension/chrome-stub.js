// Imitación mínima de las APIs de Chrome para ver el popup y las opciones en una pestaña
// normal durante el desarrollo. NO forma parte de la extensión.
(() => {
  const almacen = (area) => ({
    async get(clave) {
      const todo = JSON.parse(localStorage.getItem(`stub:${area}`) || "{}");
      return clave ? { [clave]: todo[clave] } : todo;
    },
    async set(obj) {
      const todo = JSON.parse(localStorage.getItem(`stub:${area}`) || "{}");
      localStorage.setItem(`stub:${area}`, JSON.stringify({ ...todo, ...obj }));
    },
  });
  const avisar = (texto) => parent.postMessage({ tipo: "stub", texto }, "*");
  window.chrome = {
    storage: { local: almacen("local"), session: almacen("session"), onChanged: { addListener() {} } },
    runtime: {
      async sendMessage(m) { avisar(`runtime.sendMessage(${m.tipo})`); return true; },
      openOptionsPage() { avisar("abrir opciones"); },
      getURL: (r) => r,
    },
    permissions: {
      async request(p) { avisar(`permiso solicitado: ${p.origins}`); localStorage.setItem("stub:paginas", "1"); return true; },
      async contains(p) { return p.origins.length === 1 || localStorage.getItem("stub:paginas") === "1"; },
    },
    notifications: { getPermissionLevel: (cb) => cb(localStorage.getItem("stub:notif") || "granted") },
  };
})();

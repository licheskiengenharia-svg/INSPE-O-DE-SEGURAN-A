// Ponte entre o site (index.html) e o servidor Python.
// Dentro do Claude, o site usa window.claude.use("db" | "user" | "sample" | "downloads").
// Aqui os mesmos recursos são atendidos pelo servidor: banco, contas, análise do Claude e downloads.
(() => {
  const INTERVALO = 4000; // sincronização a cada 4 s
  const cache = new Map(); // path -> dados (sem fotos)
  const ouvintes = new Set();
  let seq = 0, pronto = null, eu = null;

  async function api(metodo, url, corpo) {
    const r = await fetch(url, { method: metodo, credentials: "same-origin",
      headers: corpo ? { "Content-Type": "application/json" } : {}, body: corpo ? JSON.stringify(corpo) : undefined });
    if (r.status === 401) { location.href = "/entrar?proximo=/"; throw new Error("sessao"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(j.erro || "erro " + r.status); e.code = j.erro === "permissao" ? "permission_denied" : "erro"; throw e; }
    return j;
  }
  const copia = v => v == null ? v : JSON.parse(JSON.stringify(v));

  async function sincronizar() {
    try {
      const j = await api("GET", "/api/sync?desde=" + seq);
      if (j.completo) cache.clear();
      for (const m of j.mudancas) { if (m.data == null) cache.delete(m.path); else cache.set(m.path, m.data); }
      const mudou = j.completo || j.mudancas.length > 0;
      seq = j.seq;
      if (mudou) ouvintes.forEach(f => { try { f(); } catch (e) { console.error(e); } });
    } catch (e) { if (e.message !== "sessao") console.warn("sincronização falhou", e); }
  }
  function iniciar() {
    if (!pronto) { pronto = sincronizar(); setInterval(sincronizar, INTERVALO); }
    return pronto;
  }

  function docRef(path) {
    const id = path.split("/").pop();
    return {
      id, path,
      async get() {
        if (!path.startsWith("fotos/")) { await iniciar(); return { exists: cache.has(path), id, data: () => copia(cache.get(path)) }; }
        const j = await api("GET", "/api/doc?path=" + encodeURIComponent(path));
        return { exists: j.existe, id, data: () => j.data };
      },
      async set(v) {
        await api("PUT", "/api/doc", { path, data: v });
        if (!path.startsWith("fotos/")) { cache.set(path, copia(v)); ouvintes.forEach(f => f()); }
        sincronizar();
      },
      async delete() {
        await api("DELETE", "/api/doc?path=" + encodeURIComponent(path));
        cache.delete(path); ouvintes.forEach(f => f()); sincronizar();
      },
      onSnapshot(cb, erro) {
        const f = () => cb({ exists: cache.has(path), id, data: () => copia(cache.get(path)) });
        ouvintes.add(f); iniciar().then(f, erro);
        return () => ouvintes.delete(f);
      },
    };
  }
  const novoId = () => Array.from(crypto.getRandomValues(new Uint8Array(10)), b => "abcdefghijklmnopqrstuvwxyz0123456789"[b % 36]).join("");
  const db = {
    doc: p => docRef(p),
    collection: c => ({
      doc: id => docRef(c + "/" + (id || novoId())),
      onSnapshot(cb, erro) {
        const f = () => cb({ docs: [...cache.entries()].filter(([k]) => k.split("/")[0] === c).map(([k, v]) => ({ id: k.split("/")[1], data: () => copia(v) })) });
        ouvintes.add(f); iniciar().then(f, erro);
        return () => ouvintes.delete(f);
      },
    }),
  };

  async function quemSou() { if (!eu) eu = await api("GET", "/api/eu"); return eu; }
  const user = {
    can: async () => true,
    isOwner: async () => (await quemSou()).admin,
    id: async () => (await quemSou()).id,
    profiles: async ids => api("POST", "/api/perfis", { ids }),
  };

  const paraDataUrl = b => typeof b === "string" ? Promise.resolve(b) : new Promise((ok, falha) => {
    const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = falha; r.readAsDataURL(b);
  });
  const sample = Object.assign(async prompt => ({ text: JSON.stringify(await sample.json(prompt)) }), {
    limits: async () => ((await quemSou()).ia ? { images: { maxCount: 5 } } : null),
    json: async (prompt, opcoes = {}) => api("POST", "/api/ia", { prompt, imagens: await Promise.all((opcoes.images || []).map(paraDataUrl)) }),
  });

  const downloads = {
    async save({ filename, data }) {
      const blob = data instanceof Blob ? data : new Blob([data], { type: filename.endsWith(".json") ? "application/json" : filename.endsWith(".csv") ? "text/csv;charset=utf-8" : "application/octet-stream" });
      const url = URL.createObjectURL(blob), a = document.createElement("a");
      a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    },
  };

  window.claude = {
    use: async nome => {
      if (nome === "db") return db;
      if (nome === "user") return user;
      if (nome === "downloads") return downloads;
      if (nome === "sample") return (await quemSou()).ia ? sample : null;
      return null;
    },
  };

  // barra discreta com a conta, troca de senha e (para o administrador) a página de usuários e backup
  quemSou().then(u => {
    const barra = document.createElement("div");
    barra.style.cssText = "position:fixed;right:8px;bottom:8px;z-index:50;background:#1d211c;color:#fff;font:13px system-ui,Arial,sans-serif;padding:6px 10px;border-radius:6px;opacity:.9";
    const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
    barra.innerHTML = `${esc(u.nome)} · ${u.admin ? '<a href="/usuarios" style="color:#e8b500">Usuários e backup</a> · ' : ""}<a href="/senha" style="color:#fff">Senha</a> · <a href="/sair" style="color:#fff">Sair</a>`;
    document.body.appendChild(barra);
  }).catch(() => {});
})();

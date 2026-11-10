// Cloudflare Worker: rota "loudhouse.com.br/dollar-ads-club*" -> servidor local (via túnel).
// Variáveis do Worker: ORIGEM = https://dac-origem.loudhouse.com.br  |  SEGREDO (secret) = mesmo ORIGEM_SEGREDO do .env
export default {
  async fetch(req, env) {
    const u = new URL(req.url), o = new URL(env.ORIGEM);
    u.protocol = o.protocol; u.hostname = o.hostname; u.port = "";
    const r = new Request(u, req);
    r.headers.set("x-dac-origem", env.SEGREDO);
    return fetch(r, { redirect: "manual" });
  },
};

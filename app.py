"""Dollar Ads Club · MVP como site (roda no servidor do Geeg).
Serve a página (static/index.html), guarda os dados em SQLite, chama a IA (Claude API)
e publica no Mercado Livre na conta autorizada.

Rodar:
  pip install fastapi uvicorn httpx pillow rembg
  uvicorn app:app --host 127.0.0.1 --port 8000      (o túnel da Cloudflare dá o https)

Variáveis de ambiente (nunca no código nem no GitHub):
  DAC_USUARIO, DAC_SENHA        login simples do MVP (o navegador pede)
  ANTHROPIC_API_KEY             chave da Claude API
  ANTHROPIC_MODEL               id do modelo (conferir na documentação da Anthropic)
  ML_CLIENT_ID, ML_CLIENT_SECRET, ML_REDIRECT   do app no DevCenter (ML_REDIRECT = https://ENDEREÇO/oauth/callback)
"""
import base64, io, json, os, re, secrets, sqlite3, time, pathlib, httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse

E = os.environ.get
API, AUTH = "https://api.mercadolibre.com", "https://auth.mercadolivre.com.br/authorization"
BASE = pathlib.Path(__file__).parent
DATA = pathlib.Path(E("DAC_DATA", str(BASE))); DATA.mkdir(parents=True, exist_ok=True)  # no Docker: /data (volume)
DB = sqlite3.connect(DATA / "dados.sqlite3", check_same_thread=False)
DB.execute("create table if not exists docs(col text, id text, data text, em real, primary key(col,id))")
TOK = DATA / "tokens.json"
app = FastAPI()
LIVRES = ("/oauth/callback", "/avisos", "/saude")  # o Mercado Livre chama estes sem login

@app.middleware("http")
async def login(req: Request, nxt):
    if not req.url.path.startswith(LIVRES):
        u, s = E("DAC_USUARIO", ""), E("DAC_SENHA", "")
        h = req.headers.get("authorization", "")
        ok = False
        if u and s and h.startswith("Basic "):
            try:
                a, b = base64.b64decode(h[6:]).decode().split(":", 1)
                ok = secrets.compare_digest(a, u) and secrets.compare_digest(b, s)
            except Exception:
                ok = False
        if not ok:
            return Response("login", 401, {"WWW-Authenticate": 'Basic realm="Dollar Ads Club"'})
    return await nxt(req)

# ---------- página ----------
@app.get("/")
def pagina(): return FileResponse(BASE / "static" / "index.html")

from fastapi.staticfiles import StaticFiles
app.mount("/ort", StaticFiles(directory=BASE / "static" / "ort"), name="ort")
app.mount("/modelo", StaticFiles(directory=BASE / "static" / "modelo"), name="modelo")
@app.get("/icone.png")
def icone(): return FileResponse(BASE / "static" / "icone.png")

# ---------- dados (substitui o banco do artefato) ----------
NOME = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
def chk(*xs):
    for x in xs:
        if not NOME.match(x): raise HTTPException(400, "nome inválido")

@app.get("/api/col/{col}")
def listar(col: str):
    chk(col)
    return [{"id": i, "data": json.loads(d)} for i, d in DB.execute("select id,data from docs where col=?", (col,))]

@app.get("/api/doc/{col}/{id}")
def ler(col: str, id: str):
    chk(col, id); r = DB.execute("select data from docs where col=? and id=?", (col, id)).fetchone()
    return {"exists": bool(r), "data": json.loads(r[0]) if r else None}

@app.put("/api/doc/{col}/{id}")
async def gravar(col: str, id: str, req: Request):
    chk(col, id); d = await req.json()
    DB.execute("insert or replace into docs values(?,?,?,?)", (col, id, json.dumps(d), time.time())); DB.commit()
    return {"ok": True}

@app.patch("/api/doc/{col}/{id}")
async def juntar(col: str, id: str, req: Request):
    chk(col, id); n = await req.json()
    r = DB.execute("select data from docs where col=? and id=?", (col, id)).fetchone()
    d = json.loads(r[0]) if r else {}; d.update(n)
    DB.execute("insert or replace into docs values(?,?,?,?)", (col, id, json.dumps(d), time.time())); DB.commit()
    return {"ok": True}

# ---------- IA ----------
@app.post("/api/ia")
async def ia(req: Request):
    p = (await req.json()).get("prompt", "")
    if not E("ANTHROPIC_API_KEY") or not E("ANTHROPIC_MODEL"): raise HTTPException(503, "IA não configurada")
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": E("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"},
            json={"model": E("ANTHROPIC_MODEL"), "max_tokens": 4000, "messages": [{"role": "user", "content": p}]})
    if r.status_code >= 300: raise HTTPException(502, r.text[:300])
    t = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\{.*\}", t, re.S)
    if not m: raise HTTPException(502, "a IA não devolveu JSON")
    return JSONResponse(json.loads(m.group(0)))

# ---------- Mercado Livre ----------
def tokens(): return json.loads(TOK.read_text()) if TOK.exists() else {}
def salvar(t): TOK.write_text(json.dumps(t)); TOK.chmod(0o600)

async def token():
    a = tokens()
    if not a: raise HTTPException(409, "nenhuma conta do ML autorizada: abra /oauth/inicio")
    uid, t = next(iter(a.items()))  # no MVP: 1 conta (a do Fernando)
    if t["expira"] - time.time() < 300:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{API}/oauth/token", data={"grant_type": "refresh_token", "client_id": E("ML_CLIENT_ID"), "client_secret": E("ML_CLIENT_SECRET"), "refresh_token": t["refresh_token"]})
        r.raise_for_status(); n = r.json()
        t = {"access_token": n["access_token"], "refresh_token": n["refresh_token"], "expira": time.time() + n["expires_in"]}
        a[uid] = t; salvar(a)
    return {"Authorization": f"Bearer {t['access_token']}"}

@app.get("/oauth/inicio")
def inicio():
    return HTMLResponse(f'<p><a href="{AUTH}?response_type=code&client_id={E("ML_CLIENT_ID")}&redirect_uri={E("ML_REDIRECT")}">Autorizar o Dollar Ads Club na minha conta do Mercado Livre</a></p>')

@app.get("/oauth/callback")
async def callback(code: str):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{API}/oauth/token", data={"grant_type": "authorization_code", "client_id": E("ML_CLIENT_ID"), "client_secret": E("ML_CLIENT_SECRET"), "code": code, "redirect_uri": E("ML_REDIRECT")})
    r.raise_for_status(); n = r.json()
    a = tokens(); a[str(n["user_id"])] = {"access_token": n["access_token"], "refresh_token": n["refresh_token"], "expira": time.time() + n["expires_in"]}; salvar(a)
    return HTMLResponse("Pronto. Pode fechar esta página.")

@app.get("/saude")  # healthcheck do Docker (sem login: ver LIVRES)
def saude(): return {"ok": True}

@app.get("/api/ml/estado")
def estado(): return {"conta": bool(tokens()), "ia": bool(E("ANTHROPIC_API_KEY") and E("ANTHROPIC_MODEL"))}

_s = None
@app.post("/api/ml/foto")  # recebe a foto (data URL), faz o fundo branco (estilo A, 1200x1200) e sobe ao ML
async def foto(req: Request):
    global _s
    from PIL import Image
    from rembg import remove, new_session
    d = (await req.json())["data"]; raw = base64.b64decode(d.split(",", 1)[1])
    if _s is None: _s = new_session("isnet-general-use")
    im = remove(Image.open(io.BytesIO(raw)).convert("RGB"), session=_s)
    im = im.crop(im.getbbox()); k = 1020 / max(im.size); im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
    out = Image.new("RGB", (1200, 1200), "white"); out.paste(im, ((1200 - im.width) // 2, (1200 - im.height) // 2), im)
    b = io.BytesIO(); out.save(b, "JPEG", quality=90)
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{API}/pictures/items/upload", headers=await token(), files={"file": ("foto.jpg", b.getvalue(), "image/jpeg")})
    if r.status_code >= 300: raise HTTPException(r.status_code, r.text[:500])
    return {"id": r.json()["id"]}

@app.get("/api/ml/categoria")  # previsão pelo título (endpoint a confirmar no DevCenter)
async def categoria(q: str):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{API}/sites/MLB/domain_discovery/search", params={"limit": 3, "q": q}, headers=await token())
    return JSONResponse(r.json(), r.status_code)

@app.post("/api/ml/publicar")
async def publicar(req: Request):
    item = await req.json(); desc = item.pop("description", None); h = await token()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{API}/items", json=item, headers=h)
        if r.status_code >= 300: return Response(r.text, r.status_code, media_type="application/json")
        it = r.json()
        if desc: await c.post(f"{API}/items/{it['id']}/description", json=desc, headers=h)
    return {"id": it["id"], "link": it.get("permalink")}

@app.post("/avisos")  # avisos do ML (pedidos): ficam no log; a baixa de estoque o ML faz no anúncio
async def avisos(req: Request):
    with open(DATA / "avisos.log", "a") as f: f.write(json.dumps(await req.json()) + "\n")
    return {"ok": True}

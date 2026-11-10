"""Dollar Ads Club · loudhouse.com.br/dollar-ads-club/

Empresas cadastram produtos (foto com fundo branco feita no navegador), autorizam N contas do
Mercado Livre e compram créditos (Mercado Pago). Afiliados conferem e enviam; o sistema publica na
próxima conta livre da empresa. 1 crédito = 1 anúncio, gasto só quando a API do ML confirma o
anúncio ativo. O afiliado ganha R$ 0,10 por anúncio (pendente por LIBERAR_DIAS; saque mínimo R$ 10).

Rodar local:  uvicorn app:app --port 8000   ->  http://localhost:8000/dollar-ads-club/
Variáveis: ver .env.example. Criar admin:  python app.py admin email@x.com SENHA
"""
import asyncio, hashlib, hmac, io, json, os, re, secrets, sqlite3, sys, threading, time, pathlib, base64
import httpx
from fastapi import FastAPI, APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

E = lambda k, d=None: os.environ.get(k) or d  # variável vazia = padrão
BP = "/" + E("DAC_BASE", "dollar-ads-club").strip("/")              # subcaminho
PUBLICO = E("DAC_URL", "https://loudhouse.com.br").rstrip("/") + BP   # endereço público
ML_API, ML_AUTH = "https://api.mercadolibre.com", "https://auth.mercadolivre.com.br/authorization"
MP_API = "https://api.mercadopago.com"
GANHO = int(E("GANHO_CENTAVOS", "10"))            # R$ 0,10 por anúncio
SAQUE_MIN = int(E("SAQUE_MIN_CENTAVOS", "1000"))  # R$ 10,00
LIBERAR_DIAS = float(E("LIBERAR_DIAS", "0"))  # 0 = libera assim que o anúncio fica ativo
BONUS_DIA = int(E("BONUS_CREDITOS_DIA", "10")); BONUS_DIAS = int(E("BONUS_DIAS", "30"))  # cortesia para empresas novas
PRECO_CREDITO = float(E("PRECO_CREDITO", "1.00")) # R$ por crédito (o que a empresa paga)
SESSAO_DIAS = 14
BASE = pathlib.Path(__file__).parent
DATA = pathlib.Path(E("DAC_DATA", str(BASE / "dados"))); DATA.mkdir(parents=True, exist_ok=True)
FOTOS = DATA / "fotos"; FOTOS.mkdir(exist_ok=True)

# ---------------- banco ----------------
DB = sqlite3.connect(DATA / "dac.sqlite3", check_same_thread=False, isolation_level=None)
DB.row_factory = sqlite3.Row
DB.execute("pragma journal_mode=wal"); DB.execute("pragma foreign_keys=on")
LOCK = threading.RLock()
DB.executescript("""
create table if not exists empresas(id integer primary key, nome text not null, creditos integer not null default 0, criado real);
create table if not exists usuarios(id integer primary key, email text unique not null, senha text not null, nome text,
  papel text not null check(papel in ('empresa','afiliado','admin')), empresa_id integer references empresas(id),
  pix text, aceite_em real, aceite_versao text, criado real);
create table if not exists sessoes(hash text primary key, usuario_id integer not null references usuarios(id) on delete cascade, expira real);
create table if not exists oauth_estados(estado text primary key, empresa_id integer, expira real);
create table if not exists contas_ml(id integer primary key, empresa_id integer not null references empresas(id) on delete cascade,
  ml_user_id text unique not null, apelido text, access text, refresh text, expira real, ativa integer default 1, criado real);
create table if not exists produtos(id integer primary key, empresa_id integer not null references empresas(id) on delete cascade,
  titulo text, descricao text, preco real, estoque integer, categoria_id text, condicao text default 'new', marca text,
  gtin text, sem_gtin_motivo text, extras text default '{}', fotos text default '[]', ativo integer default 0, criado real, atualizado real);
create table if not exists anuncios(id integer primary key, produto_id integer not null references produtos(id) on delete cascade,
  conta_id integer not null references contas_ml(id) on delete cascade, afiliado_id integer not null references usuarios(id),
  item_id text, link text, status text not null, erro text, criado real, atualizado real);
create table if not exists ganhos(id integer primary key, afiliado_id integer not null references usuarios(id), anuncio_id integer unique references anuncios(id),
  centavos integer not null, status text not null, liberar_em real, criado real);
create table if not exists saques(id integer primary key, afiliado_id integer not null references usuarios(id), centavos integer not null,
  pix text not null, status text not null default 'pendente', criado real, resolvido real);
create table if not exists pagamentos(id integer primary key, empresa_id integer not null references empresas(id), creditos integer not null,
  valor real not null, status text not null default 'pendente', mp_id text, criado real, aprovado real);
create table if not exists bonus(empresa_id integer not null, dia text not null, primary key(empresa_id,dia));
create table if not exists movimentos(id integer primary key, empresa_id integer not null, creditos integer not null, motivo text, ref text, criado real);
""")
# status de anúncio: reservado -> (ativo | publicado[aguardando ML] -> ativo | falhou)

def q(sql, a=()):
    with LOCK: return [dict(r) for r in DB.execute(sql, a).fetchall()]
def q1(sql, a=()):
    r = q(sql, a); return r[0] if r else None
def x(sql, a=()):
    with LOCK: c = DB.execute(sql, a); return c.lastrowid if c.lastrowid else c.rowcount
class tx:  # transação exclusiva
    def __enter__(self): LOCK.acquire(); DB.execute("begin immediate")
    def __exit__(self, t, v, tb):
        DB.execute("rollback" if t else "commit"); LOCK.release()

def mov(empresa_id, n, motivo, ref=""):
    DB.execute("update empresas set creditos=creditos+? where id=?", (n, empresa_id))
    DB.execute("insert into movimentos(empresa_id,creditos,motivo,ref,criado) values(?,?,?,?,?)", (empresa_id, n, motivo, str(ref), time.time()))

# ---------------- senhas e sessões ----------------
def hsenha(s, sal=None):
    sal = sal or secrets.token_bytes(16)
    return "scrypt$" + sal.hex() + "$" + hashlib.scrypt(s.encode(), salt=sal, n=2**14, r=8, p=1).hex()
def ok_senha(s, h):
    try: _, sal, _ = h.split("$"); return hmac.compare_digest(hsenha(s, bytes.fromhex(sal)), h)
    except Exception: return False
def sh(t): return hashlib.sha256(t.encode()).hexdigest()
TENTATIVAS = {}
def limitar(chave, n=8, janela=600):
    agora = time.time(); l = [t for t in TENTATIVAS.get(chave, []) if agora - t < janela]
    if len(l) >= n: raise HTTPException(429, "Muitas tentativas. Aguarde alguns minutos.")
    l.append(agora); TENTATIVAS[chave] = l
def ip(req): return req.headers.get("cf-connecting-ip") or (req.client.host if req.client else "?")

def eu(req, *papeis):
    t = req.cookies.get("dac_s")
    u = t and q1("select u.* from sessoes s join usuarios u on u.id=s.usuario_id where s.hash=? and s.expira>?", (sh(t), time.time()))
    if not u: raise HTTPException(401, "Entre na sua conta.")
    if papeis and u["papel"] not in papeis: raise HTTPException(403, "Sem permissão.")
    return u
def abrir_sessao(resp, uid):
    t = secrets.token_urlsafe(32)
    x("insert into sessoes values(?,?,?)", (sh(t), uid, time.time() + SESSAO_DIAS * 86400))
    resp.set_cookie("dac_s", t, max_age=SESSAO_DIAS * 86400, path=BP, httponly=True, samesite="lax",
                    secure=PUBLICO.startswith("https"))
    return resp
def publico_u(u): return {k: u[k] for k in ("id", "email", "nome", "papel", "empresa_id", "pix")}

# ---------------- app ----------------
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
r = APIRouter()
LIVRES = ("/oauth/callback", "/avisos", "/mp/webhook", "/saude")

@app.middleware("http")
async def guarda(req: Request, nxt):
    p = req.url.path
    seg = E("ORIGEM_SEGREDO")  # só o Worker da Cloudflare conhece; bloqueia acesso direto ao túnel
    if seg and req.client and req.client.host not in ("127.0.0.1", "::1") and not hmac.compare_digest(req.headers.get("x-dac-origem", ""), seg):
        return Response("acesso direto bloqueado", 403)
    # CSRF: escrita na API só com o cabeçalho que o nosso front manda (outro site não consegue)
    if p.startswith(BP + "/api/") and req.method not in ("GET", "HEAD") and req.headers.get("x-dac") != "1":
        return Response("requisição recusada", 403)
    resp = await nxt(req)
    resp.headers.update({"X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin", "X-Frame-Options": "DENY"})
    return resp

@app.get("/")
def raiz(): return RedirectResponse(BP + "/")
@app.get(BP)
def sem_barra(): return RedirectResponse(BP + "/")

@r.get("/")
def pagina(): return FileResponse(BASE / "static" / "index.html", headers={"Cache-Control": "no-cache"})
for nome in ("termos.html", "privacidade.html", "icone.png"):
    r.add_api_route("/" + nome, (lambda n=nome: FileResponse(BASE / "static" / n)), methods=["GET"])
@r.get("/saude")
def saude(): return {"ok": True}

# ---------------- contas ----------------
VERSAO_TERMOS = "2026-11-10"
@r.post("/api/cadastro")
async def cadastro(req: Request):
    limitar("cad:" + ip(req), 5, 3600)
    d = await req.json()
    email = str(d.get("email", "")).strip().lower(); senha = str(d.get("senha", "")); papel = d.get("papel")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email): raise HTTPException(400, "E-mail inválido.")
    if len(senha) < 8: raise HTTPException(400, "A senha precisa de 8 caracteres ou mais.")
    if papel not in ("empresa", "afiliado"): raise HTTPException(400, "Escolha empresa ou afiliado.")
    if d.get("aceite") is not True: raise HTTPException(400, "É preciso aceitar os Termos e a Política de Privacidade.")
    nome = str(d.get("nome", "")).strip()[:120]
    if q1("select 1 from usuarios where email=?", (email,)): raise HTTPException(409, "Este e-mail já tem conta.")
    with tx():
        emp = None
        if papel == "empresa":
            emp = DB.execute("insert into empresas(nome,criado) values(?,?)", (str(d.get("empresa") or nome)[:120], time.time())).lastrowid
            DB.execute("insert into bonus values(?,?)", (emp, time.strftime("%Y-%m-%d", time.localtime()))); mov(emp, BONUS_DIA, "bonus diario", "cadastro")
        uid = DB.execute("insert into usuarios(email,senha,nome,papel,empresa_id,aceite_em,aceite_versao,criado) values(?,?,?,?,?,?,?,?)",
                         (email, hsenha(senha), nome, papel, emp, time.time(), VERSAO_TERMOS, time.time())).lastrowid
    return abrir_sessao(JSONResponse({"ok": True}), uid)

@r.post("/api/entrar")
async def entrar(req: Request):
    d = await req.json(); email = str(d.get("email", "")).strip().lower()
    limitar("login:" + ip(req)); limitar("login:" + email, 10)
    u = q1("select * from usuarios where email=?", (email,))
    if not u or not ok_senha(str(d.get("senha", "")), u["senha"]): raise HTTPException(401, "E-mail ou senha incorretos.")
    return abrir_sessao(JSONResponse({"ok": True}), u["id"])

@r.post("/api/sair")
def sair(req: Request):
    t = req.cookies.get("dac_s")
    if t: x("delete from sessoes where hash=?", (sh(t),))
    resp = JSONResponse({"ok": True}); resp.delete_cookie("dac_s", path=BP); return resp

@r.get("/api/eu")
def quem(req: Request):
    u = eu(req); out = {"usuario": publico_u(u)}
    if u["empresa_id"]: out["empresa"] = q1("select id,nome,creditos from empresas where id=?", (u["empresa_id"],))
    out["config"] = {"ganho": GANHO, "saque_min": SAQUE_MIN, "preco_credito": PRECO_CREDITO, "liberar_dias": LIBERAR_DIAS,
                     "ia": bool(E("ANTHROPIC_API_KEY") and E("ANTHROPIC_MODEL")), "mp": bool(E("MP_ACCESS_TOKEN"))}
    return out

@r.post("/api/eu/senha")
async def trocar_senha(req: Request):
    u = eu(req); d = await req.json()
    if not ok_senha(str(d.get("atual", "")), u["senha"]): raise HTTPException(400, "Senha atual incorreta.")
    if len(str(d.get("nova", ""))) < 8: raise HTTPException(400, "A nova senha precisa de 8 caracteres ou mais.")
    x("update usuarios set senha=? where id=?", (hsenha(d["nova"]), u["id"])); x("delete from sessoes where usuario_id=?", (u["id"],))
    return abrir_sessao(JSONResponse({"ok": True}), u["id"])

@r.get("/api/eu/dados")  # LGPD: portabilidade/acesso
def meus_dados(req: Request):
    u = eu(req); d = {"usuario": {k: v for k, v in u.items() if k != "senha"}}
    if u["empresa_id"]:
        e = u["empresa_id"]
        d["empresa"] = q1("select * from empresas where id=?", (e,))
        d["contas_ml"] = q("select id,ml_user_id,apelido,criado from contas_ml where empresa_id=?", (e,))
        d["produtos"] = q("select * from produtos where empresa_id=?", (e,))
        d["pagamentos"] = q("select * from pagamentos where empresa_id=?", (e,))
        d["movimentos"] = q("select * from movimentos where empresa_id=?", (e,))
    else:
        d["anuncios"] = q("select * from anuncios where afiliado_id=?", (u["id"],))
        d["ganhos"] = q("select * from ganhos where afiliado_id=?", (u["id"],))
        d["saques"] = q("select * from saques where afiliado_id=?", (u["id"],))
    return JSONResponse(d, headers={"Content-Disposition": 'attachment; filename="meus-dados-dollar-ads-club.json"'})

@r.post("/api/eu/excluir")  # LGPD: eliminação. Registros financeiros ficam anonimizados (obrigação legal/contábil)
async def excluir(req: Request):
    u = eu(req); d = await req.json()
    if not ok_senha(str(d.get("senha", "")), u["senha"]): raise HTTPException(400, "Senha incorreta.")
    anon = f"excluido-{u['id']}@invalido"
    with tx():
        if u["empresa_id"]:
            e = u["empresa_id"]
            for p in DB.execute("select fotos from produtos where empresa_id=?", (e,)).fetchall():
                for f in json.loads(p[0] or "[]"): (FOTOS / f).unlink(missing_ok=True)
            DB.execute("update contas_ml set access=null, refresh=null, ativa=0 where empresa_id=?", (e,))
            DB.execute("update produtos set ativo=0, titulo='(excluído)', descricao=null, fotos='[]' where empresa_id=?", (e,))
            DB.execute("update empresas set nome='(excluída)' where id=?", (e,))
        DB.execute("update usuarios set email=?, nome=null, pix=null, senha='x' where id=?", (anon, u["id"]))
        DB.execute("delete from sessoes where usuario_id=?", (u["id"],))
    resp = JSONResponse({"ok": True}); resp.delete_cookie("dac_s", path=BP); return resp

# ---------------- fotos ----------------
NOME_FOTO = re.compile(r"^[a-f0-9]{32}\.jpg$")
def salvar_foto(data_url):
    if not str(data_url).startswith("data:image/jpeg;base64,"): raise HTTPException(400, "Foto inválida.")
    raw = base64.b64decode(data_url.split(",", 1)[1])
    if len(raw) > 3_000_000 or raw[:3] != b"\xff\xd8\xff": raise HTTPException(400, "Foto inválida ou grande demais.")
    n = secrets.token_hex(16) + ".jpg"; (FOTOS / n).write_bytes(raw); return n
@r.get("/api/foto/{nome}")
def foto(nome: str, req: Request):
    eu(req)
    if not NOME_FOTO.match(nome) or not (FOTOS / nome).exists(): raise HTTPException(404)
    return FileResponse(FOTOS / nome, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

# ---------------- Mercado Livre: contas da empresa ----------------
def redirect_ml(): return PUBLICO + "/oauth/callback"
@r.get("/api/empresa/ml/conectar")
def conectar(req: Request):
    u = eu(req, "empresa"); est = secrets.token_urlsafe(24)
    x("delete from oauth_estados where expira<?", (time.time(),))
    x("insert into oauth_estados values(?,?,?)", (est, u["empresa_id"], time.time() + 900))
    return {"url": f"{ML_AUTH}?response_type=code&client_id={E('ML_CLIENT_ID','')}&redirect_uri={redirect_ml()}&state={est}"}

@r.get("/oauth/callback")
async def callback(code: str = "", state: str = ""):
    s = q1("select * from oauth_estados where estado=? and expira>?", (state, time.time()))
    if not s or not code: return HTMLResponse("Link expirado ou inválido. Volte ao Dollar Ads Club e clique em conectar de novo.", 400)
    x("delete from oauth_estados where estado=?", (state,))
    async with httpx.AsyncClient(timeout=30) as c:
        t = await c.post(f"{ML_API}/oauth/token", data={"grant_type": "authorization_code", "client_id": E("ML_CLIENT_ID"),
                          "client_secret": E("ML_CLIENT_SECRET"), "code": code, "redirect_uri": redirect_ml()})
        if t.status_code >= 300: return HTMLResponse("O Mercado Livre recusou a autorização. Tente de novo.", 502)
        n = t.json()
        me = await c.get(f"{ML_API}/users/me", headers={"Authorization": f"Bearer {n['access_token']}"})
    apelido = me.json().get("nickname") if me.status_code < 300 else None
    uid = str(n["user_id"]); outra = q1("select empresa_id from contas_ml where ml_user_id=?", (uid,))
    if outra and outra["empresa_id"] != s["empresa_id"]:
        return HTMLResponse("Esta conta do Mercado Livre já está ligada a outra empresa.", 409)
    x("""insert into contas_ml(empresa_id,ml_user_id,apelido,access,refresh,expira,ativa,criado) values(?,?,?,?,?,?,1,?)
         on conflict(ml_user_id) do update set access=excluded.access, refresh=excluded.refresh, expira=excluded.expira, apelido=excluded.apelido, ativa=1""",
      (s["empresa_id"], uid, apelido, n["access_token"], n["refresh_token"], time.time() + n["expires_in"], time.time()))
    return RedirectResponse(BP + "/#contas")

async def token(conta):
    if conta["expira"] - time.time() < 300:
        async with httpx.AsyncClient(timeout=30) as c:
            t = await c.post(f"{ML_API}/oauth/token", data={"grant_type": "refresh_token", "client_id": E("ML_CLIENT_ID"),
                              "client_secret": E("ML_CLIENT_SECRET"), "refresh_token": conta["refresh"]})
        if t.status_code >= 300:
            x("update contas_ml set ativa=0 where id=?", (conta["id"],))
            raise HTTPException(409, f"A conta {conta['apelido'] or conta['ml_user_id']} perdeu a autorização. A empresa precisa reconectar.")
        n = t.json(); conta.update(access=n["access_token"], refresh=n["refresh_token"], expira=time.time() + n["expires_in"])
        x("update contas_ml set access=?, refresh=?, expira=? where id=?", (conta["access"], conta["refresh"], conta["expira"], conta["id"]))
    return {"Authorization": f"Bearer {conta['access']}"}

@r.post("/api/empresa/contas/{cid}/remover")
def remover_conta(cid: int, req: Request):
    u = eu(req, "empresa")
    x("update contas_ml set ativa=0, access=null, refresh=null where id=? and empresa_id=?", (cid, u["empresa_id"]))
    return {"ok": True}

# ---------------- empresa: produtos e créditos ----------------
def disponibilidade_sql():  # contas ativas da empresa que ainda não têm este produto (anti-duplicado do ML)
    return """(select count(*) from contas_ml c where c.empresa_id=p.empresa_id and c.ativa=1 and not exists
               (select 1 from anuncios a where a.produto_id=p.id and a.conta_id=c.id and a.status in ('reservado','publicado','ativo')))"""

@r.get("/api/empresa")
def painel_empresa(req: Request):
    u = eu(req, "empresa"); e = u["empresa_id"]
    prods = q(f"""select p.*, {disponibilidade_sql()} contas_livres,
                 (select count(*) from anuncios a where a.produto_id=p.id and a.status='ativo') anuncios_ativos
                 from produtos p where p.empresa_id=? and p.titulo!='(excluído)' order by p.id desc""", (e,))
    for p in prods: p["fotos"] = json.loads(p["fotos"]); p["extras"] = json.loads(p["extras"] or "{}")
    return {"empresa": q1("select * from empresas where id=?", (e,)),
            "contas": q("select id,ml_user_id,apelido,ativa,criado from contas_ml where empresa_id=? order by id", (e,)),
            "produtos": prods,
            "anuncios": q("""select a.id,a.status,a.link,a.item_id,a.erro,a.criado,p.titulo,c.apelido from anuncios a join produtos p on p.id=a.produto_id
                             join contas_ml c on c.id=a.conta_id where p.empresa_id=? order by a.id desc limit 200""", (e,)),
            "pagamentos": q("select * from pagamentos where empresa_id=? order by id desc limit 50", (e,))}

CAMPOS = ("titulo", "descricao", "preco", "estoque", "categoria_id", "condicao", "marca", "gtin", "sem_gtin_motivo")
@r.post("/api/produtos")
@r.put("/api/produtos/{pid}")
async def salvar_produto(req: Request, pid: int = 0):
    u = eu(req, "empresa"); d = await req.json(); e = u["empresa_id"]
    atual = q1("select * from produtos where id=? and empresa_id=?", (pid, e)) if pid else None
    if pid and not atual: raise HTTPException(404)
    v = {k: d.get(k) for k in CAMPOS}
    v["titulo"] = str(v["titulo"] or "").strip()[:60]
    if not v["titulo"]: raise HTTPException(400, "Falta o título.")
    try: v["preco"] = round(float(v["preco"]), 2); v["estoque"] = int(v["estoque"])
    except Exception: raise HTTPException(400, "Preço e estoque precisam ser números.")
    if v["preco"] <= 0 or v["estoque"] < 1: raise HTTPException(400, "Preço e estoque precisam ser maiores que zero.")
    if v["condicao"] not in ("new", "used"): v["condicao"] = "new"
    if v["gtin"] and not re.fullmatch(r"\d{8,14}", str(v["gtin"])): raise HTTPException(400, "GTIN deve ter de 8 a 14 dígitos.")
    extras = d.get("extras") or {}
    if not isinstance(extras, dict): raise HTTPException(400, "Extras inválidos.")
    antigas = json.loads(atual["fotos"]) if atual else []
    fotos = []
    for f in (d.get("fotos") or [])[:10]:
        fotos.append(f if f in antigas else salvar_foto(f))
    if not fotos: raise HTTPException(400, "Coloque ao menos 1 foto.")
    for f in set(antigas) - set(fotos): (FOTOS / f).unlink(missing_ok=True)
    vals = [v[k] for k in CAMPOS] + [json.dumps(extras), json.dumps(fotos), time.time()]
    if atual:
        x(f"update produtos set {','.join(k+'=?' for k in CAMPOS)}, extras=?, fotos=?, atualizado=? where id=?", vals + [pid])
    else:
        pid = x(f"insert into produtos({','.join(CAMPOS)},extras,fotos,atualizado,empresa_id,criado,ativo) values({','.join('?'*(len(CAMPOS)+3))},?,?,0)", vals + [e, time.time()])
    return {"id": pid}

@r.post("/api/produtos/{pid}/ativo")
async def ativar(pid: int, req: Request):
    u = eu(req, "empresa"); d = await req.json()
    p = q1("select * from produtos where id=? and empresa_id=?", (pid, u["empresa_id"]))
    if not p: raise HTTPException(404)
    if d.get("ativo") and not p["categoria_id"]: raise HTTPException(400, "Defina a categoria do ML antes de ativar.")
    x("update produtos set ativo=? where id=?", (1 if d.get("ativo") else 0, pid)); return {"ok": True}

@r.get("/api/empresa/categoria")
async def categoria(req: Request, termo: str = ""):
    u = eu(req, "empresa")
    conta = q1_conta(u["empresa_id"])
    async with httpx.AsyncClient(timeout=20) as c:
        resp = await c.get(f"{ML_API}/sites/MLB/domain_discovery/search", params={"limit": 5, "q": termo},
                           headers=(await token(conta)) if conta else {})
    return JSONResponse(resp.json(), resp.status_code)
def q1_conta(e): return q1("select * from contas_ml where empresa_id=? and ativa=1 order by id limit 1", (e,))

@r.post("/api/empresa/creditos/comprar")
async def comprar(req: Request):
    u = eu(req, "empresa"); d = await req.json()
    try: n = int(d.get("quantidade"))
    except Exception: raise HTTPException(400, "Quantidade inválida.")
    if not 1 <= n <= 100000: raise HTTPException(400, "Quantidade inválida.")
    if not E("MP_ACCESS_TOKEN"): raise HTTPException(503, "Pagamento ainda não configurado.")
    valor = round(n * PRECO_CREDITO, 2)
    pid = x("insert into pagamentos(empresa_id,creditos,valor,criado) values(?,?,?,?)", (u["empresa_id"], n, valor, time.time()))
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.post(f"{MP_API}/checkout/preferences", headers={"Authorization": f"Bearer {E('MP_ACCESS_TOKEN')}"}, json={
            "items": [{"id": "credito", "title": f"{n} crédito(s) Dollar Ads Club", "quantity": 1, "unit_price": valor, "currency_id": "BRL"}],
            "external_reference": str(pid), "notification_url": PUBLICO + "/mp/webhook",
            "payer": {"email": u["email"]},
            "back_urls": {k: PUBLICO + "/#creditos" for k in ("success", "pending", "failure")}, "auto_return": "approved"})
    if resp.status_code >= 300: raise HTTPException(502, "O Mercado Pago recusou: " + resp.text[:200])
    return {"url": resp.json()["init_point"]}

@r.post("/mp/webhook")
async def mp_webhook(req: Request):
    qs = req.query_params
    try: body = await req.json()
    except Exception: body = {}
    pay_id = str(qs.get("data.id") or (body.get("data") or {}).get("id") or "")
    tipo = qs.get("type") or body.get("type") or qs.get("topic")
    if tipo != "payment" or not pay_id.isdigit(): return {"ok": True}
    seg = E("MP_WEBHOOK_SECRET")
    if seg:  # assinatura x-signature: ts=...,v1=...
        sig = dict(p.split("=", 1) for p in req.headers.get("x-signature", "").split(",") if "=" in p)
        man = f"id:{pay_id};request-id:{req.headers.get('x-request-id','')};ts:{sig.get('ts','')};"
        if not hmac.compare_digest(hmac.new(seg.encode(), man.encode(), hashlib.sha256).hexdigest(), sig.get("v1", "")):
            raise HTTPException(401)
    await conferir_pagamento(pay_id); return {"ok": True}

async def conferir_pagamento(pay_id):  # nunca confia no aviso: busca o pagamento no MP
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.get(f"{MP_API}/v1/payments/{pay_id}", headers={"Authorization": f"Bearer {E('MP_ACCESS_TOKEN')}"})
    if resp.status_code >= 300: return
    p = resp.json(); ref = str(p.get("external_reference") or "")
    if p.get("status") != "approved" or not ref.isdigit(): return
    with tx():
        g = DB.execute("select * from pagamentos where id=?", (int(ref),)).fetchone()
        if not g or g["status"] == "aprovado" or abs(float(p.get("transaction_amount", 0)) - g["valor"]) > 0.009: return
        DB.execute("update pagamentos set status='aprovado', mp_id=?, aprovado=? where id=?", (pay_id, time.time(), g["id"]))
        mov(g["empresa_id"], g["creditos"], "compra", pay_id)

# ---------------- afiliado ----------------
def dar_bonus():  # 10 créditos/dia nos primeiros 30 dias da empresa (uma vez por dia, acumulam)
    hoje = time.strftime("%Y-%m-%d", time.localtime())
    for e in q("select id from empresas where criado>? and nome!='(excluída)'", (time.time() - BONUS_DIAS * 86400,)):
        with tx():
            if DB.execute("insert or ignore into bonus values(?,?)", (e["id"], hoje)).rowcount: mov(e["id"], BONUS_DIA, "bonus diario", hoje)

def libera_ganhos():
    with tx():
        DB.execute("update ganhos set status='disponivel' where status='pendente' and liberar_em<=? and anuncio_id in (select id from anuncios where status='ativo')", (time.time(),))

@r.get("/api/afiliado")
def painel_afiliado(req: Request):
    u = eu(req, "afiliado"); libera_ganhos()
    prods = q(f"""select p.id,p.titulo,p.descricao,p.preco,p.estoque,p.condicao,p.marca,p.fotos,e.nome empresa, {disponibilidade_sql()} contas_livres
                 from produtos p join empresas e on e.id=p.empresa_id where p.ativo=1 and e.creditos>0 order by p.atualizado desc""")
    prods = [dict(p, fotos=json.loads(p["fotos"])) for p in prods if p["contas_livres"] > 0]
    s = lambda st: (q1("select coalesce(sum(centavos),0) v from ganhos where afiliado_id=? and status=?", (u["id"], st)) or {"v": 0})["v"]
    sacado = q1("select coalesce(sum(centavos),0) v from saques where afiliado_id=? and status!='recusado'", (u["id"],))["v"]
    return {"produtos": prods,
            "anuncios": q("""select a.id,a.status,a.link,a.erro,a.criado,p.titulo,g.centavos,g.status ganho,g.liberar_em from anuncios a
                             join produtos p on p.id=a.produto_id left join ganhos g on g.anuncio_id=a.id where a.afiliado_id=? order by a.id desc limit 200""", (u["id"],)),
            "saldo": {"pendente": s("pendente"), "disponivel": s("disponivel") - sacado, "sacado": sacado},
            "saques": q("select * from saques where afiliado_id=? order by id desc", (u["id"],))}

@r.post("/api/afiliado/publicar")
async def publicar(req: Request):
    u = eu(req, "afiliado"); d = await req.json(); limitar(f"pub:{u['id']}", 30, 600)
    titulo = str(d.get("titulo", "")).strip()[:60]; descricao = str(d.get("descricao", "")).strip()[:50000]
    if len(titulo) < 10: raise HTTPException(400, "Título curto demais.")
    # 1) reserva: crédito sai da empresa e a conta livre fica presa a este anúncio (tudo junto)
    with tx():
        p = DB.execute("select p.*, e.creditos from produtos p join empresas e on e.id=p.empresa_id where p.id=? and p.ativo=1", (int(d.get("produto_id", 0)),)).fetchone()
        if not p or p["creditos"] < 1: raise HTTPException(409, "Este produto não está mais disponível.")
        conta = DB.execute("""select * from contas_ml c where c.empresa_id=? and c.ativa=1 and not exists (select 1 from anuncios a where a.produto_id=?
                              and a.conta_id=c.id and a.status in ('reservado','publicado','ativo')) order by random() limit 1""", (p["empresa_id"], p["id"])).fetchone()
        if not conta: raise HTTPException(409, "Este produto já está em todas as contas da empresa.")
        p, conta = dict(p), dict(conta)
        aid = DB.execute("insert into anuncios(produto_id,conta_id,afiliado_id,status,criado,atualizado) values(?,?,?,'reservado',?,?)",
                         (p["id"], conta["id"], u["id"], time.time(), time.time())).lastrowid
        mov(p["empresa_id"], -1, "reserva", aid)
    # 2) publica no ML
    try:
        item_id, link, status = await publicar_ml(conta, p, titulo, descricao)
    except Exception as ex:
        falhar(aid, p["empresa_id"], getattr(ex, "detail", None) or str(ex))
        raise HTTPException(502, "O Mercado Livre recusou: " + str(getattr(ex, "detail", ex))[:400])
    x("update anuncios set item_id=?, link=?, status='publicado', atualizado=? where id=?", (item_id, link, time.time(), aid))
    if status == "active": confirmar(aid)
    return {"id": aid, "status": "ativo" if status == "active" else "publicado", "link": link}

def falhar(aid, empresa_id, erro):  # devolve o crédito
    with tx():
        if DB.execute("update anuncios set status='falhou', erro=?, atualizado=? where id=? and status in ('reservado','publicado')", (str(erro)[:1000], time.time(), aid)).rowcount:
            mov(empresa_id, 1, "devolucao", aid)
def confirmar(aid):  # crédito gasto de vez + ganho do afiliado
    with tx():
        a = DB.execute("select * from anuncios where id=?", (aid,)).fetchone()
        if a and a["status"] != "ativo":
            DB.execute("update anuncios set status='ativo', atualizado=? where id=?", (time.time(), aid))
            DB.execute("insert or ignore into ganhos(afiliado_id,anuncio_id,centavos,status,liberar_em,criado) values(?,?,?,'pendente',?,?)",
                       (a["afiliado_id"], aid, GANHO, time.time() + LIBERAR_DIAS * 86400, time.time()))

class MLErro(Exception):
    def __init__(s, detail): s.detail = detail; super().__init__(detail)
async def publicar_ml(conta, p, titulo, descricao):
    h = await token(conta)
    async with httpx.AsyncClient(timeout=60) as c:
        fotos = []
        for f in json.loads(p["fotos"]):
            resp = await c.post(f"{ML_API}/pictures/items/upload", headers=h, files={"file": (f, (FOTOS / f).read_bytes(), "image/jpeg")})
            if resp.status_code >= 300: raise MLErro("foto: " + resp.text[:300])
            fotos.append({"id": resp.json()["id"]})
        attrs = [{"id": "BRAND", "value_name": p["marca"]}] if p["marca"] else []
        if p["gtin"]: attrs.append({"id": "GTIN", "value_name": p["gtin"]})
        elif p["sem_gtin_motivo"]: attrs.append({"id": "EMPTY_GTIN_REASON", "value_name": p["sem_gtin_motivo"]})
        extras = json.loads(p["extras"] or "{}")
        attrs += [a for a in extras.pop("attributes", []) if isinstance(a, dict)]
        item = {"title": titulo, "category_id": p["categoria_id"], "price": p["preco"], "currency_id": "BRL",
                "available_quantity": p["estoque"], "buying_mode": "buy_it_now", "listing_type_id": E("ML_TIPO_ANUNCIO", "gold_special"),
                "condition": p["condicao"], "pictures": fotos, "attributes": attrs, **extras}
        resp = await c.post(f"{ML_API}/items", json=item, headers=h)
        if resp.status_code >= 300: raise MLErro(resp.text[:800])
        it = resp.json()
        if descricao: await c.post(f"{ML_API}/items/{it['id']}/description", json={"plain_text": descricao}, headers=h)
        st = await c.get(f"{ML_API}/items/{it['id']}", headers=h)
    return it["id"], it.get("permalink"), (st.json().get("status") if st.status_code < 300 else it.get("status"))

@r.post("/api/afiliado/saque")
async def saque(req: Request):
    u = eu(req, "afiliado"); d = await req.json(); pix = str(d.get("pix", "")).strip()[:140]
    if len(pix) < 5: raise HTTPException(400, "Informe a chave Pix.")
    libera_ganhos()
    with tx():
        disp = DB.execute("select coalesce(sum(centavos),0) from ganhos where afiliado_id=? and status='disponivel'", (u["id"],)).fetchone()[0] \
             - DB.execute("select coalesce(sum(centavos),0) from saques where afiliado_id=? and status!='recusado'", (u["id"],)).fetchone()[0]
        if disp < SAQUE_MIN: raise HTTPException(400, "Saque mínimo de R$ " + f"{SAQUE_MIN/100:.2f}".replace(".", ",") + ".")
        DB.execute("insert into saques(afiliado_id,centavos,pix,criado) values(?,?,?,?)", (u["id"], disp, pix, time.time()))
        DB.execute("update usuarios set pix=? where id=?", (pix, u["id"]))
    return {"ok": True, "centavos": disp}

@r.post("/api/ia/variar")  # sugestão de título/descrição para o afiliado conferir (não inventa dados)
async def variar(req: Request):
    eu(req, "afiliado"); d = await req.json()
    p = q1("select titulo,descricao,marca,condicao from produtos where id=? and ativo=1", (int(d.get("produto_id", 0)),))
    if not p: raise HTTPException(404)
    if not (E("ANTHROPIC_API_KEY") and E("ANTHROPIC_MODEL")): raise HTTPException(503, "IA não configurada.")
    prompt = ("Reescreva o anúncio do Mercado Livre abaixo com outras palavras, sem inventar nenhuma característica, medida, garantia ou promessa. "
              "Título até 60 caracteres, sem caixa alta e sem palavras como 'melhor' ou 'original'. Responda só com JSON {\"titulo\":...,\"descricao\":...}.\n\n"
              + json.dumps(p, ensure_ascii=False))
    async with httpx.AsyncClient(timeout=90) as c:
        corpo = {"model": E("ANTHROPIC_MODEL"), "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]}
        hd = {"x-api-key": E("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"}
        if E("ANTHROPIC_EFFORT"): corpo["output_config"] = {"effort": E("ANTHROPIC_EFFORT")}
        resp = await c.post("https://api.anthropic.com/v1/messages", headers=hd, json=corpo)
        if resp.status_code == 400 and "output_config" in corpo:
            corpo.pop("output_config"); resp = await c.post("https://api.anthropic.com/v1/messages", headers=hd, json=corpo)
    if resp.status_code >= 300: raise HTTPException(502, "IA indisponível.")
    t = "".join(b.get("text", "") for b in resp.json().get("content", [])); m = re.search(r"\{.*\}", t, re.S)
    if not m: raise HTTPException(502, "A IA não respondeu no formato esperado.")
    o = json.loads(m.group(0)); return {"titulo": str(o.get("titulo", ""))[:60], "descricao": str(o.get("descricao", ""))}

# ---------------- admin (Loud House) ----------------
@r.get("/api/admin")
def painel_admin(req: Request):
    eu(req, "admin")
    return {"saques": q("select s.*, u.email, u.nome from saques s join usuarios u on u.id=s.afiliado_id order by s.status='pendente' desc, s.id desc limit 300"),
            "empresas": q("select e.*, (select count(*) from contas_ml c where c.empresa_id=e.id and c.ativa=1) contas, (select count(*) from produtos p where p.empresa_id=e.id and p.ativo=1) ativos from empresas e order by e.id desc"),
            "falhas": q("select a.id,a.erro,a.criado,p.titulo from anuncios a join produtos p on p.id=a.produto_id where a.status='falhou' order by a.id desc limit 50")}
@r.post("/api/admin/saques/{sid}")
async def resolver_saque(sid: int, req: Request):
    eu(req, "admin"); st = (await req.json()).get("status")
    if st not in ("pago", "recusado"): raise HTTPException(400)
    x("update saques set status=?, resolvido=? where id=? and status='pendente'", (st, time.time(), sid)); return {"ok": True}
@r.post("/api/admin/ml/usuario-teste")  # cria usuário de teste do ML (sem custo) usando uma conta já conectada
async def usuario_teste(req: Request):
    eu(req, "admin"); conta = q1("select * from contas_ml where ativa=1 order by id limit 1")
    if not conta: raise HTTPException(409, "Conecte antes uma conta do ML (pode ser a sua) numa empresa de teste.")
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.post(f"{ML_API}/users/test_user", json={"site_id": "MLB"}, headers=await token(conta))
    if resp.status_code >= 300: raise HTTPException(502, "O ML recusou: " + resp.text[:300])
    return resp.json()  # id, nickname, password, site_status

@r.post("/api/admin/empresas/{eid}/creditos")
async def ajustar(eid: int, req: Request):
    u = eu(req, "admin"); d = await req.json(); n = int(d.get("quantidade", 0))
    with tx(): mov(eid, n, "ajuste: " + str(d.get("motivo", ""))[:100], f"admin {u['id']}")
    return {"ok": True}

# ---------------- avisos do ML ----------------
@r.post("/avisos")
async def avisos(req: Request):
    b = await req.body()
    if len(b) > 20000: return Response(status_code=413)
    try: d = json.loads(b)
    except Exception: return {"ok": True}
    if not q1("select 1 from contas_ml where ml_user_id=?", (str(d.get("user_id")),)): return {"ok": True}  # só das nossas contas
    if d.get("topic") == "items": asyncio.create_task(rechecar())
    log = DATA / "avisos.log"
    if log.exists() and log.stat().st_size > 20_000_000: log.replace(DATA / "avisos.old.log")
    with open(log, "a") as f: f.write(json.dumps(d) + "\n")
    return {"ok": True}

# ---------------- rotina: confirma anúncios em análise e libera ganhos ----------------
async def rechecar():
    for a in q("select a.*, p.empresa_id from anuncios a join produtos p on p.id=a.produto_id where a.status in ('publicado','ativo') and a.item_id is not null and (a.status='publicado' or a.id in (select anuncio_id from ganhos where status='pendente'))"):
        conta = q1("select * from contas_ml where id=?", (a["conta_id"],))
        try:
            h = await token(conta)
            async with httpx.AsyncClient(timeout=20) as c: st = (await c.get(f"{ML_API}/items/{a['item_id']}", headers=h)).json().get("status")
        except Exception: continue
        if st == "active" and a["status"] == "publicado": confirmar(a["id"])
        elif st in ("closed", "inactive") or (a["status"] == "publicado" and st != "under_review" and time.time() - a["criado"] > 3 * 86400):
            if a["status"] == "publicado": falhar(a["id"], a["empresa_id"], f"não ficou ativo no ML (status {st})")
            else:  # estava ativo e saiu antes de liberar o ganho: ganho cancelado
                x("update anuncios set status='encerrado', atualizado=? where id=?", (time.time(), a["id"]))
                x("update ganhos set status='cancelado' where anuncio_id=? and status='pendente'", (a["id"],))
    libera_ganhos(); dar_bonus()

async def rotina():
    while True:
        try: await rechecar()
        except Exception as ex: print("rotina:", ex, flush=True)
        await asyncio.sleep(600)
@app.on_event("startup")
async def iniciar():
    if E("DAC_ROTINA", "1") == "1": asyncio.create_task(rotina())

app.include_router(r, prefix=BP)
app.mount(BP + "/ort", StaticFiles(directory=BASE / "static" / "ort"), name="ort")
app.mount(BP + "/modelo", StaticFiles(directory=BASE / "static" / "modelo"), name="modelo")

if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "admin":
    em = sys.argv[2].lower()
    x("insert into usuarios(email,senha,nome,papel,criado) values(?,?,?,'admin',?) on conflict(email) do update set senha=excluded.senha, papel='admin'",
      (em, hsenha(sys.argv[3]), "Admin", time.time()))
    print("admin pronto:", em)

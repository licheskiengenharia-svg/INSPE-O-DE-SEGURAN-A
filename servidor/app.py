"""Servidor do Sistema de Inspeção de Segurança (ISE).

Serve o mesmo site do repositório (index.html) e substitui os recursos que,
dentro do Claude, vinham da plataforma: banco compartilhado, contas, análise
do Claude e downloads. O arquivo static/plataforma.js faz essa ponte no
navegador; este servidor guarda os dados (PostgreSQL ou SQLite) e aplica as permissões.

Variáveis de ambiente (veja README.md):
  ISE_SECRET_KEY         obrigatória: chave das sessões de login
  DATABASE_URL           banco PostgreSQL (ex.: Neon gratuito); sem ela, usa SQLite
  ISE_BANCO              caminho do SQLite (padrão: dados/ise.sqlite3)
  ISE_ADMIN_EMAIL / ISE_ADMIN_NOME / ISE_ADMIN_SENHA
                         criam o administrador na primeira vez que o servidor sobe
  ISE_BACKUP_TOKEN       senha da chamada diária que dispara o backup (GitHub Actions)
  ANTHROPIC_API_KEY      chave da API do Claude (sem ela, a análise fica desligada)
  ISE_MODELO             modelo do Claude (padrão: claude-opus-5-5)
  ISE_DRIVE_CREDENCIAL   credencial da conta de serviço do Google (arquivo ou JSON)
  ISE_DRIVE_PASTA        id da pasta "ISE — BACKUP SEGURANÇA DO TRABALHO"
  ISE_BACKUP_AUTOMATICO  1 para o próprio servidor agendar o backup das 23h
                         (só em servidor que não dorme; no Render gratuito use o GitHub Actions)
  ISE_HTTPS              1 quando o site estiver atrás de HTTPS (cookie seguro)
"""
import datetime
import functools
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, Response, abort, g, jsonify, redirect, render_template_string, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from banco import POSTGRES, ErroIntegridade, conectar, gravar_doc

RAIZ = Path(__file__).resolve().parent
SITE = RAIZ.parent / "index.html"
FUSO = ZoneInfo("America/Sao_Paulo")

app = Flask(__name__, static_folder=str(RAIZ / "static"), static_url_path="/static")
app.config.update(
    SECRET_KEY=os.environ.get("ISE_SECRET_KEY") or "",
    MAX_CONTENT_LENGTH=32 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("ISE_HTTPS") == "1",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=12),
)

def db():
    if "con" not in g:
        g.con = conectar()
    return g.con


@app.teardown_appcontext
def fechar(_):
    con = g.pop("con", None)
    if con:
        con.close()


def auditar(op, alvo="", detalhe="", usuario=None):
    u = usuario if usuario is not None else (session.get("uid") or "")
    db().execute("INSERT INTO auditoria (ts, usuario, op, alvo, ip, detalhe) VALUES (?, ?, ?, ?, ?, ?)",
                 (time.time(), u, op, alvo, request.headers.get("X-Forwarded-For", request.remote_addr or ""), detalhe))


# ---------------- contas ----------------
def usuario_atual():
    uid = session.get("uid")
    if not uid:
        return None
    u = db().execute("SELECT * FROM usuarios WHERE id = ? AND ativo = 1", (uid,)).fetchone()
    return u


def exige_login(f):
    @functools.wraps(f)
    def w(*a, **k):
        u = usuario_atual()
        if not u:
            if request.path.startswith("/api/"):
                return jsonify(erro="sessao"), 401
            return redirect(url_for("entrar", proximo=request.path))
        g.usuario = u
        return f(*a, **k)
    return w


def exige_admin(f):
    @exige_login
    @functools.wraps(f)
    def w(*a, **k):
        if g.usuario["papel"] != "admin":
            abort(403)
        return f(*a, **k)
    return w


def criar_usuario(con, email, nome, senha, papel):
    if len(senha) < 8:
        raise ValueError("A senha precisa ter pelo menos 8 caracteres.")
    uid = "u_" + secrets.token_urlsafe(16)[:22]
    con.execute("INSERT INTO usuarios (id, email, nome, senha, papel, ativo, criado_em) VALUES (?, ?, ?, ?, ?, 1, ?)",
                (uid, email.strip().lower(), nome.strip(), generate_password_hash(senha), papel, time.time()))
    return uid


def token_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def confere_csrf():
    if request.form.get("csrf") != session.get("csrf"):
        abort(400)


_tentativas = {}


@app.route("/entrar", methods=["GET", "POST"])
def entrar():
    erro = ""
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
        agora = time.time()
        lista = [t for t in _tentativas.get(ip, []) if agora - t < 900]
        if len(lista) >= 10:
            erro = "Muitas tentativas. Aguarde 15 minutos."
        else:
            u = db().execute("SELECT * FROM usuarios WHERE email = ? AND ativo = 1",
                             (request.form.get("email", "").strip().lower(),)).fetchone()
            if u and check_password_hash(u["senha"], request.form.get("senha", "")):
                session.clear()
                session.permanent = True
                session["uid"] = u["id"]
                auditar("login", usuario=u["id"])
                proximo = request.args.get("proximo", "/")
                return redirect(proximo if proximo.startswith("/") and not proximo.startswith("//") else "/")
            lista.append(agora)
            _tentativas[ip] = lista
            auditar("login-falhou", detalhe=request.form.get("email", "")[:80], usuario="")
            erro = "E-mail ou senha incorretos."
    return pagina("Entrar", FORM_ENTRAR, erro=erro)


@app.route("/sair")
def sair():
    if session.get("uid"):
        auditar("logout")
    session.clear()
    return redirect(url_for("entrar"))


@app.route("/senha", methods=["GET", "POST"])
@exige_login
def trocar_senha():
    msg = ""
    if request.method == "POST":
        confere_csrf()
        if not check_password_hash(g.usuario["senha"], request.form.get("atual", "")):
            msg = "Senha atual incorreta."
        elif len(request.form.get("nova", "")) < 8:
            msg = "A nova senha precisa ter pelo menos 8 caracteres."
        else:
            db().execute("UPDATE usuarios SET senha = ? WHERE id = ?",
                         (generate_password_hash(request.form["nova"]), g.usuario["id"]))
            auditar("senha-alterada")
            msg = "Senha alterada."
    return pagina("Trocar senha", FORM_SENHA, erro=msg, csrf=token_csrf())


@app.route("/usuarios", methods=["GET", "POST"])
@exige_admin
def usuarios():
    msg = ""
    con = db()
    if request.method == "POST":
        confere_csrf()
        acao = request.form.get("acao")
        try:
            if acao == "criar":
                uid = criar_usuario(con, request.form["email"], request.form["nome"], request.form["senha"], "tst")
                auditar("usuario-criado", uid, request.form["email"])
                msg = "TST cadastrado."
            elif acao in ("desativar", "ativar"):
                alvo = request.form["id"]
                if alvo == g.usuario["id"]:
                    raise ValueError("Você não pode desativar a própria conta.")
                con.execute("UPDATE usuarios SET ativo = ? WHERE id = ? AND papel = 'tst'", (1 if acao == "ativar" else 0, alvo))
                auditar("usuario-" + acao, alvo)
                msg = "Conta atualizada."
            elif acao == "senha":
                if len(request.form.get("senha", "")) < 8:
                    raise ValueError("A senha precisa ter pelo menos 8 caracteres.")
                con.execute("UPDATE usuarios SET senha = ? WHERE id = ? AND papel = 'tst'",
                            (generate_password_hash(request.form["senha"]), request.form["id"]))
                auditar("usuario-senha-redefinida", request.form["id"])
                msg = "Senha redefinida."
            elif acao == "backup":
                import backup_drive
                threading.Thread(target=backup_drive.executar, daemon=True).start()
                auditar("backup-manual-iniciado")
                msg = "Backup iniciado. Atualize a página em alguns instantes para ver o resultado."
            elif acao == "importar":
                import importar_backup
                arq = request.files.get("arquivo")
                if not arq or not arq.filename:
                    raise ValueError("Escolha o arquivo ISE_BACKUP_….json.")
                try:
                    pacote = json.load(arq.stream)
                except (ValueError, UnicodeDecodeError):
                    raise ValueError("O arquivo não é um backup do ISE.")
                gravados, pulados = importar_backup.importar(con, pacote, substituir=request.form.get("substituir") == "1",
                                                             usuario=g.usuario["id"])
                msg = f"{pacote.get('nome', 'Backup')}: {gravados} registro(s) importado(s), {pulados} já existiam e foram mantidos."
        except ErroIntegridade:
            msg = "Já existe uma conta com esse e-mail."
        except (ValueError, KeyError) as e:
            msg = str(e) or "Dados incompletos."
    lista = con.execute("SELECT id, email, nome, papel, ativo FROM usuarios ORDER BY papel, nome").fetchall()
    bks = con.execute("SELECT * FROM backups ORDER BY id DESC LIMIT 10").fetchall()
    return pagina("Usuários e backup", PAINEL_ADMIN, erro=msg, csrf=token_csrf(),
                                  lista=lista, bks=bks, fmt=lambda t: datetime.datetime.fromtimestamp(t, FUSO).strftime("%d/%m/%Y %H:%M") if t else "",
                                  drive=bool(os.environ.get("ISE_DRIVE_CREDENCIAL") and os.environ.get("ISE_DRIVE_PASTA")),
                                  temporario=bool(os.environ.get("RENDER")) and not POSTGRES)


# ---------------- site ----------------
@app.route("/")
@exige_login
def site():
    html = SITE.read_text(encoding="utf-8")
    ponte = '<script src="/static/plataforma.js"></script>'
    html = html.replace("<body>", "<body>\n" + ponte, 1) if "<body>" in html else ponte + html
    r = Response(html, mimetype="text/html")
    r.headers["Cache-Control"] = "no-store"
    return r


# ---------------- API usada por static/plataforma.js ----------------
PATH_OK = re.compile(r"^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-.~:@+]+)+$")
SO_ADMIN_GRAVA = ("config", "aprovacoes")
COLECOES_LIVRES = ("inspecoes", "fotos")


def checa_path(path):
    if not path or not PATH_OK.match(path) or len(path.split("/")) != 2:
        abort(400)
    if path.split("/")[0] not in SO_ADMIN_GRAVA + COLECOES_LIVRES:
        abort(400)
    return path


@app.get("/api/eu")
@exige_login
def api_eu():
    u = g.usuario
    return jsonify(id=u["id"], nome=u["nome"], admin=u["papel"] == "admin",
                   ia=bool(os.environ.get("ANTHROPIC_API_KEY")))


@app.post("/api/perfis")
@exige_login
def api_perfis():
    ids = [str(i) for i in (request.get_json(silent=True) or {}).get("ids", [])][:64]
    if not ids:
        return jsonify({})
    q = ",".join("?" * len(ids))
    rows = db().execute(f"SELECT id, nome FROM usuarios WHERE id IN ({q})", ids).fetchall()
    return jsonify({r["id"]: {"name": r["nome"]} for r in rows})


@app.get("/api/sync")
@exige_login
def api_sync():
    """Mudanças desde a última sincronização (fotos ficam de fora; são lidas uma a uma)."""
    desde = request.args.get("desde", "0")
    desde = int(desde) if desde.isdigit() else 0
    if desde == 0:
        rows = db().execute("SELECT path, data, seq FROM docs WHERE colecao != 'fotos' AND data IS NOT NULL").fetchall()
    else:
        rows = db().execute("SELECT path, data, seq FROM docs WHERE colecao != 'fotos' AND seq > ?", (desde,)).fetchall()
    seq = db().execute("SELECT seq FROM contador WHERE id = 1").fetchone()[0]
    return jsonify(seq=seq, completo=desde == 0,
                   mudancas=[{"path": r["path"], "data": json.loads(r["data"]) if r["data"] else None} for r in rows])


@app.get("/api/doc")
@exige_login
def api_doc_get():
    path = checa_path(request.args.get("path", ""))
    r = db().execute("SELECT data FROM docs WHERE path = ?", (path,)).fetchone()
    if not r or r["data"] is None:
        return jsonify(existe=False)
    return jsonify(existe=True, data=json.loads(r["data"]))


@app.put("/api/doc")
@exige_login
def api_doc_put():
    corpo = request.get_json(silent=True) or {}
    path = checa_path(corpo.get("path", ""))
    if path.split("/")[0] in SO_ADMIN_GRAVA and g.usuario["papel"] != "admin":
        return jsonify(erro="permissao"), 403
    if not isinstance(corpo.get("data"), dict):
        abort(400)
    try:
        seq = gravar_doc(db(), path, corpo["data"], g.usuario["id"])
    except ValueError:
        return jsonify(erro="tamanho"), 413
    if not path.startswith("fotos/"):
        auditar("gravou", path)
    return jsonify(ok=True, seq=seq)


@app.delete("/api/doc")
@exige_login
def api_doc_delete():
    path = checa_path(request.args.get("path", ""))
    if g.usuario["papel"] != "admin":  # só o administrador exclui (diretriz 2)
        return jsonify(erro="permissao"), 403
    gravar_doc(db(), path, None, g.usuario["id"])
    auditar("excluiu", path)
    return jsonify(ok=True)


@app.post("/api/ia")
@exige_login
def api_ia():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify(erro="ia_desligada"), 503
    corpo = request.get_json(silent=True) or {}
    prompt = str(corpo.get("prompt", ""))[:60000]
    imagens = [str(i) for i in corpo.get("imagens", [])][:5]
    import ia
    try:
        resposta = ia.analisar(prompt, imagens)
    except ia.ErroIA as e:
        return jsonify(erro=str(e)), 502
    auditar("analise-ia", detalhe=f"{len(imagens)} imagem(ns)")
    return jsonify(resposta)


# ---------------- páginas simples (login e administração) ----------------
PAGINA = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ titulo }} · Inspeção de Segurança</title><style>
body{font-family:system-ui,Arial,sans-serif;background:#f4f3ef;color:#1d211c;margin:0}
.faixa{height:6px;background:repeating-linear-gradient(-45deg,#e8b500 0 12px,#1d211c 12px 24px)}
main{max-width:760px;margin:0 auto;padding:24px 16px}h1{font-size:22px}
form.caixa,section{background:#fff;border:1px solid #ddd;border-radius:8px;padding:16px;margin-bottom:16px}
label{display:block;margin:10px 0 4px;font-weight:600}input{width:100%;box-sizing:border-box;padding:9px;border:1px solid #bbb;border-radius:6px;font-size:15px}
button{margin-top:12px;padding:9px 16px;border:0;border-radius:6px;background:#1d211c;color:#fff;font-size:15px;cursor:pointer}
button.leve{background:#e9e7df;color:#1d211c;margin-top:0}.erro{background:#fff4d6;border:1px solid #e8b500;padding:10px;border-radius:6px}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{border-bottom:1px solid #eee;padding:6px;text-align:left;vertical-align:top}
td form{display:inline}a{color:#1d211c}.ok{color:#1f7a3d}.bad{color:#c0281e}
</style></head><body><div class="faixa"></div><main><h1>{{ titulo }}</h1>
{% if erro %}<p class="erro">{{ erro }}</p>{% endif %}"""
PAGINA_FIM = "</main></body></html>"


def pagina(titulo, corpo, **kw):
    return render_template_string(PAGINA + corpo + PAGINA_FIM, titulo=titulo, **kw)

FORM_ENTRAR = """<form class="caixa" method="post"><label for="e">E-mail</label><input id="e" name="email" type="email" autocomplete="username" required>
<label for="s">Senha</label><input id="s" name="senha" type="password" autocomplete="current-password" required><button>Entrar</button></form>"""

FORM_SENHA = """<form class="caixa" method="post"><input type="hidden" name="csrf" value="{{ csrf }}">
<label for="a">Senha atual</label><input id="a" name="atual" type="password" required>
<label for="n">Nova senha (mínimo 8 caracteres)</label><input id="n" name="nova" type="password" minlength="8" required>
<button>Salvar</button></form><p><a href="/">Voltar ao sistema</a></p>"""

PAINEL_ADMIN = """<p><a href="/">Voltar ao sistema</a> · <a href="/senha">Trocar minha senha</a> · <a href="/sair">Sair</a></p>
<section><h2>Técnicos de Segurança</h2><table><tr><th>Nome</th><th>E-mail</th><th>Situação</th><th></th></tr>
{% for u in lista %}<tr><td>{{ u.nome }}{% if u.papel == 'admin' %} (administrador){% endif %}</td><td>{{ u.email }}</td>
<td>{{ 'Ativo' if u.ativo else 'Desativado' }}</td><td>{% if u.papel == 'tst' %}
<form method="post"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="id" value="{{ u.id }}">
<button class="leve" name="acao" value="{{ 'desativar' if u.ativo else 'ativar' }}">{{ 'Desativar' if u.ativo else 'Ativar' }}</button></form>
<form method="post" style="display:flex;gap:6px;margin-top:6px"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="id" value="{{ u.id }}">
<input name="senha" type="password" placeholder="Nova senha" minlength="8" required style="padding:5px"><button class="leve" name="acao" value="senha">Redefinir</button></form>{% endif %}</td></tr>{% endfor %}</table></section>
<form class="caixa" method="post"><h2 style="margin-top:0">Cadastrar TST</h2><input type="hidden" name="csrf" value="{{ csrf }}">
<label for="n">Nome</label><input id="n" name="nome" required><label for="e">E-mail</label><input id="e" name="email" type="email" required>
<label for="s">Senha inicial (mínimo 8 caracteres)</label><input id="s" name="senha" type="password" minlength="8" required>
<button name="acao" value="criar">Cadastrar</button></form>
<form class="caixa" method="post" enctype="multipart/form-data"><h2 style="margin-top:0">Importar ou restaurar backup</h2>
<p style="margin:0">Aceita o arquivo baixado pelo botão "Baixar backup completo" do site no Claude e os backups automáticos do Drive. Cada registro é conferido antes de gravar; se algum não conferir, nada é gravado.</p>
<input type="hidden" name="csrf" value="{{ csrf }}"><label for="arq">Arquivo ISE_BACKUP_….json</label><input id="arq" name="arquivo" type="file" accept=".json,application/json" required>
<label style="font-weight:400"><input type="checkbox" name="substituir" value="1" style="width:auto"> Restaurar por cima dos registros que já existem</label>
<button name="acao" value="importar">Importar</button></form>
<section><h2>Backup no Google Drive</h2>
{% if temporario %}<p class="bad"><b>Atenção:</b> o banco deste servidor é temporário (sem DATABASE_URL). Os dados se perdem quando o servidor reinicia. Configure o PostgreSQL gratuito (veja o README).</p>{% endif %}
{% if not drive %}<p class="bad">O backup automático não está configurado. Falta a credencial do Google Drive (ISE_DRIVE_CREDENCIAL) ou o id da pasta (ISE_DRIVE_PASTA).</p>{% endif %}
<form method="post"><input type="hidden" name="csrf" value="{{ csrf }}"><button name="acao" value="backup"{% if not drive %} disabled{% endif %}>Executar backup agora</button></form>
<table style="margin-top:12px"><tr><th>Início</th><th>Arquivo</th><th>Resultado</th></tr>
{% for b in bks %}<tr><td>{{ fmt(b.inicio) }}</td><td>{{ b.nome or '' }}</td><td class="{{ 'ok' if b.ok else 'bad' }}">{{ ('Íntegro · %s registros, %s arquivos' % (b.registros, b.arquivos)) if b.ok else ('Falha: ' ~ (b.erro or 'em andamento')) }}</td></tr>{% else %}<tr><td colspan="3">Nenhum backup ainda.</td></tr>{% endfor %}</table></section>"""

# ---------------- agendamento e linha de comando ----------------
def agendar_backup():
    if os.environ.get("ISE_BACKUP_AUTOMATICO") != "1":
        return
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    import backup_drive
    s = BackgroundScheduler(timezone="America/Sao_Paulo")
    s.add_job(backup_drive.executar, CronTrigger(hour=23, minute=0, timezone="America/Sao_Paulo"), id="backup-diario", max_instances=1, coalesce=True, misfire_grace_time=3600)
    s.start()


@app.cli.command("criar-admin")
def cli_criar_admin():
    """Cria a conta do administrador (só pode existir um)."""
    import getpass
    con = conectar()
    if con.execute("SELECT 1 FROM usuarios WHERE papel = 'admin'").fetchone():
        raise SystemExit("Já existe um administrador.")
    email = input("E-mail do administrador: ")
    nome = input("Nome: ")
    senha = getpass.getpass("Senha (mínimo 8 caracteres): ")
    criar_usuario(con, email, nome, senha, "admin")
    print("Administrador criado.")


@app.post("/api/backup/agendado")
def api_backup_agendado():
    """Chamado todo dia às 23h pelo GitHub Actions (.github/workflows/backup-diario.yml).

    No Render gratuito o servidor dorme quando ninguém usa; a chamada o acorda e roda o backup.
    """
    token = os.environ.get("ISE_BACKUP_TOKEN", "")
    if not token or not secrets.compare_digest(request.headers.get("X-ISE-Token", ""), token):
        abort(403)
    import backup_drive
    status = backup_drive.executar()
    return jsonify(status), (200 if status.get("ok") else 500)


def criar_admin_inicial():
    """Cria o administrador a partir das variáveis de ambiente, se ainda não existir nenhum."""
    email, senha = os.environ.get("ISE_ADMIN_EMAIL", "").strip(), os.environ.get("ISE_ADMIN_SENHA", "")
    if not email or not senha:
        return
    con = conectar()
    try:
        if not con.execute("SELECT 1 FROM usuarios WHERE papel = 'admin'").fetchone():
            criar_usuario(con, email, os.environ.get("ISE_ADMIN_NOME", "Administrador"), senha, "admin")
            print("Administrador inicial criado:", email)
    except ErroIntegridade:
        pass  # outro processo criou ao mesmo tempo
    finally:
        con.close()


if not app.config["SECRET_KEY"]:
    raise SystemExit("Defina a variável ISE_SECRET_KEY antes de iniciar o servidor (veja README.md).")
if os.environ.get("RENDER") and not POSTGRES:
    print("ATENÇÃO: sem DATABASE_URL, o banco é temporário e os dados se perdem a cada reinício.", flush=True)
criar_admin_inicial()
agendar_backup()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")))

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, redirect, send_file, session, send_from_directory
from packer.dbpf import DbpfPackage
from packer.stbl import Stbl
from password_policy import validate_new_password
from email_template import render_code_email, ICON_PNG
import os
import tempfile
import uuid
import hashlib
import hmac
import time
import smtplib
import ssl
from email.message import EmailMessage
from flask import has_request_context
from email.utils import formataddr, formatdate, make_msgid
import secrets
import json
import re
import unicodedata
import urllib.parse
import html as _html
import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import timedelta
from supabase import create_client, Client
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import requests as http_requests

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET_NAME = "packages"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')

app = Flask(__name__,
            template_folder=TEMPLATE_DIR,
            static_folder=os.path.join(BASE_DIR, 'img'),
            static_url_path='/img')
app.secret_key = os.environ.get("FLASK_SECRET_KEY")
app.permanent_session_lifetime = timedelta(days=30)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
# A sessão fica inteira no cookie (last-write-wins). Com o padrão (True), TODA resposta reenviava o cookie, e uma
# resposta lenta que começou antes do login podia sobrescrever o cookie novo (logado) pelo antigo (visitante).
# Com False, só quem realmente altera a sessão grava o cookie.
app.config["SESSION_REFRESH_EACH_REQUEST"] = False
# Teto do corpo de qualquer requisição (uploads grandes vão direto pro Supabase, não passam por aqui)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024

UPLOAD_FOLDER = tempfile.gettempdir()


@app.route('/manifest.json')
def manifest():
    return send_from_directory('.', 'manifest.json')


@app.route('/.well-known/assetlinks.json')
def assetlinks():
    return send_from_directory('.well-known', 'assetlinks.json', mimetype='application/json')


@app.route('/sw.js')
def service_worker():
    # Content-Type application/javascript + escopo raiz é obrigatório pro
    # navegador aceitar registrar o Service Worker.
    resp = send_from_directory('.', 'sw.js', mimetype='application/javascript')
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp


@app.before_request
def ensure_guest_session():
    if 'user_id' not in session and 'guest_session_id' not in session:
        session['guest_session_id'] = str(uuid.uuid4())
        session.permanent = True


@app.before_request
def drop_revoked_sessions():
    """O cookie de sessão do Flask é assinado e vale 30 dias; sozinho ele não tem como ser revogado.
    Por isso a sessão guarda a 'session_version' da conta no momento do login, e quando a senha muda a
    versão sobe no banco: cookies antigos (de quem roubou, de aparelho perdido) deixam de valer.
    Sessões criadas antes desta mudança não têm o campo e contam como versão 0."""
    uid = session.get('user_id')
    if not uid or request.endpoint in (None, 'static') or request.path.startswith('/img/'):
        return
    try:
        conn = get_db()
        try:
            c = conn.cursor()
            c.execute("SELECT session_version FROM users WHERE id = %s", (uid,))
            row = c.fetchone()
        finally:
            conn.close()
    except Exception as e:   # banco/coluna indisponível: não derruba todo mundo por causa disso
        print("Aviso: não consegui checar a versão da sessão:", e)
        return
    if row is None or int(row[0] or 0) != int(session.get('sv', 0) or 0):
        print("DIAG sessão derrubada: user_id =", uid, "| linha no banco =", row, "| sv no cookie =", session.get('sv'))
        session.pop('user_id', None)
        session.pop('sv', None)
        session.setdefault('guest_session_id', str(uuid.uuid4()))
        session.permanent = True


@app.after_request
def security_headers(response):
    """Cabeçalhos de proteção em todas as respostas. A CSP aqui é a parte que NÃO quebra o site (o app usa
    scripts/estilos inline): trava embutir o site em iframe de terceiros (clickjacking), <base>/<object>
    injetados e envio de formulário pra fora."""
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Strict-Transport-Security", "max-age=31536000")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Content-Security-Policy", "base-uri 'self'; object-src 'none'; frame-ancestors 'self'; form-action 'self'")
    return response


@app.after_request
def theme_cookie_for_new_device(response):
    """Quem tem conta e abre o site num aparelho novo (sem o cookie "theme") recebe o tema da conta
    já na 1ª página, pra o theme.js aplicá-lo antes de a tela aparecer (sem piscar o tema padrão).
    Valores: id do tema, "default" ou "none" (conta sem tema escolhido). Só roda 1 consulta por aparelho."""
    try:
        if request.method != "GET" or response.mimetype != "text/html":
            return response
        if request.cookies.get("theme") is not None:
            return response
        user_id = session.get("user_id")
        if not user_id:
            return response
        theme = None
        conn = get_db()
        try:
            c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            c.execute("SELECT theme FROM users WHERE id = %s", (user_id,))
            row = c.fetchone()
            theme = (row["theme"] if row else None) or None
        except Exception:
            conn.rollback()
        finally:
            conn.close()
        value = theme if theme and re.fullmatch(r"[a-z0-9_-]{1,32}", theme) else "none"
        response.set_cookie("theme", value, max_age=31536000, samesite="Lax", secure=True, path="/")
    except Exception as e:
        print("Cookie de tema:", e)
    return response


def get_db():
    db_url = os.environ.get("DATABASE_URL")
    if db_url and "sslmode" not in db_url:
        separator = "&" if "?" in db_url else "?"
        db_url += f"{separator}sslmode=require"
    conn = psycopg2.connect(db_url, connect_timeout=5)
    return conn


def init_db():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                profile_pic TEXT
            );
        ''')
        try:
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS security_question TEXT;")
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS security_answer_hash VARCHAR(255);")
        except Exception as e:
            print("Colunas de segurança já existem ou houve um aviso:", e)

        # Suporte a login com Google
        try:
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS google_id VARCHAR(255) UNIQUE;")
            # Permite usuários Google sem senha local
            c.execute("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;")
        except Exception as e:
            print("Colunas google_id já existem ou houve um aviso:", e)

        try:
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url TEXT;")
        except Exception as e:
            print("Coluna avatar_url já existe ou houve um aviso:", e)

        # Apelido usado nas saudações da home (separado do username de login)
        try:
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname VARCHAR(16);")
        except Exception as e:
            print("Coluna nickname já existe ou houve um aviso:", e)

        # CSS personalizado de quem está logado (só o dono vê/aplica)
        try:
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS custom_css TEXT;")
        except Exception as e:
            print("Coluna custom_css já existe ou houve um aviso:", e)

        # Tema pronto escolhido ('default' = padrão; NULL = nunca escolheu)
        try:
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS theme VARCHAR(32);")
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_requested_at TIMESTAMP;")
        except Exception as e:
            print("Coluna theme já existe ou houve um aviso:", e)

        # Versão da sessão: sobe a cada troca/redefinição de senha e derruba os logins antigos (cookies roubados)
        try:
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 0;")
        except Exception as e:
            print("Coluna session_version já existe ou houve um aviso:", e)

        # Login com Google: token de uso único, curto (guardamos só o hash) pra "entregar" a sessão depois do redirect
        try:
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS login_token VARCHAR(64);")
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS login_token_expires TIMESTAMP;")
        except Exception as e:
            print("Colunas login_token já existem ou houve um aviso:", e)

        c.execute('''
            CREATE TABLE IF NOT EXISTS saves (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                guest_session_id VARCHAR(255),
                project_name VARCHAR(255),
                source_lang VARCHAR(50),
                target_lang VARCHAR(50),
                translation_data TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        try:
            c.execute("ALTER TABLE saves ADD COLUMN IF NOT EXISTS package_id VARCHAR(255);")
        except Exception as e:
            print("Coluna package_id já existe:", e)

        # ------------------------------------------------------------------
        # Mapeamento file_id (nome UUID salvo no Supabase Storage) <-> nome
        # original do .package enviado pelo usuário <-> projeto ao qual
        # aquele arquivo pertence. Sem isso, olhando o Storage só se vê
        # hashes aleatórios sem nenhuma referência ao arquivo real.
        # ------------------------------------------------------------------
        c.execute('''
            CREATE TABLE IF NOT EXISTS package_files (
                id SERIAL PRIMARY KEY,
                file_id VARCHAR(255) UNIQUE NOT NULL,
                original_name VARCHAR(255),
                project_name VARCHAR(255),
                user_id INTEGER REFERENCES users(id),
                guest_session_id VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_package_files_project_name ON package_files (project_name);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_package_files_original_name ON package_files (original_name);")
        except Exception as e:
            print("Índices de package_files já existem ou houve um aviso:", e)

        try:
            c.execute("ALTER TABLE package_files ADD COLUMN IF NOT EXISTS orphaned_at TIMESTAMP;")
            c.execute("CREATE INDEX IF NOT EXISTS idx_package_files_orphaned_at ON package_files (orphaned_at);")
        except Exception as e:
            print("Coluna orphaned_at já existe ou houve um aviso:", e)

        # ------------------------------------------------------------------
        # Inscrições de notificação push (sininho). Uma linha por navegador
        # que aceitou notificações — funciona igual pro site aberto no Chrome
        # (PWA) e pro app instalado (TWA), porque os dois usam a mesma Push
        # API por baixo dos panos.
        # ------------------------------------------------------------------
        c.execute('''
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id SERIAL PRIMARY KEY,
                endpoint TEXT UNIQUE NOT NULL,
                p256dh VARCHAR(255) NOT NULL,
                auth VARCHAR(255) NOT NULL,
                user_id INTEGER REFERENCES users(id),
                guest_session_id VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')

        # ------------------------------------------------------------------
        # Histórico de novidades mostrado dentro do site (lista no painel do
        # sininho). Cada disparo de notificação vira uma linha aqui também,
        # então quem não tinha notificação ativada ainda consegue ver o que
        # perdeu.
        # ------------------------------------------------------------------
        c.execute('''
            CREATE TABLE IF NOT EXISTS site_updates (
                id SERIAL PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                body TEXT,
                url VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')

        # ------------------------------------------------------------------
        # E-mail de recuperação de senha (opcional, só contas com senha).
        # Só vale como recuperação depois de confirmado por código.
        # email_codes guarda os códigos de 6 dígitos (só o hash) enviados por
        # e-mail: purpose 'verify' (confirmar e-mail) e 'reset' (trocar senha).
        # ------------------------------------------------------------------
        try:
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255);")
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP;")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_verified_uniq ON users (LOWER(email)) WHERE email_verified_at IS NOT NULL;")
            c.execute('''
                CREATE TABLE IF NOT EXISTS email_codes (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    purpose VARCHAR(16) NOT NULL,
                    email VARCHAR(255) NOT NULL,
                    code_hash VARCHAR(64) NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT LOCALTIMESTAMP,
                    expires_at TIMESTAMP NOT NULL
                );
            ''')
            c.execute("CREATE INDEX IF NOT EXISTS idx_email_codes_user ON email_codes (user_id, purpose);")
            # Limite de tentativas (login, pergunta de segurança) e de pedidos de código.
            # A chave é "tipo:nome digitado" (exista a conta ou não), então o bloqueio
            # se comporta igual pra usuário real e inexistente.
            c.execute('''
                CREATE TABLE IF NOT EXISTS rate_limits (
                    key VARCHAR(160) PRIMARY KEY,
                    fails INTEGER NOT NULL DEFAULT 0,
                    strikes INTEGER NOT NULL DEFAULT 0,
                    locked_until TIMESTAMP,
                    window_start TIMESTAMP NOT NULL DEFAULT LOCALTIMESTAMP,
                    last_at TIMESTAMP NOT NULL DEFAULT LOCALTIMESTAMP
                );
            ''')
        except Exception as e:
            print("Colunas/tabela de e-mail já existem ou houve um aviso:", e)

        conn.commit()
        conn.close()
    except Exception as e:
        print("Aviso ao iniciar DB:", e)


def _bind_session_version(c, user_id):
    """Chamar logo depois de gravar session['user_id']: guarda a versão atual da conta na sessão."""
    try:
        c.execute("SELECT session_version FROM users WHERE id = %s", (user_id,))
        row = c.fetchone()
        v = row["session_version"] if isinstance(row, dict) else (row[0] if row else 0)
        session['sv'] = int(v or 0)
    except Exception as e:
        print("Aviso: não consegui ler a versão da sessão:", e)
        session['sv'] = 0


def register_package_file(file_id, original_name, project_name=None):
    """
    Registra (ou atualiza) o mapeamento entre o file_id usado no Supabase
    Storage e o nome original do .package enviado pelo usuário.

    Chamado sempre que um novo arquivo é enviado ao Storage. Se o file_id
    já existir (upload repetido, retry, etc.), atualiza o nome original e
    preserva o project_name já associado (caso exista).
    """
    if not file_id:
        return
    try:
        conn = get_db()
        c = conn.cursor()
        user_id = session.get('user_id')
        guest_id = session.get('guest_session_id')
        c.execute(
            """
            INSERT INTO package_files (file_id, original_name, project_name, user_id, guest_session_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (file_id) DO UPDATE SET
                original_name = EXCLUDED.original_name,
                user_id = COALESCE(package_files.user_id, EXCLUDED.user_id),
                guest_session_id = COALESCE(package_files.guest_session_id, EXCLUDED.guest_session_id),
                orphaned_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (file_id, original_name, project_name, user_id, guest_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        # Falha no mapeamento não pode quebrar o fluxo de upload/tradução.
        print("Aviso: falha ao registrar mapeamento de arquivo:", e)


def update_package_project_name(file_id, project_name):
    """
    Atualiza o project_name associado a um file_id já registrado.
    Chamado quando o usuário nomeia/salva o projeto (o nome do projeto só
    é conhecido depois do upload, então o vínculo é completado aqui).
    """
    if not file_id or not project_name:
        return
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "UPDATE package_files SET project_name = %s, orphaned_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE file_id = %s",
            (project_name, file_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print("Aviso: falha ao atualizar project_name do mapeamento:", e)


_FILE_ID_RE = re.compile(r"(?:out_)?[0-9a-f]{32}\.package")


def _owns_file(file_id):
    """True se o arquivo do Storage (file_id) é de quem está fazendo a requisição: ou foi registrado em
    package_files por esta conta/sessão de convidado, ou algum save desta conta/sessão aponta pra ele.
    Sem essa checagem, qualquer um que soubesse (ou vazasse) o id baixava arquivo de outra pessoa."""
    if not file_id or not _FILE_ID_RE.fullmatch(str(file_id)):
        return False
    uid = session.get('user_id')
    gid = session.get('guest_session_id')
    conds, params = [], []
    if uid:
        conds.append("user_id = %s"); params.append(uid)
    if gid:
        conds.append("guest_session_id = %s"); params.append(gid)
    if not conds:
        return False
    where = "(" + " OR ".join(conds) + ")"
    try:
        conn = get_db()
        try:
            c = conn.cursor()
            c.execute(
                f"SELECT 1 FROM package_files WHERE file_id = %s AND {where} "
                f"UNION ALL SELECT 1 FROM saves WHERE package_id = %s AND {where} LIMIT 1",
                [file_id] + params + [file_id] + params)
            return c.fetchone() is not None
        finally:
            conn.close()
    except Exception as e:
        print("Aviso: falha ao checar dono do arquivo:", e)
        return False


def _safe_download_name(raw, default="nome.package"):
    name = re.sub(r'[\x00-\x1f\x7f"\\/]', "_", str(raw or "")).strip()[:150]
    return name or default


def get_original_name_for_file(file_id):
    """Busca o nome original do .package a partir do file_id (UUID no Storage)."""
    if not file_id:
        return None
    try:
        conn = get_db()
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT original_name FROM package_files WHERE file_id = %s", (file_id,))
        row = c.fetchone()
        conn.close()
        return row["original_name"] if row else None
    except Exception as e:
        print("Aviso: falha ao buscar original_name do mapeamento:", e)
        return None


def load_stbl(pkg, rid):
    resource = pkg[rid]
    content = pkg.content(resource)
    stbl = Stbl(rid, content)
    for key, value in stbl.strings.items():
        stbl._strings[key] = value
    return stbl


def _process_package_bytes(file_bytes):
    """
    Recebe os bytes de um .package, extrai todas as instâncias STBL
    e retorna a lista de instâncias + um dict de cache de strings
    indexado por instance (int).

    O cache evita re-downloads do Supabase nas rotas /api/strings e /save.
    """
    all_instances = []
    strings_cache = {}  # { instance_int: [ {key, key_hex, value}, ... ] }

    with tempfile.NamedTemporaryFile(delete=False, suffix=".package") as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        with DbpfPackage.read(tmp_path) as pkg:
            for rid in pkg.search_stbl():
                lang = rid.language or "UNKNOWN"
                all_instances.append({
                    "instance":      rid.instance,
                    "instance_hex":  rid.hex_instance,
                    "group_hex":     rid.str_group,
                    "language":      lang,
                    "language_code": rid.language_code or "0x0017",
                })
                stbl = load_stbl(pkg, rid)
                strings_cache[rid.instance] = [
                    {"key": k, "key_hex": hex(k), "value": v}
                    for k, v in stbl._strings.items()
                ]
    finally:
        os.remove(tmp_path)

    return all_instances, strings_cache


@app.route("/api/push/vapid_public_key")
def push_vapid_public_key():
    return {"key": os.environ.get("VAPID_PUBLIC_KEY", "")}


@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    keys = data.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not endpoint or not p256dh or not auth:
        return {"error": "Inscrição inválida"}, 400
    if (not isinstance(endpoint, str) or not endpoint.startswith("https://") or len(endpoint) > 1000
            or not isinstance(p256dh, str) or not isinstance(auth, str) or len(p256dh) > 255 or len(auth) > 255):
        return {"error": "Inscrição inválida"}, 400

    user_id = session.get('user_id')
    guest_id = session.get('guest_session_id')

    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_id, guest_session_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (endpoint) DO UPDATE SET
                p256dh = EXCLUDED.p256dh,
                auth = EXCLUDED.auth,
                user_id = COALESCE(EXCLUDED.user_id, push_subscriptions.user_id),
                guest_session_id = COALESCE(EXCLUDED.guest_session_id, push_subscriptions.guest_session_id)
            """,
            (endpoint, p256dh, auth, user_id, guest_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print("Erro ao salvar inscrição push:", e)
        return {"error": "Falha ao salvar inscrição"}, 500

    return {"ok": True}


@app.route("/api/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    if not endpoint or not isinstance(endpoint, str) or len(endpoint) > 1000:
        return {"error": "endpoint ausente"}, 400

    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,))
        conn.commit()
        conn.close()
    except Exception as e:
        print("Erro ao remover inscrição push:", e)
        return {"error": "Falha ao remover inscrição"}, 500

    return {"ok": True}


# Marcações dentro do texto da novidade: o admin escreve [nome](endereço) pra link
# e {atributos|trecho} pra formatação (b i u s g p #cor m#destaque, separados por
# vírgula, ex. {b,#ff4fa3|texto}). O site mostra só o nome/trecho, formatado. No
# banco o texto fica com a marcação; já o push (notificação do sistema, que não
# aceita link nem formatação no meio do texto) leva texto simples.
_UPDATE_LINK_RE = re.compile(r"\[([^\[\]\n]{1,120})\]\(([^()\s]{1,500})\)")
_UPDATE_FX_ATTR = r"(?:[biusgpc]|m?#[0-9a-fA-F]{6})"
_UPDATE_FX_RE = re.compile(r"\{" + _UPDATE_FX_ATTR + r"(?:," + _UPDATE_FX_ATTR + r")*\|([^{}]+)\}")
# Linha horizontal: uma linha só com {hr}. No push (texto simples) ela some.
_UPDATE_HR_RE = re.compile(r"(?:^|\n)\{hr\}(?=\n|$)")


def strip_update_markup(text):
    text = _UPDATE_FX_RE.sub(r"\1", text or "")
    text = _UPDATE_HR_RE.sub("", text).lstrip("\n")

    def _sub(m):
        url = m.group(2)
        if re.match(r"^/(?![/\\])", url) or re.match(r"(?i)^https?://", url):
            return m.group(1)
        return m.group(0)
    return _UPDATE_LINK_RE.sub(_sub, text)


@app.route("/api/updates")
def get_updates():
    # Só devolve a lista de novidades pra quem realmente tem uma inscrição
    # de push ativa no servidor — o navegador manda o endpoint da própria
    # inscrição como prova. Sem isso (ou com endpoint desconhecido),
    # devolve vazio e "subscribed": false, pro front mostrar uma mensagem
    # pedindo pra ativar em vez da lista.
    endpoint = request.args.get("endpoint", "").strip()
    if not endpoint:
        return {"updates": [], "subscribed": False}, 200, {"Cache-Control": "no-store"}

    try:
        conn = get_db()
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        c.execute("SELECT 1 FROM push_subscriptions WHERE endpoint = %s", (endpoint,))
        if not c.fetchone():
            conn.close()
            return {"updates": [], "subscribed": False}, 200, {"Cache-Control": "no-store"}

        c.execute("SELECT id, title, body, url, created_at FROM site_updates ORDER BY created_at DESC LIMIT 30")
        rows = c.fetchall()
        conn.close()
    except Exception as e:
        print("Erro ao buscar updates:", e)
        return {"updates": []}, 500

    updates = [
        {
            "id": r["id"],
            "title": r["title"],
            "body": r["body"],
            "url": r["url"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None
        }
        for r in rows
    ]
    return {"updates": updates, "subscribed": True}, 200, {"Cache-Control": "no-store"}


def _admin_denied():
    """None se o X-Admin-Secret está certo; senão a resposta de erro. Compara em tempo constante e limita
    tentativas por IP (30 a cada 15 min), pra não dar pra descobrir a chave testando sem parar."""
    wait = _window_take(f"adm:{_client_ip()}", 30, 900)
    if wait > 0:
        return {"error": f"Muitas tentativas. Tente de novo em {_fmt_wait(wait)}."}, 429
    secret = request.headers.get("X-Admin-Secret", "")
    expected = os.environ.get("ADMIN_NOTIFY_SECRET", "")
    if not expected or not hmac.compare_digest(secret.encode(), expected.encode()):
        return {"error": "Não autorizado"}, 401
    return None


@app.route("/admin")
def admin_notify_page():
    response = send_file(os.path.join(TEMPLATE_DIR, "admin_notify.html"))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/admin/delete_update", methods=["POST"])
def admin_delete_update():
    denied = _admin_denied()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    if ids is None:
        single_id = data.get("id")
        ids = [single_id] if single_id else []
    if not ids:
        return {"error": "Faltou o id (ou ids)"}, 400

    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM site_updates WHERE id = ANY(%s)", (ids,))
        conn.commit()
        conn.close()
    except Exception as e:
        print("Falha ao apagar novidades:", e)
        return {"error": "Falha ao apagar (veja o log do servidor)."}, 500

    # Manda um push "silencioso" (sem abrir notificação nova) pra todo
    # mundo inscrito, avisando pra fechar a(s) notificação(ões) específica(s)
    # que estiver(em) aberta(s) na barra e recarregar a listinha de quem
    # estiver com o site aberto — sem isso, some só quando a pessoa arrasta
    # pro lado manualmente ou recarrega a página.
    try:
        _push_dismiss_updates(ids)
    except Exception as e:
        print("Aviso: falha ao mandar dismiss push:", e)

    return {"deleted": ids}


def _push_dismiss_updates(ids=None):
    from pywebpush import webpush, WebPushException

    vapid_private_key = os.environ.get("VAPID_PRIVATE_KEY", "")
    vapid_claims = {"sub": os.environ.get("VAPID_CLAIM_EMAIL", "mailto:contato@example.com")}
    if not vapid_private_key:
        return

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions")
    subs = c.fetchall()
    conn.close()

    payload = json.dumps({"type": "dismiss-updates", "ids": ids or []})
    dead_ids = []
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}
                },
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims=dict(vapid_claims)
            )
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                dead_ids.append(sub["id"])

    if dead_ids:
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute("DELETE FROM push_subscriptions WHERE id = ANY(%s)", (dead_ids,))
            conn.commit()
            conn.close()
        except Exception:
            pass


@app.route("/api/admin/edit_update", methods=["POST"])
def admin_edit_update():
    denied = _admin_denied()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    update_id = data.get("id")
    title = data.get("title") or "Sims 4 Translator"
    body = data.get("body") or "Tem novidade por aqui!"
    url = data.get("url") or "/"

    if not update_id:
        return {"error": "Faltou o id"}, 400

    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "UPDATE site_updates SET title = %s, body = %s, url = %s WHERE id = %s",
            (title, body, url, update_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print("Falha ao editar novidade:", e)
        return {"error": "Falha ao editar (veja o log do servidor)."}, 500

    # Push silencioso: só atualiza o texto de quem AINDA tiver a notificação
    # na barra (renotify/silent no service worker). Quem já dispensou não
    # recebe nada de volta — a edição não "ressuscita" notificação fechada.
    try:
        _push_edit_update(update_id, title, strip_update_markup(body), url)
    except Exception as e:
        print("Aviso: falha ao mandar edit push:", e)

    return {"ok": True, "id": update_id}


def _push_edit_update(update_id, title, body, url):
    from pywebpush import webpush, WebPushException

    vapid_private_key = os.environ.get("VAPID_PRIVATE_KEY", "")
    vapid_claims = {"sub": os.environ.get("VAPID_CLAIM_EMAIL", "mailto:contato@example.com")}
    if not vapid_private_key:
        return

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions")
    subs = c.fetchall()
    conn.close()

    payload = json.dumps({
        "type": "edit-update",
        "id": update_id,
        "tag": f"update-{update_id}",
        "title": title,
        "body": body,
        "url": url
    })

    dead_ids = []
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}
                },
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims=dict(vapid_claims)
            )
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                dead_ids.append(sub["id"])

    if dead_ids:
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute("DELETE FROM push_subscriptions WHERE id = ANY(%s)", (dead_ids,))
            conn.commit()
            conn.close()
        except Exception:
            pass


@app.route("/api/admin/updates")
def admin_list_updates():
    denied = _admin_denied()
    if denied:
        return denied

    try:
        conn = get_db()
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT id, title, body, url, created_at FROM site_updates ORDER BY created_at DESC LIMIT 30")
        rows = c.fetchall()
        conn.close()
    except Exception as e:
        print("Erro ao buscar updates (admin):", e)
        return {"updates": []}, 500

    updates = [
        {
            "id": r["id"],
            "title": r["title"],
            "body": r["body"],
            "url": r["url"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None
        }
        for r in rows
    ]
    return {"updates": updates}


@app.route("/api/admin/notify_update", methods=["POST"])
def admin_notify_update():
    denied = _admin_denied()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    title = data.get("title") or "Sims 4 Translator"
    body = data.get("body") or "Tem novidade por aqui!"
    url = data.get("url") or "/"

    # Grava no histórico do site independente de ter alguém inscrito em
    # push ou não — é o que alimenta a listinha dentro do painel do sininho.
    new_id = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO site_updates (title, body, url) VALUES (%s, %s, %s) RETURNING id",
            (title, body, url)
        )
        new_id = c.fetchone()[0]
        conn.commit()
        conn.close()
    except Exception as e:
        print("Erro ao gravar update no histórico:", e)

    from pywebpush import webpush, WebPushException

    vapid_private_key = os.environ.get("VAPID_PRIVATE_KEY", "")
    vapid_claims = {"sub": os.environ.get("VAPID_CLAIM_EMAIL", "mailto:contato@example.com")}

    sent, failed, removed = 0, 0, 0

    try:
        conn = get_db()
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions")
        subs = c.fetchall()
        conn.close()
    except Exception as e:
        print("Falha ao ler inscrições:", e)
        return {"error": "Falha ao ler inscrições (veja o log do servidor)."}, 500

    # Tag única por notificação (baseada no id do banco) — assim cada
    # novidade empilha separada na barra em vez de substituir a anterior,
    # e dá pra fechar/editar uma específica sem mexer nas outras.
    tag = f"update-{new_id}" if new_id else "update"
    payload = json.dumps({"title": title, "body": strip_update_markup(body), "url": url, "tag": tag})

    dead_ids = []
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}
                },
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims=dict(vapid_claims)
            )
            sent += 1
        except WebPushException as e:
            failed += 1
            # 404/410 = inscrição expirada (usuário desinstalou, limpou dados etc.)
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                dead_ids.append(sub["id"])

    if dead_ids:
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute("DELETE FROM push_subscriptions WHERE id = ANY(%s)", (dead_ids,))
            conn.commit()
            conn.close()
            removed = len(dead_ids)
        except Exception as e:
            print("Erro ao limpar inscrições mortas:", e)

    return {"ok": True, "sent": sent, "failed": failed, "removed": removed, "total": len(subs)}


@app.route("/")
def home():
    # Só descarta o projeto aberto (arquivo/idioma da sessão anterior). Antes era session.clear() + restaurar
    # user_id/guest_id, o que regravava o cookie a cada visita (e já perdeu o 'sv' uma vez).
    for k in ("package_id", "original_name", "all_instances", "db_save_id"):
        session.pop(k, None)
    if not session.permanent:   # sem isso o cookie vira "de sessão" e o Android descarta ao matar o app
        session.permanent = True

    response = send_file(os.path.join(TEMPLATE_DIR, "index.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


# ---------------------------------------------------------------------------
#  FLUXO DE UPLOAD (upload direto Frontend → Supabase)
# ---------------------------------------------------------------------------

@app.route("/api/upload_url", methods=["POST"])
def get_upload_url():
    """
    Gera uma signed URL para o frontend fazer o upload do .package
    diretamente no Supabase Storage, sem passar pelo Flask.
    """
    wait = _window_take(f"upl:{_client_ip()}", 60, 3600)   # sem isso qualquer um enche o Storage
    if wait > 0:
        return _too_many_json(wait, "Muitos envios deste aparelho/rede")
    file_id = f"{uuid.uuid4().hex}.package"
    res = supabase.storage.from_(BUCKET_NAME).create_signed_upload_url(file_id)
    # Já amarra o arquivo a quem pediu o upload: o /upload/confirm só aceita ids que sejam de quem confirma.
    register_package_file(file_id, None)
    return app.response_class(
        response=json.dumps({
            "file_id":    file_id,
            "signed_url": res["signed_url"],
        }),
        mimetype="application/json"
    )


@app.route("/upload/confirm", methods=["POST"])
def upload_confirm():
    """
    Chamado pelo frontend APÓS o upload direto para o Supabase.
    Baixa o arquivo UMA vez, processa todas as STBLs e cacheia as strings
    na sessão para que /api/strings nunca precise baixar de novo.
    """
    data          = request.get_json(silent=True) or {}
    file_id       = data.get("file_id")
    original_name = str(data.get("original_name") or "nome.package")[:255]

    if not file_id:
        return json.dumps({"error": "file_id ausente"}), 400
    if not _owns_file(file_id) or str(file_id).startswith("out_"):
        return json.dumps({"error": "Arquivo não encontrado. Envie o .package de novo."}), 403

    try:
        file_bytes = supabase.storage.from_(BUCKET_NAME).download(file_id)
    except Exception as e:
        print("Erro ao baixar arquivo do storage:", e)
        return json.dumps({"error": "Não consegui ler o arquivo enviado. Tente enviar de novo."}), 500

    all_instances, strings_cache = _process_package_bytes(file_bytes)

    if not all_instances:
        return json.dumps({"error": "Nenhuma STBL encontrada no arquivo"}), 400

    session["package_id"]     = file_id
    session["original_name"]  = original_name
    session["all_instances"]  = all_instances
    # strings_cache removido da sessão: o texto das strings é grande demais
    # pra caber no cookie (~4KB), o que estava silenciosamente corrompendo
    # a sessão e mandando o usuário de volta pra home depois do upload.

    # Registra o mapeamento file_id (UUID no Storage) -> nome original,
    # para que o hash aleatório salvo no Storage não vire uma via de mão única.
    register_package_file(file_id, original_name)

    return json.dumps({"success": True, "redirect": "/info"})


# Rota legada mantida para compatibilidade com o Android/Termux que ainda usa
# o form submit. Remove quando migrar o index.html do app nativo também.
@app.route("/upload", methods=["POST"])
def upload():
    if "package" not in request.files:
        return "Nenhum arquivo enviado", 400

    f = request.files["package"]
    if not f.filename.endswith(".package"):
        return "Arquivo inválido. Envie um .package", 400

    file_id    = f"{uuid.uuid4().hex}.package"
    file_bytes = f.read()

    try:
        supabase.storage.from_(BUCKET_NAME).upload(file_id, file_bytes)
    except Exception as e:
        print("Erro no upload:", e)
        return "Erro no upload. Tente de novo.", 500

    all_instances, strings_cache = _process_package_bytes(file_bytes)

    if not all_instances:
        return "Nenhuma STBL encontrada no arquivo", 400

    session["package_id"]    = file_id
    session["original_name"] = f.filename
    session["all_instances"] = all_instances
    # strings_cache removido da sessão (ver comentário em upload_confirm)

    register_package_file(file_id, f.filename)

    return redirect("/info")


# ---------------------------------------------------------------------------

def send_page(name):
    """Serve uma página HTML de templates/ sem cair em cache velho.
    O send_file puro gera o ETag a partir de data de modificação + tamanho do arquivo. Na Vercel a data
    dos arquivos não muda entre deploys, então editar o HTML sem mudar o nº de bytes (ex.: trocar uma data)
    mantém o mesmo ETag e o navegador/CDN responde 304 e continua mostrando a página antiga.
    Aqui o ETag vem do CONTEÚDO (md5): mudou 1 caractere, mudou o ETag."""
    with open(os.path.join(TEMPLATE_DIR, name), "rb") as f:
        body = f.read()
    response = app.response_class(body, mimetype="text/html")
    response.set_etag(hashlib.md5(body).hexdigest())
    response.headers["Cache-Control"] = "no-cache"
    return response.make_conditional(request)


@app.route("/info")
def info():
    if not session.get("all_instances"):
        return redirect("/")
    return send_page("info.html")


@app.route("/suporte-css")
def suporte_css():
    return send_page("suporte-css.html")


@app.route("/privacidade")
def privacidade():
    return send_page("privacidade.html")


@app.route("/montar-tema")
def montar_tema():
    return send_page("montar-tema.html")


@app.route("/montar-tema/previa/<tela>")
def montar_tema_previa(tela):
    """Telas de verdade (CSS real + dados de exemplo, sem scripts) pro iframe do Montador de tema."""
    from previa_tema import montar_previa
    html = montar_previa(tela, request.args.get("m", ""), TEMPLATE_DIR)
    if html is None:
        return "Tela não encontrada", 404
    response = app.response_class(html, mimetype="text/html")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/desenhar-svg")
def desenhar_svg():
    return send_page("desenhar-svg.html")


@app.route("/api/instances")
def api_instances():
    all_instances = session.get("all_instances", [])
    original_name = session.get("original_name", "nome.package")

    seen_bases = {}
    for inst in all_instances:
        base = inst["instance_hex"][4:]
        if base not in seen_bases:
            seen_bases[base] = inst
        else:
            if seen_bases[base]["language"] != "ENG_US" and inst["language"] == "ENG_US":
                seen_bases[base] = inst

    unique_instances = list(seen_bases.values())

    safe = [
        {
            "instance_str": str(i["instance"]),
            "instance_hex": i["instance_hex"],
            "group_hex": i["group_hex"],
            "language": i["language"],
        }
        for i in unique_instances
    ]

    langs_present = sorted(set(i["language"] for i in all_instances))

    return app.response_class(
        response=json.dumps({
            "instances": safe,
            "original_name": original_name,
            "langs_present": langs_present,
            "total_stbl": len(all_instances),
            "package_id": session.get("package_id", ""),
        }),
        mimetype="application/json"
    )


@app.route("/editor")
def editor():
    # "recover=1" é usado pelo botão "Recuperar" da home quando existe um
    # rascunho de tradução salvo no localStorage do navegador. Nesse caso o
    # editor carrega os dados direto do rascunho (client-side), então não
    # depende da sessão do servidor ainda ter o package_id — o que é
    # justamente o cenário que pode ter se perdido (sessão expirada, muito
    # tempo fora do site, etc).
    if not session.get("package_id") and request.args.get("recover") != "1":
        return redirect("/")
    response = send_file(os.path.join(TEMPLATE_DIR, "editor.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route("/api/strings")
def api_strings():
    instance_str = request.args.get("instance")
    if not instance_str:
        return "Instância não informada", 400

    pkg_id = session.get("package_id")

    # --- Caso especial: save do banco sem .package associado ---
    if pkg_id == "DATABASE_SAVE":
        db_save_id = session.get("db_save_id")
        if not db_save_id:
            return app.response_class(response=json.dumps({"strings": []}), mimetype="application/json")

        conn = get_db()
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT translation_data FROM saves WHERE id = %s", (db_save_id,))
        row = c.fetchone()
        conn.close()

        db_strings = json.loads(row["translation_data"]) if row else []
        return app.response_class(response=json.dumps({"strings": db_strings}), mimetype="application/json")

    # --- Fallback: baixa do Supabase (cache de sessão removido por exceder o limite do cookie) ---
    instance_int = int(instance_str)
    strings = []

    file_bytes = supabase.storage.from_(BUCKET_NAME).download(pkg_id)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".package") as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        with DbpfPackage.read(tmp_path) as pkg:
            for rid in pkg.search_stbl():
                if rid.instance == instance_int:
                    stbl = load_stbl(pkg, rid)
                    for key, value in stbl._strings.items():
                        strings.append({
                            "key": key,
                            "key_hex": hex(key),
                            "value": value,
                        })
                    break
    finally:
        os.remove(tmp_path)

    return app.response_class(
        response=json.dumps({"strings": strings}),
        mimetype="application/json"
    )


@app.route("/save", methods=["POST"])
def save():
    data = request.get_json()
    instance_str = str(data["instance"])
    instance_int = int(instance_str)
    strings_edited = data["strings"]
    target_lang = data.get("target_language", "POR_BR")
    output_name = data.get("output_name", "nome").strip()
    custom_instance_base = data.get("custom_instance_base", None)  # 14 dígitos hex opcionais
    if not output_name:
        output_name = "nome"
    if not output_name.endswith(".package"):
        output_name += ".package"

    pkg_id = session.get("package_id")
    if not pkg_id or pkg_id == "DATABASE_SAVE":
        pkg_id = data.get("package_id", "")
        if pkg_id and pkg_id != "DATABASE_SAVE" and not _owns_file(pkg_id):
            pkg_id = ""   # id que não é seu: trata como "sem arquivo" (nunca baixa arquivo de outra pessoa)
    if not pkg_id or pkg_id == "DATABASE_SAVE":
        return json.dumps({"success": False, "error": "Arquivo original não disponível. Reimporte o .package para gerar o arquivo traduzido."}), 400

    target_rid = None
    target_stbl = None
    output_id = None

    original_bytes = supabase.storage.from_(BUCKET_NAME).download(pkg_id)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".package") as tmp_in, \
         tempfile.NamedTemporaryFile(delete=False, suffix=".package") as tmp_out:
        tmp_in.write(original_bytes)
        tmp_in_path = tmp_in.name
        tmp_out_path = tmp_out.name

    try:
        with DbpfPackage.read(tmp_in_path) as pkg:
            for rid in pkg.search_stbl():
                if rid.instance == instance_int:
                    target_rid = rid
                    target_stbl = load_stbl(pkg, rid)
                    break

        if target_stbl is None:
            return json.dumps({"error": "STBL não encontrada"}), 400

        for item in strings_edited:
            target_stbl.add(int(item["key"]), item["value"])

        try:
            new_rid = target_rid.convert_instance(locale=target_lang)
            # Se o usuário forneceu uma instância base customizada (14 dígitos hex),
            # substitui os 14 dígitos base mantendo o language.code do locale no início
            if custom_instance_base and len(custom_instance_base) == 14:
                # hex_instance = "0x115BB614C9012DBA" -> locale code = 2 dígitos após "0x"
                locale_code = new_rid.hex_instance[2:4]  # ex: "11"
                new_instance_int = int(locale_code + custom_instance_base, 16)
                new_rid = new_rid._replace(instance=new_instance_int)
        except Exception as e:
            print(f"Aviso: não foi possível converter para o idioma {target_lang!r} ({e}); "
                  "salvando no idioma original da STBL.")
            new_rid = target_rid

        with DbpfPackage.write(tmp_out_path) as outpkg:
            with DbpfPackage.read(tmp_in_path) as original:
                for rid in original.search():
                    resource = original[rid]
                    content = original.content(resource)
                    if rid == target_rid:
                        outpkg.put(new_rid, target_stbl.binary)
                    elif rid == new_rid:
                        pass
                    else:
                        outpkg.put(rid, content)

        output_id = f"out_{uuid.uuid4().hex}.package"
        with open(tmp_out_path, "rb") as f_out:
            supabase.storage.from_(BUCKET_NAME).upload(output_id, f_out.read())
        register_package_file(output_id, output_name)   # o /download só entrega pra quem gerou
    finally:
        os.remove(tmp_in_path)
        os.remove(tmp_out_path)

    return json.dumps({
        "success": True,
        "output_name": output_name,
        "output_id": output_id,
        "saved_to_downloads": False,
    })


@app.route("/download")
def download():
    output_id = request.args.get("file")
    output_name = _safe_download_name(request.args.get("name", "nome.package"))
    if not output_id or output_id == "null" or not _owns_file(output_id):
        return "Arquivo não encontrado", 404

    url_res = supabase.storage.from_(BUCKET_NAME).create_signed_url(output_id, 60, {"download": output_name})
    return redirect(url_res['signedURL'])


def _normalize_engine_id(raw: str) -> str | None:
    """O front atual já manda o id exato (ex.: 'featherless-rocinante'),
    então isso é checado primeiro. O fuzzy-match abaixo só existe pra manter
    compatibilidade com valores soltos de versões antigas do front
    (ex.: 'groq (llama)'). Retorna None se não reconhecer nada — nesse caso
    a cadeia de fallback padrão decide sozinha."""
    if not raw:
        return None
    from translator_v2 import ENGINES
    if raw in ENGINES:
        return raw
    r = raw.lower()
    if "google" in r:
        return "google"
    if "groq" in r or "llama" in r:
        return "groq-llama"
    if "venice" in r or "dolphin" in r:
        return "openrouter-venice"
    if "rocinante" in r or "heretic" in r:
        return "featherless-rocinante"
    if "storm" in r:
        return "featherless-stormseeker"
    if "floppa" in r:
        return "featherless-floppa"
    if "hauhau" in r:
        return "phone-hauhau"
    if "ministral" in r or "heresy" in r:
        return "phone-ministral"
    return None


@app.route("/api/engines")
def api_engines():
    from translator_v2 import list_available_engines
    return json.dumps(list_available_engines())


TRANSLATE_MAX_CHARS = 5000
TRANSLATE_BULK_MAX_ITEMS = 100


@app.route("/api/translate", methods=["POST"])
def api_translate():
    from translator_v2 import translate_with_fallback

    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "")
    engine = _normalize_engine_id(data.get("engine", ""))
    target_lang = str(data.get("target_lang") or "POR_BR")[:16]
    tone = str(data.get("tone") or "formal")[:32]  # front ainda não manda isso - default formal

    if not text:
        return json.dumps({"error": "No text provided"}), 400
    if len(text) > TRANSLATE_MAX_CHARS:
        return json.dumps({"success": False, "translated": None, "error": "Texto grande demais pra traduzir de uma vez."}), 413
    # as engines gastam chaves/cota de IA pagas: limite por IP (o "Traduzir tudo" faz uma chamada por string)
    wait = _window_take(f"trn:{_client_ip()}", 300, 60)
    if wait > 0:
        return json.dumps({"success": False, "translated": None, "error": f"Muitas traduções seguidas. Tente de novo em {_fmt_wait(wait)}."}), 429

    try:
        result = translate_with_fallback(text, target_lang=target_lang, tone=tone, preferred_engine=engine)
        return json.dumps({
            "success": True,
            "translated": result["text"],
            "engine_used": result["engine_used"],
            "error": None,
        })
    except RuntimeError as e:
        print("Falha na tradução:", str(e)[:500])   # o detalhe (respostas das APIs) fica só no log do servidor
        return json.dumps({"success": False, "translated": None, "error": "Nenhum motor de tradução conseguiu traduzir agora. Tente de novo em instantes."}), 502


@app.route("/api/translate_bulk", methods=["POST"])
def api_translate_bulk():
    """Tradução em massa (botão 'Traduzir tudo'). Roda em paralelo com um
    limite de workers - rate limit de LLM costuma ser por minuto, não por
    conexão simultânea, então mais threads só faz todo mundo cair em 429
    junto."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from translator_v2 import translate_with_fallback

    data = request.get_json(silent=True) or {}
    items = data.get("items", [])  # [{"idx": 12, "text": "...", "tone": "formal"}, ...]
    target_lang = str(data.get("target_lang") or "POR_BR")[:16]
    engine = _normalize_engine_id(data.get("engine", ""))

    if not isinstance(items, list) or not items:
        return json.dumps({"results": []})
    if len(items) > TRANSLATE_BULK_MAX_ITEMS or any(
            not isinstance(it, dict) or "idx" not in it or len(str(it.get("text") or "")) > TRANSLATE_MAX_CHARS for it in items):
        return json.dumps({"results": [], "error": f"Envie no máximo {TRANSLATE_BULK_MAX_ITEMS} textos (até {TRANSLATE_MAX_CHARS} caracteres cada) por vez."}), 413
    wait = _window_take(f"trnb:{_client_ip()}", 6, 60)   # cada chamada traz até 100 textos
    if wait > 0:
        return json.dumps({"results": [], "error": f"Muitas traduções seguidas. Tente de novo em {_fmt_wait(wait)}."}), 429

    MAX_WORKERS = 4
    results = [None] * len(items)

    def worker(position, item):
        try:
            r = translate_with_fallback(
                item.get("text", ""),
                target_lang=target_lang,
                tone=item.get("tone", "formal"),
                preferred_engine=engine,
            )
            return position, {"idx": item["idx"], "translated": r["text"], "engine_used": r["engine_used"], "error": None}
        except RuntimeError as e:
            # uma string falhando não derruba as outras - erro só nesse item
            print("Falha na tradução (bulk):", str(e)[:300])
            return position, {"idx": item["idx"], "translated": None, "error": "Não deu pra traduzir este texto agora."}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(worker, i, item) for i, item in enumerate(items)]
        for future in as_completed(futures):
            position, result = future.result()
            results[position] = result

    return json.dumps({"results": results})


@app.route("/api/save_progress", methods=["POST"])
def save_progress():
    data = request.get_json()
    project_name = data.get("project_name", "Projeto sem nome")
    source_lang = data.get("source_lang", "ENG_US")
    target_lang = data.get("target_lang", "POR_BR")
    translation_data = json.dumps(data.get("strings", []))
    package_id = data.get("package_id", "")
    if package_id and package_id != "DATABASE_SAVE" and package_id != session.get("package_id") and not _owns_file(package_id):
        package_id = ""                                   # id de arquivo que não é seu: ignora (não vincula arquivo alheio ao save)
    if not package_id or package_id == "DATABASE_SAVE":
        package_id = session.get("package_id") or ""      # o navegador não mandou: usa o arquivo aberto na sessão
    if package_id == "DATABASE_SAVE":
        package_id = ""                                   # "sem arquivo" nunca é gravado como se fosse um arquivo
    user_id = session.get('user_id')
    guest_id = session.get('guest_session_id')

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if user_id:
        c.execute("SELECT id FROM saves WHERE user_id = %s AND project_name = %s", (user_id, project_name))
    else:
        c.execute("SELECT id FROM saves WHERE guest_session_id = %s AND project_name = %s", (guest_id, project_name))
    existing_save = c.fetchone()

    if existing_save:
        c.execute(
            "UPDATE saves SET translation_data = %s, package_id = COALESCE(NULLIF(%s, ''), package_id), last_updated = CURRENT_TIMESTAMP WHERE id = %s",
            (translation_data, package_id, existing_save['id'])
        )
    else:
        c.execute(
            "INSERT INTO saves (user_id, guest_session_id, project_name, source_lang, target_lang, translation_data, package_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (user_id, guest_id, project_name, source_lang, target_lang, translation_data, package_id)
        )

    conn.commit()
    conn.close()

    # O nome do projeto só é conhecido neste momento (upload não sabe o
    # nome do projeto ainda) - completa o mapeamento file_id -> project_name.
    update_package_project_name(package_id, project_name)

    return json.dumps({"success": True})


@app.route("/api/get_saves")
def get_saves():
    user_id = session.get('user_id')
    guest_id = session.get('guest_session_id')

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if user_id:
        c.execute("SELECT id, project_name, source_lang, target_lang, last_updated FROM saves WHERE user_id = %s ORDER BY last_updated DESC", (user_id,))
    else:
        c.execute("SELECT id, project_name, source_lang, target_lang, last_updated FROM saves WHERE guest_session_id = %s ORDER BY last_updated DESC", (guest_id,))

    rows = c.fetchall()
    conn.close()

    saves = [dict(row) for row in rows]
    for save in saves:
        if 'last_updated' in save and save['last_updated']:
            save['last_updated'] = str(save['last_updated'])

    return json.dumps({"saves": saves})


@app.route("/api/delete_save", methods=["POST"])
def delete_save():
    data = request.get_json()
    save_id = data.get("id")
    user_id = session.get('user_id')
    guest_id = session.get('guest_session_id')

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Busca o package_id ANTES de apagar o save, pra saber qual arquivo do
    # Storage está associado a este projeto (e confirma que pertence a
    # quem está pedindo a exclusão).
    if user_id:
        c.execute("SELECT package_id FROM saves WHERE id = %s AND user_id = %s", (save_id, user_id))
    else:
        c.execute("SELECT package_id FROM saves WHERE id = %s AND guest_session_id = %s", (save_id, guest_id))
    save = c.fetchone()

    if not save:
        conn.close()
        return json.dumps({"success": False, "error": "Save não encontrado ou acesso negado"}), 404

    package_id = save["package_id"]

    if user_id:
        c.execute("DELETE FROM saves WHERE id = %s AND user_id = %s", (save_id, user_id))
    else:
        c.execute("DELETE FROM saves WHERE id = %s AND guest_session_id = %s", (save_id, guest_id))

    # Antes de mexer no Storage, confirma que nenhum OUTRO projeto ainda
    # aponta pro mesmo package_id (ex.: "salvar como" um novo nome
    # reaproveitando o mesmo .package sem reenviar o arquivo).
    if package_id and package_id != "DATABASE_SAVE":
        c.execute("SELECT COUNT(*) AS total FROM saves WHERE package_id = %s", (package_id,))
        row = c.fetchone()
        file_still_in_use = bool(row and row["total"] > 0)

        if not file_still_in_use:
            # Ninguém mais usa esse arquivo AGORA, mas pode existir uma aba/
            # sessão ainda traduzindo esse mesmo .package sem ter salvo ainda
            # (o /api/strings e o /save-translated rebaixam o arquivo do
            # Storage sob demanda). Por isso o arquivo NÃO é apagado aqui:
            # só marcamos como "órfão". A remoção de verdade acontece depois
            # de um período de carência, em /api/admin/cleanup_orphaned_files,
            # e se o arquivo voltar a ser salvo como projeto antes disso, a
            # marca de órfão é removida automaticamente.
            c.execute("UPDATE package_files SET orphaned_at = CURRENT_TIMESTAMP WHERE file_id = %s", (package_id,))

    conn.commit()
    conn.close()

    return json.dumps({"success": True})


def _cleanup_authorized():
    """
    Autoriza a limpeza definitiva de arquivos órfãos por dois caminhos:
    - Vercel Cron: define automaticamente o header
      'Authorization: Bearer <CRON_SECRET>' quando a env var CRON_SECRET
      está configurada no projeto.
    - Disparo manual: acessar a rota com ?key=<a mesma secret>.
    """
    secret = os.environ.get("CRON_SECRET") or os.environ.get("ADMIN_CLEANUP_KEY")
    if not secret:
        return False
    auth_header = request.headers.get("Authorization", "")
    if hmac.compare_digest(auth_header.encode(), f"Bearer {secret}".encode()):
        return True
    return hmac.compare_digest(request.args.get("key", "").encode(), secret.encode())


@app.route("/api/admin/cleanup_orphaned_files")
def cleanup_orphaned_files():
    """
    Remove DE VERDADE, do Supabase Storage, os arquivos que:
    - foram marcados como órfãos (projeto deletado e nenhum outro save usa)
    - já passaram do período de carência (grace_hours, padrão 48h)
    - e continuam sem nenhum save apontando pra eles (checagem repetida
      aqui, pro caso de terem sido "resgatados" nesse meio tempo).

    Protegida por chave secreta (CRON_SECRET/ADMIN_CLEANUP_KEY) para não
    poder ser disparada por qualquer um. Pensada para rodar via Vercel Cron
    (ver vercel.json) ou manualmente, acessando a URL com ?key=...
    """
    wait = _window_take(f"adm:{_client_ip()}", 30, 900)
    if wait > 0:
        return json.dumps({"error": "too many attempts"}), 429
    if not _cleanup_authorized():
        return json.dumps({"error": "unauthorized"}), 401

    try:
        accounts = purge_expired_accounts()
    except Exception as e:
        print("Aviso: falha na limpeza de contas:", e)
        accounts = {"error": "falhou (veja o log do servidor)"}

    try:
        grace_hours = int(request.args.get("grace_hours", 48))
    except ValueError:
        grace_hours = 48
    # Trava de segurança: mesmo com a secret correta, um "0" ou número
    # negativo digitado errado não pode apagar órfãos recém-criados.
    # 6h é o mínimo aceito (dá tempo de qualquer sessão em andamento salvar).
    grace_hours = max(grace_hours, 6)

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute(
        """
        SELECT file_id FROM package_files
        WHERE orphaned_at IS NOT NULL
          AND orphaned_at < NOW() - (%s || ' hours')::interval
        """,
        (grace_hours,)
    )
    candidates = [row["file_id"] for row in c.fetchall()]

    removed, rescued, failed = [], [], []
    for file_id in candidates:
        c.execute("SELECT COUNT(*) AS total FROM saves WHERE package_id = %s", (file_id,))
        if c.fetchone()["total"] > 0:
            # Alguém salvou um projeto usando esse arquivo nesse meio tempo.
            c.execute("UPDATE package_files SET orphaned_at = NULL WHERE file_id = %s", (file_id,))
            rescued.append(file_id)
            continue

        try:
            supabase.storage.from_(BUCKET_NAME).remove([file_id])
            c.execute("DELETE FROM package_files WHERE file_id = %s", (file_id,))
            removed.append(file_id)
        except Exception as e:
            print(f"Aviso: falha ao remover {file_id} do Storage:", e)
            failed.append(file_id)

    conn.commit()
    conn.close()

    return json.dumps({
        "accounts": accounts,
        "grace_hours": grace_hours,
        "removed": removed,
        "rescued": rescued,
        "failed": failed,
    })


@app.route("/api/load_save/<int:save_id>")
def load_save(save_id):
    user_id = session.get('user_id')
    guest_id = session.get('guest_session_id')

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if user_id:
        c.execute("SELECT * FROM saves WHERE id = %s AND user_id = %s", (save_id, user_id))
    else:
        c.execute("SELECT * FROM saves WHERE id = %s AND guest_session_id = %s", (save_id, guest_id))

    save = c.fetchone()
    conn.close()

    if not save:
        return "Save não encontrado ou acesso negado", 404

    strings = json.loads(save["translation_data"])
    pkg_id = save.get("package_id") or "DATABASE_SAVE"

    # Tenta resolver o nome original de verdade a partir do mapeamento
    # file_id -> original_name; só cai no "chute" (nome do projeto) se o
    # arquivo for anterior à existência da tabela package_files.
    original_name = get_original_name_for_file(pkg_id) or save["project_name"].split(" (")[0]

    session["package_id"] = pkg_id
    session["original_name"] = original_name
    session["db_save_id"] = save_id

    return json.dumps({
        "success": True,
        "project_name": save["project_name"],
        "source_lang": save["source_lang"],
        "target_lang": save["target_lang"],
        "translation_data": strings,
        "package_id": pkg_id,
        "original_name": original_name,
    })


USERNAME_MIN, USERNAME_MAX = 3, 32
_PIC_RE = re.compile(r"data:image/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+")
PROFILE_PIC_MAX = 400_000   # caracteres (o app já reduz a foto antes de enviar)


def clean_username(raw):
    """Normaliza e valida o nome de usuário. Retorna (nome, None) ou (None, erro)."""
    name = unicodedata.normalize("NFC", str(raw or "")).strip()
    if len(name) < USERNAME_MIN:
        return None, f"O nome de usuário precisa ter pelo menos {USERNAME_MIN} caracteres."
    if len(name) > USERNAME_MAX:
        return None, f"O nome de usuário pode ter no máximo {USERNAME_MAX} caracteres."
    if any(unicodedata.category(ch)[0] == "C" for ch in name):   # controle, invisíveis, quebra de linha...
        return None, "O nome de usuário tem caracteres inválidos."
    return name, None


def _valid_profile_pic(pic):
    if not pic:
        return True
    if len(pic) > PROFILE_PIC_MAX:
        return False
    return bool(_PIC_RE.fullmatch(pic)) or pic.startswith("https://") or pic.startswith("/img/")


@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "").strip()
    question = str(data.get("security_question") or "").strip()
    answer = str(data.get("security_answer") or "").strip()

    wait = _window_take(f"reg:{_client_ip()}", 8, 3600)   # criação de contas em massa
    if wait > 0:
        return _too_many_json(wait, "Muitas contas criadas deste aparelho/rede")

    if not username or not password or not question or not answer:
        return json.dumps({"success": False, "error": "Preencha todos os campos."}), 400
    username, name_error = clean_username(username)
    if name_error:
        return json.dumps({"success": False, "error": name_error}), 400
    pw_error = validate_new_password(password, username)
    if pw_error:
        return json.dumps({"success": False, "error": pw_error}), 400

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    c.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(%s)", (username,))   # "Ana" e "ana" são o mesmo nome
    if c.fetchone():
        conn.close()
        return json.dumps({"success": False, "error": "Nome de usuário já existe."}), 400

    pass_hash = generate_password_hash(password)
    answer_hash = generate_password_hash(answer.lower())

    c.execute(
        "INSERT INTO users (username, password_hash, security_question, security_answer_hash) VALUES (%s, %s, %s, %s) RETURNING id",
        (username, pass_hash, question, answer_hash)
    )
    new_user_id = c.fetchone()['id']

    guest_id = session.get('guest_session_id')
    if guest_id:
        c.execute("UPDATE saves SET user_id = %s WHERE guest_session_id = %s AND user_id IS NULL", (new_user_id, guest_id))

    conn.commit()
    conn.close()

    session['user_id'] = new_user_id
    session['sv'] = 0
    session.permanent = True
    return json.dumps({"success": True})


# ─── LIMITE DE TENTATIVAS ───────────────────────────────────────────────────
# Login e pergunta de segurança: 5 erros bloqueiam por 30 min; se voltar e errar
# 5 de novo, 35 min, depois 40... (+5 min a cada bloqueio). Acertar zera tudo, e
# 24h sem nenhum erro também zera. Pedido de código por e-mail: 3 por janela de 2 dias.
LOGIN_MAX_FAILS = 5
LOCK_BASE_MIN = 30
LOCK_STEP_MIN = 5
LOCK_MEMORY_HOURS = 24
RESET_REQ_MAX = 3
RESET_REQ_WINDOW_S = 2 * 24 * 3600


def _rl_key(kind, username):
    name = unicodedata.normalize("NFKC", str(username or "")).strip().lower()[:64]
    return f"{kind}:{name}"


def _fmt_wait(seconds):
    m = max(1, int((float(seconds) + 59) // 60))
    d, rem = divmod(m, 1440)
    h, mm = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d} dia{'s' if d > 1 else ''}")
    if h:
        parts.append(f"{h} hora{'s' if h > 1 else ''}")
    if mm and not d:
        parts.append(f"{mm} minuto{'s' if mm > 1 else ''}")
    return " e ".join(parts) or "1 minuto"


def _rl_row(c, key):
    """Garante a linha e a trava (FOR UPDATE) pra contar sem corrida. SEM commit."""
    c.execute("INSERT INTO rate_limits (key) VALUES (%s) ON CONFLICT (key) DO NOTHING", (key,))
    c.execute(
        "SELECT fails, strikes, "
        "EXTRACT(EPOCH FROM (LOCALTIMESTAMP - window_start)) AS window_age, "
        "EXTRACT(EPOCH FROM (LOCALTIMESTAMP - last_at)) AS idle "
        "FROM rate_limits WHERE key = %s FOR UPDATE", (key,))
    return c.fetchone()


def _lock_wait_seconds(c, key):
    """Segundos que faltam pro bloqueio acabar (0 = livre)."""
    c.execute("SELECT EXTRACT(EPOCH FROM (locked_until - LOCALTIMESTAMP)) AS left_s FROM rate_limits WHERE key = %s", (key,))
    r = c.fetchone()
    left = float(r["left_s"]) if r and r["left_s"] is not None else 0
    return left if left > 0 else 0


def _lock_register_fail(c, key, max_fails=None):
    """Conta 1 erro. Retorna (minutos_de_bloqueio, tentativas_restantes); minutos > 0 se ESTE erro bloqueou. SEM commit."""
    c.execute("DELETE FROM rate_limits WHERE last_at < LOCALTIMESTAMP - INTERVAL '3 days' "
              "AND (locked_until IS NULL OR locked_until < LOCALTIMESTAMP)")
    row = _rl_row(c, key)
    fails, strikes = row["fails"], row["strikes"]
    if row["idle"] is not None and float(row["idle"]) > LOCK_MEMORY_HOURS * 3600:
        fails, strikes = 0, 0           # ficou 24h sem errar: começa do zero
    fails += 1
    max_fails = max_fails or LOGIN_MAX_FAILS
    if fails >= max_fails:
        strikes += 1
        minutes = LOCK_BASE_MIN + LOCK_STEP_MIN * (strikes - 1)
        c.execute("UPDATE rate_limits SET fails = 0, strikes = %s, last_at = LOCALTIMESTAMP, "
                  "locked_until = LOCALTIMESTAMP + (%s || ' minutes')::interval WHERE key = %s",
                  (strikes, str(minutes), key))
        return minutes, 0
    c.execute("UPDATE rate_limits SET fails = %s, strikes = %s, last_at = LOCALTIMESTAMP WHERE key = %s", (fails, strikes, key))
    return 0, max_fails - fails


def _lock_clear(c, key):
    c.execute("DELETE FROM rate_limits WHERE key = %s", (key,))


LOGIN_GLOBAL_MAX_FAILS = 25   # erros somando TODOS os IPs contra o mesmo usuário (limite por usuário+IP é LOGIN_MAX_FAILS)


def _client_ip():
    """IP de quem fez a requisição (na Vercel o primeiro do X-Forwarded-For é o do cliente)."""
    fwd = request.headers.get("X-Forwarded-For", "")
    ip = (fwd.split(",")[0].strip() if fwd else "") or request.headers.get("X-Real-IP", "") or (request.remote_addr or "")
    return re.sub(r"[^0-9a-fA-F:.]", "", ip)[:45] or "?"


def _lock_clear_family(c, key):
    """Zera o bloqueio do usuário (key) e os bloqueios por usuário+IP (key|ip)."""
    c.execute("DELETE FROM rate_limits WHERE key = %s OR left(key, %s) = %s", (key, len(key) + 1, key + "|"))


def _window_take(key, max_hits, window_s):
    """Contador de janela fixa (ex.: pedidos por IP). Retorna 0 se liberado, senão os segundos até liberar.
    Usa conexão própria e, se o banco falhar, libera (não derruba o site por causa do limite)."""
    try:
        conn = get_db()
        try:
            c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if secrets.randbelow(50) == 0:   # faxina de vez em quando
                c.execute("DELETE FROM rate_limits WHERE last_at < LOCALTIMESTAMP - INTERVAL '3 days' "
                          "AND (locked_until IS NULL OR locked_until < LOCALTIMESTAMP)")
            row = _rl_row(c, key)
            hits = row["fails"]
            age = float(row["window_age"]) if row["window_age"] is not None else 0
            if age >= window_s:
                hits, age = 0, 0
                c.execute("UPDATE rate_limits SET window_start = LOCALTIMESTAMP WHERE key = %s", (key,))
            if hits >= max_hits:
                conn.commit()
                return max(1, window_s - age)
            c.execute("UPDATE rate_limits SET fails = %s, last_at = LOCALTIMESTAMP WHERE key = %s", (hits + 1, key))
            conn.commit()
            return 0
        finally:
            conn.close()
    except Exception as e:
        print("Aviso: limite por IP indisponível:", e)
        return 0


def _too_many_json(wait, msg="Muitas tentativas"):
    return json.dumps({"success": False, "error": f"{msg}. Tente de novo em {_fmt_wait(wait)}."}), 429


def _reset_req_take(c, key):
    """Conta 1 pedido de código. Retorna 0 se liberado, senão os segundos até poder pedir de novo. SEM commit."""
    row = _rl_row(c, key)
    fails = row["fails"]
    age = float(row["window_age"]) if row["window_age"] is not None else 0
    if age >= RESET_REQ_WINDOW_S:        # passou a janela de 2 dias: recomeça
        fails, age = 0, 0
        c.execute("UPDATE rate_limits SET window_start = LOCALTIMESTAMP WHERE key = %s", (key,))
    if fails >= RESET_REQ_MAX:
        return RESET_REQ_WINDOW_S - age
    c.execute("UPDATE rate_limits SET fails = %s, last_at = LOCALTIMESTAMP WHERE key = %s", (fails + 1, key))
    return 0


def _guard_password(conn, c, user_id, pw_hash, password):
    """Confere a SENHA ATUAL de quem já está logado (trocar/remover e-mail, excluir conta) com o mesmo
    limite de tentativas do login. Sem isso, quem tivesse a sessão de outra pessoa poderia chutar a
    senha à vontade por essas rotas, driblando o bloqueio do login.
    Retorna (True, None, 200) ou (False, mensagem, status). Já faz commit do contador."""
    key = f"pw:{user_id}"
    wait = _lock_wait_seconds(c, key)
    if wait > 0:   # bloqueado: nem confere a senha
        return False, f"Muitas tentativas erradas. Tente de novo em {_fmt_wait(wait)}.", 429
    if pw_hash and check_password_hash(pw_hash, password):
        _lock_clear(c, key)
        conn.commit()
        return True, None, 200
    minutes, left = _lock_register_fail(c, key)
    conn.commit()
    if minutes:
        return False, f"Senha incorreta. Muitas tentativas: tente de novo em {_fmt_wait(minutes * 60)}.", 429
    msg = "Senha incorreta."
    if left <= 2:
        msg += f" Restam {left} tentativa{'s' if left > 1 else ''} antes do bloqueio."
    return False, msg, 401


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "").strip()
    lock_key = _rl_key("login", username)
    pair_key = f"{lock_key}|{_client_ip()}"   # bloqueio por usuário+IP: quem erra não trava o dono da conta em outro lugar

    ip_wait = _window_take(f"ipl:{_client_ip()}", 120, 600)   # chutar vários usuários do mesmo IP
    if ip_wait > 0:
        return _too_many_json(ip_wait, "Muitas tentativas deste aparelho/rede")

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    wait = max(_lock_wait_seconds(c, lock_key), _lock_wait_seconds(c, pair_key))
    if wait > 0:   # bloqueado: nem confere a senha (senão dava pra continuar chutando)
        conn.close()
        return json.dumps({"success": False, "error": f"Muitas tentativas erradas. Tente de novo em {_fmt_wait(wait)}."}), 429

    c.execute("SELECT id, password_hash, profile_pic, avatar_url FROM users WHERE username = %s", (username,))
    user = c.fetchone()

    # conta só-Google não tem password_hash (antes isso dava erro 500)
    if user and user['password_hash'] and check_password_hash(user['password_hash'], password):
        _lock_clear(c, lock_key)
        _lock_clear(c, pair_key)
        session['user_id'] = user['id']
        session.permanent = True
        _bind_session_version(c, user['id'])

        guest_id = session.get('guest_session_id')
        if guest_id:
            c.execute("UPDATE saves SET user_id = %s WHERE guest_session_id = %s AND user_id IS NULL", (user['id'], guest_id))

        conn.commit()
        conn.close()
        # a foto já vai junto: o front guarda e pré-carrega antes de recarregar a página (sem esperar o /api/me)
        return json.dumps({"success": True, "profile_pic": user['profile_pic'] or user['avatar_url'] or None})

    minutes, left = _lock_register_fail(c, pair_key)   # conta o erro mesmo se o usuário não existir
    g_minutes, _ = _lock_register_fail(c, lock_key, LOGIN_GLOBAL_MAX_FAILS)
    minutes = max(minutes, g_minutes)
    conn.commit()
    conn.close()
    if minutes:
        return json.dumps({"success": False, "error": f"Usuário ou senha incorretos. Muitas tentativas: tente de novo em {_fmt_wait(minutes * 60)}."}), 429
    msg = "Usuário ou senha incorretos."
    if left <= 2:
        msg += f" Restam {left} tentativa{'s' if left > 1 else ''} antes do bloqueio."
    return json.dumps({"success": False, "error": msg}), 401


@app.route("/api/google_login", methods=["POST"])
def google_login():
    data = request.get_json()
    credential = data.get("credential", "")

    if not credential:
        return json.dumps({"success": False, "error": "Token ausente."}), 400

    if not GOOGLE_CLIENT_ID:
        return json.dumps({"success": False, "error": "Google login não configurado no servidor."}), 500

    try:
        # Valida o JWT com a API do Google — garante que o token é legítimo
        id_info = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID
        )
    except ValueError as e:
        print("Token do Google inválido:", e)
        return json.dumps({"success": False, "error": "Token do Google inválido ou expirado."}), 401

    google_id = id_info["sub"]
    email     = id_info.get("email", "")
    name      = id_info.get("name", "") or email.split("@")[0]
    avatar_url = id_info.get("picture", "")

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 1) Já tem conta vinculada ao google_id?
    c.execute("SELECT id FROM users WHERE google_id = %s", (google_id,))
    user = c.fetchone()

    if user:
        # Atualiza avatar sempre que logar (pode ter mudado no Google)
        c.execute("UPDATE users SET avatar_url = %s WHERE id = %s", (avatar_url, user["id"]))

    if not user:
        # Usuário novo — cria conta sem senha local.
        # SEGURANÇA: NÃO vincula a contas existentes pelo nome de exibição do
        # Google (qualquer um escolhe o próprio nome, isso permitia assumir
        # contas alheias). Garante username único se o nome já existir.
        base_name = name
        suffix = 1
        while True:
            c.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(%s)", (name,))
            if not c.fetchone():
                break
            name = f"{base_name}{suffix}"
            suffix += 1

        c.execute(
            "INSERT INTO users (username, password_hash, google_id, avatar_url) VALUES (%s, NULL, %s, %s) RETURNING id",
            (name, google_id, avatar_url)
        )
        user = c.fetchone()

    # Mesma lógica do /api/login: seta sessão e migra saves de convidado
    session['user_id'] = user['id']
    session.permanent = True
    _bind_session_version(c, user['id'])

    guest_id = session.get('guest_session_id')
    if guest_id:
        c.execute(
            "UPDATE saves SET user_id = %s WHERE guest_session_id = %s AND user_id IS NULL",
            (user['id'], guest_id)
        )

    conn.commit()
    conn.close()
    return json.dumps({"success": True})


GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI") or "https://sims4androidtranslator.vercel.app/api/google_callback"


GOOGLE_LOGIN_TOKEN_MINUTES = 15   # quanto tempo o token do retorno do Google vale (uso único, guardado só como hash)


def _hash_login_token(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


@app.route("/api/google_start")
def google_start():
    """Começa o login com Google: gera o 'state', guarda num cookie só deste navegador e manda pro Google."""
    if not GOOGLE_CLIENT_ID:
        return redirect("/?google_error=config")
    state = secrets.token_urlsafe(24)
    params = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "prompt": "select_account",
        "state": state,
    })
    resp = redirect("https://accounts.google.com/o/oauth2/v2/auth?" + params)
    resp.set_cookie("g_state", state, max_age=600, httponly=True, secure=True, samesite="Lax", path="/")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/google_callback")
def google_callback():
    """
    Recebe o ?code= do Google após o usuário escolher a conta no redirect flow.
    Troca o code por tokens, valida o id_token e faz login/cadastro.
    """
    code = request.args.get("code")
    error = request.args.get("error")

    # Confere o "state" (anti login-CSRF): tem que ser o mesmo que o /api/google_start deixou no cookie deste
    # navegador. Sem isso, alguém poderia mandar um link com o código da PRÓPRIA conta dele e fazer a vítima
    # entrar na conta do atacante. Desligável só em emergência: GOOGLE_OAUTH_STATE=off.
    if os.environ.get("GOOGLE_OAUTH_STATE", "on").lower() != "off":
        state = request.args.get("state", "")
        cookie_state = request.cookies.get("g_state", "")
        if not state or not cookie_state or not hmac.compare_digest(state, cookie_state):
            resp = redirect("/?google_error=state")
            resp.delete_cookie("g_state", path="/")
            return resp

    if error or not code:
        resp = redirect("/?google_error=cancelled")
        resp.delete_cookie("g_state", path="/")
        return resp

    if not GOOGLE_CLIENT_ID:
        return redirect("/?google_error=config")

    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
    redirect_uri = GOOGLE_REDIRECT_URI

    # Troca o authorization code por tokens
    try:
        token_resp = http_requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        token_data = token_resp.json()
    except Exception as e:
        return redirect(f"/?google_error=token_request")

    if "id_token" not in token_data:
        return redirect("/?google_error=no_id_token")

    # Valida o id_token (mesma lógica da rota /api/google_login)
    try:
        id_info = id_token.verify_oauth2_token(
            token_data["id_token"],
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except ValueError:
        return redirect("/?google_error=invalid_token")

    google_id  = id_info["sub"]
    email      = id_info.get("email", "")
    name       = id_info.get("name", "") or email.split("@")[0]
    avatar_url = id_info.get("picture", "")

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 1) Já tem conta vinculada ao google_id?
    c.execute("SELECT id FROM users WHERE google_id = %s", (google_id,))
    user = c.fetchone()

    if user:
        # Atualiza avatar sempre que logar (pode ter mudado no Google)
        c.execute("UPDATE users SET avatar_url = %s WHERE id = %s", (avatar_url, user["id"]))

    if not user:
        # Cria conta nova sem senha local.
        # SEGURANÇA: NÃO vincula a contas existentes pelo nome de exibição do
        # Google (ver /api/google_login).
        base_name = name
        suffix = 1
        while True:
            c.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(%s)", (name,))
            if not c.fetchone():
                break
            name = f"{base_name}{suffix}"
            suffix += 1

        c.execute(
            "INSERT INTO users (username, password_hash, google_id, avatar_url) VALUES (%s, NULL, %s, %s) RETURNING id",
            (name, google_id, avatar_url),
        )
        user = c.fetchone()

    # Gera token temporário para contornar bloqueio de cookie SameSite=Lax
    # em redirect cross-site (Google → seu site). Aleatório forte, vale GOOGLE_LOGIN_TOKEN_MINUTES, uso único,
    # e no banco fica só o hash (se o banco vazar, não dá pra usar o token).
    login_token = secrets.token_urlsafe(32)
    user_id = user["id"]

    guest_id = session.get("guest_session_id")
    if guest_id:
        c.execute(
            "UPDATE saves SET user_id = %s WHERE guest_session_id = %s AND user_id IS NULL",
            (user_id, guest_id),
        )

    c.execute(
        "UPDATE users SET login_token = %s, login_token_expires = LOCALTIMESTAMP + (%s || ' minutes')::interval WHERE id = %s",
        (_hash_login_token(login_token), str(GOOGLE_LOGIN_TOKEN_MINUTES), user_id))

    conn.commit()
    conn.close()
    resp = redirect(f"/?login_token={login_token}")
    resp.delete_cookie("g_state", path="/")
    return resp


@app.route("/api/google_token_login", methods=["POST"])
def google_token_login():
    """
    Consome o token temporário gerado pelo google_callback e seta a session.
    Chamado pelo JS da home logo após o redirect do Google.
    """
    data = request.get_json(silent=True) or {}
    token = str(data.get("token") or "").strip()
    if not token or len(token) > 200:
        return json.dumps({"success": False}), 400

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Confere e consome num passo só (uso único de verdade, sem brecha pra dois pedidos simultâneos).
    c.execute(
        "UPDATE users SET login_token = NULL, login_token_expires = NULL "
        "WHERE login_token = %s AND login_token_expires > LOCALTIMESTAMP RETURNING id, profile_pic, avatar_url",
        (_hash_login_token(token),))
    user = c.fetchone()
    if user:
        _bind_session_version(c, user["id"])
    conn.commit()
    conn.close()

    if not user:
        return json.dumps({"success": False, "error": "Token inválido ou expirado."}), 401

    session["user_id"] = user["id"]
    session.permanent = True
    return json.dumps({"success": True, "profile_pic": user["profile_pic"] or user["avatar_url"] or None})


@app.route("/api/forgot_password", methods=["POST"])
def forgot_password():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    question = str(data.get("security_question") or "").strip()
    answer = str(data.get("security_answer") or "").strip()
    new_password = str(data.get("new_password") or "").strip()

    if not username or not question or not answer or not new_password:
        return json.dumps({"success": False, "error": "Dados inválidos. Preencha todos os campos corretamente."}), 400
    pw_error = validate_new_password(new_password, username)   # antes de mexer em qualquer coisa: não gasta tentativa
    if pw_error:
        return json.dumps({"success": False, "error": pw_error}), 400

    lock_key = _rl_key("qa", username)
    pair_key = f"{lock_key}|{_client_ip()}"
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    wait = max(_lock_wait_seconds(c, lock_key), _lock_wait_seconds(c, pair_key))
    if wait > 0:
        conn.close()
        return json.dumps({"success": False, "error": f"Muitas tentativas erradas. Tente de novo em {_fmt_wait(wait)}."}), 429

    c.execute("SELECT id, security_question, security_answer_hash, email, (email_verified_at IS NOT NULL) AS has_email "
              "FROM users WHERE username = %s", (username,))
    user = c.fetchone()

    # conta só-Google não tem resposta de segurança (antes isso dava erro 500)
    answer_ok = bool(user and user['security_answer_hash'] and user['security_question'] == question
                     and check_password_hash(user['security_answer_hash'], answer.lower()))
    # A pergunta de segurança é a 2ª chance de quem perdeu o acesso ao e-mail confirmado: vale pra qualquer conta.
    if answer_ok:
        pass_hash = generate_password_hash(new_password)
        c.execute("UPDATE users SET password_hash = %s, session_version = session_version + 1 WHERE id = %s", (pass_hash, user['id']))
        _lock_clear_family(c, lock_key)
        _lock_clear_family(c, _rl_key("login", username))   # trocou a senha por aqui: libera o login também
        conn.commit()
        conn.close()
        _notify_password_changed(user["email"] if user["has_email"] else None)
        return json.dumps({"success": True})

    # Mesma resposta pra usuário inexistente e pra resposta errada (antes dava 404 "não encontrado" e revelava quem tem conta)
    minutes, left = _lock_register_fail(c, pair_key)
    g_minutes, _ = _lock_register_fail(c, lock_key, 15)   # soma de todos os IPs contra o mesmo usuário
    minutes = max(minutes, g_minutes)
    conn.commit()
    conn.close()
    if minutes:
        return json.dumps({"success": False, "error": f"Pergunta ou resposta de segurança incorreta. Muitas tentativas: tente de novo em {_fmt_wait(minutes * 60)}."}), 429
    msg = "Pergunta ou resposta de segurança incorreta."
    if left <= 2:
        msg += f" Restam {left} tentativa{'s' if left > 1 else ''} antes do bloqueio."
    return json.dumps({"success": False, "error": msg}), 401


# ─── E-MAIL DE RECUPERAÇÃO ──────────────────────────────────────────────────
# Códigos de 6 dígitos enviados por e-mail (Brevo). Só o HASH do código fica no
# banco; cada código vale 15 min, aceita 5 tentativas e só o mais novo conta.
# Envio (escolhe sozinho): se SMTP_USER e SMTP_PASSWORD existirem, usa SMTP (ex.: Gmail
# com "senha de app"); senão, usa o Brevo (BREVO_API_KEY + BREVO_SENDER_EMAIL).
#   SMTP:  SMTP_USER, SMTP_PASSWORD  (+ opcionais SMTP_HOST=smtp.gmail.com, SMTP_PORT=587,
#          SMTP_FROM, SMTP_SECURITY=starttls|ssl|none)
#   Nome que aparece como remetente (opcional): EMAIL_SENDER_NAME
EMAIL_CODE_TTL_MIN = 15
EMAIL_CODE_MAX_ATTEMPTS = 5
EMAIL_CODE_COOLDOWN_S = 60
EMAIL_CODE_MAX_PER_HOUR = 5
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def _normalize_email(raw):
    e = unicodedata.normalize("NFKC", str(raw or "")).strip().lower()
    if len(e) > 254 or not _EMAIL_RE.match(e):
        return None
    return e


def _mask_email(e):
    if not e or "@" not in e:
        return None
    local, domain = e.split("@", 1)
    return (local[:1] + "***@" + domain)


def _smtp_configured():
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def _brevo_configured():
    return bool(os.environ.get("BREVO_API_KEY") and os.environ.get("BREVO_SENDER_EMAIL"))


def _email_configured():
    return _smtp_configured() or _brevo_configured()


def _sender_name():
    return os.environ.get("EMAIL_SENDER_NAME") or os.environ.get("BREVO_SENDER_NAME") or "Sims 4 Translator"


def _send_email_smtp(to_email, subject, text, html, inline_logo=None):
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
        user = os.environ["SMTP_USER"]
        password = os.environ["SMTP_PASSWORD"].replace(" ", "")   # o Google mostra a senha de app com espaços
        sender = os.environ.get("SMTP_FROM") or user
        security = (os.environ.get("SMTP_SECURITY") or ("ssl" if port == 465 else "starttls" if port == 587 else "none")).lower()

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = formataddr((_sender_name(), sender))
        msg["To"] = to_email
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
        msg["Auto-Submitted"] = "auto-generated"
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        if inline_logo:   # ícone embutido no próprio e-mail (cid:logo), não depende de hospedagem
            msg.get_payload()[1].add_related(inline_logo, "image", "png", cid="<logo>")

        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=10, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=10)
        with server:
            server.ehlo()   # sem isso o smtplib não sabe o que o servidor oferece (inclusive o login)
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()   # depois do STARTTLS é obrigatório se apresentar de novo
            if server.has_extn("auth"):
                server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as e:   # nunca loga a senha: só o tipo do erro e a resposta do servidor
        print("Falha ao enviar e-mail (SMTP):", type(e).__name__, str(e)[:200])
        return False


def _send_email_brevo(to_email, subject, text, html):
    try:
        r = http_requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": os.environ["BREVO_API_KEY"], "accept": "application/json", "content-type": "application/json"},
            json={
                "sender": {"name": _sender_name(), "email": os.environ["BREVO_SENDER_EMAIL"]},
                "to": [{"email": to_email}],
                "subject": subject,
                "textContent": text,
                "htmlContent": html,
            },
            timeout=10,
        )
        if r.status_code not in (200, 201, 202):
            print("Brevo recusou o envio:", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        print("Falha ao enviar e-mail (Brevo):", e)
        return False


def _send_email(to_email, subject, text, html, inline_logo=None):
    """Envia por SMTP (se configurado) ou pelo Brevo. Retorna True/False (nunca levanta).
    inline_logo só vale no SMTP (no Brevo o HTML vai sem a imagem)."""
    if _smtp_configured():
        return _send_email_smtp(to_email, subject, text, html, inline_logo)
    if _brevo_configured():
        return _send_email_brevo(to_email, subject, text, html)
    return False


SITE_URL_DEFAULT = "https://sims4androidtranslator.vercel.app"


def _notify_password_changed(to_email):
    """Avisa por e-mail que a senha foi redefinida (se não foi a pessoa, ela percebe na hora). Nunca levanta."""
    try:
        if not to_email or not _email_configured():
            return
        name = _sender_name()
        text = (f"{name}\n\nA senha da sua conta foi redefinida agora há pouco.\n"
                "Se foi você, está tudo certo e não precisa fazer nada.\n"
                "Se NÃO foi você, alguém pode ter acesso à sua conta: entre pelo e-mail de recuperação "
                "para trocar a senha de novo e remova o acesso de outras pessoas.\n")
        h = _html.escape
        html = (f'<div style="font-family:Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:460px;margin:0 auto;padding:24px;color:#2a1f3d">'
                f'<h2 style="margin:0 0 12px">{h(name)}</h2>'
                f'<p style="font-size:15px;line-height:1.5">A senha da sua conta foi <b>redefinida</b> agora há pouco.</p>'
                f'<p style="font-size:14px;line-height:1.5">Se foi você, está tudo certo e não precisa fazer nada.</p>'
                f'<p style="font-size:14px;line-height:1.5">Se <b>não</b> foi você, alguém pode ter acesso à sua conta: '
                f'use a recuperação por e-mail para trocar a senha de novo.</p></div>')
        _send_email(to_email, "Sua senha foi redefinida", text, html)
    except Exception as e:
        print("Aviso: não consegui enviar o aviso de troca de senha:", type(e).__name__)


def _send_code_email(to_email, code, purpose):
    site_url = (os.environ.get("SITE_URL") or (request.host_url if has_request_context() else SITE_URL_DEFAULT)).rstrip("/")
    use_logo = _smtp_configured()   # o ícone vai embutido (cid) no SMTP; no Brevo o e-mail sai sem imagem
    text, html = render_code_email(code, purpose, EMAIL_CODE_TTL_MIN, _sender_name(), site_url,
                                   logo_src="cid:logo" if use_logo else None)
    return _send_email(to_email, f"Seu código: {code}", text, html, inline_logo=ICON_PNG if use_logo else None)


def _hash_code(purpose, user_id, code):
    msg = f"{purpose}:{user_id}:{code}".encode()
    return hmac.new((app.secret_key or "").encode(), msg, hashlib.sha256).hexdigest()


def _issue_code(c, user_id, purpose, email, check_limits=True):
    """Cria um código novo (SEM commit). Retorna (code, None) ou (None, mensagem) se passou do limite.
    check_limits=False no 'reset': lá o limite é o de 3 pedidos por 2 dias (_reset_req_take)."""
    c.execute("DELETE FROM email_codes WHERE created_at < LOCALTIMESTAMP - INTERVAL '1 day'")
    if check_limits:
        c.execute(
            "SELECT COUNT(*) AS n, EXTRACT(EPOCH FROM (LOCALTIMESTAMP - MAX(created_at))) AS since "
            "FROM email_codes WHERE user_id = %s AND purpose = %s AND created_at > LOCALTIMESTAMP - INTERVAL '1 hour'",
            (user_id, purpose))
        r = c.fetchone()
        if r["n"] >= EMAIL_CODE_MAX_PER_HOUR:
            return None, "Muitos códigos pedidos. Tente de novo mais tarde."
        if r["n"] and r["since"] is not None and float(r["since"]) < EMAIL_CODE_COOLDOWN_S:
            return None, "Espere um minutinho antes de pedir outro código."
    code = f"{secrets.randbelow(10**6):06d}"
    c.execute(
        "INSERT INTO email_codes (user_id, purpose, email, code_hash, expires_at) "
        "VALUES (%s, %s, %s, %s, LOCALTIMESTAMP + (%s || ' minutes')::interval)",
        (user_id, purpose, email, _hash_code(purpose, user_id, code), str(EMAIL_CODE_TTL_MIN)))
    return code, None


def _check_code(c, user_id, purpose, code):
    """Confere o código mais novo. Retorna (linha, None) ou (None, mensagem). Quem chama dá commit (conta tentativas)."""
    c.execute(
        "SELECT id, email, code_hash, attempts FROM email_codes "
        "WHERE user_id = %s AND purpose = %s AND expires_at > LOCALTIMESTAMP ORDER BY id DESC LIMIT 1",
        (user_id, purpose))
    row = c.fetchone()
    if not row:
        return None, "Código expirado ou inexistente. Peça um novo."
    if row["attempts"] >= EMAIL_CODE_MAX_ATTEMPTS:
        return None, "Muitas tentativas erradas. Peça um novo código."
    if not hmac.compare_digest(row["code_hash"], _hash_code(purpose, user_id, code)):
        c.execute("UPDATE email_codes SET attempts = attempts + 1 WHERE id = %s", (row["id"],))
        return None, "Código incorreto."
    return row, None


RESET_REQUEST_MIN_SECONDS = 4.0   # tempo mínimo de resposta do pedido de código (mascara quem tem e-mail)


def _pad_response_time(start, min_seconds=RESET_REQUEST_MIN_SECONDS):
    """Espera até completar min_seconds desde `start`. Assim a resposta demora o mesmo
    tempo exista ou não a conta/o e-mail (senão dava pra descobrir pelo tempo do envio)."""
    left = min_seconds - (time.monotonic() - start)
    if left > 0:
        time.sleep(left)


def _only_digits(raw):
    return re.sub(r"\D", "", str(raw or ""))


@app.route("/api/email/request", methods=["POST"])
def email_request():
    """Logado: cadastra/troca o e-mail de recuperação. Exige a senha atual (senão quem roubar a sessão trocaria o e-mail)."""
    user_id = session.get("user_id")
    if not user_id:
        return json.dumps({"success": False, "error": "Não logado"}), 401
    data = request.get_json(silent=True) or {}
    email = _normalize_email(data.get("email"))
    password = str(data.get("password") or "")
    if not email:
        return json.dumps({"success": False, "error": "E-mail inválido."}), 400
    if not _email_configured():
        return json.dumps({"success": False, "error": "Envio de e-mail não está configurado no servidor."}), 503

    conn = get_db()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
        u = c.fetchone()
        if not u or not u["password_hash"]:
            return json.dumps({"success": False, "error": "Só contas com senha usam e-mail de recuperação."}), 400
        ok, err, status = _guard_password(conn, c, user_id, u["password_hash"], password)
        if not ok:
            return json.dumps({"success": False, "error": err}), status
        c.execute("SELECT id FROM users WHERE LOWER(email) = %s AND email_verified_at IS NOT NULL AND id <> %s", (email, user_id))
        if c.fetchone():
            return json.dumps({"success": False, "error": "Esse e-mail já está em uso em outra conta."}), 400
        code, err = _issue_code(c, user_id, "verify", email)
        if err:
            return json.dumps({"success": False, "error": err}), 429
        if not _send_code_email(email, code, "verify"):
            conn.rollback()
            return json.dumps({"success": False, "error": "Não consegui enviar o e-mail agora. Tenta de novo daqui a pouco."}), 502
        conn.commit()
        return json.dumps({"success": True})
    finally:
        conn.close()


@app.route("/api/email/confirm", methods=["POST"])
def email_confirm():
    user_id = session.get("user_id")
    if not user_id:
        return json.dumps({"success": False, "error": "Não logado"}), 401
    code = _only_digits((request.get_json(silent=True) or {}).get("code"))
    if len(code) != 6:
        return json.dumps({"success": False, "error": "O código tem 6 dígitos."}), 400

    conn = get_db()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        row, err = _check_code(c, user_id, "verify", code)
        if err:
            conn.commit()   # grava a tentativa errada
            return json.dumps({"success": False, "error": err}), 400
        try:
            c.execute("UPDATE users SET email = %s, email_verified_at = LOCALTIMESTAMP WHERE id = %s", (row["email"], user_id))
        except psycopg2.IntegrityError:
            conn.rollback()
            return json.dumps({"success": False, "error": "Esse e-mail já está em uso em outra conta."}), 400
        c.execute("DELETE FROM email_codes WHERE user_id = %s AND purpose = 'verify'", (user_id,))
        conn.commit()
        return json.dumps({"success": True, "email": _mask_email(row["email"])})
    finally:
        conn.close()


@app.route("/api/email/remove", methods=["POST"])
def email_remove():
    user_id = session.get("user_id")
    if not user_id:
        return json.dumps({"success": False, "error": "Não logado"}), 401
    password = str((request.get_json(silent=True) or {}).get("password") or "")
    conn = get_db()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
        u = c.fetchone()
        if not u or not u["password_hash"]:
            return json.dumps({"success": False, "error": "Senha incorreta."}), 401
        ok, err, status = _guard_password(conn, c, user_id, u["password_hash"], password)
        if not ok:
            return json.dumps({"success": False, "error": err}), status
        c.execute("UPDATE users SET email = NULL, email_verified_at = NULL WHERE id = %s", (user_id,))
        c.execute("DELETE FROM email_codes WHERE user_id = %s", (user_id,))
        conn.commit()
        return json.dumps({"success": True})
    finally:
        conn.close()


@app.route("/api/forgot_password/email_request", methods=["POST"])
def forgot_email_request():
    """Pede o código de redefinição. A resposta é SEMPRE a mesma (exista ou não a conta / o e-mail), pra não revelar quem tem conta."""
    start = time.monotonic()
    username = str((request.get_json(silent=True) or {}).get("username") or "").strip()
    if not username:
        return json.dumps({"success": False, "error": "Informe o usuário."}), 400
    if not _email_configured():
        return json.dumps({"success": False, "error": "Recuperação por e-mail não está disponível no momento."}), 503
    generic = json.dumps({"success": True, "message": "Se esse usuário tiver um e-mail confirmado, enviamos um código."})

    conn = get_db()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Limite: 3 pedidos a cada 2 dias POR NOME DIGITADO (exista a conta ou não), então
        # a mensagem de "limite atingido" é a mesma pra todo mundo e não revela nada.
        wait = _reset_req_take(c, _rl_key("reset_req", username))
        conn.commit()   # o pedido conta mesmo que o envio falhe
        if wait > 0:
            return json.dumps({"success": False, "error": f"Você já pediu {RESET_REQ_MAX} códigos. Tente de novo em {_fmt_wait(wait)}. (A pergunta de segurança continua disponível.)"}), 429
        c.execute("SELECT id, email FROM users WHERE username = %s AND password_hash IS NOT NULL AND email_verified_at IS NOT NULL", (username,))
        u = c.fetchone()
        if u:
            code, err = _issue_code(c, u["id"], "reset", u["email"], check_limits=False)
            if code and _send_code_email(u["email"], code, "reset"):
                conn.commit()
            else:
                conn.rollback()
    finally:
        conn.close()
    _pad_response_time(start)   # mesma duração com ou sem e-mail cadastrado
    return generic


@app.route("/api/forgot_password/email_confirm", methods=["POST"])
def forgot_email_confirm():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    code = _only_digits(data.get("code"))
    new_password = str(data.get("new_password") or "").strip()
    if not username or len(code) != 6 or not new_password:
        return json.dumps({"success": False, "error": "Preencha o usuário, o código de 6 dígitos e a nova senha."}), 400
    pw_error = validate_new_password(new_password, username)
    if pw_error:
        return json.dumps({"success": False, "error": pw_error}), 400

    # Mensagem ÚNICA pra qualquer falha: usuário inexistente, sem e-mail, sem código,
    # código errado ou travado. Mensagens diferentes revelariam quem tem e-mail cadastrado.
    bad = json.dumps({"success": False, "error": "Código incorreto ou expirado. Se errou várias vezes, peça um novo código."}), 400
    conn = get_db()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT id, email FROM users WHERE username = %s AND password_hash IS NOT NULL AND email_verified_at IS NOT NULL", (username,))
        u = c.fetchone()
        if not u:
            return bad
        row, err = _check_code(c, u["id"], "reset", code)
        if err:
            conn.commit()   # grava a tentativa errada
            return bad
        c.execute("UPDATE users SET password_hash = %s, session_version = session_version + 1 WHERE id = %s", (generate_password_hash(new_password), u["id"]))
        c.execute("DELETE FROM email_codes WHERE user_id = %s AND purpose = 'reset'", (u["id"],))
        _lock_clear_family(c, _rl_key("login", username))   # trocou a senha por aqui: libera o login também
        conn.commit()
        _notify_password_changed(u["email"])
        return json.dumps({"success": True})
    finally:
        conn.close()


NICK_MIN, NICK_MAX = 2, 8
# letras/números (qualquer idioma), com espaço ou hífen SÓ entre palavras
_NICK_RE = re.compile(r"[^\W_]+(?:[ \-][^\W_]+)*")


def clean_nickname(raw):
    """Normaliza e valida o apelido. Retorna (apelido, None) ou (None, erro)."""
    nick = unicodedata.normalize("NFC", str(raw or ""))
    nick = re.sub(r"\s+", " ", nick).strip()
    if len(nick) < NICK_MIN:
        return None, f"Apelido muito curto (mínimo {NICK_MIN} caracteres)."
    if len(nick) > NICK_MAX:
        return None, f"Apelido muito longo (máximo {NICK_MAX} caracteres)."
    if not _NICK_RE.fullmatch(nick):
        return None, "Use só letras, números, espaço e hífen."
    return nick, None


@app.route("/api/me")
def me():
    user_id = session.get('user_id')
    if not user_id:
        return json.dumps({"logged_in": False})

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT username, profile_pic, avatar_url FROM users WHERE id = %s", (user_id,))
    user = c.fetchone()

    # Busca o apelido à parte: se a coluna ainda não existir, o login não quebra.
    nickname = None
    if user:
        try:
            c.execute("SELECT nickname FROM users WHERE id = %s", (user_id,))
            row = c.fetchone()
            nickname = row["nickname"] if row else None
        except Exception:
            conn.rollback()
        # à parte (como o apelido): conta com senha? exclusão agendada? Se a coluna não existir, o login não quebra.
        has_password, deletion_days_left = True, None
        email_masked = None
        try:
            c.execute(
                "SELECT (password_hash IS NOT NULL) AS has_pw, deletion_requested_at, "
                "CEIL(EXTRACT(EPOCH FROM (deletion_requested_at + (%s || ' days')::interval - LOCALTIMESTAMP)) / 86400) AS days_left "
                "FROM users WHERE id = %s", (ACCOUNT_DELETION_GRACE_DAYS, user_id))
            r = c.fetchone()
            if r:
                has_password = bool(r["has_pw"])
                if r["deletion_requested_at"]:
                    deletion_days_left = max(1, int(r["days_left"] or 1))
        except Exception:
            conn.rollback()
        try:
            c.execute("SELECT email FROM users WHERE id = %s AND email_verified_at IS NOT NULL", (user_id,))
            r = c.fetchone()
            email_masked = _mask_email(r["email"]) if r else None
        except Exception:
            conn.rollback()
    conn.close()

    if user:
        return json.dumps({
            "logged_in": True,
            "username": user["username"],
            "profile_pic": user["profile_pic"] or user["avatar_url"] or None,
            "nickname": nickname,
            "has_password": has_password,
            "email": email_masked,
            "deletion_days_left": deletion_days_left
        })
    return json.dumps({"logged_in": False})


# ─── CSS PERSONALIZADO ──────────────────────────────────────────────────────
CSS_MAX = 50000
CSS_MAX_IMPORTS = 5
_CSS_BLOCKED = re.compile(
    r"@import|javascript\s*:|vbscript\s*:|expression\s*\(|(?<![\w-])behavior\s*:|-moz-binding"
    r"|cross-fade\s*\(|</style|<script"
    r"|(?<![\w-])(?:src|image|element|paint)\s*\("
)
_CSS_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)"'\s]*))\s*\)""")

# @import: só do Google Fonts (CSS de lá só traz @font-face). Qualquer outro @import continua bloqueado,
# porque o CSS importado NÃO passaria por esta validação.
_GF_URL = r"https://fonts\.googleapis\.com/css2?\?[^\"'()\s\\<>]+"
_CSS_IMPORT_OK = re.compile(
    r"@import\s*(?:url\(\s*(?:\"" + _GF_URL + r"\"|'" + _GF_URL + r"'|" + _GF_URL + r")\s*\)"
    r"|\"" + _GF_URL + r"\"|'" + _GF_URL + r"')\s*;"
)


def _css_for_check(css):
    """Tira comentários e decodifica escapes (\\75 rl -> url) pra checar as regras sem drible."""
    text = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    def _hex(m):
        try:
            return chr(int(m.group(1), 16))
        except (ValueError, OverflowError):
            return ""

    text = re.sub(r"\\([0-9a-fA-F]{1,6})[ \t\r\n\f]?", _hex, text)
    text = re.sub(r"\\(.)", r"\1", text, flags=re.S)
    return text.lower()


def _image_set_error(check):
    """image-set() só com url(...). Texto solto entre aspas aceitaria qualquer endereço sem passar pela
    checagem de url(), então fica de fora. Retorna uma mensagem de erro ou None."""
    masked = _CSS_URL.sub("url(x)", check)
    masked = re.sub(r"""type\(\s*(?:"[^"]*"|'[^']*')\s*\)""", "type(x)", masked)
    for m in re.finditer(r"image-set\s*\(", masked):
        depth, j = 1, m.end()
        while j < len(masked) and depth:
            if masked[j] == "(":
                depth += 1
            elif masked[j] == ")":
                depth -= 1
            j += 1
        inner = masked[m.end():j]
        if depth or '"' in inner or "'" in inner:
            return "Dentro de image-set(...) use só url(...): endereço solto entre aspas não vale."
    return None


def sanitize_user_css(raw):
    """Valida o CSS pessoal. Retorna (css, None) ou (None, mensagem de erro)."""
    css = str(raw or "").strip()
    if not css:
        return "", None
    if len(css) > CSS_MAX:
        return None, f"CSS muito grande (máximo {CSS_MAX} caracteres)."
    if "\x00" in css:
        return None, "CSS com caractere inválido."

    check = _css_for_check(css)

    # @import do Google Fonts: tem que vir no começo (o navegador ignora @import depois de qualquer regra).
    imports = list(_CSS_IMPORT_OK.finditer(check))
    if len(imports) > CSS_MAX_IMPORTS:
        return None, f"Muitos @import (máximo {CSS_MAX_IMPORTS})."
    rest = check.lstrip()
    while True:
        m = _CSS_IMPORT_OK.match(rest)
        if not m:
            break
        rest = rest[m.end():].lstrip()
    if _CSS_IMPORT_OK.search(rest):
        return None, "O @import precisa ficar no começo do CSS, antes de qualquer outra regra."
    check = _CSS_IMPORT_OK.sub(" ", check)      # o que sobrar de @import (outro endereço) cai no bloqueio abaixo

    bad = _CSS_BLOCKED.search(check)
    if bad:
        token = bad.group(0).strip()[:30]
        if token == "@import":
            return None, "@import só vale pro Google Fonts (https://fonts.googleapis.com/css2?family=…;), com ponto e vírgula no fim."
        return None, f"Esse CSS usa algo que não é permitido: {token}"

    err = _image_set_error(check)
    if err:
        return None, err

    urls = list(_CSS_URL.finditer(check))
    if len(urls) != check.count("url("):
        return None, "Não consegui ler um url(...) no seu CSS. Confira as aspas e parênteses."
    for m in urls:
        target = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if target and not (target.startswith("https://") or target.startswith("data:image/") or target.startswith("/img/")):
            return None, "Em url(...) só são permitidos links https:// e imagens embutidas (data:image/)."

    return css, None


THEMES_DIR = os.path.join(TEMPLATE_DIR, "themes")


def load_themes():
    """Lê templates/themes/themes.json + <id>.css e devolve {id: {nome, descrição, cores, fontes, css}}."""
    try:
        with open(os.path.join(THEMES_DIR, "themes.json"), encoding="utf-8") as f:
            metas = json.load(f)
    except Exception as e:
        print("Temas indisponíveis:", e)
        return {}
    themes = {}
    for meta in metas:
        tid = str(meta.get("id", ""))
        if not re.fullmatch(r"[a-z0-9_-]{1,32}", tid):
            continue
        try:
            with open(os.path.join(THEMES_DIR, tid + ".css"), encoding="utf-8") as f:
                css = f.read()
        except Exception as e:
            print(f"Tema {tid} sem arquivo .css:", e)
            continue
        themes[tid] = {
            "name": meta.get("name", tid),
            "blurb": meta.get("blurb", ""),
            "swatch": meta.get("swatch", []),
            "nameFont": meta.get("nameFont", ""),
            "fonts": meta.get("fonts", ""),
            "css": css,
        }
    return themes


@app.route("/theme.js")
def theme_js():
    with open(os.path.join(TEMPLATE_DIR, "theme.js"), encoding="utf-8") as f:
        base = f.read()
    body = "window.__THEMES__=" + json.dumps(load_themes()) + ";\n" + base
    response = app.response_class(body, mimetype="application/javascript")
    response.set_etag(hashlib.md5(body.encode("utf-8")).hexdigest())
    response.headers["Cache-Control"] = "no-cache"
    return response.make_conditional(request)


@app.route("/api/my_css")
def my_css():
    user_id = session.get('user_id')
    payload = {"logged_in": False, "css": ""}
    if user_id:
        conn = get_db()
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)   # dict por nome de coluna (row["custom_css"]); com cursor comum dava TypeError e a rota respondia "não logado"
        css = ""
        try:
            c.execute("SELECT custom_css FROM users WHERE id = %s", (user_id,))
            row = c.fetchone()
            css = (row["custom_css"] if row else None) or ""
            payload = {"logged_in": bool(row), "css": css, "theme": None}
        except Exception:
            conn.rollback()
        if payload["logged_in"]:
            try:   # à parte: se a coluna ainda não existir, o CSS continua funcionando
                c.execute("SELECT theme FROM users WHERE id = %s", (user_id,))
                trow = c.fetchone()
                payload["theme"] = (trow["theme"] if trow else None) or None
            except Exception:
                conn.rollback()
        conn.close()
    response = app.response_class(json.dumps(payload), mimetype="application/json")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/save_theme", methods=["POST"])
def save_theme():
    user_id = session.get('user_id')
    if not user_id:
        print("DIAG save_theme 401: cookie 'session' enviado =", 'session' in request.cookies,
              "| chaves na sessão =", sorted(session.keys()))
        return json.dumps({"success": False, "error": "Não logado"}), 401

    data = request.get_json(silent=True) or {}
    theme = str(data.get("theme") or "").strip()
    if theme != "default" and theme not in load_themes():
        return json.dumps({"success": False, "error": "Tema desconhecido."}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET theme = %s WHERE id = %s", (theme, user_id))
    conn.commit()
    conn.close()
    return json.dumps({"success": True, "theme": theme})


@app.route("/api/save_css", methods=["POST"])
def save_css():
    user_id = session.get('user_id')
    if not user_id:
        return json.dumps({"success": False, "error": "Não logado"}), 401

    data = request.get_json(silent=True) or {}
    css, error = sanitize_user_css(data.get("css"))
    if error:
        return json.dumps({"success": False, "error": error}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET custom_css = %s WHERE id = %s", (css or None, user_id))
    conn.commit()
    conn.close()
    return json.dumps({"success": True, "css": css})


@app.route("/api/update_nickname", methods=["POST"])
def update_nickname():
    user_id = session.get('user_id')
    if not user_id:
        return json.dumps({"success": False, "error": "Não logado"}), 401

    data = request.get_json(silent=True) or {}
    nickname, error = clean_nickname(data.get("nickname"))
    if error:
        return json.dumps({"success": False, "error": error}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET nickname = %s WHERE id = %s", (nickname, user_id))
    conn.commit()
    conn.close()
    return json.dumps({"success": True, "nickname": nickname})


@app.route("/api/update_profile", methods=["POST"])
def update_profile():
    user_id = session.get('user_id')
    if not user_id:
        return json.dumps({"success": False, "error": "Não logado"}), 401

    data = request.get_json(silent=True) or {}
    new_username = str(data.get("username") or "").strip()
    new_pic = str(data.get("profile_pic") or "").strip()

    conn = get_db()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT username, profile_pic FROM users WHERE id = %s", (user_id,))
        cur = c.fetchone()
        if not cur:
            return json.dumps({"success": False, "error": "Não logado"}), 401

        if not new_username or new_username == cur["username"]:
            new_username = cur["username"]          # vazio/igual: mantém (antes vazio gravava um nome em branco)
        else:
            new_username, name_error = clean_username(new_username)
            if name_error:
                return json.dumps({"success": False, "error": name_error}), 400
            c.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(%s) AND id != %s", (new_username, user_id))
            if c.fetchone():
                return json.dumps({"success": False, "error": "Nome de usuário já está em uso."}), 400

        if new_pic != (cur["profile_pic"] or "") and not _valid_profile_pic(new_pic):
            return json.dumps({"success": False, "error": "Imagem de perfil inválida ou grande demais."}), 400

        try:
            c.execute("UPDATE users SET username = %s, profile_pic = %s WHERE id = %s", (new_username, new_pic, user_id))
            conn.commit()
        except psycopg2.IntegrityError:
            conn.rollback()
            return json.dumps({"success": False, "error": "Nome de usuário já está em uso."}), 400
    finally:
        conn.close()
    return json.dumps({"success": True})


@app.route("/api/import_translations", methods=["POST"])
def import_translations():
    data = request.get_json()
    save_id = data.get("save_id")
    user_id = session.get('user_id')
    guest_id = session.get('guest_session_id')

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if user_id:
        c.execute("SELECT translation_data FROM saves WHERE id = %s AND user_id = %s", (save_id, user_id))
    else:
        c.execute("SELECT translation_data FROM saves WHERE id = %s AND guest_session_id = %s", (save_id, guest_id))

    row = c.fetchone()
    conn.close()

    if not row:
        return json.dumps({"success": False, "error": "Save não encontrado"}), 404

    strings = json.loads(row["translation_data"])
    translation_map = {str(s["key"]): s["value"] for s in strings if s.get("value")}

    return json.dumps({"success": True, "translations": translation_map})


# ─── EXCLUSÃO DE CONTA ────────────────────────────────────────
ACCOUNT_DELETION_GRACE_DAYS = 7      # prazo pra recuperar; muda só aqui (o texto do site lê esse valor)
DELETE_WORD_SCHEDULED = "BANANA"     # exclusão com recuperação
DELETE_WORD_NOW = "EXCLUIR"          # exclusão permanente


def purge_user(conn, user_id):
    """Apaga TUDO de uma conta: projetos, inscrições de notificação, arquivos do Storage e o próprio usuário.
    (As chaves estrangeiras não têm CASCADE, então a ordem importa.) Faz commit no fim; quem chama trata o erro."""
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute(
        "SELECT file_id AS fid FROM package_files WHERE user_id = %s "
        "UNION SELECT package_id AS fid FROM saves WHERE user_id = %s "
        "AND package_id IS NOT NULL AND package_id NOT IN ('', 'DATABASE_SAVE')", (user_id, user_id))
    file_ids = [r["fid"] for r in c.fetchall()]

    c.execute("DELETE FROM saves WHERE user_id = %s", (user_id,))
    c.execute("DELETE FROM push_subscriptions WHERE user_id = %s", (user_id,))

    for fid in file_ids:
        c.execute("SELECT COUNT(*) AS n FROM saves WHERE package_id = %s", (fid,))
        if c.fetchone()["n"] > 0:                      # outro save ainda usa o arquivo: só solta do usuário
            c.execute("UPDATE package_files SET user_id = NULL WHERE file_id = %s", (fid,))
            continue
        try:
            supabase.storage.from_(BUCKET_NAME).remove([fid])
            c.execute("DELETE FROM package_files WHERE file_id = %s", (fid,))
        except Exception as e:                         # falhou agora? vira órfão e o cron remove depois
            print(f"Aviso: falha ao remover {fid} do Storage:", e)
            c.execute("UPDATE package_files SET user_id = NULL, orphaned_at = CURRENT_TIMESTAMP WHERE file_id = %s", (fid,))

    c.execute("DELETE FROM users WHERE id = %s", (user_id,))
    conn.commit()
    return len(file_ids)


def purge_expired_accounts():
    """Chamada pelo cron diário: apaga de vez as contas cujo prazo de recuperação acabou."""
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute(
        "SELECT id FROM users WHERE deletion_requested_at IS NOT NULL "
        "AND deletion_requested_at < LOCALTIMESTAMP - (%s || ' days')::interval", (ACCOUNT_DELETION_GRACE_DAYS,))
    ids = [r["id"] for r in c.fetchall()]
    deleted, failed = 0, 0
    for uid in ids:
        try:
            purge_user(conn, uid)
            deleted += 1
        except Exception as e:
            conn.rollback()
            failed += 1
            print(f"Aviso: falha ao excluir a conta {uid}:", e)
    conn.close()
    return {"deleted": deleted, "failed": failed}


@app.route("/api/account/delete", methods=["POST"])
def account_delete():
    user_id = session.get("user_id")
    if not user_id:
        return json.dumps({"success": False, "error": "Não logado"}), 401

    data = request.get_json(silent=True) or {}
    permanent = bool(data.get("permanent"))
    expected = DELETE_WORD_NOW if permanent else DELETE_WORD_SCHEDULED
    if str(data.get("word") or "").strip().upper() != expected:
        return json.dumps({"success": False, "error": f"Digite {expected} pra confirmar."}), 400

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT username, password_hash FROM users WHERE id = %s", (user_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return json.dumps({"success": False, "error": "Não logado"}), 401

    if row["password_hash"]:                            # conta com senha: palavra + senha
        ok, err, status = _guard_password(conn, c, user_id, row["password_hash"], str(data.get("password") or ""))
        if not ok:
            conn.close()
            return json.dumps({"success": False, "error": err}), status
    elif str(data.get("username") or "").strip() != row["username"]:   # conta Google: palavra + nome de usuário
        conn.close()
        return json.dumps({"success": False, "error": "Digite seu nome de usuário exatamente como ele aparece."}), 400

    try:
        if permanent:
            purge_user(conn, user_id)
        else:
            c.execute("UPDATE users SET deletion_requested_at = LOCALTIMESTAMP WHERE id = %s", (user_id,))
            conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        print("Erro ao excluir conta:", e)
        return json.dumps({"success": False, "error": "Não deu pra excluir agora. Tenta de novo daqui a pouco."}), 500
    conn.close()

    session.clear()
    return json.dumps({"success": True, "permanent": permanent, "days": ACCOUNT_DELETION_GRACE_DAYS})


@app.route("/api/account/restore", methods=["POST"])
def account_restore():
    user_id = session.get("user_id")
    if not user_id:
        return json.dumps({"success": False, "error": "Não logado"}), 401
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET deletion_requested_at = NULL WHERE id = %s", (user_id,))
    conn.commit()
    conn.close()
    return json.dumps({"success": True})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return json.dumps({"success": True})


@app.route("/api/import_from_package", methods=["POST"])
def import_from_package():
    if "package" not in request.files:
        return json.dumps({"success": False, "error": "Nenhum arquivo enviado"}), 400

    f = request.files["package"]
    if not f.filename.endswith(".package"):
        return json.dumps({"success": False, "error": "Arquivo inválido"}), 400

    # Idioma de destino do projeto atual (ex: "POR_BR"), enviado pelo front-end.
    # Sem isso, um .package com várias tabelas de string (uma por idioma) faz
    # a última tabela processada sobrescrever as anteriores na hora de montar
    # o dicionário de traduções — e se essa última for a tabela em inglês (ou
    # outro idioma que não o de destino), a "tradução" aplicada acaba sendo
    # só o texto original, sem avisar ninguém (a contagem bate igual, porque
    # as chaves são as mesmas em todas as tabelas de idioma).
    target_lang = (request.form.get("target_lang") or "").strip().upper()

    strings = {}
    strings_by_lang = {}  # lang -> {key: value}, só usado se target_lang não vier/não bater
    with tempfile.NamedTemporaryFile(delete=False, suffix=".package") as tmp:
        f.save(tmp.name)
        tmp_path = tmp.name

    try:
        with DbpfPackage.read(tmp_path) as pkg:
            for rid in pkg.search_stbl():
                stbl = load_stbl(pkg, rid)
                lang = rid.language  # ex: "POR_BR", "ENG_US", ou None se não reconhecido
                bucket = strings_by_lang.setdefault(lang, {})
                for key, value in stbl._strings.items():
                    if value:
                        bucket[str(key)] = value
    finally:
        os.remove(tmp_path)

    if target_lang and target_lang in strings_by_lang:
        # Achou a tabela exata do idioma de destino do projeto: usa só ela.
        strings = strings_by_lang[target_lang]
    elif len(strings_by_lang) == 1:
        # Só tinha uma tabela de string no pacote (comum em packages simples
        # já filtrados por idioma) — usa ela mesmo sem confirmação de idioma.
        strings = next(iter(strings_by_lang.values()))
    elif target_lang:
        # Tinha várias tabelas de idioma, mas nenhuma bate com o idioma de
        # destino do projeto. Evita misturar idiomas silenciosamente.
        found_langs = sorted(l for l in strings_by_lang if l)
        return json.dumps({
            "success": False,
            "error": "O arquivo não tem uma tabela de strings para " + target_lang +
                      (". Idiomas encontrados no arquivo: " + ", ".join(found_langs) if found_langs else ".")
        }), 400
    else:
        # Não recebemos target_lang do front-end (chamada antiga/sem esse
        # campo) — mantém o comportamento anterior como último recurso.
        for bucket in strings_by_lang.values():
            strings.update(bucket)

    return json.dumps({"success": True, "translations": strings})


@app.route("/merge")
def merge_page():
    return send_page("merge.html")


# Marca reservada exclusiva do app pra identificar merges "com memória":
# type=0x53344D46 soletra "S4MF" em ASCII — extremamente improvável de colidir
# com um resource type real do jogo. group/instance são constantes fixas.
MERGE_MANIFEST_TYPE = 0x53344D46
MERGE_MANIFEST_GROUP = 0x00000000
MERGE_MANIFEST_INSTANCE = 0x0000000000000001


@app.route("/api/merge", methods=["POST"])
def api_merge():
    from packer.resource import ResourceID

    files = request.files.getlist("packages")
    mode = request.form.get("mode", "simple")  # "simple" ou "smart"
    if not files or len(files) < 2:
        return json.dumps({"error": "Envie pelo menos 2 arquivos .package"}), 400

    for f in files:
        if not f.filename.endswith(".package"):
            return json.dumps({"error": f"Arquivo inválido: {f.filename}"}), 400

    tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix=".package")
    tmp_out_path = tmp_out.name
    tmp_out.close()

    tmp_inputs = []
    try:
        # No modo "smart", guardamos de qual arquivo de origem cada resource
        # veio — é o que permite separar o merge de volta depois, com certeza
        # absoluta (ao contrário do palpite por Group ID usado no /api/unmerge
        # para arquivos que não têm essa marca).
        manifest_resources = []
        source_names = [f.filename for f in files]

        with DbpfPackage.write(tmp_out_path) as outpkg:
            for src_idx, f in enumerate(files):
                tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix=".package")
                f.save(tmp_in.name)
                tmp_in.close()
                tmp_inputs.append(tmp_in.name)

                with DbpfPackage.read(tmp_in.name) as pkg:
                    for rid in pkg.search():
                        resource = pkg[rid]
                        compression, raw_bytes, decompressed_size = pkg.raw_content(resource)
                        try:
                            outpkg.put_raw(rid, compression, raw_bytes, decompressed_size)
                            if mode == "smart":
                                manifest_resources.append(
                                    [hex(rid.type), hex(rid.group), hex(rid.instance), src_idx]
                                )
                        except Exception:
                            pass  # ignora conflitos de key duplicada

            if mode == "smart":
                manifest = {
                    "format": "s4at_merge_manifest_v1",
                    "sources": source_names,
                    "resources": manifest_resources,
                }
                manifest_rid = ResourceID(
                    type=MERGE_MANIFEST_TYPE,
                    group=MERGE_MANIFEST_GROUP,
                    instance=MERGE_MANIFEST_INSTANCE,
                )
                outpkg.put(manifest_rid, json.dumps(manifest).encode("utf-8"))

        return send_file(
            tmp_out_path,
            as_attachment=True,
            download_name="merged.package",
            mimetype="application/octet-stream"
        )
    except Exception as e:
        print("Erro ao mesclar:", e)
        return json.dumps({"error": "Não consegui mesclar esses arquivos. Confira se são .package válidos."}), 500
    finally:
        for p in tmp_inputs:
            try:
                os.remove(p)
            except Exception:
                pass


@app.route("/api/unmerge", methods=["POST"])
def api_unmerge():
    """
    Recebe um único .package já mesclado e devolve ele "desmembrado".

    Dois caminhos:
      1) Se o arquivo tem a marca de manifesto (criada pelo modo "smart" do
         /api/merge), a separação é EXATA: sabemos de verdade qual resource
         veio de qual arquivo original.
      2) Se não tem (merge feito por outra ferramenta, ou pelo modo "simples"
         daqui mesmo), caímos pro palpite por Group ID: agrupamos os
         resources que compartilham o mesmo Group, já que é convenção comum
         entre criadores de CC manter um Group ID fixo por mod. Não é 100%
         garantido (é a mesma técnica que o Sims 4 Studio usa no "Split
         Merged Package" dele), então avisamos isso pro usuário na resposta.
    """
    from packer.resource import ResourceID
    import base64

    f = request.files.get("package")
    if not f or not f.filename.endswith(".package"):
        return json.dumps({"error": "Envie um arquivo .package válido"}), 400

    tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix=".package")
    f.save(tmp_in.name)
    tmp_in.close()

    tmp_outputs = []
    try:
        manifest = None
        all_resources = []  # [(rid, resource_obj), ...] exceto o próprio manifesto

        with DbpfPackage.read(tmp_in.name) as pkg:
            manifest_rid = ResourceID(
                type=MERGE_MANIFEST_TYPE,
                group=MERGE_MANIFEST_GROUP,
                instance=MERGE_MANIFEST_INSTANCE,
            )
            for rid in pkg.search():
                if rid == manifest_rid:
                    resource = pkg[rid]
                    try:
                        manifest = json.loads(pkg.content(resource).decode("utf-8"))
                    except Exception:
                        manifest = None
                    continue
                resource = pkg[rid]
                compression, raw_bytes, decompressed_size = pkg.raw_content(resource)
                all_resources.append((rid, resource, (compression, raw_bytes, decompressed_size)))

            # ---- agrupa: por manifesto (exato) ou por Group ID (palpite) ----
            groups = {}  # chave -> {"label": str, "items": [(rid, content), ...]}
            used_manifest = False

            if manifest and manifest.get("format") == "s4at_merge_manifest_v1":
                used_manifest = True
                sources = manifest.get("sources", [])
                origin_by_key = {
                    (r[0], r[1], r[2]): r[3] for r in manifest.get("resources", [])
                }
                for rid, resource, raw in all_resources:
                    key = (hex(rid.type), hex(rid.group), hex(rid.instance))
                    src_idx = origin_by_key.get(key)
                    if src_idx is not None and src_idx < len(sources):
                        label = sources[src_idx]
                    else:
                        label = "desconhecido.package"
                    groups.setdefault(label, []).append((rid, raw))
            else:
                for rid, resource, raw in all_resources:
                    label = f"grupo_{rid.group:08X}.package"
                    groups.setdefault(label, []).append((rid, raw))

            parts = []
            for label, items in groups.items():
                tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix=".package")
                tmp_out_path = tmp_out.name
                tmp_out.close()
                tmp_outputs.append(tmp_out_path)

                with DbpfPackage.write(tmp_out_path) as outpkg:
                    for rid, raw in items:
                        try:
                            compression, raw_bytes, decompressed_size = raw
                            outpkg.put_raw(rid, compression, raw_bytes, decompressed_size)
                        except Exception:
                            pass

                with open(tmp_out_path, "rb") as fh:
                    data = fh.read()

                parts.append({
                    "filename": label,
                    "size": len(data),
                    "resource_count": len(items),
                    "data_b64": base64.b64encode(data).decode("ascii"),
                })

        parts.sort(key=lambda p: p["filename"].lower())

        return json.dumps({
            "success": True,
            "exact": used_manifest,
            "parts": parts,
        })
    except Exception as e:
        print("Erro ao separar:", e)
        return json.dumps({"error": "Não consegui separar esse arquivo. Confira se é um .package válido."}), 500
    finally:
        try:
            os.remove(tmp_in.name)
        except Exception:
            pass
        for p in tmp_outputs:
            try:
                os.remove(p)
            except Exception:
                pass


# ---------------------------------------------------------------------------
#  CRIADOR DE MODS — traços, interações sociais e STBLs
# ---------------------------------------------------------------------------

def _parse_resource_key(value):
    """
    Converte o valor de um campo de ícone/ResourceKey vindo do JSON do
    frontend numa instância de packer.resource.ResourceID.

    Aceita duas formas:
      - dict:   {"type": "0x00B2D882", "group": "0x00000000", "instance": "0xC4EC3E0A..."}
                (valores podem ser string hex com "0x" ou int puro)
      - string: "00B2D882-00000000-C4EC3E0A68620978"  (mesmo formato "TYPE-GROUP-INSTANCE"
                usado no <T n="icon"> do XML e no ResourceKeyCell.toXmlNode do S4TK)

    Retorna None se value for None/vazio. Lança ValueError se o formato
    for reconhecido mas os dados forem inválidos.
    """
    from packer.resource import ResourceID

    if not value:
        return None

    def _to_int(v):
        if isinstance(v, int):
            return v
        return int(str(v), 16)

    if isinstance(value, dict):
        return ResourceID(
            type=_to_int(value.get("type", 0)),
            group=_to_int(value.get("group", 0)),
            instance=_to_int(value.get("instance", 0)),
        )

    if isinstance(value, str):
        parts = value.strip().split("-")
        if len(parts) != 3:
            raise ValueError(
                f"ResourceKey '{value}' inválido: use o formato TYPE-GROUP-INSTANCE"
            )
        type_str, group_str, instance_str = parts
        return ResourceID(
            type=int(type_str, 16),
            group=int(group_str, 16),
            instance=int(instance_str, 16),
        )

    raise ValueError(f"Tipo de ResourceKey não suportado: {type(value)}")


@app.route("/create")
def create_page():
    """Página de criação de mods (traços + interações sociais)."""
    return send_page("create.html")


@app.route("/api/mod/preview", methods=["POST"])
def api_mod_preview():
    """
    Recebe os dados do formulário e devolve um preview dos recursos
    que serão gerados (hashes, instance IDs, strings) — sem criar arquivo.
    """
    from packer.tuning import fnv32, fnv64, TYPE_TRAIT, TYPE_SOCIAL

    data = request.get_json()
    traits_in  = data.get("traits", [])
    socials_in = data.get("socials", [])

    preview = {
        "traits":       [],
        "socials":      [],
        "stbl_strings": [],
        "errors":       [],
    }

    seen_names = set()

    for t in traits_in:
        name    = t.get("name", "").strip()
        display = t.get("display_name", "").strip()
        desc    = t.get("description", "").strip()

        if not name or not display or not desc:
            preview["errors"].append("Traço incompleto: preencha nome interno, título e descrição.")
            continue
        if ":" not in name:
            preview["errors"].append(f"Nome '{name}' deve ter namespace (ex: meu_mod:trait_nome).")
            continue
        if name in seen_names:
            preview["errors"].append(f"Nome duplicado: '{name}'.")
            continue
        seen_names.add(name)

        dn_hash   = fnv32(display)
        desc_hash = fnv32(desc)
        inst_id   = fnv64(name)

        icon_preview = None
        icon_value = t.get("icon")
        if icon_value:
            try:
                icon_rid = _parse_resource_key(icon_value)
                icon_preview = "{:08X}-{:08X}-{:016X}".format(
                    icon_rid.type, icon_rid.group, icon_rid.instance
                )
            except ValueError as e:
                preview["errors"].append(f"Ícone do traço '{name}': {e}")

        preview["traits"].append({
            "name":         name,
            "display_name": display,
            "description":  desc,
            "instance_id":  f"0x{inst_id:016X}",
            "dn_hash":      f"0x{dn_hash:08X}",
            "desc_hash":    f"0x{desc_hash:08X}",
            "type_hex":     f"0x{TYPE_TRAIT:08X}",
            "icon":         icon_preview,
        })
        preview["stbl_strings"].append({"hash": f"0x{dn_hash:08X}",  "text": display})
        preview["stbl_strings"].append({"hash": f"0x{desc_hash:08X}", "text": desc})

    for s in socials_in:
        name    = s.get("name", "").strip()
        display = s.get("display_name", "").strip()

        if not name or not display:
            preview["errors"].append("Interação incompleta: preencha nome interno e título.")
            continue
        if ":" not in name:
            preview["errors"].append(f"Nome '{name}' deve ter namespace.")
            continue
        if name in seen_names:
            preview["errors"].append(f"Nome duplicado: '{name}'.")
            continue
        seen_names.add(name)

        dn_hash = fnv32(display)
        inst_id = fnv64(name)

        preview["socials"].append({
            "name":         name,
            "display_name": display,
            "instance_id":  f"0x{inst_id:016X}",
            "dn_hash":      f"0x{dn_hash:08X}",
            "type_hex":     f"0x{TYPE_SOCIAL:08X}",
        })
        preview["stbl_strings"].append({"hash": f"0x{dn_hash:08X}", "text": display})

    return app.response_class(
        response=json.dumps(preview),
        mimetype="application/json"
    )


@app.route("/api/mod/build", methods=["POST"])
def api_mod_build():
    """
    Gera o .package completo do mod (XMLs de tuning + STBL) e
    sobe no Supabase, devolvendo o output_id para download.
    """
    from packer.tuning import build_mod_package

    data        = request.get_json()
    namespace   = data.get("namespace", "mod").strip().lower().replace(" ", "_")
    target_lang = data.get("target_lang", "POR_BR")
    traits_in   = data.get("traits", [])
    socials_in  = data.get("socials", [])

    if not traits_in and not socials_in:
        return json.dumps({"success": False, "error": "Adicione pelo menos um traço ou interação."}), 400

    for item in traits_in + socials_in:
        if ":" not in item.get("name", ""):
            return json.dumps({
                "success": False,
                "error": f"Nome '{item.get('name')}' inválido. Use o formato namespace:nome"
            }), 400

    # Converte os campos de ícone/ResourceKey (JSON) em ResourceID reais.
    # Aceitos por traço: "icon", "cas_selected_icon", "cas_idle_asm_key",
    # cada um como dict {type,group,instance} ou string "TYPE-GROUP-INSTANCE".
    # Nenhum é obrigatório — se ausente, o traço sai sem ícone customizado
    # (mesmo comportamento de antes).
    try:
        for t in traits_in:
            t["icon_rid"] = _parse_resource_key(t.get("icon"))
            t["cas_selected_icon_rid"] = _parse_resource_key(t.get("cas_selected_icon"))
            t["cas_idle_asm_key_rid"] = _parse_resource_key(t.get("cas_idle_asm_key"))
    except ValueError as e:
        return json.dumps({"success": False, "error": f"Ícone inválido: {e}"}), 400

    tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix=".package")
    tmp_out_path = tmp_out.name
    tmp_out.close()

    try:
        build_mod_package(
            output_path=tmp_out_path,
            traits=traits_in,
            socials=socials_in,
            stbl_strings={},
            target_lang=target_lang,
        )

        output_name = f"{namespace}.package"
        output_id   = f"mod_{uuid.uuid4().hex}.package"

        with open(tmp_out_path, "rb") as f_out:
            supabase.storage.from_(BUCKET_NAME).upload(output_id, f_out.read())

    except Exception as e:
        return json.dumps({"success": False, "error": f"Erro ao gerar mod: {str(e)}"}), 500
    finally:
        try:
            os.remove(tmp_out_path)
        except Exception:
            pass

    return json.dumps({
        "success":     True,
        "output_name": output_name,
        "output_id":   output_id,
    })


with app.app_context():
    init_db()

if __name__ == "__main__":
    os.makedirs("templates", exist_ok=True)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

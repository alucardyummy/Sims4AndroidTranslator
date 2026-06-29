from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, redirect, send_file, session, send_from_directory
from packer.dbpf import DbpfPackage
from packer.stbl import Stbl
import os
import tempfile
import uuid
import json
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

UPLOAD_FOLDER = tempfile.gettempdir()


@app.route('/manifest.json')
def manifest():
    return send_from_directory('.', 'manifest.json')


@app.before_request
def ensure_guest_session():
    if 'user_id' not in session and 'guest_session_id' not in session:
        session['guest_session_id'] = str(uuid.uuid4())
        session.permanent = True


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

        conn.commit()
        conn.close()
    except Exception as e:
        print("Aviso ao iniciar DB:", e)


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


@app.route("/")
def home():
    user_id = session.get('user_id')
    guest_id = session.get('guest_session_id')
    session.clear()
    if user_id:
        session['user_id'] = user_id
    if guest_id:
        session['guest_session_id'] = guest_id
    # session.clear() também apaga a flag interna "_permanent" do Flask, que é
    # o que faz o cookie durar os 30 dias de app.permanent_session_lifetime.
    # Sem isso, o cookie volta a ser um cookie de sessão comum (sem prazo
    # fixo), que o Android Chrome descarta quando mata o processo do app em
    # segundo plano — e o login parece "expirar" sem motivo.
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
    file_id = f"{uuid.uuid4().hex}.package"
    res = supabase.storage.from_(BUCKET_NAME).create_signed_upload_url(file_id)
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
    data          = request.get_json()
    file_id       = data.get("file_id")
    original_name = data.get("original_name", "output.package")

    if not file_id:
        return json.dumps({"error": "file_id ausente"}), 400

    try:
        file_bytes = supabase.storage.from_(BUCKET_NAME).download(file_id)
    except Exception as e:
        return json.dumps({"error": f"Erro ao baixar arquivo do storage: {e}"}), 500

    all_instances, strings_cache = _process_package_bytes(file_bytes)

    if not all_instances:
        return json.dumps({"error": "Nenhuma STBL encontrada no arquivo"}), 400

    session["package_id"]     = file_id
    session["original_name"]  = original_name
    session["all_instances"]  = all_instances
    # strings_cache removido da sessão: o texto das strings é grande demais
    # pra caber no cookie (~4KB), o que estava silenciosamente corrompendo
    # a sessão e mandando o usuário de volta pra home depois do upload.

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
        return f"Erro no upload: {e}", 500

    all_instances, strings_cache = _process_package_bytes(file_bytes)

    if not all_instances:
        return "Nenhuma STBL encontrada no arquivo", 400

    session["package_id"]    = file_id
    session["original_name"] = f.filename
    session["all_instances"] = all_instances
    # strings_cache removido da sessão (ver comentário em upload_confirm)

    return redirect("/info")


# ---------------------------------------------------------------------------

@app.route("/info")
def info():
    if not session.get("all_instances"):
        return redirect("/")
    return send_file(os.path.join(TEMPLATE_DIR, "info.html"))


@app.route("/api/instances")
def api_instances():
    all_instances = session.get("all_instances", [])
    original_name = session.get("original_name", "output.package")

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
    if not session.get("package_id"):
        return redirect("/")
    return send_file(os.path.join(TEMPLATE_DIR, "editor.html"))


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
    output_name = data.get("output_name", "output").strip()
    custom_instance_base = data.get("custom_instance_base", None)  # 14 dígitos hex opcionais
    if not output_name:
        output_name = "output"
    if not output_name.endswith(".package"):
        output_name += ".package"

    pkg_id = session.get("package_id")
    if not pkg_id or pkg_id == "DATABASE_SAVE":
        pkg_id = data.get("package_id", "")
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
        except Exception:
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
    output_name = request.args.get("name", "output.package")
    if not output_id or output_id == "null":
        return "Arquivo não encontrado", 404

    url_res = supabase.storage.from_(BUCKET_NAME).create_signed_url(output_id, 60, {"download": output_name})
    return redirect(url_res['signedURL'])


@app.route("/api/translate", methods=["POST"])
def api_translate():
    from translator import translator
    data = request.get_json()
    text = data.get("text", "")
    engine = data.get("engine", "google")
    source_lang = data.get("source_lang", "ENG_US")
    target_lang = data.get("target_lang", "POR_BR")

    if not text:
        return json.dumps({"error": "No text provided"}), 400

    result = translator.translate(engine, text, source_lang=source_lang, target_lang=target_lang)

    return json.dumps({
        "success": result['status_code'] == 200,
        "translated": result['text'] if result['status_code'] == 200 else None,
        "error": result['text'] if result['status_code'] != 200 else None
    })


@app.route("/api/save_progress", methods=["POST"])
def save_progress():
    data = request.get_json()
    project_name = data.get("project_name", "Projeto sem nome")
    source_lang = data.get("source_lang", "ENG_US")
    target_lang = data.get("target_lang", "POR_BR")
    translation_data = json.dumps(data.get("strings", []))
    package_id = data.get("package_id", "")
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
            "UPDATE saves SET translation_data = %s, package_id = %s, last_updated = CURRENT_TIMESTAMP WHERE id = %s",
            (translation_data, package_id, existing_save['id'])
        )
    else:
        c.execute(
            "INSERT INTO saves (user_id, guest_session_id, project_name, source_lang, target_lang, translation_data, package_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (user_id, guest_id, project_name, source_lang, target_lang, translation_data, package_id)
        )

    conn.commit()
    conn.close()
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
    c = conn.cursor()

    if user_id:
        c.execute("DELETE FROM saves WHERE id = %s AND user_id = %s", (save_id, user_id))
    else:
        c.execute("DELETE FROM saves WHERE id = %s AND guest_session_id = %s", (save_id, guest_id))

    conn.commit()
    conn.close()
    return json.dumps({"success": True})


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

    session["package_id"] = pkg_id
    session["original_name"] = save["project_name"].split(" (")[0]
    session["db_save_id"] = save_id

    return json.dumps({
        "success": True,
        "project_name": save["project_name"],
        "source_lang": save["source_lang"],
        "target_lang": save["target_lang"],
        "translation_data": strings,
        "package_id": pkg_id,
    })


@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    question = data.get("security_question", "").strip()
    answer = data.get("security_answer", "").strip()

    if not username or len(password) < 6 or not question or not answer:
        return json.dumps({"success": False, "error": "Preencha todos os campos. A senha deve ter no mínimo 6 caracteres."}), 400

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    c.execute("SELECT id FROM users WHERE username = %s", (username,))
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
        c.execute("UPDATE saves SET user_id = %s WHERE guest_session_id = %s", (new_user_id, guest_id))

    conn.commit()
    conn.close()

    session['user_id'] = new_user_id
    return json.dumps({"success": True})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
    user = c.fetchone()

    if user and check_password_hash(user['password_hash'], password):
        session['user_id'] = user['id']
        session.permanent = True

        guest_id = session.get('guest_session_id')
        if guest_id:
            c.execute("UPDATE saves SET user_id = %s WHERE guest_session_id = %s AND user_id IS NULL", (user['id'], guest_id))

        conn.commit()
        conn.close()
        return json.dumps({"success": True})

    conn.close()
    return json.dumps({"success": False, "error": "Usuário ou senha incorretos."}), 401


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
        return json.dumps({"success": False, "error": f"Token inválido: {e}"}), 401

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
        # 2) Tem conta com o mesmo username (email local)?
        c.execute("SELECT id FROM users WHERE username = %s", (name,))
        user = c.fetchone()
        if user:
            # Vincula o google_id à conta existente e atualiza avatar
            c.execute("UPDATE users SET google_id = %s, avatar_url = %s WHERE id = %s", (google_id, avatar_url, user['id']))
        else:
            # 3) Usuário novo — cria conta sem senha local
            # Garante username único se o nome já existir
            base_name = name
            suffix = 1
            while True:
                c.execute("SELECT id FROM users WHERE username = %s", (name,))
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

    guest_id = session.get('guest_session_id')
    if guest_id:
        c.execute(
            "UPDATE saves SET user_id = %s WHERE guest_session_id = %s AND user_id IS NULL",
            (user['id'], guest_id)
        )

    conn.commit()
    conn.close()
    return json.dumps({"success": True})


@app.route("/api/google_callback")
def google_callback():
    """
    Recebe o ?code= do Google após o usuário escolher a conta no redirect flow.
    Troca o code por tokens, valida o id_token e faz login/cadastro.
    """
    code = request.args.get("code")
    error = request.args.get("error")

    if error or not code:
        return redirect("/?google_error=cancelled")

    if not GOOGLE_CLIENT_ID:
        return redirect("/?google_error=config")

    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
    redirect_uri = "https://sims4androidtranslator.vercel.app/api/google_callback"

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
        # 2) Tem conta com o mesmo username?
        c.execute("SELECT id FROM users WHERE username = %s", (name,))
        user = c.fetchone()
        if user:
            c.execute(
                "UPDATE users SET google_id = %s, avatar_url = %s WHERE id = %s",
                (google_id, avatar_url, user["id"]),
            )
        else:
            # 3) Cria conta nova sem senha local
            base_name = name
            suffix = 1
            while True:
                c.execute("SELECT id FROM users WHERE username = %s", (name,))
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
    # em redirect cross-site (Google → seu site).
    login_token = str(uuid.uuid4())
    user_id = user["id"]

    guest_id = session.get("guest_session_id")
    if guest_id:
        c.execute(
            "UPDATE saves SET user_id = %s WHERE guest_session_id = %s AND user_id IS NULL",
            (user_id, guest_id),
        )

    try:
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS login_token VARCHAR(64);")
    except Exception:
        pass
    c.execute("UPDATE users SET login_token = %s WHERE id = %s", (login_token, user_id))

    conn.commit()
    conn.close()
    return redirect(f"/?login_token={login_token}")


@app.route("/api/google_token_login", methods=["POST"])
def google_token_login():
    """
    Consome o token temporário gerado pelo google_callback e seta a session.
    Chamado pelo JS da home logo após o redirect do Google.
    """
    data = request.get_json()
    token = data.get("token", "").strip()
    if not token:
        return json.dumps({"success": False}), 400

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT id FROM users WHERE login_token = %s", (token,))
    user = c.fetchone()

    if not user:
        conn.close()
        return json.dumps({"success": False, "error": "Token inválido ou expirado."}), 401

    # Consome o token (uso único)
    c.execute("UPDATE users SET login_token = NULL WHERE id = %s", (user["id"],))
    conn.commit()
    conn.close()

    session["user_id"] = user["id"]
    session.permanent = True
    return json.dumps({"success": True})


@app.route("/api/forgot_password", methods=["POST"])
def forgot_password():
    data = request.get_json()
    username = data.get("username", "").strip()
    question = data.get("security_question", "").strip()
    answer = data.get("security_answer", "").strip()
    new_password = data.get("new_password", "").strip()

    if not username or not question or not answer or len(new_password) < 6:
        return json.dumps({"success": False, "error": "Dados inválidos. Preencha todos os campos corretamente."}), 400

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT id, security_question, security_answer_hash FROM users WHERE username = %s", (username,))
    user = c.fetchone()

    if user:
        if user['security_question'] == question and check_password_hash(user['security_answer_hash'], answer.lower()):
            pass_hash = generate_password_hash(new_password)
            c.execute("UPDATE users SET password_hash = %s WHERE id = %s", (pass_hash, user['id']))
            conn.commit()
            conn.close()
            return json.dumps({"success": True})
        else:
            conn.close()
            return json.dumps({"success": False, "error": "Pergunta ou resposta de segurança incorreta."}), 401

    conn.close()
    return json.dumps({"success": False, "error": "Usuário não encontrado."}), 404


@app.route("/api/me")
def me():
    user_id = session.get('user_id')
    if not user_id:
        return json.dumps({"logged_in": False})

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT username, profile_pic, avatar_url FROM users WHERE id = %s", (user_id,))
    user = c.fetchone()
    conn.close()

    if user:
        return json.dumps({
            "logged_in": True,
            "username": user["username"],
            "profile_pic": user["profile_pic"] or user["avatar_url"] or None
        })
    return json.dumps({"logged_in": False})


@app.route("/api/update_profile", methods=["POST"])
def update_profile():
    user_id = session.get('user_id')
    if not user_id:
        return json.dumps({"success": False, "error": "Não logado"}), 401

    data = request.get_json()
    new_username = data.get("username", "").strip()
    new_pic = data.get("profile_pic", "").strip()

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if new_username:
        c.execute("SELECT id FROM users WHERE username = %s AND id != %s", (new_username, user_id))
        if c.fetchone():
            conn.close()
            return json.dumps({"success": False, "error": "Nome de usuário já está em uso."}), 400

    c.execute("UPDATE users SET username = %s, profile_pic = %s WHERE id = %s", (new_username, new_pic, user_id))
    conn.commit()
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

    strings = {}
    with tempfile.NamedTemporaryFile(delete=False, suffix=".package") as tmp:
        f.save(tmp.name)
        tmp_path = tmp.name

    try:
        with DbpfPackage.read(tmp_path) as pkg:
            for rid in pkg.search_stbl():
                stbl = load_stbl(pkg, rid)
                for key, value in stbl._strings.items():
                    if value:
                        strings[str(key)] = value
    finally:
        os.remove(tmp_path)

    return json.dumps({"success": True, "translations": strings})


@app.route("/merge")
def merge_page():
    return send_file(os.path.join(TEMPLATE_DIR, "merge.html"))


@app.route("/api/merge", methods=["POST"])
def api_merge():
    files = request.files.getlist("packages")
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
        with DbpfPackage.write(tmp_out_path) as outpkg:
            for f in files:
                tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix=".package")
                f.save(tmp_in.name)
                tmp_in.close()
                tmp_inputs.append(tmp_in.name)

                with DbpfPackage.read(tmp_in.name) as pkg:
                    for rid in pkg.search():
                        resource = pkg[rid]
                        content = pkg.content(resource)
                        try:
                            outpkg.put(rid, content)
                        except Exception:
                            pass  # ignora conflitos de key duplicada

        return send_file(
            tmp_out_path,
            as_attachment=True,
            download_name="merged.package",
            mimetype="application/octet-stream"
        )
    except Exception as e:
        return json.dumps({"error": f"Erro ao mesclar: {e}"}), 500
    finally:
        for p in tmp_inputs:
            try:
                os.remove(p)
            except Exception:
                pass


with app.app_context():
    init_db()

if __name__ == "__main__":
    os.makedirs("templates", exist_ok=True)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False) 

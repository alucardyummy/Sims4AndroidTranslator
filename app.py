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

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET_NAME = "packages"


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')

app = Flask(__name__,
            template_folder=TEMPLATE_DIR,
            static_folder=os.path.join(BASE_DIR, 'img'),
            static_url_path='/img')
app.secret_key = "sims4translator_secret"
app.permanent_session_lifetime = timedelta(days=30)

UPLOAD_FOLDER = tempfile.gettempdir()


@app.route('/manifest.json')
def manifest():
    return send_from_directory('.', 'manifest.json')


@app.before_request
def ensure_guest_session():
    if 'user_id' not in session and 'guest_session_id' not in session:
        session['guest_session_id'] = str(uuid.uuid4())


# --- CONFIGURAÇÃO DO BANCO DE DADOS EM NUVEM (POSTGRESQL) ---
def get_db():

    db_url = os.environ.get("DATABASE_URL")

    if db_url and "sslmode" not in db_url:

        separator = "&" if "?" in db_url else "?"

        db_url += f"{separator}sslmode=require"

    conn = psycopg2.connect(db_url, connect_timeout=5)

    return conn

def init_db():
    """Cria ou atualiza as tabelas no banco em nuvem caso elas ainda não existam."""
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
        
        # --- ATUALIZAÇÃO SEGURA: Adiciona os campos de segurança se não existirem ---
        try:
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS security_question TEXT;")
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS security_answer_hash VARCHAR(255);")
        except Exception as e:
            print("Colunas de segurança já existem ou houve um aviso:", e)
            
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


@app.route("/")
def home():
    user_id = session.get('user_id')
    guest_id = session.get('guest_session_id')
    session.clear()
    if user_id:
        session['user_id'] = user_id
    if guest_id:
        session['guest_session_id'] = guest_id
    response = send_file(os.path.join(TEMPLATE_DIR, "index.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route("/upload", methods=["POST"])
def upload():
    if "package" not in request.files:
        return "Nenhum arquivo enviado", 400
    f = request.files["package"]
    if not f.filename.endswith(".package"):
        return "Arquivo inválido. Envie um .package", 400

    file_id = f"{uuid.uuid4().hex}.package"
    file_bytes = f.read()

    supabase.storage.from_(BUCKET_NAME).upload(file_id, file_bytes)

    all_instances = []
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
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
    finally:
        os.remove(tmp_path) # Apaga do disco efêmero

    if not all_instances:
        return "Nenhuma STBL encontrada no arquivo", 400

    session["package_id"] = file_id 
    session["original_name"] = f.filename
    session["all_instances"] = all_instances
    return redirect("/info")


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
            "group_hex":    i["group_hex"],
            "language":     i["language"],
        }
        for i in unique_instances
    ]

    langs_present = sorted(set(i["language"] for i in all_instances))

    return app.response_class(
        response=json.dumps({
            "instances":     safe,
            "original_name": original_name,
            "langs_present": langs_present,
            "total_stbl":    len(all_instances),
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

    if session.get("package_id") == "DATABASE_SAVE":
        db_strings = session.get("db_save_strings", [])
        return app.response_class(
            response=json.dumps({"strings": db_strings}),
            mimetype="application/json"
        )

    instance_int = int(instance_str)
    strings = []

    file_bytes = supabase.storage.from_(BUCKET_NAME).download(pkg_id)
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        with DbpfPackage.read(tmp_path) as pkg:
            for rid in pkg.search_stbl():
                if rid.instance == instance_int:
                    stbl = load_stbl(pkg, rid)
                    for key, value in stbl._strings.items():
                        strings.append({
                            "key":     key,
                            "key_hex": hex(key),
                            "value":   value,
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
    instance_str   = str(data["instance"])
    instance_int   = int(instance_str)
    strings_edited = data["strings"]
    target_lang    = data.get("target_language", "POR_BR")
    output_name    = data.get("output_name", "output").strip()

    if not output_name:
        output_name = "output"
    if not output_name.endswith(".package"):
        output_name += ".package"

    print(f">>> /save chamado. package_id da sessão: {session.get('package_id')}")

    if session.get("package_id") == "DATABASE_SAVE":
        return json.dumps({"success": True, "output_name": output_name, "saved_to_downloads": False})

    pkg_id = session.get("package_id")
    print(f">>> pkg_id: {pkg_id}")
    
    if not pkg_id:
        return json.dumps({"error": "package_id não encontrado na sessão"}), 400

    pkg_id = session.get("package_id")
    target_rid  = None
    target_stbl = None

    # Baixa o original do Supabase para ler
    original_bytes = supabase.storage.from_(BUCKET_NAME).download(pkg_id)
    
    with tempfile.NamedTemporaryFile(delete=False) as tmp_in, tempfile.NamedTemporaryFile(delete=False) as tmp_out:
        tmp_in.write(original_bytes)
        tmp_in_path = tmp_in.name
        tmp_out_path = tmp_out.name

    try:
        with DbpfPackage.read(tmp_in_path) as pkg:
            for rid in pkg.search_stbl():
                if rid.instance == instance_int:
                    target_rid  = rid
                    target_stbl = load_stbl(pkg, rid)
                    break

        if target_stbl is None:
            return json.dumps({"error": "STBL não encontrada"}), 400

        for item in strings_edited:
            target_stbl.add(int(item["key"]), item["value"])

        try:
            new_rid = target_rid.convert_instance(locale=target_lang)
        except Exception:
            new_rid = target_rid

        # Escreve o pacote final no arquivo temporário de saída
        with DbpfPackage.write(tmp_out_path) as outpkg:
            with DbpfPackage.read(tmp_in_path) as original:
                for rid in original.search():
                    resource = original[rid]
                    content  = original.content(resource)
                    if rid == target_rid:
                        outpkg.put(new_rid, target_stbl.binary)
                    elif rid == new_rid:
                        pass
                    else:
                        outpkg.put(rid, content)
        
        # Sobe o arquivo editado para o Supabase
        output_id = f"out_{uuid.uuid4().hex}.package"
        with open(tmp_out_path, "rb") as f_out:
            supabase.storage.from_(BUCKET_NAME).upload(output_id, f_out.read())


    finally:
        os.remove(tmp_in_path)
        os.remove(tmp_out_path)

    return json.dumps({
        "success":            True,
        "output_name":        output_name,
        "output_id": output_id,
        "saved_to_downloads": False,
    })


	@app.route("/download")
def download():
    output_id = request.args.get("file")
    output_name = request.args.get("name", "output.package")
    
    if not output_id:
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
        "success":    result['status_code'] == 200,
        "translated": result['text'] if result['status_code'] == 200 else None,
        "error":      result['text'] if result['status_code'] != 200 else None
    })


@app.route("/api/save_progress", methods=["POST"])
def save_progress():
    data = request.get_json()
    project_name     = data.get("project_name", "Projeto sem nome")
    source_lang      = data.get("source_lang", "ENG_US")
    target_lang      = data.get("target_lang", "POR_BR")
    translation_data = json.dumps(data.get("strings", []))

    user_id  = session.get('user_id')
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
            "UPDATE saves SET translation_data = %s, last_updated = CURRENT_TIMESTAMP WHERE id = %s",
            (translation_data, existing_save['id'])
        )
    else:
        c.execute(
            "INSERT INTO saves (user_id, guest_session_id, project_name, source_lang, target_lang, translation_data) VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, guest_id, project_name, source_lang, target_lang, translation_data)
        )

    conn.commit()
    conn.close()
    return json.dumps({"success": True})


@app.route("/api/get_saves")
def get_saves():
    user_id  = session.get('user_id')
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
    # Converte timestamp para string para serializar o JSON corretamente
    for save in saves:
        if 'last_updated' in save and save['last_updated']:
            save['last_updated'] = str(save['last_updated'])

    return json.dumps({"saves": saves})


@app.route("/api/delete_save", methods=["POST"])
def delete_save():
    data     = request.get_json()
    save_id  = data.get("id")
    user_id  = session.get('user_id')
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
    user_id  = session.get('user_id')
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

    session["package_id"]    = "DATABASE_SAVE"
    session["original_name"]   = save["project_name"].split(" (")[0]
    session["db_save_strings"] = strings

    return json.dumps({
        "success":          True,
        "project_name":     save["project_name"],
        "source_lang":      save["source_lang"],
        "target_lang":      save["target_lang"],
        "translation_data": strings
    })


@app.route("/api/register", methods=["POST"])
def register():
    data     = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    question = data.get("security_question", "").strip()
    answer   = data.get("security_answer", "").strip()

    if not username or len(password) < 6 or not question or not answer:
        return json.dumps({"success": False, "error": "Preencha todos os campos. A senha deve ter no mínimo 6 caracteres."}), 400

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    c.execute("SELECT id FROM users WHERE username = %s", (username,))
    if c.fetchone():
        conn.close()
        return json.dumps({"success": False, "error": "Nome de usuário já existe."}), 400

    pass_hash = generate_password_hash(password)
    # Criptografa a resposta também para segurança máxima
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
    data     = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
    user = c.fetchone()

    if user and check_password_hash(user['password_hash'], password):
        session['user_id'] = user['id']
        session.permanent  = True

        guest_id = session.get('guest_session_id')
        if guest_id:
            c.execute("UPDATE saves SET user_id = %s WHERE guest_session_id = %s AND user_id IS NULL", (user['id'], guest_id))

        conn.commit()
        conn.close()
        return json.dumps({"success": True})

    conn.close()
    return json.dumps({"success": False, "error": "Usuário ou senha incorretos."}), 401


@app.route("/api/forgot_password", methods=["POST"])
def forgot_password():
    data         = request.get_json()
    username     = data.get("username", "").strip()
    question     = data.get("security_question", "").strip()
    answer       = data.get("security_answer", "").strip()
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
    c.execute("SELECT username, profile_pic FROM users WHERE id = %s", (user_id,))
    user = c.fetchone()
    conn.close()

    if user:
        return json.dumps({
            "logged_in":   True,
            "username":    user["username"],
            "profile_pic": user["profile_pic"]
        })
    return json.dumps({"logged_in": False})


@app.route("/api/update_profile", methods=["POST"])
def update_profile():
    user_id = session.get('user_id')
    if not user_id:
        return json.dumps({"success": False, "error": "Não logado"}), 401

    data         = request.get_json()
    new_username = data.get("username", "").strip()
    new_pic      = data.get("profile_pic", "").strip()

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


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return json.dumps({"success": True})

with app.app_context():
    init_db()

if __name__ == "__main__":
    os.makedirs("templates", exist_ok=True)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

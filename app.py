from flask import Flask, request, redirect, send_file, session
from packer.dbpf import DbpfPackage
from packer.stbl import Stbl
import os
import tempfile

# Caminho base: funciona tanto no Termux quanto dentro do APK
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')

app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.secret_key = "sims4translator_secret"

UPLOAD_FOLDER = tempfile.gettempdir()


def load_stbl(pkg, rid):
    """Carrega um STBL e popula _strings corretamente."""
    resource = pkg[rid]
    content = pkg.content(resource)
    stbl = Stbl(rid, content)
    for key, value in stbl.strings.items():
        stbl._strings[key] = value
    return stbl


@app.route("/")
def home():
    session.clear()
    return send_file(os.path.join(TEMPLATE_DIR, "index.html"))


@app.route("/upload", methods=["POST"])
def upload():
    if "package" not in request.files:
        return "Nenhum arquivo enviado", 400
    f = request.files["package"]
    if not f.filename.endswith(".package"):
        return "Arquivo inválido. Envie um .package", 400

    save_path = os.path.join(UPLOAD_FOLDER, "upload_" + f.filename)
    f.save(save_path)
    session["package_path"] = save_path
    session["original_name"] = f.filename

    all_instances = []
    with DbpfPackage.read(save_path) as pkg:
        for rid in pkg.search_stbl():
            lang = rid.language or "UNKNOWN"
            all_instances.append({
                "instance":      rid.instance,
                "instance_hex":  rid.hex_instance,
                "group_hex":     rid.str_group,
                "language":      lang,
                "language_code": rid.language_code or "0x0017",
            })

    if not all_instances:
        return "Nenhuma STBL encontrada no arquivo", 400

    session["all_instances"] = all_instances
    return redirect("/info")


@app.route("/info")
def info():
    if not session.get("all_instances"):
        return redirect("/")
    return send_file(os.path.join(TEMPLATE_DIR, "info.html"))


@app.route("/api/instances")
def api_instances():
    import json
    all_instances = session.get("all_instances", [])
    original_name = session.get("original_name", "output.package")

    # Agrupa por base (últimos 14 hex chars da instance = conteúdo sem o código de idioma).
    # Cada base é um "bloco de strings" único — mostra o primeiro idioma encontrado
    # (geralmente ENG_US se existir, senão o que vier primeiro).
    seen_bases = {}
    for inst in all_instances:
        # hex_instance = "0xAABBBBBBBBBBBBBB"
        # índice 4 em diante = parte base (sem prefixo de idioma)
        base = inst["instance_hex"][4:]
        if base not in seen_bases:
            seen_bases[base] = inst
        else:
            # Prefere ENG_US se ainda não tinha
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
            "instances":    safe,
            "original_name": original_name,
            "langs_present": langs_present,
            "total_stbl":   len(all_instances),
        }),
        mimetype="application/json"
    )


@app.route("/editor")
def editor():
    if not session.get("package_path"):
        return redirect("/")
    return send_file(os.path.join(TEMPLATE_DIR, "editor.html"))


@app.route("/api/strings")
def api_strings():
    import json
    instance_str = request.args.get("instance")
    if not instance_str:
        return "Instância não informada", 400

    instance_int = int(instance_str)
    pkg_path = session.get("package_path")
    strings = []

    with DbpfPackage.read(pkg_path) as pkg:
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

    return app.response_class(
        response=json.dumps({"strings": strings}),
        mimetype="application/json"
    )


@app.route("/save", methods=["POST"])
def save():
    import json
    import shutil

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

    pkg_path = session.get("package_path")

    target_rid  = None
    target_stbl = None

    with DbpfPackage.read(pkg_path) as pkg:
        for rid in pkg.search_stbl():
            if rid.instance == instance_int:
                target_rid  = rid
                target_stbl = load_stbl(pkg, rid)
                break

    if target_stbl is None:
        return json.dumps({"error": "STBL não encontrada"}), 400

    # Aplica edições do usuário
    for item in strings_edited:
        target_stbl.add(int(item["key"]), item["value"])

    # Gera novo rid com o idioma destino
    try:
        new_rid = target_rid.convert_instance(locale=target_lang)
    except Exception:
        new_rid = target_rid

    output_path = os.path.join(UPLOAD_FOLDER, output_name)

    with DbpfPackage.write(output_path) as outpkg:
        with DbpfPackage.read(pkg_path) as original:
            for rid in original.search():
                resource = original[rid]
                content  = original.content(resource)
                if rid == target_rid:
                    outpkg.put(new_rid, target_stbl.binary)
                elif rid == new_rid:
                    pass  # pula duplicata se idioma destino já existia
                else:
                    outpkg.put(rid, content)

    session["output_path"] = output_path
    session["output_name"] = output_name

    saved_to_downloads = False
    downloads_dir = os.path.expanduser("~/storage/downloads")
    if os.path.exists(downloads_dir):
        try:
            shutil.copy(output_path, os.path.join(downloads_dir, output_name))
            saved_to_downloads = True
        except Exception:
            pass

    return json.dumps({
        "success":            True,
        "output_name":        output_name,
        "saved_to_downloads": saved_to_downloads,
    })


@app.route("/download")
def download():
    output_path = session.get("output_path")
    output_name = session.get("output_name", "output.package")
    if not output_path or not os.path.exists(output_path):
        return "Arquivo não encontrado", 404
    return send_file(output_path, as_attachment=True, download_name=output_name)


@app.route("/api/translate", methods=["POST"])
def api_translate():
    import json
    from translator import translator
    
    data = request.get_json()
    text = data.get("text", "")
    engine = data.get("engine", "google")
    
    if not text:
        return json.dumps({"error": "No text provided"}), 400
    
    result = translator.translate(engine, text, source_lang='en', target_lang='pt')
    
    return json.dumps({
        "success": result['status_code'] == 200,
        "translated": result['text'] if result['status_code'] == 200 else None,
        "error": result['text'] if result['status_code'] != 200 else None
    })



if __name__ == "__main__":
    os.makedirs("templates", exist_ok=True)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

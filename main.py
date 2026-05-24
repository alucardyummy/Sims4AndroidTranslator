import threading
import os


def is_android():
    try:
        from jnius import autoclass
        autoclass('org.kivy.android.PythonActivity')
        return True
    except Exception:
        return False


def _write_log(name, text):
    for path in ['/sdcard/', '/storage/emulated/0/']:
        try:
            with open(path + 'sims4_' + name + '.txt', 'w') as f:
                f.write(text)
            return
        except Exception:
            pass


def get_cert_dir():
    """Pasta gravável dentro do APK para guardar o certificado."""
    if is_android():
        from android.storage import app_storage_path
        d = os.path.join(app_storage_path(), 'certs')
    else:
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'certs')
    os.makedirs(d, exist_ok=True)
    return d


def generate_cert():
    """Gera certificado self-signed e retorna (cert_path, key_path)."""
    cert_dir = get_cert_dir()
    cert_path = os.path.join(cert_dir, 'cert.pem')
    key_path  = os.path.join(cert_dir, 'key.pem')

    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path

    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName(u"localhost"),
            x509.IPAddress(__import__('ipaddress').IPv4Address('127.0.0.1')),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )

    with open(cert_path, 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, 'wb') as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()
        ))

    return cert_path, key_path


def run_termux():
    import time
    print("\n=== Sims 4 Translator ===")

    def start():
        try:
            base = os.path.dirname(os.path.abspath(__file__))
            import app as flask_app
            flask_app.TEMPLATE_DIR = os.path.join(base, 'templates')
            flask_app.app.template_folder = flask_app.TEMPLATE_DIR
            flask_app.app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
        except Exception:
            import traceback; print(traceback.format_exc())

    threading.Thread(target=start, daemon=True).start()
    time.sleep(1.5)
    print("Servidor rodando em: http://localhost:5000")
    print("Pressione Ctrl+C para parar.\n")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("\nServidor encerrado.")


def run_android():
    from kivy.app import App
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.label import Label
    from kivy.clock import Clock
    from kivy.core.window import Window

    status_label = Label(
        text='Iniciando...',
        font_size='14sp',
        color=(0.8, 0.7, 0.4, 1),
        halign='center', valign='middle',
    )

    flask_ready = threading.Event()
    flask_error = {'msg': None}

    def flask_thread():
        try:
            base = os.path.dirname(os.path.abspath(__file__))
            import app as flask_app
            flask_app.TEMPLATE_DIR = os.path.join(base, 'templates')
            flask_app.app.template_folder = flask_app.TEMPLATE_DIR

            cert_path, key_path = generate_cert()
            ssl_ctx = (cert_path, key_path)

            flask_ready.set()
            flask_app.app.run(
                host="127.0.0.1", port=5000,
                debug=False, use_reloader=False,
                ssl_context=ssl_ctx
            )
        except Exception:
            import traceback
            err = traceback.format_exc()
            flask_error['msg'] = err
            flask_ready.set()
            _write_log('flask_error', err)

    def open_webview(dt):
        try:
            from android.runnable import run_on_ui_thread
            from jnius import autoclass, PythonJavaClass, java_method

            WebView        = autoclass('android.webkit.WebView')
            LayoutParams   = autoclass('android.widget.FrameLayout$LayoutParams')
            FrameLayout    = autoclass('android.widget.FrameLayout')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')

            class TrustingWebViewClient(PythonJavaClass):
                __javainterfaces__ = ['android/webkit/WebViewClient']
                __javacontext__ = 'app'

                @java_method('(Landroid/webkit/WebView;Landroid/webkit/SslErrorHandler;Landroid/net/http/SslError;)V')
                def onReceivedSslError(self, view, handler, error):
                    handler.proceed()

            @run_on_ui_thread
            def _do():
                try:
                    activity = PythonActivity.mActivity
                    frame = FrameLayout(activity)
                    lp = LayoutParams(
                        LayoutParams.MATCH_PARENT,
                        LayoutParams.MATCH_PARENT
                    )
                    wv = WebView(activity)
                    s = wv.getSettings()
                    s.setJavaScriptEnabled(True)
                    s.setDomStorageEnabled(True)
                    s.setAllowFileAccess(True)
                    s.setAllowContentAccess(True)
                    wv.setWebViewClient(TrustingWebViewClient())
                    wv.loadUrl("https://127.0.0.1:5000")
                    frame.addView(wv, lp)
                    activity.setContentView(frame)
                except Exception:
                    import traceback
                    err = traceback.format_exc()
                    _write_log('webview_error', err)
                    Clock.schedule_once(
                        lambda dt: setattr(status_label, 'text', '[ERRO WebView]\n' + err[:700]), 0
                    )
            _do()

        except Exception:
            import traceback
            err = traceback.format_exc()
            _write_log('webview_import_error', err)
            Clock.schedule_once(
                lambda dt: setattr(status_label, 'text', '[ERRO]\n' + err[:700]), 0
            )

    def check_ready(dt):
        if flask_ready.is_set():
            if flask_error['msg']:
                status_label.text = '[ERRO Flask]\n' + flask_error['msg'][:600]
            else:
                status_label.text = 'Servidor pronto! Abrindo...'
                Clock.schedule_once(open_webview, 0.3)
            return False

    class MainWidget(BoxLayout):
        def __init__(self, **kwargs):
            super().__init__(orientation='vertical', **kwargs)
            self.add_widget(status_label)
            threading.Thread(target=flask_thread, daemon=True).start()
            Clock.schedule_interval(check_ready, 0.5)

    class Sims4App(App):
        def build(self):
            Window.clearcolor = (0.06, 0.06, 0.06, 1)
            return MainWidget()

    Sims4App().run()


if __name__ == '__main__':
    if is_android():
        run_android()
    else:
        run_termux()

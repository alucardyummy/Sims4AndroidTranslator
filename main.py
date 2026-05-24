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


def get_cert_paths():
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'certs'),
        '/data/data/com.alucardyummy.sims4translator/files/app/certs',
        '/data/data/com.alucardyummy.sims4translator/files/certs',
    ]
    for d in candidates:
        cert = os.path.join(d, 'cert.pem')
        key  = os.path.join(d, 'key.pem')
        if os.path.exists(cert) and os.path.exists(key):
            return cert, key
    raise FileNotFoundError('Certs nao encontrados. Tentados: ' + str(candidates))


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
    from kivy.uix.image import Image
    from kivy.uix.floatlayout import FloatLayout
    from kivy.clock import Clock
    from kivy.core.window import Window

    base_path = os.path.dirname(os.path.abspath(__file__))
    
    splash = Image(
        source=os.path.join(base_path, 'img', 'icon-inicio.png'),
        size_hint=(None, None),
        size=(180, 180),
        pos_hint={'center_x': 0.5, 'center_y': 0.5}
    )
    
    layout = FloatLayout()
    layout.add_widget(splash)

    flask_ready = threading.Event()
    flask_error = {'msg': None}

    def flask_thread():
        try:
            import app as flask_app
            flask_app.TEMPLATE_DIR = os.path.join(base_path, 'templates')
            flask_app.app.template_folder = flask_app.TEMPLATE_DIR
            cert_path, key_path = get_cert_paths()
            flask_ready.set()
            flask_app.app.run(
                host="127.0.0.1", port=5000,
                debug=False, use_reloader=False,
                ssl_context=(cert_path, key_path)
            )
        except Exception:
            import traceback
            err = traceback.format_exc()
            flask_error['msg'] = err
            flask_ready.set()
            _write_log('flask_error', err)

    def open_webview(dt):
        if flask_error['msg']:
            _write_log('startup_error', flask_error['msg'])
            return

        try:
            from android.runnable import run_on_ui_thread
            from jnius import autoclass, PythonJavaClass, java_method

            WebView = autoclass('android.webkit.WebView')
            LayoutParams = autoclass('android.widget.FrameLayout$LayoutParams')
            FrameLayout = autoclass('android.widget.FrameLayout')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')

            class FileChooserClient(PythonJavaClass):
                __javainterfaces__ = ['android/webkit/WebChromeClient']
                __javacontext__ = 'app'
                
                @java_method('(Landroid/webkit/WebView;Landroid/webkit/ValueCallback;Landroid/webkit/WebChromeClient$FileChooserParams;)Z')
                def onShowFileChooser(self, webView, filePathCallback, fileChooserParams):
                    Intent = autoclass('android.content.Intent')
                    intent = Intent(Intent.ACTION_GET_CONTENT)
                    intent.setType("*/*")
                    PythonActivity.mActivity.startActivityForResult(intent, 1)
                    return True

            @run_on_ui_thread
            def _do():
                try:
                    activity = PythonActivity.mActivity
                    frame = FrameLayout(activity)
                    lp = LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT)
                    wv = WebView(activity)
                    
                    s = wv.getSettings()
                    s.setJavaScriptEnabled(True)
                    s.setDomStorageEnabled(True)
                    s.setAllowFileAccess(True)
                    s.setAllowContentAccess(True)
                    
                    TrustingClient = autoclass('org.kivy.TrustingWebViewClient')
                    wv.setWebViewClient(TrustingClient())
                    wv.setWebChromeClient(FileChooserClient())
                    wv.loadUrl("https://127.0.0.1:5000")
                    
                    frame.addView(wv, lp)
                    activity.setContentView(frame)
                except Exception:
                    import traceback
                    err = traceback.format_exc()
                    _write_log('webview_error', err)
            _do()

        except Exception:
            import traceback
            err = traceback.format_exc()
            _write_log('webview_import_error', err)

    def check_ready(dt):
        if flask_ready.is_set():
            Clock.schedule_once(open_webview, 0.5)
            return False

    class Sims4App(App):
        def build(self):
            Window.clearcolor = (0.06, 0.06, 0.06, 1)
            threading.Thread(target=flask_thread, daemon=True).start()
            Clock.schedule_interval(check_ready, 0.5)
            return layout

    Sims4App().run()


if __name__ == '__main__':
    if is_android():
        run_android()
    else:
        run_termux()

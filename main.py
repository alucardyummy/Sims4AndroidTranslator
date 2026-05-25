import threading
import os


def is_android():
    try:
        from jnius import autoclass
        autoclass('org.kivy.android.PythonActivity')
        return True
    except Exception:
        return False


def _log(name, text):
    """Salva log em /sdcard para debug."""
    for path in ['/sdcard/', '/storage/emulated/0/']:
        try:
            with open(path + 'sims4_' + name + '.txt', 'w') as f:
                f.write(str(text))
            return
        except Exception:
            pass


def run_termux():
    import time
    print("\n=== Sims 4 Translator ===")
    base = os.path.dirname(os.path.abspath(__file__))

    def start():
        try:
            import app as flask_app
            flask_app.app.template_folder = os.path.join(base, 'templates')
            flask_app.app.run(
                host="127.0.0.1", port=5000,
                debug=False, use_reloader=False
            )
        except Exception:
            import traceback
            print(traceback.format_exc())

    threading.Thread(target=start, daemon=True).start()
    time.sleep(1.5)
    print("Acesse: http://localhost:5000")
    print("Ctrl+C para parar.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nEncerrado.")


def run_android():
    from kivy.app import App
    from kivy.uix.widget import Widget
    from kivy.clock import Clock
    from kivy.core.window import Window

    _log('startup', 'Iniciando...')
    base = os.path.dirname(os.path.abspath(__file__))
    flask_ready = threading.Event()
    flask_error = {'msg': None}

    def flask_thread():
        try:
            _log('flask', 'Subindo Flask...')
            import app as flask_app
            flask_app.app.template_folder = os.path.join(base, 'templates')
            # SEM SSL — localhost não precisa
            flask_app.app.run(
                host="127.0.0.1", port=5000,
                debug=False, use_reloader=False
            )
        except Exception:
            import traceback
            err = traceback.format_exc()
            flask_error['msg'] = err
            _log('flask_error', err)
        finally:
            flask_ready.set()

    def check_flask(dt):
        """Tenta conectar ao Flask; quando responder, abre a WebView."""
        import urllib.request
        try:
            urllib.request.urlopen("http://127.0.0.1:5000/", timeout=1)
            _log('startup', 'Flask respondeu, abrindo WebView')
            Clock.schedule_once(open_webview, 0)
            return False  # cancela o schedule_interval
        except Exception:
            pass  # ainda não subiu, tenta de novo

    def open_webview(dt):
        if flask_error['msg']:
            _log('startup_error', flask_error['msg'])
            return

        try:
            from android.runnable import run_on_ui_thread
            from jnius import autoclass

            WebView       = autoclass('android.webkit.WebView')
            WebViewClient = autoclass('android.webkit.WebViewClient')
            FrameLayout   = autoclass('android.widget.FrameLayout')
            LayoutParams  = autoclass('android.widget.FrameLayout$LayoutParams')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')

            @run_on_ui_thread
            def _create():
                try:
                    activity = PythonActivity.mActivity
                    wv = WebView(activity)

                    s = wv.getSettings()
                    s.setJavaScriptEnabled(True)
                    s.setDomStorageEnabled(True)
                    s.setAllowFileAccess(True)
                    s.setAllowContentAccess(True)
                    # Permite upload de arquivos
                    s.setAllowUniversalAccessFromFileURLs(True)

                    # Cliente simples — sem SSL, sem complicação
                    wv.setWebViewClient(WebViewClient())

                    wv.loadUrl("http://127.0.0.1:5000")

                    lp = LayoutParams(
                        LayoutParams.MATCH_PARENT,
                        LayoutParams.MATCH_PARENT
                    )
                    frame = FrameLayout(activity)
                    frame.addView(wv, lp)
                    activity.setContentView(frame)

                    _log('webview', 'WebView criada com sucesso')
                except Exception:
                    import traceback
                    _log('webview_error', traceback.format_exc())

            _create()

        except Exception:
            import traceback
            _log('webview_import_error', traceback.format_exc())

    class Sims4App(App):
        def build(self):
            Window.clearcolor = (0.06, 0.06, 0.06, 1)
            # Sobe Flask em background
            threading.Thread(target=flask_thread, daemon=True).start()
            # Verifica a cada 0.5s se o Flask já respondeu
            Clock.schedule_interval(check_flask, 0.5)
            return Widget()

    Sims4App().run()


if __name__ == '__main__':
    if is_android():
        run_android()
    else:
        run_termux()

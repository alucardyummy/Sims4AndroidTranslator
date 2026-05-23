import threading
import time
import os
import sys


def is_android():
    try:
        from jnius import autoclass
        autoclass('org.kivy.android.PythonActivity')
        return True
    except Exception:
        return False


def start_flask():
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        template_dir = os.path.join(base, 'templates')

        import app as flask_app
        flask_app.app.template_folder = template_dir
        flask_app.TEMPLATE_DIR = template_dir
        flask_app.app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
    except Exception as e:
        # Salva erro em arquivo para debug
        try:
            with open('/sdcard/sims4_flask_error.txt', 'w') as f:
                import traceback
                f.write(traceback.format_exc())
        except Exception:
            pass


def run_termux():
    print("\n=== Sims 4 Translator ===")
    print("Iniciando servidor...")
    t = threading.Thread(target=start_flask, daemon=True)
    t.start()
    time.sleep(1.5)
    print("Servidor rodando em: http://localhost:5000")
    print("Abra o link acima no navegador do celular.")
    print("Pressione Ctrl+C para parar.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nServidor encerrado.")


def run_android():
    from kivy.app import App
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.label import Label
    from kivy.uix.scrollview import ScrollView
    from kivy.clock import Clock
    from kivy.core.window import Window
    from android.runnable import run_on_ui_thread
    from jnius import autoclass

    WebView        = autoclass('android.webkit.WebView')
    WebViewClient  = autoclass('android.webkit.WebViewClient')
    LayoutParams   = autoclass('android.view.ViewGroup$LayoutParams')
    PythonActivity = autoclass('org.kivy.android.PythonActivity')

    URL = "http://127.0.0.1:5000"

    # Label de status visível na tela enquanto o Flask sobe
    status_label = Label(
        text='Iniciando servidor...',
        font_size='16sp',
        color=(0.8, 0.7, 0.4, 1),
        halign='center',
        valign='middle',
    )

    flask_ready = threading.Event()
    flask_error = {'msg': None}

    def start_flask_thread():
        try:
            base = os.path.dirname(os.path.abspath(__file__))
            template_dir = os.path.join(base, 'templates')

            import app as flask_app
            flask_app.app.template_folder = template_dir
            flask_app.TEMPLATE_DIR = template_dir

            # Testa se o servidor sobe antes de sinalizar pronto
            import socket
            sock = socket.socket()
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', 5000))
            sock.close()

            flask_ready.set()
            flask_app.app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
        except Exception:
            import traceback
            err = traceback.format_exc()
            flask_error['msg'] = err
            flask_ready.set()  # libera a espera mesmo com erro
            try:
                with open('/sdcard/sims4_flask_error.txt', 'w') as f:
                    f.write(err)
            except Exception:
                pass

    class WebViewWidget(BoxLayout):
        def __init__(self, **kwargs):
            super().__init__(orientation='vertical', **kwargs)
            self.add_widget(status_label)

            t = threading.Thread(target=start_flask_thread, daemon=True)
            t.start()
            Clock.schedule_interval(self._check_ready, 0.5)

        def _check_ready(self, dt):
            if flask_ready.is_set():
                if flask_error['msg']:
                    # Mostra o erro na tela
                    status_label.text = (
                        '[ERRO] Flask falhou ao iniciar:\n\n' +
                        flask_error['msg'][:800]
                    )
                    return False  # para o schedule
                else:
                    status_label.text = 'Servidor pronto! Abrindo...'
                    Clock.schedule_once(self._open_webview, 0.5)
                    return False

        @run_on_ui_thread
        def _open_webview(self, dt):
            try:
                activity = PythonActivity.mActivity
                wv = WebView(activity)
                settings = wv.getSettings()
                settings.setJavaScriptEnabled(True)
                settings.setDomStorageEnabled(True)
                settings.setAllowFileAccess(True)
                settings.setAllowContentAccess(True)
                settings.setMixedContentMode(0)
                wv.setWebViewClient(WebViewClient())
                wv.loadUrl(URL)
                lp = LayoutParams(
                    LayoutParams.MATCH_PARENT,
                    LayoutParams.MATCH_PARENT
                )
                activity.getWindow().getDecorView().addView(wv, lp)
            except Exception:
                import traceback
                Clock.schedule_once(
                    lambda dt: setattr(
                        status_label, 'text',
                        '[ERRO] WebView falhou:\n\n' + traceback.format_exc()[:800]
                    ), 0
                )

    class Sims4TranslatorApp(App):
        def build(self):
            Window.clearcolor = (0.06, 0.06, 0.06, 1)
            return WebViewWidget()

    Sims4TranslatorApp().run()


if __name__ == '__main__':
    if is_android():
        run_android()
    else:
        run_termux()

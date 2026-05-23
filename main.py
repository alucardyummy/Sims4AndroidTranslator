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
        text='Iniciando servidor...',
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
            flask_ready.set()
            flask_app.app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
        except Exception:
            import traceback
            err = traceback.format_exc()
            flask_error['msg'] = err
            flask_ready.set()
            _write_log('flask_error', err)

    def open_webview(dt):
        try:
            from android.runnable import run_on_ui_thread
            from jnius import autoclass

            WebView        = autoclass('android.webkit.WebView')
            WebViewClient  = autoclass('android.webkit.WebViewClient')
            FrameLayout    = autoclass('android.widget.FrameLayout')
            LayoutParams   = autoclass('android.widget.FrameLayout$LayoutParams')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')

            @run_on_ui_thread
            def _do():
                try:
                    activity = PythonActivity.mActivity

                    # Cria um FrameLayout que ocupa a tela toda
                    frame = FrameLayout(activity)
                    lp_fill = LayoutParams(
                        LayoutParams.MATCH_PARENT,
                        LayoutParams.MATCH_PARENT
                    )

                    wv = WebView(activity)
                    s = wv.getSettings()
                    s.setJavaScriptEnabled(True)
                    s.setDomStorageEnabled(True)
                    s.setAllowFileAccess(True)
                    s.setAllowContentAccess(True)
                    s.setMixedContentMode(0)
                    wv.setWebViewClient(WebViewClient())
                    wv.loadUrl("http://127.0.0.1:5000")

                    frame.addView(wv, lp_fill)

                    # Adiciona o frame na janela via setContentView
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
                lambda dt: setattr(status_label, 'text', '[ERRO import]\n' + err[:700]), 0
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

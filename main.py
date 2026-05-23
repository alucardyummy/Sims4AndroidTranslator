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
    except Exception:
        import traceback
        _write_log('flask_error', traceback.format_exc())


def _write_log(name, text):
    for path in ['/sdcard/', '/storage/emulated/0/', '/data/data/com.alucardyummy.sims4translator/files/']:
        try:
            with open(path + 'sims4_' + name + '.txt', 'w') as f:
                f.write(text)
            return
        except Exception:
            pass


def run_termux():
    print("\n=== Sims 4 Translator ===")
    t = threading.Thread(target=start_flask, daemon=True)
    t.start()
    time.sleep(1.5)
    print("Servidor rodando em: http://localhost:5000")
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
    from kivy.clock import Clock
    from kivy.core.window import Window

    status_label = Label(
        text='Iniciando servidor...',
        font_size='14sp',
        color=(0.8, 0.7, 0.4, 1),
        halign='center',
        valign='middle',
        text_size=(Window.width * 0.9, None),
    )

    flask_ready = threading.Event()
    flask_error = {'msg': None}

    def flask_thread():
        try:
            base = os.path.dirname(os.path.abspath(__file__))
            template_dir = os.path.join(base, 'templates')
            import app as flask_app
            flask_app.app.template_folder = template_dir
            flask_app.TEMPLATE_DIR = template_dir
            flask_ready.set()
            flask_app.app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
        except Exception:
            import traceback
            err = traceback.format_exc()
            flask_error['msg'] = err
            flask_ready.set()
            _write_log('flask_error', err)

    def open_webview_safe(dt):
        try:
            from android.runnable import run_on_ui_thread
            from jnius import autoclass

            WebView        = autoclass('android.webkit.WebView')
            WebViewClient  = autoclass('android.webkit.WebViewClient')
            LayoutParams   = autoclass('android.view.ViewGroup$LayoutParams')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')

            @run_on_ui_thread
            def _do():
                try:
                    activity = PythonActivity.mActivity
                    wv = WebView(activity)
                    s = wv.getSettings()
                    s.setJavaScriptEnabled(True)
                    s.setDomStorageEnabled(True)
                    s.setAllowFileAccess(True)
                    s.setAllowContentAccess(True)
                    s.setMixedContentMode(0)
                    wv.setWebViewClient(WebViewClient())
                    wv.loadUrl("http://127.0.0.1:5000")
                    lp = LayoutParams(
                        LayoutParams.MATCH_PARENT,
                        LayoutParams.MATCH_PARENT
                    )
                    activity.getWindow().getDecorView().addView(wv, lp)
                except Exception:
                    import traceback
                    err = traceback.format_exc()
                    _write_log('webview_error', err)
                    Clock.schedule_once(
                        lambda dt: setattr(status_label, 'text', '[ERRO WebView]\n' + err[:600]), 0
                    )
            _do()

        except Exception:
            import traceback
            err = traceback.format_exc()
            _write_log('webview_import_error', err)
            Clock.schedule_once(
                lambda dt: setattr(status_label, 'text', '[ERRO import WebView]\n' + err[:600]), 0
            )

    def check_ready(dt):
        if flask_ready.is_set():
            if flask_error['msg']:
                status_label.text = '[ERRO Flask]\n' + flask_error['msg'][:600]
            else:
                status_label.text = 'Servidor pronto! Abrindo WebView...'
                Clock.schedule_once(open_webview_safe, 0.3)
            return False

    class MainWidget(BoxLayout):
        def __init__(self, **kwargs):
            super().__init__(orientation='vertical', **kwargs)
            self.add_widget(status_label)
            t = threading.Thread(target=flask_thread, daemon=True)
            t.start()
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

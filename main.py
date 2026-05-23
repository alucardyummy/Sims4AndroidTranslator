import threading
from kivy.app import App
from kivy.uix.widget import Widget
from kivy.clock import Clock
from android.runnable import run_on_ui_thread
from jnius import autoclass

WebView        = autoclass('android.webkit.WebView')
WebViewClient  = autoclass('android.webkit.WebViewClient')
LayoutParams   = autoclass('android.view.ViewGroup$LayoutParams')
PythonActivity = autoclass('org.kivy.android.PythonActivity')

URL = "http://127.0.0.1:5000"

def start_flask():
    from app import app
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)

class WebViewWidget(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        t = threading.Thread(target=start_flask, daemon=True)
        t.start()
        Clock.schedule_once(self._open_webview, 1.5)

    @run_on_ui_thread
    def _open_webview(self, dt):
        activity = PythonActivity.mActivity
        wv = WebView(activity)
        settings = wv.getSettings()
        settings.setJavaScriptEnabled(True)
        settings.setDomStorageEnabled(True)
        settings.setAllowFileAccess(True)
        settings.setMixedContentMode(0)
        wv.setWebViewClient(WebViewClient())
        wv.loadUrl(URL)
        layout = activity.getWindow().getDecorView()
        layout.addView(wv, LayoutParams(
            LayoutParams.MATCH_PARENT,
            LayoutParams.MATCH_PARENT
        ))

class Sims4TranslatorApp(App):
    def build(self):
        return WebViewWidget()

if __name__ == '__main__':
    Sims4TranslatorApp().run()

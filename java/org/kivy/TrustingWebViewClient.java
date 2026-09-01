
package org.kivy;

import android.webkit.WebView;

import android.webkit.WebViewClient;

import android.webkit.SslErrorHandler;

import android.net.http.SslError;



public class TrustingWebViewClient extends WebViewClient {

    @Override

    public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {

        handler.proceed();

    }

}


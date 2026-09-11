package de.simpleoffice4me.android;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.res.AssetManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.FrameLayout;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final String LOCAL_URL = "http://127.0.0.1:8765/";
    private static final String RUNTIME_PREFS = "simpleoffice-runtime";
    private static final String RUNTIME_VERSION = "bundle-version";
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private WebView webView;
    private ProgressBar progress;
    private TextView status;
    private Bundle pendingWebState;
    private boolean mainFrameLoadFailed;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        pendingWebState = savedInstanceState;
        buildUi();
        executor.execute(this::prepareAndStartBackend);
    }

    private void buildUi() {
        FrameLayout root = new FrameLayout(this);
        webView = new WebView(this);
        progress = new ProgressBar(this);
        status = new TextView(this);
        status.setText("SimpleOffice4Me wird gestartet …");
        status.setTextSize(18f);
        status.setPadding(32, 32, 32, 32);
        status.setGravity(Gravity.CENTER_HORIZONTAL);
        status.setTextIsSelectable(true);
        progress.setContentDescription("SimpleOffice4Me wird gestartet");

        root.addView(webView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));
        root.addView(status, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT));
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(96, 96);
        progressParams.gravity = Gravity.CENTER;
        root.addView(progress, progressParams);
        setContentView(root);

        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setUserAgentString(settings.getUserAgentString()
                + " SimpleOffice4Me-Android/" + BuildConfig.VERSION_NAME);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            settings.setSafeBrowsingEnabled(true);
        }

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String host = uri.getHost();
                if (("127.0.0.1".equals(host) || "localhost".equals(host)) && uri.getPort() == 8765) {
                    return false;
                }
                String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
                if ("http".equals(scheme) || "https".equals(scheme)
                        || "mailto".equals(scheme) || "tel".equals(scheme)) {
                    try {
                        startActivity(new Intent(Intent.ACTION_VIEW, uri));
                    } catch (ActivityNotFoundException error) {
                        showStatus("Keine passende App für diesen Link gefunden.", false);
                    }
                } else {
                    showStatus("Externer Link mit nicht unterstütztem Schema blockiert: " + scheme, false);
                }
                return true;
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                super.onPageStarted(view, url, favicon);
                if (isLocalUrl(url)) {
                    mainFrameLoadFailed = false;
                    showStatus("SimpleOffice4Me wird geladen …", true);
                }
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                if (isLocalUrl(url) && !mainFrameLoadFailed) {
                    progress.setVisibility(View.GONE);
                    status.setVisibility(View.GONE);
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                super.onReceivedError(view, request, error);
                if (request.isForMainFrame()) {
                    mainFrameLoadFailed = true;
                    showStatus("Seite konnte nicht geladen werden:\n" + error.getDescription(), false);
                }
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse errorResponse) {
                super.onReceivedHttpError(view, request, errorResponse);
                if (request.isForMainFrame() && errorResponse.getStatusCode() >= 500) {
                    mainFrameLoadFailed = true;
                    showStatus("Lokaler Serverfehler: HTTP " + errorResponse.getStatusCode(), false);
                }
            }
        });
    }

    private static boolean isLocalUrl(String value) {
        try {
            Uri uri = Uri.parse(value);
            String host = uri.getHost();
            return ("127.0.0.1".equals(host) || "localhost".equals(host)) && uri.getPort() == 8765;
        } catch (Exception error) {
            return false;
        }
    }

    private void showStatus(String message, boolean showProgress) {
        mainHandler.post(() -> {
            if (status != null) {
                status.setText(message);
                status.setVisibility(View.VISIBLE);
            }
            if (progress != null) progress.setVisibility(showProgress ? View.VISIBLE : View.GONE);
        });
    }

    private void syncRuntimeAssets(File runtimeRoot) throws IOException {
        SharedPreferences preferences = getSharedPreferences(RUNTIME_PREFS, MODE_PRIVATE);
        int installedVersion = preferences.getInt(RUNTIME_VERSION, -1);
        if (installedVersion == BuildConfig.VERSION_CODE && runtimeRoot.isDirectory()) return;
        showStatus("Lokale Programmdateien werden aktualisiert …", true);
        copyAssetTree(getAssets(), "simpleoffice", runtimeRoot);
        preferences.edit().putInt(RUNTIME_VERSION, BuildConfig.VERSION_CODE).apply();
    }

    private void prepareAndStartBackend() {
        try {
            File runtimeRoot = new File(getFilesDir(), "simpleoffice-runtime");
            syncRuntimeAssets(runtimeRoot);

            showStatus("Python-Laufzeit wird gestartet …", true);
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(this));
            }
            Python python = Python.getInstance();
            PyObject module = python.getModule("android_runtime");
            module.callAttr("start", runtimeRoot.getAbsolutePath(), BuildConfig.ERROR_REPORT_URL);

            showStatus("Lokales Backend wird geprüft …", true);
            waitForBackend();
            mainHandler.post(() -> {
                if (webView == null) return;
                Bundle state = pendingWebState;
                pendingWebState = null;
                if (state != null && webView.restoreState(state) != null) {
                    return;
                }
                webView.loadUrl(LOCAL_URL);
            });
        } catch (Exception error) {
            String detail = error.getMessage();
            if (detail == null || detail.trim().isEmpty()) detail = error.getClass().getSimpleName();
            showStatus("Start fehlgeschlagen:\n" + detail, false);
        }
    }

    private void waitForBackend() throws Exception {
        long deadline = System.currentTimeMillis() + 45000;
        Exception lastError = null;
        while (System.currentTimeMillis() < deadline) {
            if (Thread.currentThread().isInterrupted()) {
                throw new InterruptedException("Backend-Prüfung wurde abgebrochen");
            }
            HttpURLConnection connection = null;
            try {
                connection = (HttpURLConnection) new URL(LOCAL_URL).openConnection();
                connection.setConnectTimeout(800);
                connection.setReadTimeout(800);
                connection.setInstanceFollowRedirects(false);
                connection.setUseCaches(false);
                connection.setRequestProperty("Connection", "close");
                int statusCode = connection.getResponseCode();
                if (statusCode >= 200 && statusCode < 500) {
                    return;
                }
                lastError = new IOException("HTTP " + statusCode);
            } catch (Exception error) {
                lastError = error;
            } finally {
                if (connection != null) connection.disconnect();
            }
            Thread.sleep(250);
        }
        String detail = lastError == null ? "" : ": " + lastError.getClass().getSimpleName()
                + (lastError.getMessage() == null ? "" : " - " + lastError.getMessage());
        throw new IllegalStateException("Lokales Python-Backend antwortet nicht" + detail, lastError);
    }

    private static void copyAssetTree(AssetManager assets, String assetPath, File target) throws IOException {
        String[] children = assets.list(assetPath);
        if (children != null && children.length > 0) {
            if (!target.exists() && !target.mkdirs() && !target.isDirectory()) {
                throw new IOException("Verzeichnis kann nicht erstellt werden: " + target);
            }
            for (String child : children) {
                copyAssetTree(assets, assetPath + "/" + child, new File(target, child));
            }
            return;
        }

        File parent = target.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs() && !parent.isDirectory()) {
            throw new IOException("Verzeichnis kann nicht erstellt werden: " + parent);
        }
        try (InputStream input = assets.open(assetPath);
             FileOutputStream output = new FileOutputStream(target, false)) {
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                output.write(buffer, 0, read);
            }
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        if (webView != null) webView.saveState(outState);
        super.onSaveInstanceState(outState);
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.stopLoading();
            webView.removeAllViews();
            webView.destroy();
            webView = null;
        }
        executor.shutdownNow();
        super.onDestroy();
    }
}

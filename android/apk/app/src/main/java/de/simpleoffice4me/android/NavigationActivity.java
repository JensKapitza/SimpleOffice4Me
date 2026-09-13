package de.simpleoffice4me.android;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class NavigationActivity extends MainActivity {
    static final long EXIT_CONFIRM_WINDOW_MS = 1800L;
    private static final String REMOTE_BUILD_GRADLE =
            "https://raw.githubusercontent.com/JensKapitza/SimpleOffice4Me/main/android/apk/app/build.gradle";
    private static final Pattern VERSION_CODE_PATTERN = Pattern.compile("\\bversionCode\\s+(\\d+)\\b");
    private static final Pattern VERSION_NAME_PATTERN = Pattern.compile("\\bversionName\\s+['\"]([^'\"]+)['\"]");

    private final ExecutorService navigationExecutor = Executors.newSingleThreadExecutor();
    private long lastBackPressAt;
    private ConnectivityManager connectivityManager;
    private ConnectivityManager.NetworkCallback networkCallback;
    private volatile boolean internetAvailable;
    private volatile String availableUpdateVersion;
    private volatile int availableUpdateCode;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        registerNetworkMonitor();
        navigationExecutor.execute(this::checkForUpdate);
    }

    @Override
    protected void onResume() {
        super.onResume();
        publishNetworkState();
        publishUpdateState();
    }

    @Override
    public void onBackPressed() {
        long now = SystemClock.elapsedRealtime();
        if (lastBackPressAt > 0L && now - lastBackPressAt <= EXIT_CONFIRM_WINDOW_MS) {
            lastBackPressAt = 0L;
            finishAndRemoveTask();
            return;
        }

        lastBackPressAt = now;
        WebView webView = findWebView(getWindow().getDecorView());
        if (webView != null && webView.canGoBack()) webView.goBack();
        Toast.makeText(this, "Noch einmal Zurück schließt SimpleOffice.", Toast.LENGTH_SHORT).show();
    }

    private void registerNetworkMonitor() {
        connectivityManager = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        if (connectivityManager == null) return;
        networkCallback = new ConnectivityManager.NetworkCallback() {
            @Override
            public void onAvailable(Network network) {
                refreshNetworkState();
            }

            @Override
            public void onLost(Network network) {
                refreshNetworkState();
            }

            @Override
            public void onCapabilitiesChanged(Network network, NetworkCapabilities capabilities) {
                refreshNetworkState();
            }
        };
        try {
            connectivityManager.registerDefaultNetworkCallback(networkCallback);
        } catch (RuntimeException ignored) {
        }
        refreshNetworkState();
    }

    private void refreshNetworkState() {
        ConnectivityManager manager = connectivityManager;
        if (manager == null) return;
        boolean available = false;
        try {
            Network network = manager.getActiveNetwork();
            NetworkCapabilities capabilities = network == null ? null : manager.getNetworkCapabilities(network);
            available = capabilities != null
                    && capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                    && capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED);
        } catch (RuntimeException ignored) {
        }
        internetAvailable = available;
        runOnUiThread(this::publishNetworkState);
    }

    private void publishNetworkState() {
        WebView webView = findWebView(getWindow().getDecorView());
        if (webView == null || webView.getUrl() == null) return;
        String script = "(function(online){"
                + "let box=document.getElementById('android-network-state');"
                + "if(online){if(box)box.remove();document.documentElement.dataset.soNetwork='online';return;}"
                + "document.documentElement.dataset.soNetwork='offline';"
                + "const root=document.getElementById('main-content')||document.querySelector('main')||document.body;if(!root)return;"
                + "if(!box){box=document.createElement('div');box.id='android-network-state';box.className='alert alert-warning py-2';"
                + "box.setAttribute('role','status');root.insertBefore(box,root.firstChild);}"
                + "box.textContent='Keine Internetverbindung. Lokale SimpleOffice-Funktionen bleiben verfügbar.';"
                + "})(" + (internetAvailable ? "true" : "false") + ");";
        webView.evaluateJavascript(script, null);
    }

    private void checkForUpdate() {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(REMOTE_BUILD_GRADLE).openConnection();
            connection.setConnectTimeout(4000);
            connection.setReadTimeout(4000);
            connection.setInstanceFollowRedirects(false);
            connection.setUseCaches(false);
            connection.setRequestProperty("Accept", "text/plain");
            connection.setRequestProperty("User-Agent", "SimpleOffice4Me-Android/" + BuildConfig.VERSION_NAME);
            if (connection.getResponseCode() != HttpURLConnection.HTTP_OK) return;
            StringBuilder body = new StringBuilder();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                    connection.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null && body.length() < 65536) {
                    body.append(line).append('\n');
                }
            }
            Matcher codeMatcher = VERSION_CODE_PATTERN.matcher(body);
            if (!codeMatcher.find()) return;
            int remoteCode = Integer.parseInt(codeMatcher.group(1));
            if (remoteCode <= BuildConfig.VERSION_CODE) return;
            Matcher nameMatcher = VERSION_NAME_PATTERN.matcher(body);
            availableUpdateVersion = nameMatcher.find() ? nameMatcher.group(1) : String.valueOf(remoteCode);
            availableUpdateCode = remoteCode;
            runOnUiThread(this::publishUpdateState);
        } catch (Exception ignored) {
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private void publishUpdateState() {
        if (availableUpdateCode <= BuildConfig.VERSION_CODE) return;
        WebView webView = findWebView(getWindow().getDecorView());
        if (webView == null || webView.getUrl() == null) return;
        String version = JSONObject.quote(availableUpdateVersion == null ? String.valueOf(availableUpdateCode) : availableUpdateVersion);
        String script = "(function(version){"
                + "const root=document.getElementById('main-content')||document.querySelector('main')||document.body;if(!root)return;"
                + "let box=document.getElementById('android-update-state');if(box)return;"
                + "box=document.createElement('div');box.id='android-update-state';box.className='alert alert-info d-flex flex-wrap align-items-center justify-content-between gap-2';"
                + "const text=document.createElement('span');text.textContent='Eine neuere SimpleOffice-APK ist verfügbar: '+version;"
                + "const link=document.createElement('a');link.className='btn btn-sm btn-outline-primary';link.href='https://github.com/JensKapitza/SimpleOffice4Me';link.textContent='Projekt öffnen';"
                + "box.append(text,link);root.insertBefore(box,root.firstChild);"
                + "})(" + version + ");";
        webView.evaluateJavascript(script, null);
    }

    private static WebView findWebView(View view) {
        if (view instanceof WebView) return (WebView) view;
        if (!(view instanceof ViewGroup)) return null;
        ViewGroup group = (ViewGroup) view;
        for (int index = 0; index < group.getChildCount(); index++) {
            WebView result = findWebView(group.getChildAt(index));
            if (result != null) return result;
        }
        return null;
    }

    @Override
    protected void onDestroy() {
        ConnectivityManager manager = connectivityManager;
        ConnectivityManager.NetworkCallback callback = networkCallback;
        networkCallback = null;
        connectivityManager = null;
        if (manager != null && callback != null) {
            try {
                manager.unregisterNetworkCallback(callback);
            } catch (RuntimeException ignored) {
            }
        }
        navigationExecutor.shutdownNow();
        super.onDestroy();
    }
}

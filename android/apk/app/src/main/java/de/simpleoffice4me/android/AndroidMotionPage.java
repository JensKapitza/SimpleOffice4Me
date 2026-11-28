package de.simpleoffice4me.android;

import android.app.Activity;
import android.os.Handler;
import android.os.Looper;
import android.webkit.CookieManager;
import android.webkit.WebView;
import org.json.JSONObject;
import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.Proxy;
import java.net.URL;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.function.BooleanSupplier;

/** Dedicated bridge for a signed-in bundled Data Logger page, not a general sensor API. */
final class AndroidMotionPage {
    static final String URL = "http://127.0.0.1:8765/datalogger/android-motion";
    private final AndroidMotionCapture capture;
    private final BooleanSupplier enabled;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ThreadPoolExecutor checks =
            new ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, new ArrayBlockingQueue<>(1));
    private WebView view;
    private String nonce = "";
    private Request active;

    private record Request(String nonce, String kind, String csrf, String cookie, String agent) {}

    AndroidMotionPage(Activity activity, BooleanSupplier enabled) {
        capture = new AndroidMotionCapture(activity);
        this.enabled = enabled;
    }

    void bind(WebView web, String current, String bridgeToken) {
        if (!URL.equals(current) || !URL.equals(web.getUrl()) || !enabled.getAsBoolean()) return;
        clear();
        view = web;
        nonce = UUID.randomUUID().toString();
        script("window.SimpleOfficeAndroidMotion?.bind(" + JSONObject.quote(bridgeToken)
                + "," + JSONObject.quote(nonce)
                + "," + capture.available(AndroidMotionSample.Kind.ACCELERATION)
                + "," + capture.available(AndroidMotionSample.Kind.ANGULAR_SPEED) + ");");
    }

    void requested(String selectedNonce, String key, String csrf) {
        if (!current(selectedNonce) || !AndroidDeviceSourcePreview.isCsrf(csrf)) return;
        AndroidMotionSample.Kind kind = switch (key == null ? "" : key) {
            case "acceleration" -> AndroidMotionSample.Kind.ACCELERATION;
            case "angular_speed" -> AndroidMotionSample.Kind.ANGULAR_SPEED;
            default -> null;
        };
        if (kind == null || active != null) return;
        String cookie = CookieManager.getInstance().getCookie(URL);
        if (cookie == null || cookie.isEmpty()) {
            publish(key, "blocked", null);
            return;
        }
        Request req = new Request(nonce, key, csrf, cookie, view.getSettings().getUserAgentString());
        active = req;
        try {
            checks.execute(() -> {
                boolean ok = authorized(req);
                main.post(() -> {
                    if (!current(req)) return;
                    if (!ok) { finish(req, "blocked", null); return; }
                    capture.capture(kind, () -> current(req), result -> finish(req,
                            result.status().name().toLowerCase(java.util.Locale.ROOT), result.sample()));
                });
            });
        } catch (RejectedExecutionException unavailable) {
            finish(req, "unavailable", null);
        }
    }

    private static boolean authorized(Request req) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(URL + "/authorize").openConnection(Proxy.NO_PROXY);
            connection.setRequestMethod("POST");
            connection.setDoOutput(true);
            connection.setFixedLengthStreamingMode(0);
            connection.setConnectTimeout(2000);
            connection.setReadTimeout(2000);
            connection.setInstanceFollowRedirects(false);
            connection.setUseCaches(false);
            connection.setRequestProperty("Cookie", req.cookie());
            connection.setRequestProperty("User-Agent", req.agent());
            connection.setRequestProperty("X-CSRF-Token", req.csrf());
            try (var ignored = connection.getOutputStream()) {
                // Empty preflight: no motion value goes to this route.
            }
            return connection.getResponseCode() == 204;
        } catch (IOException | RuntimeException failure) {
            return false;
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private boolean current(String candidate) {
        return candidate != null && !nonce.isEmpty() && nonce.equals(candidate)
                && enabled.getAsBoolean() && view != null && URL.equals(view.getUrl());
    }

    private boolean current(Request req) {
        return active == req && current(req.nonce()) && req.cookie().equals(CookieManager.getInstance().getCookie(URL));
    }

    private void finish(Request req, String status, AndroidMotionSample sample) {
        if (!current(req)) return;
        active = null;
        publish(req.kind(), status, sample);
    }

    private void publish(String key, String status, AndroidMotionSample sample) {
        script("window.SimpleOfficeAndroidMotion?.result(" + JSONObject.quote(nonce)
                + "," + JSONObject.quote(key) + "," + JSONObject.quote(status)
                + "," + (sample == null ? "null" : Double.toString(sample.magnitude())) + ");");
    }

    private void script(String statement) {
        if (view != null && URL.equals(view.getUrl())) {
            view.evaluateJavascript("if(location.href===" + JSONObject.quote(URL) + "){" + statement + "}", null);
        }
    }

    void clear() {
        if (!nonce.isEmpty()) script("window.SimpleOfficeAndroidMotion?.clear();");
        nonce = "";
        active = null;
        checks.getQueue().clear();
        capture.cancel();
        view = null;
    }

    void close() {
        clear();
        checks.shutdownNow();
    }
}

package de.simpleoffice4me.android;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Handler;
import android.webkit.CookieManager;
import android.webkit.WebMessage;
import android.webkit.WebMessagePort;
import android.webkit.WebView;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.Proxy;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/** Archive-only main-frame channel. The native network connection carries dates, never SMS. */
final class AndroidSmsImportController {
    static final int PERMISSION_REQUEST = 726;
    private static final String ORIGIN = "http://127.0.0.1:8765";
    private static final String PAGE = SmsShareText.URL;
    private static final String OFFER = "simpleoffice:sms-device-import";
    private final Activity activity;
    private final Handler main;
    private final ExecutorService worker =
            new ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, new ArrayBlockingQueue<>(1));
    private WebView view;
    private WebMessagePort port;
    private AlertDialog dialog;
    private Request pending;
    private String lease = "";
    private boolean resumed;
    private boolean closed;
    private boolean loadFailed;
    private boolean asking;
    private boolean permissionReturned;

    private record Request(String csrf, String from, String through, String stage, String preview,
                           String cookie, String userAgent) {}

    AndroidSmsImportController(Activity activity, Handler main) {
        this.activity = activity;
        this.main = main;
    }

    void onLoadStarted() {
        reset();
        loadFailed = false;
    }

    void onLoadFailed() {
        loadFailed = true;
        reset();
    }

    void onHistory(String url) {
        if (!archivePage(url)) reset();
    }

    void onLoadFinished(WebView current, String url) {
        if (!BuildConfig.DEVICE_SMS_IMPORT || closed || loadFailed || !archivePage(url)
                || !url.equals(current.getUrl()) || port != null) return;
        WebMessagePort[] pair = null;
        try {
            pair = current.createWebMessageChannel();
            view = current;
            port = pair[0];
            port.setWebMessageCallback(new WebMessagePort.WebMessageCallback() {
                @Override public void onMessage(WebMessagePort sender, WebMessage message) {
                    begin(sender, message.getData());
                }
            }, main);
            current.postWebMessage(new WebMessage(OFFER, new WebMessagePort[]{pair[1]}), Uri.parse(ORIGIN));
        } catch (RuntimeException unavailable) {
            reset();
            if (pair != null) closePort(pair[1]);
        }
    }

    private void begin(WebMessagePort sender, String raw) {
        if (sender != port || !resumed || !currentPage() || asking || pending != null || dialog != null) return;
        if (raw == null || raw.length() > 440) return;
        Request request;
        try {
            JSONObject message = new JSONObject(raw);
            String csrf = message.getString("csrf");
            String from = message.getString("from");
            String through = message.getString("through");
            String stage = message.getString("stage");
            String preview = message.getString("preview");
            if (message.length() != 5 || !AndroidDeviceSourcePreview.isCsrf(csrf)
                    || !date(from) || !date(through)
                    || !(("preview".equals(stage) && preview.isEmpty())
                    || ("commit".equals(stage) && preview.matches("[a-f0-9]{32}")))) return;
            String cookie = CookieManager.getInstance().getCookie(PAGE);
            if (cookie == null || cookie.isEmpty()) {
                send(state("unauthorized"));
                return;
            }
            request = new Request(csrf, from, through, stage, preview, cookie, view.getSettings().getUserAgentString());
        } catch (JSONException invalid) {
            return;
        }

        boolean commit = "commit".equals(request.stage);
        AlertDialog confirmation = new AlertDialog.Builder(activity)
                .setTitle(R.string.sms_device_import_title)
                .setMessage(commit ? R.string.sms_device_import_confirm : R.string.sms_device_preview_confirm)
                .setNegativeButton(android.R.string.cancel, (ignored, which) -> send(state("cancelled")))
                .setPositiveButton(commit ? R.string.sms_device_import_action : R.string.sms_device_preview_action, (ignored, which) -> confirmed(request))
                .create();
        dialog = confirmation;
        confirmation.setOnCancelListener(ignored -> send(state("cancelled")));
        confirmation.setOnDismissListener(ignored -> {
            if (dialog == confirmation) dialog = null;
        });
        confirmation.show();
    }

    private void confirmed(Request request) {
        if (!resumed || !current(request)) {
            send(state("cancelled"));
            return;
        }
        pending = request;
        if (permitted()) {
            start(request);
            return;
        }
        asking = true;
        try {
            activity.requestPermissions(new String[]{Manifest.permission.READ_SMS}, PERMISSION_REQUEST);
        } catch (RuntimeException unavailable) {
            asking = false;
            complete(request, state("denied"));
        }
    }

    void onPermissionResult() {
        if (!asking) return;
        asking = false;
        permissionReturned = true;
        resumeAfterPermission();
    }

    private void resumeAfterPermission() {
        if (!permissionReturned || !resumed) return;
        permissionReturned = false;
        Request request = pending;
        if (request != null && current(request) && permitted()) start(request);
        else if (request != null) complete(request, state("denied"));
    }

    private boolean permitted() {
        return activity.checkSelfPermission(Manifest.permission.READ_SMS) == PackageManager.PERMISSION_GRANTED;
    }

    private void start(Request request) {
        String token = AndroidSmsDeviceImport.begin();
        if (token.isEmpty()) {
            complete(request, state("busy"));
            return;
        }
        lease = token;
        try {
            worker.execute(() -> {
                String result;
                try {
                    result = post(request, token);
                } finally {
                    AndroidSmsDeviceImport.finish(token);
                }
                main.post(() -> complete(request, result));
            });
        } catch (RejectedExecutionException unavailable) {
            AndroidSmsDeviceImport.finish(token);
            complete(request, state("unavailable"));
        }
    }

    private static String post(Request request, String token) {
        HttpURLConnection connection = null;
        try {
            byte[] body = new JSONObject().put("from", request.from).put("through", request.through)
                    .put("stage", request.stage).put("preview", request.preview)
                    .toString().getBytes(StandardCharsets.UTF_8);
            connection = (HttpURLConnection) URI.create(PAGE + "/device-import").toURL().openConnection(Proxy.NO_PROXY);
            connection.setRequestMethod("POST");
            connection.setInstanceFollowRedirects(false);
            connection.setUseCaches(false);
            connection.setConnectTimeout(2_000);
            connection.setReadTimeout(20_000);
            connection.setDoOutput(true);
            connection.setFixedLengthStreamingMode(body.length);
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setRequestProperty("Cookie", request.cookie);
            connection.setRequestProperty("X-CSRF-Token", request.csrf);
            connection.setRequestProperty("X-SimpleOffice-SMS-Import", token);
            connection.setRequestProperty("User-Agent", request.userAgent);
            try (OutputStream stream = connection.getOutputStream()) {
                stream.write(body);
            }
            int status = connection.getResponseCode();
            if (status == 401 || status == 403 || status == 404 || (status >= 300 && status < 400)) {
                return state("unauthorized");
            }
            try (InputStream stream = status == 200 ? connection.getInputStream() : connection.getErrorStream()) {
                if (stream == null) return state("unavailable");
                return readStatus(stream);
            }
        } catch (IOException | JSONException | RuntimeException unavailable) {
            return state("unavailable");
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private static String readStatus(InputStream input) throws IOException, JSONException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] bytes = new byte[256];
        int count;
        while ((count = input.read(bytes)) != -1) {
            if (buffer.size() + count > 512) return state("unavailable");
            buffer.write(bytes, 0, count);
        }
        JSONObject result = new JSONObject(new String(buffer.toByteArray(), StandardCharsets.UTF_8));
        String state = result.getString("state");
        switch (state) {
            case "ok":
            case "preview":
                int added = result.getInt("added");
                int already = result.getInt("already");
                if (added < 0 || added > 1_000 || already < 0 || already > 1_000 - added) break;
                JSONObject output = new JSONObject().put("state", state).put("added", added).put("already", already);
                if ("preview".equals(state)) {
                    String ticket = result.getString("preview");
                    if (!ticket.matches("[a-f0-9]{32}")) break;
                    output.put("preview", ticket);
                }
                return output.toString();
            case "preview_changed":
            case "preview_expired":
            case "invalid_range":
            case "denied":
            case "unavailable":
            case "too_many":
            case "invalid_rows":
            case "cancelled":
            case "conflict":
                return state(state);
            default:
                break;
        }
        return state("unavailable");
    }

    private static String state(String state) {
        return "{\"state\":\"" + state + "\"}";
    }

    private static boolean date(String value) {
        return value.isEmpty() || value.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}");
    }

    private static boolean archivePage(String url) {
        return PAGE.equals(url) || (PAGE + "#sms-manual").equals(url) || (PAGE + "#sms-restore").equals(url);
    }

    private boolean currentPage() {
        return !closed && port != null && view != null && archivePage(view.getUrl())
                && !activity.isFinishing() && !activity.isDestroyed();
    }

    private boolean current(Request request) {
        return currentPage() && request.cookie.equals(CookieManager.getInstance().getCookie(PAGE));
    }

    private void complete(Request request, String result) {
        if (request != pending) return;
        pending = null;
        lease = "";
        if (current(request)) send(result);
    }

    void setResumed(boolean value) {
        resumed = value;
        if (resumed) {
            resumeAfterPermission();
        } else if (!asking && !permissionReturned && pending != null) {
            AndroidSmsDeviceImport.finish(lease);
            complete(pending, state("cancelled"));
        } else if (dialog != null) {
            dialog.cancel();
        }
    }

    private void send(String state) {
        if (!currentPage()) return;
        try {
            port.postMessage(new WebMessage(state));
        } catch (RuntimeException unavailable) {
            reset();
        }
    }

    private void reset() {
        AndroidSmsDeviceImport.finish(lease);
        lease = "";
        pending = null;
        asking = false;
        permissionReturned = false;
        if (dialog != null) {
            AlertDialog stale = dialog;
            dialog = null;
            stale.dismiss();
        }
        if (port != null) closePort(port);
        port = null;
        view = null;
    }

    private static void closePort(WebMessagePort channel) {
        try {
            channel.close();
        } catch (IllegalStateException alreadyClosed) {
            // Android WebView may already have consumed or disposed the endpoint.
        }
    }

    void close() {
        closed = true;
        reset();
        worker.shutdownNow();
    }
}

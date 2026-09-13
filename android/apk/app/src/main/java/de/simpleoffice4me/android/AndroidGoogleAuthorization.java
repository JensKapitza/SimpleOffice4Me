package de.simpleoffice4me.android;

import android.app.Activity;
import android.app.PendingIntent;
import android.content.Intent;
import android.content.IntentSender;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.webkit.CookieManager;

import com.google.android.gms.auth.api.identity.AuthorizationClient;
import com.google.android.gms.auth.api.identity.AuthorizationRequest;
import com.google.android.gms.auth.api.identity.AuthorizationResult;
import com.google.android.gms.auth.api.identity.Identity;
import com.google.android.gms.common.api.ApiException;
import com.google.android.gms.common.api.Scope;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Native Google Drive authorization for the embedded Android runtime. */
final class AndroidGoogleAuthorization {
    static final int REQUEST_CODE = 705;
    static final String DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file";
    private static final String TOKEN_ENDPOINT = "http://127.0.0.1:8765/settings/google-drive/android-token";
    private static final int MAX_RESPONSE_BYTES = 64 * 1024;

    interface ResultCallback {
        void onResult(String action, String status);
    }

    private final Activity activity;
    private final AuthorizationClient client;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final ResultCallback callback;
    private String pendingAction = "";
    private String pendingCsrf = "";
    private boolean pending;

    AndroidGoogleAuthorization(Activity activity, ResultCallback callback) {
        this.activity = activity;
        this.client = Identity.getAuthorizationClient(activity);
        this.callback = callback;
    }

    String authorize(String action, String csrfToken) {
        String normalizedAction = String.valueOf(action == null ? "" : action).trim().toLowerCase(Locale.ROOT);
        String csrf = String.valueOf(csrfToken == null ? "" : csrfToken).trim();
        if (!("connect".equals(normalizedAction) || "sync".equals(normalizedAction))) return "invalid";
        if (csrf.length() < 32 || csrf.length() > 256) return "invalid";
        if (pending) return "busy";
        pending = true;
        pendingAction = normalizedAction;
        pendingCsrf = csrf;
        AuthorizationRequest request = AuthorizationRequest.builder()
                .setRequestedScopes(Arrays.asList(new Scope(DRIVE_SCOPE)))
                .build();
        client.authorize(request)
                .addOnSuccessListener(this::handleInitialResult)
                .addOnFailureListener(error -> finish("error"));
        return "ok";
    }

    boolean onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode != REQUEST_CODE) return false;
        if (!pending) return true;
        if (resultCode != Activity.RESULT_OK || data == null) {
            finish("cancelled");
            return true;
        }
        try {
            handleAuthorizedResult(client.getAuthorizationResultFromIntent(data));
        } catch (ApiException error) {
            finish("error");
        }
        return true;
    }

    void close() {
        pending = false;
        pendingAction = "";
        pendingCsrf = "";
        executor.shutdownNow();
    }

    private void handleInitialResult(AuthorizationResult result) {
        if (!pending) return;
        if (result.hasResolution()) {
            PendingIntent resolution = result.getPendingIntent();
            if (resolution == null) {
                finish("error");
                return;
            }
            try {
                activity.startIntentSenderForResult(
                        resolution.getIntentSender(), REQUEST_CODE, null, 0, 0, 0);
            } catch (IntentSender.SendIntentException error) {
                finish("error");
            }
            return;
        }
        handleAuthorizedResult(result);
    }

    private void handleAuthorizedResult(AuthorizationResult result) {
        if (!pending || result == null) return;
        String accessToken = String.valueOf(result.getAccessToken() == null ? "" : result.getAccessToken()).trim();
        List<String> granted = result.getGrantedScopes() == null
                ? new ArrayList<>() : new ArrayList<>(result.getGrantedScopes());
        if (accessToken.isEmpty() || !granted.contains(DRIVE_SCOPE)) {
            finish("denied");
            return;
        }
        String action = pendingAction;
        String csrf = pendingCsrf;
        String cookie = CookieManager.getInstance().getCookie(TOKEN_ENDPOINT);
        executor.execute(() -> postToken(action, csrf, accessToken, granted, cookie == null ? "" : cookie));
    }

    private void postToken(String action, String csrf, String accessToken, List<String> scopes, String cookie) {
        HttpURLConnection connection = null;
        try {
            JSONObject payload = new JSONObject();
            payload.put("action", action);
            payload.put("access_token", accessToken);
            payload.put("scopes", new JSONArray(scopes));
            byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
            URL endpoint = new URL(TOKEN_ENDPOINT);
            if (!"127.0.0.1".equals(endpoint.getHost()) || endpoint.getPort() != 8765) {
                throw new IOException("invalid local token endpoint");
            }
            connection = (HttpURLConnection) endpoint.openConnection();
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(10_000);
            connection.setReadTimeout(30_000);
            connection.setInstanceFollowRedirects(false);
            connection.setUseCaches(false);
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("X-CSRF-Token", csrf);
            connection.setRequestProperty("User-Agent", "SimpleOffice4Me-Android/" + BuildConfig.VERSION_NAME);
            if (!cookie.isEmpty()) connection.setRequestProperty("Cookie", cookie);
            try (OutputStream output = connection.getOutputStream()) {
                output.write(body);
                output.flush();
            }
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) throw new IOException("token handoff rejected");
            try (InputStream input = connection.getInputStream()) {
                byte[] response = readBounded(input, MAX_RESPONSE_BYTES);
                JSONObject result = new JSONObject(new String(response, StandardCharsets.UTF_8));
                if (!result.optBoolean("ok", false)) throw new IOException("token handoff failed");
            }
            finish("sync".equals(action) ? "synced" : "connected");
        } catch (Exception error) {
            finish("error");
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private static byte[] readBounded(InputStream input, int maximum) throws IOException {
        java.io.ByteArrayOutputStream output = new java.io.ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int read;
        while ((read = input.read(buffer)) != -1) {
            if (output.size() + read > maximum) throw new IOException("authorization response too large");
            output.write(buffer, 0, read);
        }
        return output.toByteArray();
    }

    private void finish(String status) {
        String action = pendingAction;
        pending = false;
        pendingAction = "";
        pendingCsrf = "";
        mainHandler.post(() -> callback.onResult(action, status));
    }
}

package de.simpleoffice4me.android;

import android.accounts.AccountManager;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.res.AssetManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputFilter;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.webkit.CookieManager;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Native Android entry point.
 *
 * The embedded Flask application is local to this APK, so normal startup must
 * not expose the browser login page. On first launch Android's own account
 * chooser supplies a Google account identity. The account is then provisioned
 * locally and a Flask session cookie is created before NavigationActivity opens
 * the WebView. Password protection is optional and, when enabled, is requested
 * with a native password dialog rather than an HTML login form.
 */
public final class BootstrapActivity extends Activity {
    private static final String LOCAL_URL = "http://127.0.0.1:8765/";
    private static final String RUNTIME_PREFS = "simpleoffice-runtime";
    private static final String RUNTIME_VERSION = "bundle-version";
    private static final String IDENTITY_PREFS = "simpleoffice-android-identity";
    private static final String IDENTITY_READY = "identity-ready";
    private static final String IDENTITY_EMAIL = "google-email";
    private static final int GOOGLE_ACCOUNT_REQUEST = 706;
    private static final int MAX_LOCAL_RESPONSE = 64 * 1024;

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private Intent originalIntent;
    private TextView status;
    private ProgressBar progress;
    private volatile boolean destroyed;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        originalIntent = new Intent(getIntent());
        buildSplash();

        SharedPreferences identity = getSharedPreferences(IDENTITY_PREFS, MODE_PRIVATE);
        if (identity.getBoolean(IDENTITY_READY, false)) {
            beginNativeStartup(identity.getString(IDENTITY_EMAIL, ""));
        } else {
            chooseGoogleAccount();
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        originalIntent = new Intent(intent);
    }

    private void buildSplash() {
        FrameLayout root = new FrameLayout(this);
        status = new TextView(this);
        status.setText("SimpleOffice4Me wird lokal gestartet …");
        status.setTextSize(18f);
        status.setPadding(32, 48, 32, 32);
        status.setGravity(Gravity.CENTER_HORIZONTAL);
        progress = new ProgressBar(this);
        progress.setContentDescription("SimpleOffice4Me wird gestartet");
        root.addView(status, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT));
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(96, 96);
        progressParams.gravity = Gravity.CENTER;
        root.addView(progress, progressParams);
        setContentView(root);
    }

    private void chooseGoogleAccount() {
        showStatus("Android-Konto wird übernommen …", true);
        try {
            Intent chooser = AccountManager.newChooseAccountIntent(
                    null,
                    null,
                    new String[]{"com.google"},
                    "Google-Konto für SimpleOffice4Me",
                    null,
                    null,
                    null);
            startActivityForResult(chooser, GOOGLE_ACCOUNT_REQUEST);
        } catch (RuntimeException error) {
            rememberIdentity("");
            beginNativeStartup("");
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode != GOOGLE_ACCOUNT_REQUEST) {
            super.onActivityResult(requestCode, resultCode, data);
            return;
        }
        String email = "";
        if (resultCode == RESULT_OK && data != null) {
            String type = data.getStringExtra(AccountManager.KEY_ACCOUNT_TYPE);
            String name = data.getStringExtra(AccountManager.KEY_ACCOUNT_NAME);
            if ("com.google".equals(type) && looksLikeEmail(name)) {
                email = name.trim().toLowerCase(Locale.ROOT);
            }
        }
        rememberIdentity(email);
        beginNativeStartup(email);
    }

    private static boolean looksLikeEmail(String value) {
        if (value == null) return false;
        String clean = value.trim();
        if (clean.length() > 320 || clean.indexOf('@') <= 0
                || clean.indexOf('@') != clean.lastIndexOf('@')) return false;
        for (int index = 0; index < clean.length(); index++) {
            if (Character.isWhitespace(clean.charAt(index))) return false;
        }
        return true;
    }

    private void rememberIdentity(String email) {
        getSharedPreferences(IDENTITY_PREFS, MODE_PRIVATE)
                .edit()
                .putBoolean(IDENTITY_READY, true)
                .putString(IDENTITY_EMAIL, email == null ? "" : email)
                .apply();
    }

    private void beginNativeStartup(String accountEmail) {
        clearPreviousWebSession();
        executor.execute(() -> {
            try {
                File runtimeRoot = new File(getFilesDir(), "simpleoffice-runtime");
                syncRuntimeAssets(runtimeRoot);
                showStatus("Lokale Python-Laufzeit wird gestartet …", true);
                if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
                String bootstrapToken = UUID.randomUUID().toString() + UUID.randomUUID();
                PyObject module = Python.getInstance().getModule("android_runtime");
                module.callAttr(
                        "start",
                        runtimeRoot.getAbsolutePath(),
                        BuildConfig.ERROR_REPORT_URL,
                        accountEmail == null ? "" : accountEmail,
                        bootstrapToken);
                showStatus("Lokales Benutzerkonto wird vorbereitet …", true);
                waitForBackend();
                establishNativeSession(bootstrapToken);
            } catch (Exception error) {
                showFatal(error);
            }
        });
    }

    private void establishNativeSession(String bootstrapToken) throws Exception {
        NativeResponse challenge = requestLocal(
                "GET", "/auth/android/challenge", null, bootstrapToken, "", "");
        if (challenge.status != 200 || !challenge.json.optBoolean("ok")) {
            throw new IOException("Android-Sitzung konnte nicht vorbereitet werden (HTTP " + challenge.status + ")");
        }
        String csrf = challenge.json.optString("csrf_token", "");
        if (csrf.length() < 32) throw new IOException("Android-Sitzung lieferte kein CSRF-Token");
        String challengeCookie = cookieHeader(challenge.setCookies);

        NativeResponse bootstrap = requestLocal(
                "POST", "/auth/android/bootstrap", "{}", bootstrapToken, csrf, challengeCookie);
        List<String> cookies = new ArrayList<>(challenge.setCookies);
        cookies.addAll(bootstrap.setCookies);
        if (bootstrap.status != 200 || !bootstrap.json.optBoolean("ok")) {
            String error = bootstrap.json.optString("error", "bootstrap_failed");
            throw new IOException("Lokales Android-Konto konnte nicht geöffnet werden: " + error);
        }
        if (bootstrap.json.optBoolean("password_required")) {
            mainHandler.post(() -> showPasswordDialog(bootstrapToken, csrf, challengeCookie, cookies));
            return;
        }
        applyCookiesAndOpen(cookies);
    }

    private void showPasswordDialog(
            String bootstrapToken,
            String csrf,
            String sessionCookie,
            List<String> existingCookies) {
        if (destroyed) return;
        EditText password = new EditText(this);
        password.setSingleLine(true);
        password.setHint("App-Passwort");
        password.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        password.setFilters(new InputFilter[]{new InputFilter.LengthFilter(128)});

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("SimpleOffice4Me entsperren")
                .setMessage("Für diese lokale App wurde ein Passwortschutz aktiviert.")
                .setView(password)
                .setPositiveButton("Entsperren", null)
                .setNegativeButton("Beenden", (ignored, which) -> finishAndRemoveTask())
                .setCancelable(false)
                .create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
            String value = password.getText().toString();
            if (value.isEmpty()) {
                password.setError("Passwort eingeben");
                return;
            }
            View button = dialog.getButton(AlertDialog.BUTTON_POSITIVE);
            button.setEnabled(false);
            executor.execute(() -> {
                try {
                    JSONObject body = new JSONObject().put("password", value);
                    NativeResponse unlocked = requestLocal(
                            "POST", "/auth/android/unlock", body.toString(),
                            bootstrapToken, csrf, sessionCookie);
                    if (unlocked.status == 200 && unlocked.json.optBoolean("ok")) {
                        List<String> cookies = new ArrayList<>(existingCookies);
                        cookies.addAll(unlocked.setCookies);
                        mainHandler.post(() -> {
                            if (dialog.isShowing()) dialog.dismiss();
                            applyCookiesAndOpen(cookies);
                        });
                        return;
                    }
                    String error = unlocked.json.optString("error", "invalid_password");
                    mainHandler.post(() -> {
                        button.setEnabled(true);
                        password.setText("");
                        if ("throttled".equals(error)) {
                            password.setError("Zu viele Versuche. Bitte später erneut versuchen.");
                        } else {
                            password.setError("Passwort ist falsch.");
                        }
                        password.requestFocus();
                    });
                } catch (Exception error) {
                    mainHandler.post(() -> {
                        button.setEnabled(true);
                        password.setError("Entsperren ist fehlgeschlagen.");
                    });
                }
            });
        }));
        dialog.setOnDismissListener(ignored -> password.setText(""));
        dialog.show();
        password.requestFocus();
    }

    private void clearPreviousWebSession() {
        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setCookie(LOCAL_URL, "session=; Max-Age=0; Path=/; SameSite=Lax");
        cookies.flush();
    }

    private void applyCookiesAndOpen(List<String> cookies) {
        mainHandler.post(() -> {
            if (destroyed) return;
            CookieManager manager = CookieManager.getInstance();
            manager.setAcceptCookie(true);
            for (String cookie : cookies) {
                if (cookie != null && !cookie.trim().isEmpty()) manager.setCookie(LOCAL_URL, cookie);
            }
            manager.flush();
            startActivity(forwardedIntent());
            finish();
        });
    }

    private Intent forwardedIntent() {
        Intent source = originalIntent == null ? new Intent() : originalIntent;
        Intent target = new Intent(this, NavigationActivity.class);
        target.setAction(source.getAction());
        if (source.getType() != null) target.setDataAndType(source.getData(), source.getType());
        else target.setData(source.getData());
        Bundle extras = source.getExtras();
        if (extras != null) target.putExtras(extras);
        ClipData clipData = source.getClipData();
        if (clipData != null) target.setClipData(clipData);
        int grantFlags = source.getFlags() & (
                Intent.FLAG_GRANT_READ_URI_PERMISSION
                        | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                        | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
                        | Intent.FLAG_GRANT_PREFIX_URI_PERMISSION);
        target.addFlags(grantFlags);
        return target;
    }

    private NativeResponse requestLocal(
            String method,
            String path,
            String body,
            String bootstrapToken,
            String csrf,
            String cookie) throws Exception {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(LOCAL_URL + path.substring(1)).openConnection();
            connection.setConnectTimeout(3000);
            connection.setReadTimeout(5000);
            connection.setInstanceFollowRedirects(false);
            connection.setUseCaches(false);
            connection.setRequestMethod(method);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Connection", "close");
            connection.setRequestProperty("X-SimpleOffice-Android-Token", bootstrapToken);
            if (!csrf.isEmpty()) connection.setRequestProperty("X-CSRF-Token", csrf);
            if (!cookie.isEmpty()) connection.setRequestProperty("Cookie", cookie);
            if (body != null) {
                byte[] encoded = body.getBytes(StandardCharsets.UTF_8);
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                connection.setFixedLengthStreamingMode(encoded.length);
                try (java.io.OutputStream output = connection.getOutputStream()) {
                    output.write(encoded);
                }
            }
            int responseCode = connection.getResponseCode();
            InputStream input = responseCode >= 400 ? connection.getErrorStream() : connection.getInputStream();
            String text = input == null ? "{}" : readBounded(input);
            JSONObject json = text.trim().isEmpty() ? new JSONObject() : new JSONObject(text);
            return new NativeResponse(responseCode, json, responseCookies(connection));
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private static String readBounded(InputStream input) throws IOException {
        try (InputStream stream = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int read;
            int total = 0;
            while ((read = stream.read(buffer)) >= 0) {
                total += read;
                if (total > MAX_LOCAL_RESPONSE) throw new IOException("Lokale Antwort ist zu groß");
                output.write(buffer, 0, read);
            }
            return new String(output.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    private static List<String> responseCookies(HttpURLConnection connection) {
        List<String> result = new ArrayList<>();
        for (Map.Entry<String, List<String>> entry : connection.getHeaderFields().entrySet()) {
            if (entry.getKey() == null || !"Set-Cookie".equalsIgnoreCase(entry.getKey())) continue;
            if (entry.getValue() != null) result.addAll(entry.getValue());
        }
        return result;
    }

    private static String cookieHeader(List<String> setCookies) {
        Map<String, String> values = new LinkedHashMap<>();
        for (String setCookie : setCookies) {
            if (setCookie == null) continue;
            String pair = setCookie.split(";", 2)[0].trim();
            int separator = pair.indexOf('=');
            if (separator <= 0) continue;
            values.put(pair.substring(0, separator), pair.substring(separator + 1));
        }
        StringBuilder result = new StringBuilder();
        for (Map.Entry<String, String> entry : values.entrySet()) {
            if (result.length() > 0) result.append("; ");
            result.append(entry.getKey()).append('=').append(entry.getValue());
        }
        return result.toString();
    }

    private void syncRuntimeAssets(File runtimeRoot) throws IOException {
        SharedPreferences preferences = getSharedPreferences(RUNTIME_PREFS, MODE_PRIVATE);
        int installedVersion = preferences.getInt(RUNTIME_VERSION, -1);
        if (installedVersion == BuildConfig.VERSION_CODE && runtimeRoot.isDirectory()) return;
        showStatus("Lokale Programmdateien werden aktualisiert …", true);
        copyAssetTree(getAssets(), "simpleoffice", runtimeRoot);
        preferences.edit().putInt(RUNTIME_VERSION, BuildConfig.VERSION_CODE).apply();
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
            while ((read = input.read(buffer)) >= 0) output.write(buffer, 0, read);
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
                if (statusCode >= 200 && statusCode < 500) return;
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

    private void showStatus(String message, boolean showProgress) {
        mainHandler.post(() -> {
            if (destroyed) return;
            if (status != null) status.setText(message);
            if (progress != null) progress.setVisibility(showProgress ? View.VISIBLE : View.GONE);
        });
    }

    private void showFatal(Exception error) {
        String detail = error.getMessage();
        if (detail == null || detail.trim().isEmpty()) detail = error.getClass().getSimpleName();
        showStatus("Start fehlgeschlagen:\n" + detail, false);
    }

    @Override
    protected void onDestroy() {
        destroyed = true;
        executor.shutdownNow();
        super.onDestroy();
    }

    private static final class NativeResponse {
        final int status;
        final JSONObject json;
        final List<String> setCookies;

        NativeResponse(int status, JSONObject json, List<String> setCookies) {
            this.status = status;
            this.json = json;
            this.setCookies = setCookies;
        }
    }
}

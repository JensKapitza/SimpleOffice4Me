package de.simpleoffice4me.android;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ContentUris;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.CancellationSignal;
import android.os.Handler;
import android.os.Looper;
import android.provider.CalendarContract.Instances;
import android.provider.ContactsContract.Contacts;
import android.provider.Settings;
import android.text.format.DateFormat;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.CookieManager;

import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.Proxy;
import java.net.URL;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.TimeZone;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.function.BooleanSupplier;

/** Per-source Android permission and native-only display. No data goes to Flask or WebView. */
final class AndroidDeviceSourcePreview {
    static final String PAGE = "http://127.0.0.1:8765/android/device-sources/";
    static final int PERMISSION_REQUEST = 707;
    private static final int CALENDAR_LIMIT = 12;
    private static final int CONTACTS_LIMIT = 20;
    private static final long DAY_MS = 86_400_000L;
    private static final int PROVIDER_TIMEOUT_MS = 5_000;
    enum Source {
        CALENDAR("calendar", Manifest.permission.READ_CALENDAR, "Calendar", "Kalender"),
        CONTACTS("contacts", Manifest.permission.READ_CONTACTS, "Contacts", "Kontakte");

        final String key;
        final String permission;
        final String english;
        final String german;

        Source(String key, String permission, String english, String german) {
            this.key = key;
            this.permission = permission;
            this.english = english;
            this.german = german;
        }

        static Source parse(String key) {
            for (Source source : values()) if (source.key.equals(key)) return source;
            return null;
        }

        String title(boolean en) {
            return en ? english : german;
        }
    }

    private record Request(Source source, String csrf, String userAgent, String cookie, boolean en) {}
    private record Preview(List<String> lines, boolean limited) {}

    private final Activity activity;
    private final BooleanSupplier currentPage;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService worker =
            new ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, new ArrayBlockingQueue<>(1));
    private final SharedPreferences permissions;
    private Request active;
    private Request permissionPending;
    private Request resumeAfterPermission;
    private AlertDialog dialog;
    private CancellationSignal query;
    private boolean resumed;
    private boolean closed;

    AndroidDeviceSourcePreview(Activity activity, BooleanSupplier currentPage) {
        this.activity = activity;
        this.currentPage = currentPage;
        permissions = activity.getSharedPreferences("device-source-permissions", Context.MODE_PRIVATE);
    }

    static boolean isPage(String url) {
        return PAGE.equals(url);
    }

    static boolean isCsrf(String value) {
        return value != null && value.matches("[A-Za-z0-9_-]{32,256}");
    }

    void open(Source source, String csrf, String userAgent, boolean en) {
        if (!isActivePage() || active != null || permissionPending != null || dialog != null) return;
        String cookie = CookieManager.getInstance().getCookie(PAGE);
        if (cookie == null || cookie.isEmpty()) {
            show(source, message(en, "Sign in again to view device sources.",
                    "Melde dich erneut an, um Gerätequellen anzusehen."), en, false, null);
            return;
        }
        authorizeAndRead(new Request(source, csrf, userAgent, cookie, en), true);
    }

    private void authorizeAndRead(Request request, boolean allowPermissionPrompt) {
        active = request;
        submit(request, () -> {
            boolean authorized = authorized(request);
            main.post(() -> {
                if (!isCurrent(request)) return;
                if (!authorized) {
                    finish(request, message(request.en, "Session or feature permission is no longer valid.",
                            "Sitzung oder Funktionsberechtigung ist nicht mehr gültig."), false);
                } else if (granted(request.source)) {
                    permissions.edit().putBoolean(request.source.key + "_granted", true).apply();
                    load(request);
                } else if (allowPermissionPrompt) {
                    ask(request);
                } else {
                    denied(request);
                }
            });
        });
    }

    private static boolean authorized(Request request) {
        HttpURLConnection connection = null;
        try {
            URL endpoint = new URL(PAGE + "authorize/" + request.source.key);
            connection = (HttpURLConnection) endpoint.openConnection(Proxy.NO_PROXY);
            connection.setRequestMethod("POST");
            connection.setDoOutput(true);
            connection.setFixedLengthStreamingMode(0);
            connection.setInstanceFollowRedirects(false);
            connection.setUseCaches(false);
            connection.setConnectTimeout(2_000);
            connection.setReadTimeout(2_000);
            connection.setRequestProperty("Cookie", request.cookie);
            connection.setRequestProperty("User-Agent", request.userAgent);
            connection.setRequestProperty("X-CSRF-Token", request.csrf);
            connection.getOutputStream().close();
            return connection.getResponseCode() == 204;
        } catch (IOException | SecurityException unavailable) {
            return false;
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private void ask(Request request) {
        active = null;
        boolean grantedBefore = permissions.getBoolean(request.source.key + "_granted", false);
        boolean askedBefore = permissions.getBoolean(request.source.key + "_asked", false);
        boolean canPrompt = !askedBefore || grantedBefore
                || activity.shouldShowRequestPermissionRationale(request.source.permission);
        String prompt = grantedBefore
                ? message(request.en, "Android access was revoked.", "Der Android-Zugriff wurde widerrufen.")
                : askedBefore
                ? message(request.en, "Android access has not been granted.", "Der Android-Zugriff wurde nicht erteilt.")
                : message(request.en, "Allow read-only access to view this source. Nothing is uploaded.",
                        "Erlaube lesenden Zugriff, um diese Quelle anzusehen. Es wird nichts hochgeladen.");
        show(request.source, prompt, request.en, askedBefore, canPrompt ? () -> askSystem(request) : null);
    }

    private void askSystem(Request request) {
        if (!isActivePage()) return;
        permissions.edit().putBoolean(request.source.key + "_asked", true).apply();
        permissionPending = request;
        try {
            activity.requestPermissions(new String[]{request.source.permission}, PERMISSION_REQUEST);
        } catch (IllegalStateException | SecurityException unavailable) {
            permissionPending = null;
            finish(request, message(request.en, "The Android permission dialog is unavailable.",
                    "Der Android-Berechtigungsdialog ist nicht verfügbar."), true);
        }
    }

    void onPermissionResult() {
        Request request = permissionPending;
        permissionPending = null;
        if (request == null || closed) return;
        resumeAfterPermission = request;
        if (resumed) consumePermissionResult();
    }

    private void consumePermissionResult() {
        Request request = resumeAfterPermission;
        resumeAfterPermission = null;
        if (request == null || !isActivePage()) return;
        // Re-check the web session/feature after the asynchronous Android dialog.
        authorizeAndRead(request, false);
    }

    private void denied(Request request) {
        finish(request, message(request.en, "Android access was not granted. Check app permissions in Settings.",
                "Der Android-Zugriff wurde nicht erteilt. Prüfe die App-Berechtigungen in den Einstellungen."), true);
    }

    private void load(Request request) {
        CancellationSignal signal = new CancellationSignal();
        query = signal;
        main.postDelayed(() -> {
            if (query == signal && isCurrent(request)) {
                finish(request, message(request.en, "The Android provider did not respond in time.",
                        "Der Android-Anbieter hat nicht rechtzeitig geantwortet."), false);
            }
        }, PROVIDER_TIMEOUT_MS);
        submit(request, () -> {
            if (signal.isCanceled()) return;
            String result;
            boolean accessLost = false;
            try {
                Preview preview = read(request, signal);
                result = authorized(request)
                        ? render(request, preview)
                        : message(request.en, "Session or feature permission is no longer valid.",
                                "Sitzung oder Funktionsberechtigung ist nicht mehr gültig.");
            } catch (SecurityException revoked) {
                result = message(request.en, "Android access is no longer available.",
                        "Der Android-Zugriff ist nicht mehr verfügbar.");
                accessLost = true;
            } catch (RuntimeException unavailable) {
                result = message(request.en, "The Android provider is unavailable.",
                        "Der Android-Anbieter ist nicht verfügbar.");
            }
            String output = result;
            boolean settings = accessLost;
            main.post(() -> {
                if (!isCurrent(request) || signal.isCanceled()) return;
                query = null;
                if (!granted(request.source)) denied(request);
                else finish(request, output, settings);
            });
        });
    }

    private Preview read(Request request, CancellationSignal signal) {
        boolean calendar = request.source == Source.CALENDAR;
        Uri.Builder period = Instances.CONTENT_URI.buildUpon();
        long now = System.currentTimeMillis();
        ContentUris.appendId(period, now);
        ContentUris.appendId(period, now + 30 * DAY_MS);
        Uri uri = calendar ? period.build() : Contacts.CONTENT_URI;
        String[] fields = calendar
                ? new String[]{Instances.TITLE, Instances.BEGIN, Instances.ALL_DAY, Instances.CALENDAR_DISPLAY_NAME}
                : new String[]{Contacts.DISPLAY_NAME_PRIMARY};
        int limit = calendar ? CALENDAR_LIMIT : CONTACTS_LIMIT;
        List<String> result = new ArrayList<>(limit);
        try (Cursor cursor = activity.getContentResolver().query(uri, fields,
                calendar ? Instances.VISIBLE + " = ?" : null,
                calendar ? new String[]{"1"} : null,
                calendar ? Instances.BEGIN + " ASC" : Contacts.SORT_KEY_PRIMARY + " ASC", signal)) {
            if (cursor == null) throw new IllegalStateException("No Android provider cursor");
            while (result.size() < limit && cursor.moveToNext()) {
                signal.throwIfCanceled();
                result.add(calendar ? eventLine(request, cursor) : safeText(cursor.getString(0),
                        message(request.en, "Unnamed contact", "Kontakt ohne Namen")));
            }
            return new Preview(result, result.size() == limit && cursor.moveToNext());
        }
    }

    private String eventLine(Request request, Cursor cursor) {
        Date start = new Date(cursor.getLong(1));
        boolean allDay = cursor.getInt(2) != 0;
        java.text.DateFormat dates = DateFormat.getMediumDateFormat(activity);
        if (allDay) dates.setTimeZone(TimeZone.getTimeZone("UTC"));
        String time = allDay ? "" : " " + DateFormat.getTimeFormat(activity).format(start);
        return dates.format(start) + time + " · " + safeText(cursor.getString(0),
                message(request.en, "Untitled event", "Termin ohne Titel")) + " ("
                + safeText(cursor.getString(3), request.source.title(request.en)) + ")";
    }

    static String safeText(String text, String fallback) {
        if (text == null) return fallback;
        StringBuilder plain = new StringBuilder();
        for (int i = 0; i < text.length() && plain.length() < 100; ) {
            int cp = text.codePointAt(i);
            i += Character.charCount(cp);
            if (Character.isISOControl(cp) || Character.getType(cp) == Character.FORMAT
                    || Character.getType(cp) == Character.LINE_SEPARATOR
                    || Character.getType(cp) == Character.PARAGRAPH_SEPARATOR) plain.append(' ');
            else plain.appendCodePoint(cp);
        }
        String compact = plain.toString().trim();
        return compact.isEmpty() ? fallback : compact;
    }

    private static String render(Request request, Preview preview) {
        String source = request.source == Source.CALENDAR
                ? message(request.en, "Android calendar · next 30 days", "Android-Kalender · nächste 30 Tage")
                : message(request.en, "Android contacts · device preview", "Android-Kontakte · Gerätevorschau");
        if (preview.lines.isEmpty()) {
            return source + "\n\n" + message(request.en, "The source is available, but no entries were found.",
                    "Die Quelle ist verfügbar, enthält aber keine passenden Einträge.");
        }
        String count = preview.limited
                ? message(request.en, "Showing the first ", "Angezeigt werden die ersten ")
                : message(request.en, "Entries shown: ", "Angezeigte Einträge: ");
        return source + "\n" + count + preview.lines.size() + "\n\n" + android.text.TextUtils.join("\n", preview.lines);
    }

    private boolean granted(Source source) {
        return activity.checkSelfPermission(source.permission) == PackageManager.PERMISSION_GRANTED;
    }

    private boolean isActivePage() {
        return !closed && resumed && currentPage.getAsBoolean();
    }

    private boolean isCurrent(Request request) {
        if (active != request || !isActivePage()) return false;
        if (request.cookie.equals(CookieManager.getInstance().getCookie(PAGE))) return true;
        clearView();
        return false;
    }

    private void submit(Request request, Runnable operation) {
        try {
            worker.execute(operation);
        } catch (RejectedExecutionException busy) {
            finish(request, message(request.en, "The device preview is busy. Try again shortly.",
                    "Die Gerätevorschau ist beschäftigt. Versuche es gleich erneut."), false);
        }
    }

    private void finish(Request request, String text, boolean settings) {
        active = null;
        cancelQuery();
        if (isActivePage()) show(request.source, text, request.en, settings, null);
    }

    private void show(Source source, String text, boolean en, boolean settings, Runnable request) {
        AlertDialog.Builder builder = new AlertDialog.Builder(activity)
                .setTitle(source.title(en)).setMessage(text)
                .setNegativeButton(message(en, "Close", "Schließen"), null);
        if (request != null) builder.setPositiveButton(message(en, "Request access", "Zugriff anfragen"),
                (ignored, choice) -> request.run());
        if (settings) builder.setNeutralButton(message(en, "Android settings", "Android-Einstellungen"),
                (ignored, choice) -> openSettings());
        AlertDialog created = builder.create();
        dialog = created;
        created.setOnDismissListener(ignored -> {
            if (dialog == created) dialog = null;
        });
        Window window = created.getWindow();
        if (window != null) window.addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        created.show();
    }

    private void openSettings() {
        try {
            activity.startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.fromParts("package", activity.getPackageName(), null)));
        } catch (android.content.ActivityNotFoundException unavailable) {
            // The surrounding UI already documents the manual Android settings path.
        }
    }

    private static String message(boolean en, String english, String german) {
        return en ? english : german;
    }

    private void cancelQuery() {
        CancellationSignal signal = query;
        query = null;
        // CancellationSignal can invoke a remote provider listener synchronously.
        if (signal != null) new Thread(signal::cancel, "device-source-cancel").start();
    }

    void onPageChanged() {
        permissionPending = null;
        resumeAfterPermission = null;
        clearView();
    }

    void onPause() {
        resumed = false;
        clearView();
    }

    void onResume() {
        resumed = true;
        consumePermissionResult();
    }

    private void clearView() {
        active = null;
        cancelQuery();
        if (dialog != null) dialog.dismiss();
    }

    void close() {
        closed = true;
        onPageChanged();
        worker.shutdownNow();
    }
}

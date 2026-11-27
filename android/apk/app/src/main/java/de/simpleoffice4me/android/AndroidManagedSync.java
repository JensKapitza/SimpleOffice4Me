package de.simpleoffice4me.android;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.pm.PackageManager;
import android.webkit.CookieManager;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.Proxy;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.BooleanSupplier;

/** Foreground-only, explicit SimpleOffice-to-Android synchronization. */
final class AndroidManagedSync {
    static final int PERMISSION_REQUEST = 708;
    private static final int MAX_BYTES = 440_000;
    private final Activity activity;
    private final BooleanSupplier currentPage;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private volatile boolean resumed;
    private volatile long generation;
    private volatile boolean closed;
    private boolean busy;
    private AlertDialog confirmation;
    private Request pending;

    private record Request(String source, String csrf, String userAgent, String cookie,
                           JSONArray calendarIds, long generation) {}
    private record Snapshot(String principal, List<JSONArray> pages) {}

    AndroidManagedSync(Activity activity, BooleanSupplier currentPage) {
        this.activity = activity;
        this.currentPage = currentPage;
    }

    void open(String source, String csrf, String userAgent, String calendarIds) {
        if (!resumed || closed || !currentPage.getAsBoolean() || busy || pending != null || confirmation != null) return;
        try {
            JSONArray selected = new JSONArray(calendarIds);
            if (selected.length() > 8 || ("contacts".equals(source) ? selected.length() != 0 : selected.length() == 0)) {
                message("Wähle bis zu acht Kalender.");
                return;
            }
            for (int i = 0; i < selected.length(); i++)
                if (!(selected.get(i) instanceof String id) || !id.equals("simpleoffice:birthdays") && !id.matches("[a-z0-9][a-z0-9-]{0,62}")) return;
            String cookie = CookieManager.getInstance().getCookie(AndroidDeviceSourcePreview.PAGE);
            if (cookie == null || cookie.trim().isEmpty()) { message("Bitte erneut anmelden."); return; }
            Request request = new Request(source, csrf, userAgent, cookie, selected, generation);
            String what = "contacts".equals(source) ? "Kontakte" : "die ausgewählten Kalender";
            confirmation = new AlertDialog.Builder(activity).setTitle("Mit Android abgleichen")
                    .setMessage("SimpleOffice übernimmt " + what
                            + " ins eigene Android-Konto. Andere Konten bleiben unverändert. Nichts wird zurückgeschrieben.")
                    .setNegativeButton("Abbrechen", null)
                    .setPositiveButton("Abgleichen", (dialog, button) -> startOrAsk(request)).create();
            confirmation.setOnDismissListener(ignored -> confirmation = null);
            confirmation.show();
        } catch (org.json.JSONException invalid) {
            message("Ungültige Kalender-Auswahl.");
        }
    }

    private static String[] permissions(String source) {
        return "contacts".equals(source)
                ? new String[]{Manifest.permission.READ_CONTACTS, Manifest.permission.WRITE_CONTACTS}
                : new String[]{Manifest.permission.READ_CALENDAR, Manifest.permission.WRITE_CALENDAR};
    }

    private boolean permitted(String source) {
        for (String permission : permissions(source))
            if (activity.checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) return false;
        return true;
    }

    private boolean valid(Request request) {
        return !closed && resumed && request.generation == generation
                && request.cookie.equals(CookieManager.getInstance().getCookie(AndroidDeviceSourcePreview.PAGE));
    }

    private void startOrAsk(Request request) {
        if (busy || pending != null || !valid(request) || !currentPage.getAsBoolean()) return;
        if (permitted(request.source)) submit(request);
        else {
            pending = request;
            try {
                activity.requestPermissions(permissions(request.source), PERMISSION_REQUEST);
            } catch (IllegalStateException | SecurityException unavailable) {
                pending = null;
                message("Die Android-Berechtigungsabfrage ist nicht verfügbar.");
            }
        }
    }

    private boolean permissionReturned;

    void onPermissionResult() {
        permissionReturned = true;
        resumePermission();
    }

    private void resumePermission() {
        if (!resumed || pending == null || !permissionReturned) return;
        Request request = pending;
        pending = null;
        permissionReturned = false;
        if (!valid(request) || !currentPage.getAsBoolean()) return;
        if (permitted(request.source)) submit(request);
        else message("Zugriff nicht erteilt. Du kannst ihn in den Android-App-Einstellungen freigeben.");
    }

    private void submit(Request request) {
        busy = true;
        executor.execute(() -> {
            String text;
            try {
                boolean contacts = "contacts".equals(request.source);
                Snapshot snapshot = fetchAll(request);
                if (!canContinue(request)) throw new IOException("Sync cancelled");
                AndroidManagedSyncAccount.bind(snapshot.principal());
                int count = contacts ? syncContacts(request, snapshot.pages()) : syncCalendar(request, snapshot.pages());
                text = count + " Einträge geprüft oder übernommen. Bestehende Einträge anderer Android-Konten bleiben unberührt.";
            } catch (SnapshotChanged changed) {
                text = "Die Auswahl hat sich beim Laden geändert. Bitte starte den Abgleich erneut. "
                        + "Diese Übertragung hat noch nichts auf Android geschrieben.";
            } catch (SnapshotTooLarge tooLarge) {
                text = "contacts".equals(request.source)
                        ? "Dieses Adressbuch enthält mehr als 1.000 übertragbare Kontakte."
                        : "Die Auswahl enthält mehr als 1.000 Termine oder Serienausnahmen. Wähle weniger Kalender.";
            } catch (Exception failure) {
                text = "Abgleich nicht vollständig. Prüfe Anmeldung, Android-Berechtigungen und Auswahl. Erneutes Abgleichen ist möglich.";
            } finally {
                AndroidManagedSyncAccount.clear();
            }
            final String outcome = text;
            activity.runOnUiThread(() -> {
                busy = false;
                if (valid(request) && currentPage.getAsBoolean()) message(outcome);
            });
        });
    }

    private boolean canContinue(Request request) {
        return !Thread.currentThread().isInterrupted() && valid(request) && permitted(request.source);
    }

    private int syncContacts(Request request, List<JSONArray> pages) throws Exception {
        JSONArray combined = new JSONArray();
        for (JSONArray rows : pages) {
            for (int i = 0; i < rows.length(); i++) combined.put(rows.getJSONObject(i));
        }
        return combined.length() == 0 ? 0 : AndroidManagedContacts.sync(activity, combined, () -> canContinue(request));
    }

    private int syncCalendar(Request request, List<JSONArray> pages) throws Exception {
        List<List<AndroidManagedCalendarRows.Event>> batches = AndroidManagedCalendarRows.prepare(pages);
        int count = 0;
        for (List<AndroidManagedCalendarRows.Event> batch : batches) {
            if (!canContinue(request)) throw new IOException("Sync cancelled");
            count += AndroidManagedCalendar.sync(activity, batch);
        }
        return count;
    }

    private Snapshot fetchAll(Request request) throws Exception {
        AndroidManagedSnapshotCursor cursor = new AndroidManagedSnapshotCursor();
        List<JSONArray> pages = new ArrayList<>();
        for (int pageIndex = 0; pageIndex < AndroidManagedSnapshotCursor.MAX_ROWS / AndroidManagedSnapshotCursor.PAGE_SIZE; pageIndex++) {
            if (!canContinue(request)) throw new IOException("Sync cancelled");
            JSONObject page = fetch(request, cursor.offset(), cursor.revision());
            JSONArray rows = page.getJSONArray("rows");
            Object nextValue = page.get("next_offset");
            Integer next = nextValue == JSONObject.NULL ? null : (Integer) nextValue;
            boolean done = cursor.accept(page.getString("principal"), page.getString("revision"), rows.length(), next);
            pages.add(rows);
            if (done) return new Snapshot(cursor.principal(), pages);
        }
        throw new IOException("Managed export did not end");
    }

    private static final class SnapshotChanged extends IOException {}
    private static final class SnapshotTooLarge extends IOException {}

    private static JSONObject fetch(Request request, int offset, String revision) throws Exception {
        URL endpoint = new URL(AndroidDeviceSourcePreview.PAGE + "managed/" + request.source);
        HttpURLConnection connection = (HttpURLConnection) endpoint.openConnection(Proxy.NO_PROXY);
        try {
            JSONObject payload = new JSONObject().put("calendar_ids", request.calendarIds)
                    .put("offset", offset).put("revision", revision);
            byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
            connection.setRequestMethod("POST");
            connection.setDoOutput(true);
            connection.setFixedLengthStreamingMode(body.length);
            connection.setInstanceFollowRedirects(false);
            connection.setUseCaches(false);
            connection.setConnectTimeout(3000);
            connection.setReadTimeout(5000);
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setRequestProperty("Cookie", request.cookie);
            connection.setRequestProperty("User-Agent", request.userAgent);
            connection.setRequestProperty("X-CSRF-Token", request.csrf);
            try (java.io.OutputStream output = connection.getOutputStream()) { output.write(body); }
            int status = connection.getResponseCode();
            if (status == 409) throw new SnapshotChanged();
            if (status == 413) throw new SnapshotTooLarge();
            if (status != 200) throw new IOException("Snapshot unavailable");
            try (InputStream input = connection.getInputStream(); ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
                byte[] chunk = new byte[4096];
                int len;
                while ((len = input.read(chunk)) != -1) {
                    if (bytes.size() + len > MAX_BYTES) throw new java.io.IOException("Snapshot too large");
                    bytes.write(chunk, 0, len);
                }
                return new JSONObject(new String(bytes.toByteArray(), StandardCharsets.UTF_8));
            }
        } finally {
            connection.disconnect();
        }
    }

    private void message(String text) {
        if (resumed && !closed && currentPage.getAsBoolean())
            new AlertDialog.Builder(activity).setTitle("Android-Abgleich").setMessage(text)
                    .setPositiveButton("Schließen", null).show();
    }

    void onPageChanged() {
        generation++; pending = null; permissionReturned = false;
        if (confirmation != null) confirmation.dismiss();
    }

    void onPause() {
        resumed = false;
        if (pending == null) generation++;
        if (confirmation != null) confirmation.dismiss();
    }
    void onResume() { resumed = true; resumePermission(); }
    void close() { closed = true; onPageChanged(); executor.shutdownNow(); }
}

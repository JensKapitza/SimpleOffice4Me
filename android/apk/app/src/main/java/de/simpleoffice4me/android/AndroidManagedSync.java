package de.simpleoffice4me.android;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.pm.PackageManager;
import android.os.SystemClock;
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
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/** Foreground-only, explicit SimpleOffice-to-Android synchronization. */
final class AndroidManagedSync {
    static final int PERMISSION_REQUEST = 709;
    private static final int MAX_BYTES = 440_000;
    private final Activity activity;
    private final BooleanSupplier currentPage;
    private final Consumer<String> feedback;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private volatile boolean resumed;
    private volatile long generation;
    private volatile boolean closed;
    private boolean busy;
    private boolean resumeNotice;
    private AlertDialog confirmation;
    private Prepared pending;

    private record Request(String source, String csrf, String userAgent, String cookie,
                           JSONArray calendarIds, List<String> calendarNames, long generation) {}
    private record Snapshot(String principal, String revision, List<JSONArray> pages) {}
    private record Prepared(Request request, String principal, String revision, long preparedAt,
                            int count, List<AndroidManagedContacts.Contact> contacts,
                            List<List<AndroidManagedCalendarRows.Event>> calendar) {}
    private static final String CHANGED = "Die Daten oder Freigaben haben sich geändert. Bitte öffne eine neue Vorschau. "
            + "Diese Übertragung hat noch nichts auf Android geschrieben.";

    AndroidManagedSync(Activity activity, BooleanSupplier currentPage, Consumer<String> feedback) {
        this.activity = activity;
        this.currentPage = currentPage;
        this.feedback = feedback;
    }

    void open(String source, String csrf, String userAgent, String calendarIds) {
        if (!resumed || closed || !currentPage.getAsBoolean() || busy || pending != null || confirmation != null) return;
        try {
            if (calendarIds == null || calendarIds.length() > AndroidManagedCalendarSelection.MAX_JSON_CHARS)
                throw new IllegalArgumentException("Calendar selection is too large");
            JSONArray submitted = new JSONArray(calendarIds);
            if (submitted.length() > 8 || ("contacts".equals(source) ? submitted.length() != 0 : submitted.length() == 0)) {
                message("Wähle bis zu acht Kalender.");
                return;
            }
            List<AndroidManagedCalendarSelection.Item> rawCalendars = new ArrayList<>();
            for (int i = 0; i < submitted.length(); i++) {
                if (!(submitted.get(i) instanceof JSONObject calendar))
                    throw new IllegalArgumentException("Invalid calendar selection");
                Object rawId = calendar.opt("id");
                Object rawName = calendar.opt("name");
                if (!(rawId instanceof String id) || !(rawName instanceof String name))
                    throw new IllegalArgumentException("Invalid calendar selection");
                rawCalendars.add(new AndroidManagedCalendarSelection.Item(id, name));
            }
            List<AndroidManagedCalendarSelection.Item> calendars = AndroidManagedCalendarSelection.validate(rawCalendars);
            JSONArray selected = new JSONArray();
            for (AndroidManagedCalendarSelection.Item calendar : calendars) selected.put(calendar.id());
            String cookie = CookieManager.getInstance().getCookie(AndroidDeviceSourcePreview.PAGE);
            if (cookie == null || cookie.trim().isEmpty()) { message("Bitte erneut anmelden."); return; }
            Request request = new Request(source, csrf, userAgent, cookie, selected,
                    AndroidManagedCalendarSelection.names(calendars), generation);
            prepare(request);
        } catch (org.json.JSONException | IllegalArgumentException invalid) {
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

    private void prepare(Request request) {
        busy = true;
        message("Vorschau wird geladen. Android wurde noch nicht verändert.");
        executor.execute(() -> {
            Prepared result = null;
            String error = "Vorschau nicht verfügbar. Prüfe Anmeldung und Auswahl.";
            try {
                Snapshot observed = fetchAll(request);
                List<AndroidManagedContacts.Contact> contacts = Collections.emptyList();
                List<List<AndroidManagedCalendarRows.Event>> calendars = Collections.emptyList();
                int count;
                if ("contacts".equals(request.source)) {
                    JSONArray combined = new JSONArray();
                    for (JSONArray page : observed.pages) {
                        for (int i = 0; i < page.length(); i++) combined.put(page.getJSONObject(i));
                    }
                    contacts = AndroidManagedContacts.prepare(combined);
                    count = contacts.size();
                } else {
                    calendars = AndroidManagedCalendarRows.prepare(observed.pages);
                    count = 0;
                    for (var page : calendars) count += page.size();
                }
                result = new Prepared(request, observed.principal, observed.revision, SystemClock.elapsedRealtime(),
                                      count, contacts, calendars);
            } catch (SnapshotChanged changed) {
                error = CHANGED;
            } catch (SnapshotTooLarge tooLarge) {
                error = "contacts".equals(request.source)
                        ? "Dieses Adressbuch enthält mehr als 1.000 übertragbare Kontakte."
                        : "Die Auswahl enthält mehr als 1.000 Termine oder Serienausnahmen. Wähle weniger Kalender.";
            } catch (Exception unavailable) {
                // No provider access takes place while creating a preview.
            }
            final Prepared preview = result;
            final String feedback = error;
            activity.runOnUiThread(() -> {
                busy = false;
                if (!valid(request) || !currentPage.getAsBoolean()) return;
                if (preview == null) message(feedback);
                else if (preview.count == 0) message("Keine Einträge zum Übernehmen. Android wurde nicht verändert.");
                else showPreview(preview);
            });
        });
    }

    private void showPreview(Prepared preview) {
        message("Vorschau bereit. Prüfe die Auswahl im Dialog.");
        String scope = "contacts".equals(preview.request.source)
                ? preview.count + " Kontakte"
                : AndroidManagedSyncPreview.calendarScope(preview.count, preview.request.calendarNames);
        confirmation = new AlertDialog.Builder(activity).setTitle("Android-Abgleich prüfen")
                .setMessage(scope + "\n\nDiese Auswahl kann ins eigene SimpleOffice-Konto auf diesem Gerät übernommen werden. "
                        + "Es wurde noch nichts geschrieben. Andere Konten bleiben unverändert.")
                .setNegativeButton("Abbrechen", (dialog, button) -> previewCanceled())
                .setPositiveButton("Übernehmen", (dialog, button) -> startOrAsk(preview)).create();
        confirmation.setOnCancelListener(ignored -> previewCanceled());
        confirmation.setOnDismissListener(ignored -> confirmation = null);
        confirmation.show();
    }

    private void previewCanceled() {
        message("Vorschau abgebrochen. Android wurde nicht verändert.");
    }

    private void startOrAsk(Prepared preview) {
        Request request = preview.request;
        if (busy || pending != null || !valid(request) || !currentPage.getAsBoolean()) return;
        if (!previewMatches(preview, preview.principal, preview.revision)) { message(CHANGED); return; }
        if (permitted(request.source)) submit(preview);
        else {
            pending = preview;
            permissionReturned = false;
            message("Warte auf die Android-Berechtigung.");
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
        Prepared preview = pending;
        pending = null;
        permissionReturned = false;
        Request request = preview.request;
        if (!valid(request) || !currentPage.getAsBoolean()) return;
        if (permitted(request.source)) submit(preview);
        else message("Zugriff nicht erteilt. Du kannst ihn in den Android-App-Einstellungen freigeben.");
    }

    private boolean previewMatches(Prepared preview, String principal, String revision) {
        return AndroidManagedSnapshotCursor.approvalMatches(preview.principal, preview.revision,
                preview.preparedAt, SystemClock.elapsedRealtime(), principal, revision);
    }

    private void verifyPreview(Prepared preview) throws SnapshotChanged {
        try {
            JSONObject current = fetch(preview.request, 0, "");
            if (!previewMatches(preview, current.getString("principal"), current.getString("revision")))
                throw new SnapshotChanged();
        } catch (SnapshotChanged changed) {
            throw changed;
        } catch (Exception unavailable) {
            throw new SnapshotChanged();
        }
    }

    private void submit(Prepared preview) {
        Request request = preview.request;
        busy = true;
        message("Android-Abgleich läuft. Lass diese Ansicht geöffnet.");
        executor.execute(() -> {
            String text;
            try {
                if (!canWrite(request)) throw new IOException("Sync cancelled");
                // Re-read the current revision through the authenticated server before accessing the provider.
                verifyPreview(preview);
                if (!canWrite(request)) throw new IOException("Sync cancelled");
                AndroidManagedSyncAccount.bind(preview.principal);
                int count = "contacts".equals(request.source)
                        ? AndroidManagedContacts.sync(activity, preview.contacts, () -> canWrite(request))
                        : syncCalendar(request, preview.calendar);
                text = count + " Einträge geprüft oder übernommen. Bestehende Einträge anderer Android-Konten bleiben unberührt.";
            } catch (SnapshotChanged changed) {
                text = CHANGED;
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

    private boolean canWrite(Request request) {
        return !Thread.currentThread().isInterrupted() && valid(request) && permitted(request.source);
    }

    private int syncCalendar(Request request, List<List<AndroidManagedCalendarRows.Event>> batches) throws Exception {
        int count = 0;
        for (List<AndroidManagedCalendarRows.Event> batch : batches) {
            if (!canWrite(request)) throw new IOException("Sync cancelled");
            count += AndroidManagedCalendar.sync(activity, batch);
        }
        return count;
    }

    private Snapshot fetchAll(Request request) throws Exception {
        AndroidManagedSnapshotCursor cursor = new AndroidManagedSnapshotCursor();
        List<JSONArray> pages = new ArrayList<>();
        for (int pageIndex = 0; pageIndex < AndroidManagedSnapshotCursor.MAX_ROWS / AndroidManagedSnapshotCursor.PAGE_SIZE; pageIndex++) {
            if (Thread.currentThread().isInterrupted() || !valid(request)) throw new IOException("Sync cancelled");
            JSONObject page = fetch(request, cursor.offset(), cursor.revision());
            JSONArray rows = page.getJSONArray("rows");
            Object nextValue = page.get("next_offset");
            Integer next = nextValue == JSONObject.NULL ? null : (Integer) nextValue;
            boolean done = cursor.accept(page.getString("principal"), page.getString("revision"), rows.length(), next);
            pages.add(rows);
            if (done) return new Snapshot(cursor.principal(), cursor.revision(), pages);
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
        if (resumed && !closed && currentPage.getAsBoolean()) feedback.accept(text);
    }

    void onPageChanged() {
        generation++; pending = null; permissionReturned = false; resumeNotice = false;
        if (confirmation != null) confirmation.dismiss();
    }

    void onPause() {
        resumed = false;
        if (pending == null) {
            resumeNotice = resumeNotice || busy || confirmation != null;
            generation++;
        }
        if (confirmation != null) confirmation.dismiss();
    }
    void onResume() {
        resumed = true;
        if (resumeNotice) {
            resumeNotice = false;
            message("Abgleich unterbrochen. Starte bei Bedarf erneut; bereits übernommene Einträge bleiben erhalten.");
        }
        resumePermission();
    }
    void close() { closed = true; onPageChanged(); executor.shutdownNow(); }
}

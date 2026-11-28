package de.simpleoffice4me.android;

import android.Manifest;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.os.CancellationSignal;
import android.os.Handler;
import android.os.Looper;
import android.os.OperationCanceledException;
import android.provider.Telephony.Sms;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.util.UUID;

/** Read-only provider access; results go to embedded Python, never the WebView. */
public final class AndroidSmsDeviceImport {
    private static final int MAX_ROWS = 1_000;
    private static final int MAX_TEXT_BYTES = 2 * 1024 * 1024;
    private static final long MAX_DATE_MS = 253402300799999L;
    private static final String[] COLUMNS = {Sms._ID, Sms.DATE, Sms.TYPE, Sms.ADDRESS, Sms.BODY};
    private static volatile Context context;
    private static Lease pending;
    private static boolean inFlight;

    private AndroidSmsDeviceImport() {}

    private static final class Lease {
        final String token = randomId();
        final CancellationSignal cancel = new CancellationSignal();
        boolean used;
    }

    static void initialize(Context appContext) {
        if (BuildConfig.DEVICE_SMS_IMPORT) context = appContext.getApplicationContext();
    }

    public static boolean enabled() {
        return context != null;
    }

    private static String randomId() {
        return UUID.randomUUID().toString().replace("-", "");
    }

    static synchronized String begin() {
        if (!enabled() || pending != null || inFlight) return "";
        pending = new Lease();
        return pending.token;
    }

    static void finish(String token) {
        Lease previous;
        synchronized (AndroidSmsDeviceImport.class) {
            if (find(token) == null) return;
            previous = pending;
            pending = null;
        }
        previous.cancel.cancel();
    }

    private static Lease find(String token) {
        return pending != null && pending.token.equals(token) ? pending : null;
    }

    private static synchronized Lease consume(String token) {
        Lease lease = find(token);
        if (lease == null || lease.used || lease.cancel.isCanceled()) return null;
        lease.used = true;
        inFlight = true;
        return lease;
    }

    public static synchronized boolean active(String token) {
        Lease lease = find(token);
        return lease != null && lease.used && !lease.cancel.isCanceled();
    }

    public static String read(String token, long first, long last) {
        Lease lease = consume(token);
        if (lease == null) return state("unauthorized");
        try {
            Context app = context;
            if (app == null || app.checkSelfPermission(Manifest.permission.READ_SMS) != PackageManager.PERMISSION_GRANTED) {
                return state("denied");
            }
            if (first < 0 || first > last || last > MAX_DATE_MS) return state("invalid_rows");
            Handler timer = new Handler(Looper.getMainLooper());
            Runnable cancel = lease.cancel::cancel;
            timer.postDelayed(cancel, 8_000);
            try {
                return scan(app, lease, first, last);
            } finally {
                timer.removeCallbacks(cancel);
            }
        } catch (SecurityException denied) {
            return state("denied");
        } catch (OperationCanceledException cancelled) {
            return state("cancelled");
        } catch (JSONException | RuntimeException failed) {
            android.util.Log.w("SimpleOfficeSms", "Provider import failed: " + failed.getClass().getSimpleName());
            return state("unavailable");
        } finally {
            synchronized (AndroidSmsDeviceImport.class) {
                inFlight = false;
            }
        }
    }

    private static String scan(Context app, Lease lease, long first, long last) throws JSONException {
        String source = enrollment(app);
        if (source.isEmpty()) return state("unavailable");

        String selection = Sms.TYPE + " IN (?,?) AND " + Sms.DATE + " BETWEEN ? AND ?";
        String[] arguments = {Integer.toString(Sms.MESSAGE_TYPE_INBOX), Integer.toString(Sms.MESSAGE_TYPE_SENT),
                Long.toString(first), Long.toString(last)};
        JSONArray records = new JSONArray();
        int bytes = 0;
        try (Cursor cursor = app.getContentResolver().query(Sms.CONTENT_URI, COLUMNS, selection,
                arguments, Sms._ID + " ASC", lease.cancel)) {
            if (cursor == null) return state("unavailable");
            int[] columns = new int[COLUMNS.length];
            for (int i = 0; i < columns.length; i++) columns[i] = cursor.getColumnIndexOrThrow(COLUMNS[i]);
            while (cursor.moveToNext()) {
                lease.cancel.throwIfCanceled();
                if (records.length() >= MAX_ROWS) return state("too_many");
                long id = cursor.getLong(columns[0]);
                long date = cursor.getLong(columns[1]);
                int type = cursor.getInt(columns[2]);
                String address = cursor.isNull(columns[3]) ? "" : cursor.getString(columns[3]);
                String body = cursor.isNull(columns[4]) ? "" : cursor.getString(columns[4]);
                int addressBytes = utf8Length(address, 512);
                int bodyBytes = utf8Length(body, 16_000);
                if (id < 1 || cursor.isNull(columns[1]) || date < first || date > last
                        || (type != Sms.MESSAGE_TYPE_INBOX && type != Sms.MESSAGE_TYPE_SENT)
                        || addressBytes < 0 || bodyBytes < 0) return state("invalid_rows");
                bytes += addressBytes + bodyBytes;
                if (bytes > MAX_TEXT_BYTES) return state("too_many");
                records.put(new JSONObject().put("source", source).put("provider_id", id)
                        .put("date_ms", date).put("direction", type == Sms.MESSAGE_TYPE_INBOX ? "inbox" : "sent")
                        .put("address", address).put("body", body));
            }
        }
        lease.cancel.throwIfCanceled();
        return new JSONObject().put("state", "ready").put("records", records).toString();
    }

    static int utf8Length(String value, int maximum) {
        if (value == null || value.length() > maximum) return -1;
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (Character.isHighSurrogate(c)) {
                if (++i == value.length() || !Character.isLowSurrogate(value.charAt(i))) return -1;
            } else if (Character.isLowSurrogate(c)) {
                return -1;
            }
        }
        int length = value.getBytes(StandardCharsets.UTF_8).length;
        return length <= maximum ? length : -1;
    }

    private static synchronized String enrollment(Context app) {
        SharedPreferences prefs = app.getSharedPreferences("sms-device-import", Context.MODE_PRIVATE);
        String id = prefs.getString("enrollment", "");
        if (id != null && id.matches("[a-f0-9]{32}")) return id;
        id = randomId();
        return prefs.edit().putString("enrollment", id).commit() ? id : "";
    }

    private static String state(String state) {
        return "{\"state\":\"" + state + "\"}";
    }
}

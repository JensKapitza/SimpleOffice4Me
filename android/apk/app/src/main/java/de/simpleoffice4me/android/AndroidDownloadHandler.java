package de.simpleoffice4me.android;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.ContentResolver;
import android.content.Intent;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.widget.Toast;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Save authenticated local WebView downloads through Android's Storage Access Framework. */
final class AndroidDownloadHandler {
    static final int SAVE_DOCUMENT_REQUEST = 704;
    private static final int BUFFER_SIZE = 64 * 1024;

    private final Activity activity;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private PendingDownload pending;

    AndroidDownloadHandler(Activity activity) {
        this.activity = activity;
    }

    boolean begin(String url, String userAgent, String contentDisposition, String mimeType) {
        if (!isTrustedLocalUrl(url)) {
            if (isAllowedExternalUrl(url)) return false;
            Toast.makeText(activity, "Download mit nicht unterstütztem Link-Schema wurde blockiert.", Toast.LENGTH_LONG).show();
            return true;
        }
        if (pending != null) {
            Toast.makeText(activity, "Bitte den laufenden Download zuerst abschließen.", Toast.LENGTH_SHORT).show();
            return true;
        }
        String mime = safeMimeType(mimeType);
        String filename = URLUtil.guessFileName(url, contentDisposition, mime);
        String cookie = CookieManager.getInstance().getCookie(url);
        pending = new PendingDownload(url, userAgent, mime, cookie == null ? "" : cookie);
        Intent save = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        save.addCategory(Intent.CATEGORY_OPENABLE);
        save.setType(mime);
        save.putExtra(Intent.EXTRA_TITLE, filename);
        try {
            activity.startActivityForResult(save, SAVE_DOCUMENT_REQUEST);
        } catch (ActivityNotFoundException error) {
            pending = null;
            Toast.makeText(activity, "Keine Android-Dateiverwaltung zum Speichern verfügbar.", Toast.LENGTH_LONG).show();
        }
        return true;
    }

    boolean onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode != SAVE_DOCUMENT_REQUEST) return false;
        PendingDownload download = pending;
        pending = null;
        if (download == null || resultCode != Activity.RESULT_OK || data == null || data.getData() == null) {
            return true;
        }
        Uri destination = data.getData();
        if (!"content".equalsIgnoreCase(destination.getScheme())) {
            Toast.makeText(activity, "Unsicheres Speicherziel wurde abgelehnt.", Toast.LENGTH_LONG).show();
            return true;
        }
        executor.execute(() -> downloadTo(download, destination));
        return true;
    }

    void close() {
        pending = null;
        executor.shutdownNow();
    }

    private void downloadTo(PendingDownload download, Uri destination) {
        HttpURLConnection connection = null;
        try {
            URL parsed = new URL(download.url);
            if (!isTrustedLocalUrl(download.url)) throw new IOException("download origin changed");
            connection = (HttpURLConnection) parsed.openConnection();
            connection.setConnectTimeout(10_000);
            connection.setReadTimeout(30_000);
            connection.setInstanceFollowRedirects(false);
            connection.setUseCaches(false);
            connection.setRequestProperty("Accept", "*/*");
            connection.setRequestProperty("Connection", "close");
            if (!download.userAgent.isEmpty()) connection.setRequestProperty("User-Agent", download.userAgent);
            if (!download.cookie.isEmpty()) connection.setRequestProperty("Cookie", download.cookie);
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) throw new IOException("unexpected download status");
            ContentResolver resolver = activity.getContentResolver();
            try (InputStream input = connection.getInputStream();
                 OutputStream output = resolver.openOutputStream(destination, "w")) {
                if (output == null) throw new IOException("destination is not writable");
                byte[] buffer = new byte[BUFFER_SIZE];
                int read;
                while ((read = input.read(buffer)) != -1) {
                    if (Thread.currentThread().isInterrupted()) throw new IOException("download interrupted");
                    output.write(buffer, 0, read);
                }
                output.flush();
            }
            mainHandler.post(() -> showCompleted(destination, download.mimeType));
        } catch (IOException | SecurityException error) {
            try {
                activity.getContentResolver().delete(destination, null, null);
            } catch (Exception ignored) {
            }
            mainHandler.post(() -> Toast.makeText(
                    activity, "Datei konnte nicht gespeichert werden.", Toast.LENGTH_LONG).show());
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private void showCompleted(Uri uri, String mimeType) {
        if (activity.isFinishing()) return;
        new AlertDialog.Builder(activity)
                .setTitle("Download abgeschlossen")
                .setMessage("Die Datei wurde über die Android-Dateiverwaltung gespeichert.")
                .setNegativeButton("Fertig", null)
                .setPositiveButton("Öffnen", (dialog, which) -> openDocument(uri, mimeType))
                .show();
    }

    private void openDocument(Uri uri, String mimeType) {
        Intent open = new Intent(Intent.ACTION_VIEW);
        open.setDataAndType(uri, mimeType);
        open.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try {
            activity.startActivity(Intent.createChooser(open, "Datei öffnen mit"));
        } catch (ActivityNotFoundException error) {
            Toast.makeText(activity, "Keine passende App zum Öffnen gefunden.", Toast.LENGTH_LONG).show();
        }
    }

    static boolean isTrustedLocalUrl(String value) {
        try {
            Uri uri = Uri.parse(value);
            String host = uri.getHost();
            return "http".equalsIgnoreCase(uri.getScheme())
                    && ("127.0.0.1".equals(host) || "localhost".equalsIgnoreCase(host))
                    && uri.getPort() == 8765;
        } catch (Exception error) {
            return false;
        }
    }

    static boolean isAllowedExternalUrl(String value) {
        try {
            String scheme = Uri.parse(value).getScheme();
            if (scheme == null) return false;
            String normalized = scheme.toLowerCase(Locale.ROOT);
            return "http".equals(normalized) || "https".equals(normalized);
        } catch (Exception error) {
            return false;
        }
    }

    private static String safeMimeType(String value) {
        String mime = value == null ? "" : value.trim().toLowerCase(Locale.ROOT);
        if (mime.matches("[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+*+-]+")) return mime;
        return "application/octet-stream";
    }

    private static final class PendingDownload {
        final String url;
        final String userAgent;
        final String mimeType;
        final String cookie;

        PendingDownload(String url, String userAgent, String mimeType, String cookie) {
            this.url = url;
            this.userAgent = userAgent == null ? "" : userAgent;
            this.mimeType = mimeType;
            this.cookie = cookie;
        }
    }
}

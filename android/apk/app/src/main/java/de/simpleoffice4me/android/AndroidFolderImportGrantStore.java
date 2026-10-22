package de.simpleoffice4me.android;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;

import java.util.HashSet;
import java.util.Set;

/** Tracks only temporary folder-import grants so abandoned queues can be cleaned after restart. */
final class AndroidFolderImportGrantStore {
    private static final String PREFS = "simpleoffice-folder-import-grants";
    private static final String KEY = "owned-read-grants";

    private AndroidFolderImportGrantStore() {}

    static synchronized void record(Context context, Uri uri) {
        SharedPreferences preferences = preferences(context);
        Set<String> entries = new HashSet<>(preferences.getStringSet(KEY, new HashSet<>()));
        entries.add(uri.toString());
        if (!preferences.edit().putStringSet(KEY, entries).commit()) {
            throw new IllegalStateException("Android konnte den Ordner-Importstatus nicht speichern.");
        }
    }

    static synchronized void forget(Context context, Uri uri) {
        SharedPreferences preferences = preferences(context);
        Set<String> entries = new HashSet<>(preferences.getStringSet(KEY, new HashSet<>()));
        if (!entries.remove(uri.toString())) return;
        SharedPreferences.Editor editor = preferences.edit();
        if (entries.isEmpty()) editor.remove(KEY);
        else editor.putStringSet(KEY, entries);
        if (!editor.commit()) {
            android.util.Log.w("AndroidFolderImport", "Could not clear a completed folder-grant marker.");
        }
    }

    static synchronized void cleanupAbandoned(Context context) {
        SharedPreferences preferences = preferences(context);
        Set<String> entries = new HashSet<>(preferences.getStringSet(KEY, new HashSet<>()));
        for (String value : entries) {
            try {
                context.getContentResolver().releasePersistableUriPermission(
                        Uri.parse(value), android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION);
            } catch (IllegalArgumentException | SecurityException ignored) {
                // The URI may be malformed or its grant may already have been revoked.
            }
        }
        if (preferences.contains(KEY) && !preferences.edit().remove(KEY).commit()) {
            android.util.Log.w("AndroidFolderImport", "Could not clear the abandoned folder-grant marker.");
        }
    }

    private static SharedPreferences preferences(Context context) {
        return context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}

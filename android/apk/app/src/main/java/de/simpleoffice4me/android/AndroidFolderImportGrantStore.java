package de.simpleoffice4me.android;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;
import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/** Tracks only temporary folder-import grants so abandoned queues can be cleaned after restart. */
final class AndroidFolderImportGrantStore {
    private static final String PREFS = "simpleoffice-folder-import-grants";
    private static final String KEY = "owned-read-grants";
    private static final String SOURCE_ID_SALT_KEY = "source-identity-salt-v1";
    private static final String RESUME_KEY = "pending-folder-import";
    private static final Pattern IMPORT_ID = Pattern.compile("[0-9a-f]{32}");

    private AndroidFolderImportGrantStore() {}

    static synchronized void record(Context context, Uri uri) {
        SharedPreferences preferences = preferences(context);
        Set<String> entries = new HashSet<>(preferences.getStringSet(KEY, new HashSet<>()));
        entries.add(uri.toString());
        if (!preferences.edit().putStringSet(KEY, entries).commit()) {
            throw new IllegalStateException("Android konnte den Ordner-Importstatus nicht speichern.");
        }
    }

    static synchronized byte[] sourceIdentitySalt(Context context) {
        SharedPreferences preferences = preferences(context);
        String encoded = preferences.getString(SOURCE_ID_SALT_KEY, "");
        if (encoded != null && !encoded.isEmpty()) {
            try {
                byte[] existing = Base64.decode(encoded, Base64.DEFAULT);
                if (existing.length == 32) return existing;
            } catch (IllegalArgumentException ignored) {
                // Replace corrupt app-private identity metadata with a new installation salt.
            }
            android.util.Log.w("AndroidFolderImport", "Replacing invalid local folder source-identity salt.");
        }
        byte[] generated = new byte[32];
        new SecureRandom().nextBytes(generated);
        String serialized = Base64.encodeToString(generated, Base64.NO_WRAP);
        if (!preferences.edit().putString(SOURCE_ID_SALT_KEY, serialized).commit()) {
            throw new IllegalStateException("Android konnte die lokale Quellenkennung nicht speichern.");
        }
        return generated;
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

    static synchronized ResumeState restoreResume(Context context) {
        String saved = preferences(context).getString(RESUME_KEY, "");
        if (saved.isEmpty()) return null;
        try {
            JSONObject value = new JSONObject(saved);
            List<Uri> trees = readUris(value.getJSONArray("trees"));
            List<Uri> owned = readUris(value.getJSONArray("owned"));
            String importId = value.getString("import_id");
            String csrfHash = value.getString("csrf_hash");
            if (trees.isEmpty() || trees.size() > AndroidFolderImportTask.MAX_TREES
                    || !IMPORT_ID.matcher(importId).matches() || csrfHash.length() != 64
                    || !trees.containsAll(owned)) {
                throw new JSONException("invalid saved folder-import state");
            }
            return new ResumeState(trees, owned, importId, csrfHash);
        } catch (JSONException | IllegalArgumentException error) {
            clearResume(context);
            android.util.Log.w("AndroidFolderImport", "Discarded invalid saved folder-import state.");
            return null;
        }
    }

    static synchronized void saveResume(
            Context context, List<Uri> trees, List<Uri> owned, String importId, String csrfToken) {
        if (trees.isEmpty() || trees.size() > AndroidFolderImportTask.MAX_TREES
                || !IMPORT_ID.matcher(importId).matches() || !trees.containsAll(owned)) {
            throw new IllegalArgumentException("invalid folder-import resume state");
        }
        JSONArray treeValues = new JSONArray();
        JSONArray ownedValues = new JSONArray();
        for (Uri uri : trees) treeValues.put(uri.toString());
        for (Uri uri : owned) ownedValues.put(uri.toString());
        try {
            JSONObject value = new JSONObject();
            value.put("trees", treeValues);
            value.put("owned", ownedValues);
            value.put("import_id", importId);
            value.put("csrf_hash", sha256(csrfToken));
            if (!preferences(context).edit().putString(RESUME_KEY, value.toString()).commit()) {
                throw new IllegalStateException("Android konnte den Wiederaufnahmestatus nicht speichern.");
            }
        } catch (JSONException error) {
            throw new IllegalStateException("Android konnte den Wiederaufnahmestatus nicht speichern.", error);
        }
    }

    static synchronized boolean resumeMatchesCsrf(Context context, String csrfToken) {
        ResumeState state = restoreResume(context);
        if (state == null) return false;
        return MessageDigest.isEqual(
                state.csrfHash.getBytes(StandardCharsets.US_ASCII),
                sha256(csrfToken).getBytes(StandardCharsets.US_ASCII));
    }

    static synchronized void clearResume(Context context) {
        if (!preferences(context).edit().remove(RESUME_KEY).commit()) {
            android.util.Log.w("AndroidFolderImport", "Could not clear folder-import resume state.");
        }
    }

    static synchronized void cleanupAbandoned(Context context) {
        cleanupAbandoned(context, java.util.Collections.emptySet());
    }

    static synchronized void cleanupAbandoned(Context context, Set<Uri> retained) {
        SharedPreferences preferences = preferences(context);
        Set<String> entries = new HashSet<>(preferences.getStringSet(KEY, new HashSet<>()));
        Iterator<String> iterator = entries.iterator();
        while (iterator.hasNext()) {
            String value = iterator.next();
            if (retained.contains(Uri.parse(value))) continue;
            try {
                context.getContentResolver().releasePersistableUriPermission(
                        Uri.parse(value), android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION);
            } catch (IllegalArgumentException | SecurityException ignored) {
                // The URI may be malformed or its grant may already have been revoked.
            }
            iterator.remove();
        }
        SharedPreferences.Editor editor = preferences.edit();
        if (entries.isEmpty()) editor.remove(KEY);
        else editor.putStringSet(KEY, entries);
        if (!editor.commit()) {
            android.util.Log.w("AndroidFolderImport", "Could not clear the abandoned folder-grant marker.");
        }
    }

    private static List<Uri> readUris(JSONArray values) throws JSONException {
        List<Uri> result = new ArrayList<>();
        for (int index = 0; index < values.length(); index++) {
            Uri uri = Uri.parse(values.getString(index));
            if (!uri.isHierarchical() || !"content".equalsIgnoreCase(uri.getScheme())
                    || result.contains(uri)) {
                throw new JSONException("invalid saved folder URI");
            }
            result.add(uri);
        }
        return result;
    }

    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder(digest.length * 2);
            for (byte item : digest) result.append(String.format(java.util.Locale.ROOT, "%02x", item & 0xff));
            return result.toString();
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is unavailable.", error);
        }
    }

    static final class ResumeState {
        final List<Uri> trees;
        final List<Uri> owned;
        final String importId;
        final String csrfHash;

        ResumeState(List<Uri> trees, List<Uri> owned, String importId, String csrfHash) {
            this.trees = trees;
            this.owned = owned;
            this.importId = importId;
            this.csrfHash = csrfHash;
        }
    }

    private static SharedPreferences preferences(Context context) {
        return context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}

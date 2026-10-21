package de.simpleoffice4me.android;

import android.content.ContentResolver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.net.Uri;
import android.provider.DocumentsContract;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

/** Local-only registry for Android Storage Access Framework tree grants. */
final class AndroidLocalFolderRegistry {
    private static final String PREFS = "simpleoffice-local-folders";
    private static final String IDS_KEY = "folder-ids";
    private static final String PREFIX = "folder:";
    private static final int MAX_FOLDERS = 100;
    private static final int MAX_NAME = 120;
    private static final int MAX_DESCRIPTION = 500;

    private final Context context;
    private final SharedPreferences preferences;

    AndroidLocalFolderRegistry(Context context) {
        this.context = context.getApplicationContext();
        this.preferences = this.context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    synchronized String listJson() {
        JSONArray result = new JSONArray();
        for (String id : sortedIds()) {
            JSONObject entry = readEntry(id);
            if (entry != null) {
                try {
                    result.put(publicEntry(entry));
                } catch (JSONException error) {
                    throw new IllegalStateException("Lokale Ordner konnten nicht gelesen werden.", error);
                }
            }
        }
        return result.toString();
    }

    synchronized String saveSelection(Intent result, String replaceId) throws JSONException {
        Uri treeUri = result == null ? null : result.getData();
        if (treeUri == null || !"content".equalsIgnoreCase(treeUri.getScheme())) {
            throw new IllegalArgumentException("Der ausgewählte Ordner hat keine gültige Android-Berechtigung.");
        }
        String id = replaceId == null || replaceId.trim().isEmpty()
                ? UUID.randomUUID().toString() : validId(replaceId);
        JSONObject previous = readEntry(id);
        if (replaceId != null && !replaceId.trim().isEmpty() && previous == null) {
            throw new IllegalArgumentException("Der zu ersetzende lokale Ordner wurde nicht gefunden.");
        }
        int grantFlags = result.getFlags()
                & (Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        if ((grantFlags & Intent.FLAG_GRANT_READ_URI_PERMISSION) == 0) {
            throw new SecurityException("Android hat keinen Lesezugriff auf den ausgewählten Ordner erteilt.");
        }
        ContentResolver resolver = context.getContentResolver();
        resolver.takePersistableUriPermission(treeUri, grantFlags);
        boolean reusedGrant = previous != null && treeUri.toString().equals(previous.optString("uri", ""));

        JSONObject entry = new JSONObject();
        entry.put("id", id);
        entry.put("uri", treeUri.toString());
        entry.put("grants", grantFlags);
        entry.put("name", folderName(treeUri));
        entry.put("description", previous == null ? "" : previous.optString("description", ""));
        entry.put("created_at", previous == null ? System.currentTimeMillis() : previous.optLong("created_at"));
        entry.put("updated_at", System.currentTimeMillis());

        Set<String> ids = new HashSet<>(preferences.getStringSet(IDS_KEY, new HashSet<>()));
        if (!ids.contains(id) && ids.size() >= MAX_FOLDERS) {
            if (!reusedGrant) releaseGrant(resolver, treeUri, grantFlags);
            throw new IllegalStateException("Höchstens 100 lokale Ordner können gespeichert werden.");
        }
        ids.add(id);
        boolean saved = preferences.edit()
                .putString(PREFIX + id, entry.toString())
                .putStringSet(IDS_KEY, ids)
                .commit();
        if (!saved) {
            if (!reusedGrant) releaseGrant(resolver, treeUri, grantFlags);
            throw new IllegalStateException("Die lokale Ordnerfreigabe konnte nicht gespeichert werden.");
        }
        if (previous != null && !treeUri.toString().equals(previous.optString("uri", ""))) {
            releaseGrant(resolver, Uri.parse(previous.optString("uri", "")), previous.optInt("grants"));
        }
        return publicEntry(entry).toString();
    }

    synchronized String rename(String idValue, String nameValue, String descriptionValue) throws JSONException {
        String id = validId(idValue);
        JSONObject entry = requireEntry(id);
        String name = cleanText(nameValue, MAX_NAME);
        String description = cleanText(descriptionValue, MAX_DESCRIPTION);
        if (name.isEmpty()) throw new IllegalArgumentException("Bitte einen Anzeigenamen eingeben.");
        entry.put("name", name);
        entry.put("description", description);
        entry.put("updated_at", System.currentTimeMillis());
        if (!preferences.edit().putString(PREFIX + id, entry.toString()).commit()) {
            throw new IllegalStateException("Der Anzeigename konnte nicht gespeichert werden.");
        }
        return publicEntry(entry).toString();
    }

    synchronized String check(String idValue) throws JSONException {
        JSONObject entry = requireEntry(validId(idValue));
        JSONObject result = publicEntry(entry);
        result.put("status", accessStatus(Uri.parse(entry.optString("uri", ""))));
        return result.toString();
    }

    synchronized boolean remove(String idValue) {
        String id = validId(idValue);
        JSONObject entry = readEntry(id);
        if (entry == null) return false;
        Uri uri = Uri.parse(entry.optString("uri", ""));
        Set<String> ids = new HashSet<>(preferences.getStringSet(IDS_KEY, new HashSet<>()));
        ids.remove(id);
        boolean removed = preferences.edit().remove(PREFIX + id).putStringSet(IDS_KEY, ids).commit();
        if (removed) releaseGrant(context.getContentResolver(), uri, entry.optInt("grants"));
        return removed;
    }

    synchronized Uri treeUriForImport(String idValue) {
        JSONObject entry = requireEntry(validId(idValue));
        Uri uri = Uri.parse(entry.optString("uri", ""));
        if (!hasReadGrant(context.getContentResolver(), uri)) {
            throw new SecurityException("Ordnerzugriff wurde widerrufen. Bitte den Ordner erneut auswählen.");
        }
        return uri;
    }

    private List<String> sortedIds() {
        List<String> ids = new ArrayList<>(preferences.getStringSet(IDS_KEY, new HashSet<>()));
        java.util.Collections.sort(ids);
        return ids;
    }

    private JSONObject readEntry(String id) {
        String serialized = preferences.getString(PREFIX + id, "");
        if (serialized.isEmpty()) return null;
        try {
            JSONObject entry = new JSONObject(serialized);
            if (!id.equals(entry.optString("id")) || entry.optString("uri").isEmpty()) return null;
            return entry;
        } catch (JSONException error) {
            return null;
        }
    }

    private JSONObject requireEntry(String id) {
        JSONObject entry = readEntry(id);
        if (entry == null) throw new IllegalArgumentException("Lokaler Ordner wurde nicht gefunden.");
        return entry;
    }

    private JSONObject publicEntry(JSONObject entry) throws JSONException {
        String uriValue = entry.optString("uri", "");
        String status = hasReadGrant(context.getContentResolver(), Uri.parse(uriValue))
                ? "permission_saved" : "permission_revoked";
        return new JSONObject()
                .put("id", entry.optString("id"))
                .put("name", entry.optString("name"))
                .put("description", entry.optString("description"))
                .put("status", status)
                .put("created_at", entry.optLong("created_at"))
                .put("updated_at", entry.optLong("updated_at"));
    }

    private String accessStatus(Uri treeUri) {
        ContentResolver resolver = context.getContentResolver();
        if (!hasReadGrant(resolver, treeUri)) return "permission_revoked";
        try {
            String documentId = DocumentsContract.getTreeDocumentId(treeUri);
            Uri documentUri = DocumentsContract.buildDocumentUriUsingTree(treeUri, documentId);
            try (Cursor cursor = resolver.query(
                    documentUri,
                    new String[]{DocumentsContract.Document.COLUMN_DOCUMENT_ID},
                    null,
                    null,
                    null)) {
                return cursor != null && cursor.moveToFirst() ? "available" : "unavailable";
            }
        } catch (Exception error) {
            return "unavailable";
        }
    }

    private static boolean hasReadGrant(ContentResolver resolver, Uri uri) {
        for (android.content.UriPermission permission : resolver.getPersistedUriPermissions()) {
            if (uri.equals(permission.getUri()) && permission.isReadPermission()) return true;
        }
        return false;
    }

    private String folderName(Uri treeUri) {
        try {
            String documentId = DocumentsContract.getTreeDocumentId(treeUri);
            Uri documentUri = DocumentsContract.buildDocumentUriUsingTree(treeUri, documentId);
            try (Cursor cursor = context.getContentResolver().query(
                    documentUri,
                    new String[]{DocumentsContract.Document.COLUMN_DISPLAY_NAME},
                    null,
                    null,
                    null)) {
                if (cursor != null && cursor.moveToFirst()) {
                    String value = cursor.getString(0);
                    if (value != null && !value.trim().isEmpty()) return cleanText(value, MAX_NAME);
                }
            }
        } catch (Exception ignored) {
            // Some document providers do not return root metadata; use the URI's non-path document id.
        }
        String documentId = DocumentsContract.getTreeDocumentId(treeUri);
        int split = documentId.lastIndexOf(':');
        String fallback = split >= 0 ? documentId.substring(split + 1) : documentId;
        fallback = fallback.replace('/', ' ').trim();
        return cleanText(fallback.isEmpty() ? "Lokaler Ordner" : fallback, MAX_NAME);
    }

    private static String validId(String value) {
        String id = value == null ? "" : value.trim();
        try {
            return UUID.fromString(id).toString();
        } catch (IllegalArgumentException error) {
            throw new IllegalArgumentException("Ungültige lokale Ordnerkennung.");
        }
    }

    private static String cleanText(String value, int limit) {
        String cleaned = value == null ? "" : value.trim();
        if (cleaned.length() > limit) throw new IllegalArgumentException("Text ist zu lang.");
        if (cleaned.chars().anyMatch(Character::isISOControl)) {
            throw new IllegalArgumentException("Text enthält unzulässige Steuerzeichen.");
        }
        return cleaned;
    }

    private static void releaseGrant(ContentResolver resolver, Uri uri, int flags) {
        if (uri == null) return;
        try {
            resolver.releasePersistableUriPermission(uri, flags);
        } catch (SecurityException ignored) {
            // The provider or user may already have revoked the persisted grant.
        }
    }
}

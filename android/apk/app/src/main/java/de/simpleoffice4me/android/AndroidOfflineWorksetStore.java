package de.simpleoffice4me.android;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.UUID;

/**
 * Bounded, app-private offline workset store.
 *
 * Server data remains authoritative. The store only accepts explicitly selected
 * items and a narrow mutation outbox. State is bound to the current Android
 * identity; switching account invalidates the local cache before it is exposed.
 */
final class AndroidOfflineWorksetStore {
    static final long MAX_ITEM_BYTES = 16L * 1024L * 1024L;
    static final long MAX_TOTAL_BYTES = 128L * 1024L * 1024L;
    static final int MAX_ITEMS = 128;
    static final int MAX_OUTBOX = 256;
    static final long MAX_RETENTION_SECONDS = 30L * 24L * 60L * 60L;

    private static final String IDENTITY_PREFS = "simpleoffice-android-identity";
    private static final String IDENTITY_EMAIL = "google-email";
    private static final String DIRECTORY = "offline-worksets";
    private static final String INDEX = "index.json";

    private final Context context;
    private final File root;
    private JSONObject state;

    AndroidOfflineWorksetStore(Context context) {
        this.context = context.getApplicationContext();
        this.root = new File(this.context.getFilesDir(), DIRECTORY);
        this.state = loadState();
        enforceOwner();
        pruneExpired();
    }

    synchronized String statusJson() {
        pruneExpired();
        JSONObject result = new JSONObject();
        try {
            result.put("enabled", true);
            result.put("items", state.getJSONArray("items").length());
            result.put("outbox", state.getJSONArray("outbox").length());
            result.put("bytes", totalBytes());
            result.put("maxBytes", MAX_TOTAL_BYTES);
            result.put("maxItems", MAX_ITEMS);
            result.put("owner", ownerFingerprint(currentOwner()));
            return result.toString();
        } catch (JSONException error) {
            return "{\"enabled\":false,\"error\":\"state\"}";
        }
    }

    synchronized String cacheItem(
            String itemId,
            String kind,
            String version,
            String payload,
            long retentionSeconds) {
        String normalizedId = normalizeId(itemId);
        String normalizedKind = normalizeKind(kind);
        String normalizedVersion = normalizeVersion(version);
        if (normalizedId == null || normalizedKind == null || normalizedVersion == null) return "invalid";
        if (payload == null) return "invalid";
        byte[] bytes = payload.getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_ITEM_BYTES) return "too-large";

        long retention = Math.max(60L, Math.min(retentionSeconds, MAX_RETENTION_SECONDS));
        long expiresAt = System.currentTimeMillis() + retention * 1000L;
        JSONArray items = state.optJSONArray("items");
        if (items == null) return "state-error";

        int existing = findItem(items, normalizedId, normalizedKind);
        if (existing < 0 && items.length() >= MAX_ITEMS) return "quota";

        File target = itemFile(normalizedKind, normalizedId);
        long replacedBytes = target.isFile() ? target.length() : 0L;
        if (totalBytes() - replacedBytes + bytes.length > MAX_TOTAL_BYTES) return "quota";
        if (!root.exists() && !root.mkdirs()) return "io-error";

        File temporary = new File(root, target.getName() + ".tmp");
        try (FileOutputStream output = new FileOutputStream(temporary, false)) {
            output.write(bytes);
            output.getFD().sync();
        } catch (Exception error) {
            temporary.delete();
            return "io-error";
        }
        if (target.exists() && !target.delete()) {
            temporary.delete();
            return "io-error";
        }
        if (!temporary.renameTo(target)) {
            temporary.delete();
            return "io-error";
        }

        JSONObject metadata = new JSONObject();
        try {
            metadata.put("id", normalizedId);
            metadata.put("kind", normalizedKind);
            metadata.put("version", normalizedVersion);
            metadata.put("savedAt", System.currentTimeMillis());
            metadata.put("expiresAt", expiresAt);
            metadata.put("bytes", bytes.length);
            if (existing >= 0) items.put(existing, metadata);
            else items.put(metadata);
            persist();
            return "ok";
        } catch (JSONException error) {
            target.delete();
            return "state-error";
        }
    }

    synchronized String readItem(String itemId, String kind) {
        pruneExpired();
        String normalizedId = normalizeId(itemId);
        String normalizedKind = normalizeKind(kind);
        if (normalizedId == null || normalizedKind == null) return errorJson("invalid");

        JSONArray items = state.optJSONArray("items");
        int index = items == null ? -1 : findItem(items, normalizedId, normalizedKind);
        if (index < 0) return errorJson("missing");
        JSONObject metadata = items.optJSONObject(index);
        File file = itemFile(normalizedKind, normalizedId);
        if (metadata == null || !file.isFile() || file.length() > MAX_ITEM_BYTES) return errorJson("missing");

        try (FileInputStream input = new FileInputStream(file)) {
            byte[] data = new byte[(int) file.length()];
            int offset = 0;
            while (offset < data.length) {
                int read = input.read(data, offset, data.length - offset);
                if (read < 0) break;
                offset += read;
            }
            if (offset != data.length) return errorJson("io-error");
            JSONObject result = new JSONObject();
            result.put("status", "ok");
            result.put("id", normalizedId);
            result.put("kind", normalizedKind);
            result.put("version", metadata.optString("version", ""));
            result.put("savedAt", metadata.optLong("savedAt", 0L));
            result.put("expiresAt", metadata.optLong("expiresAt", 0L));
            result.put("payload", new String(data, StandardCharsets.UTF_8));
            return result.toString();
        } catch (Exception error) {
            return errorJson("io-error");
        }
    }

    synchronized String enqueueMutation(
            String mutationType,
            String targetId,
            String baseVersion,
            String payload) {
        String normalizedType = mutationType == null ? "" : mutationType.trim().toLowerCase(Locale.ROOT);
        if (!("note".equals(normalizedType) || "task_status".equals(normalizedType))) return "unsupported";
        String normalizedId = normalizeId(targetId);
        String normalizedVersion = normalizeVersion(baseVersion);
        if (normalizedId == null || normalizedVersion == null || payload == null) return "invalid";
        byte[] payloadBytes = payload.getBytes(StandardCharsets.UTF_8);
        if (payloadBytes.length > 256 * 1024) return "too-large";

        JSONArray outbox = state.optJSONArray("outbox");
        if (outbox == null || outbox.length() >= MAX_OUTBOX) return "quota";
        JSONObject operation = new JSONObject();
        try {
            String operationId = UUID.randomUUID().toString();
            operation.put("operationId", operationId);
            operation.put("type", normalizedType);
            operation.put("targetId", normalizedId);
            operation.put("baseVersion", normalizedVersion);
            operation.put("createdAt", System.currentTimeMillis());
            operation.put("status", "pending");
            operation.put("payload", payload);
            outbox.put(operation);
            persist();
            return operationId;
        } catch (JSONException error) {
            return "state-error";
        }
    }

    synchronized String outboxJson() {
        JSONArray outbox = state.optJSONArray("outbox");
        return outbox == null ? "[]" : outbox.toString();
    }

    synchronized String acknowledgeMutation(String operationId, String resultStatus) {
        String id = operationId == null ? "" : operationId.trim();
        String status = resultStatus == null ? "" : resultStatus.trim().toLowerCase(Locale.ROOT);
        if (id.isEmpty()) return "invalid";
        if (!("synced".equals(status) || "conflict".equals(status) || "rejected".equals(status))) return "invalid";
        JSONArray outbox = state.optJSONArray("outbox");
        if (outbox == null) return "state-error";
        for (int i = 0; i < outbox.length(); i++) {
            JSONObject operation = outbox.optJSONObject(i);
            if (operation != null && id.equals(operation.optString("operationId"))) {
                if ("synced".equals(status)) {
                    outbox.remove(i);
                } else {
                    try {
                        operation.put("status", status);
                    } catch (JSONException error) {
                        return "state-error";
                    }
                }
                persist();
                return "ok";
            }
        }
        return "missing";
    }

    synchronized String clear() {
        deleteRecursively(root);
        state = emptyState(currentOwner());
        persist();
        return "ok";
    }

    private void enforceOwner() {
        String owner = currentOwner();
        String stored = state.optString("owner", "");
        if (!stored.equals(owner)) {
            deleteRecursively(root);
            state = emptyState(owner);
            persist();
        }
    }

    private String currentOwner() {
        SharedPreferences identity = context.getSharedPreferences(IDENTITY_PREFS, Context.MODE_PRIVATE);
        return identity.getString(IDENTITY_EMAIL, "") == null
                ? ""
                : identity.getString(IDENTITY_EMAIL, "").trim().toLowerCase(Locale.ROOT);
    }

    private JSONObject loadState() {
        File file = new File(root, INDEX);
        if (!file.isFile() || file.length() > 1024 * 1024) return emptyState(currentOwner());
        try (FileInputStream input = new FileInputStream(file)) {
            byte[] data = new byte[(int) file.length()];
            int offset = 0;
            while (offset < data.length) {
                int read = input.read(data, offset, data.length - offset);
                if (read < 0) break;
                offset += read;
            }
            JSONObject parsed = new JSONObject(new String(data, 0, offset, StandardCharsets.UTF_8));
            if (!(parsed.opt("items") instanceof JSONArray) || !(parsed.opt("outbox") instanceof JSONArray)) {
                return emptyState(currentOwner());
            }
            return parsed;
        } catch (Exception error) {
            return emptyState(currentOwner());
        }
    }

    private JSONObject emptyState(String owner) {
        JSONObject value = new JSONObject();
        try {
            value.put("schema", 1);
            value.put("owner", owner == null ? "" : owner);
            value.put("items", new JSONArray());
            value.put("outbox", new JSONArray());
        } catch (JSONException ignored) {
        }
        return value;
    }

    private void pruneExpired() {
        JSONArray items = state.optJSONArray("items");
        if (items == null) return;
        long now = System.currentTimeMillis();
        boolean changed = false;
        for (int i = items.length() - 1; i >= 0; i--) {
            JSONObject item = items.optJSONObject(i);
            if (item == null || item.optLong("expiresAt", 0L) <= now) {
                if (item != null) itemFile(item.optString("kind"), item.optString("id")).delete();
                items.remove(i);
                changed = true;
            }
        }
        if (changed) persist();
    }

    private long totalBytes() {
        JSONArray items = state.optJSONArray("items");
        long total = 0L;
        if (items == null) return 0L;
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item != null) total += Math.max(0L, item.optLong("bytes", 0L));
        }
        return total;
    }

    private int findItem(JSONArray items, String itemId, String kind) {
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item != null && itemId.equals(item.optString("id")) && kind.equals(item.optString("kind"))) return i;
        }
        return -1;
    }

    private File itemFile(String kind, String itemId) {
        return new File(root, sha256(kind + ":" + itemId) + ".cache");
    }

    private String normalizeId(String value) {
        String normalized = value == null ? "" : value.trim();
        if (normalized.isEmpty() || normalized.length() > 256) return null;
        return normalized;
    }

    private String normalizeKind(String value) {
        String normalized = value == null ? "" : value.trim().toLowerCase(Locale.ROOT);
        if ("document".equals(normalized) || "project".equals(normalized)
                || "note".equals(normalized) || "task".equals(normalized)) return normalized;
        return null;
    }

    private String normalizeVersion(String value) {
        String normalized = value == null ? "" : value.trim();
        return normalized.isEmpty() || normalized.length() > 256 ? null : normalized;
    }

    private void persist() {
        if (!root.exists() && !root.mkdirs()) return;
        File file = new File(root, INDEX);
        File temporary = new File(root, INDEX + ".tmp");
        try (FileOutputStream output = new FileOutputStream(temporary, false)) {
            byte[] data = state.toString().getBytes(StandardCharsets.UTF_8);
            output.write(data);
            output.getFD().sync();
        } catch (Exception error) {
            temporary.delete();
            return;
        }
        if (file.exists() && !file.delete()) {
            temporary.delete();
            return;
        }
        if (!temporary.renameTo(file)) temporary.delete();
    }

    private static String ownerFingerprint(String owner) {
        return owner == null || owner.isEmpty() ? "" : sha256(owner).substring(0, 12);
    }

    private static String sha256(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder(bytes.length * 2);
            for (byte b : bytes) result.append(String.format(Locale.ROOT, "%02x", b & 0xff));
            return result.toString();
        } catch (Exception error) {
            throw new IllegalStateException("SHA-256 unavailable", error);
        }
    }

    private static String errorJson(String status) {
        try {
            JSONObject value = new JSONObject();
            value.put("status", status);
            return value.toString();
        } catch (JSONException error) {
            return "{\"status\":\"error\"}";
        }
    }

    private static void deleteRecursively(File file) {
        if (file == null || !file.exists()) return;
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children != null) for (File child : children) deleteRecursively(child);
        }
        file.delete();
    }
}

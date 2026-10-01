package de.simpleoffice4me.android;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
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
    static final int MAX_OUTBOX = 64;
    static final long MAX_RETENTION_SECONDS = 30L * 24L * 60L * 60L;

    private static final String IDENTITY_PREFS = "simpleoffice-android-identity";
    private static final String IDENTITY_EMAIL = "google-email";
    private static final String DIRECTORY = "offline-worksets";
    private static final String INDEX = "index.json";

    private final Context context;
    private final File root;
    private JSONObject state;
    private String sessionOwner = "";

    AndroidOfflineWorksetStore(Context context) {
        this.context = context.getApplicationContext();
        this.root = new File(this.context.getFilesDir(), DIRECTORY);
        this.state = loadState();
    }

    synchronized String bindOwner(String owner) {
        String normalized = owner == null ? "" : owner.trim();
        if (!normalized.matches("[1-9][0-9]{0,18}")) return "invalid";
        sessionOwner = normalized;
        if (!enforceOwner()) return "blocked";
        recoverInterruptedItemWrites();
        pruneExpired();
        cleanupOrphanedItemFiles();
        return "ok";
    }

    synchronized void unbindOwner() {
        sessionOwner = "";
    }

    synchronized String statusJson() {
        if (!enforceOwner()) return "{\"enabled\":false,\"error\":\"unbound\"}";
        pruneExpired();
        JSONObject result = new JSONObject();
        try {
            JSONArray items = state.getJSONArray("items");
            JSONObject groups = new JSONObject();
            for (int i = 0; i < items.length(); i++) {
                JSONObject item = items.optJSONObject(i);
                if (item == null) continue;
                String name = item.optString("workset", "default");
                JSONObject group = groups.optJSONObject(name);
                if (group == null) {
                    group = new JSONObject();
                    group.put("name", name);
                    group.put("items", 0);
                    group.put("bytes", 0L);
                    groups.put(name, group);
                }
                group.put("items", group.optInt("items", 0) + 1);
                group.put("bytes", group.optLong("bytes", 0L) + Math.max(0L, item.optLong("bytes", 0L)));
            }
            JSONArray worksets = new JSONArray();
            JSONArray names = groups.names();
            if (names != null) {
                for (int i = 0; i < names.length(); i++) {
                    JSONObject group = groups.optJSONObject(names.optString(i));
                    if (group != null) worksets.put(group);
                }
            }
            result.put("enabled", true);
            result.put("items", items.length());
            result.put("outbox", state.getJSONArray("outbox").length());
            result.put("bytes", totalBytes());
            result.put("maxBytes", MAX_TOTAL_BYTES);
            result.put("maxItems", MAX_ITEMS);
            result.put("owner", ownerFingerprint(currentOwner()));
            result.put("worksets", worksets);
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
            long retentionSeconds,
            String workset) {
        if (!enforceOwner()) return "blocked";
        String normalizedId = normalizeId(itemId);
        String normalizedKind = normalizeKind(kind);
        String normalizedVersion = normalizeVersion(version);
        String normalizedWorkset = normalizeWorkset(workset);
        if (normalizedId == null || normalizedKind == null || normalizedVersion == null || normalizedWorkset == null) return "invalid";
        if (payload == null) return "invalid";
        byte[] bytes = payload.getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_ITEM_BYTES) return "too-large";
        String contentHash = sha256Bytes(bytes);

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
        File backup = new File(root, target.getName() + ".bak");
        if (backup.exists() && !backup.delete()) {
            temporary.delete();
            return "io-error";
        }
        if (target.exists() && !target.renameTo(backup)) {
            temporary.delete();
            return "io-error";
        }
        if (!temporary.renameTo(target)) {
            temporary.delete();
            if (backup.isFile()) backup.renameTo(target);
            return "io-error";
        }

        JSONObject metadata = new JSONObject();
        String previousItems = items.toString();
        try {
            metadata.put("id", normalizedId);
            metadata.put("kind", normalizedKind);
            metadata.put("version", normalizedVersion);
            metadata.put("savedAt", System.currentTimeMillis());
            metadata.put("expiresAt", expiresAt);
            metadata.put("bytes", bytes.length);
            metadata.put("workset", normalizedWorkset);
            metadata.put("contentSha256", contentHash);
            if (existing >= 0) items.put(existing, metadata);
            else items.put(metadata);
            if (!persist()) {
                state.put("items", new JSONArray(previousItems));
                target.delete();
                if (backup.isFile()) backup.renameTo(target);
                return "io-error";
            }
            backup.delete();
            return "ok";
        } catch (JSONException error) {
            target.delete();
            if (backup.isFile()) backup.renameTo(target);
            return "state-error";
        }
    }

    synchronized String readItem(String itemId, String kind) {
        if (!enforceOwner()) return errorJson("blocked");
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
            String expectedHash = metadata.optString("contentSha256", "");
            if (!expectedHash.isEmpty() && !expectedHash.equals(sha256Bytes(data))) {
                return errorJson("integrity");
            }
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

    synchronized String itemsJson() {
        if (!enforceOwner()) return "[]";
        pruneExpired();
        JSONArray source = state.optJSONArray("items");
        JSONArray result = new JSONArray();
        if (source == null) return result.toString();
        for (int i = 0; i < source.length(); i++) {
            JSONObject item = source.optJSONObject(i);
            if (item == null) continue;
            JSONObject metadata = new JSONObject();
            try {
                for (String key : new String[]{"id", "kind", "version", "savedAt", "expiresAt", "bytes", "workset"}) {
                    if (item.has(key)) metadata.put(key, item.opt(key));
                }
                if (!metadata.has("workset")) metadata.put("workset", "default");
                result.put(metadata);
            } catch (JSONException ignored) {
            }
        }
        return result.toString();
    }

    synchronized String removeItem(String itemId, String kind) {
        if (!enforceOwner()) return "blocked";
        String normalizedId = normalizeId(itemId);
        String normalizedKind = normalizeKind(kind);
        if (normalizedId == null || normalizedKind == null) return "invalid";
        JSONArray items = state.optJSONArray("items");
        int index = items == null ? -1 : findItem(items, normalizedId, normalizedKind);
        if (index < 0) return "missing";
        JSONArray outbox = state.optJSONArray("outbox");
        if (outbox != null) {
            for (int i = 0; i < outbox.length(); i++) {
                JSONObject operation = outbox.optJSONObject(i);
                if (operation == null || !normalizedId.equals(operation.optString("targetId"))) continue;
                String status = operation.optString("status", "pending");
                if ("pending".equals(status) || "conflict".equals(status) || "rejected".equals(status)) {
                    return "pending-mutation";
                }
            }
        }

        File target = itemFile(normalizedKind, normalizedId);
        File backup = new File(root, target.getName() + ".remove.bak");
        if (backup.exists() && !backup.delete()) return "io-error";
        if (target.exists() && !target.renameTo(backup)) return "io-error";
        String previousItems = items.toString();
        items.remove(index);
        if (!persist()) {
            try {
                state.put("items", new JSONArray(previousItems));
            } catch (JSONException ignored) {
            }
            if (backup.isFile()) backup.renameTo(target);
            return "io-error";
        }
        backup.delete();
        return "ok";
    }

    synchronized String enqueueMutation(
            String mutationType,
            String targetId,
            String baseVersion,
            String payload) {
        if (!enforceOwner()) return "blocked";
        String normalizedType = mutationType == null ? "" : mutationType.trim().toLowerCase(Locale.ROOT);
        if (!"task_status".equals(normalizedType)) return "unsupported";
        String normalizedId = normalizeId(targetId);
        String normalizedVersion = normalizeVersion(baseVersion);
        if (normalizedId == null || normalizedVersion == null || payload == null) return "invalid";
        byte[] payloadBytes = payload.getBytes(StandardCharsets.UTF_8);
        if (payloadBytes.length > 8 * 1024) return "too-large";

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
            String previousOutbox = outbox.toString();
            outbox.put(operation);
            if (!persist()) {
                state.put("outbox", new JSONArray(previousOutbox));
                return "io-error";
            }
            return operationId;
        } catch (JSONException error) {
            return "state-error";
        }
    }

    synchronized String outboxJson() {
        if (!enforceOwner()) return "[]";
        JSONArray outbox = state.optJSONArray("outbox");
        return outbox == null ? "[]" : outbox.toString();
    }

    synchronized String acknowledgeMutation(String operationId, String resultStatus) {
        if (!enforceOwner()) return "blocked";
        String id = operationId == null ? "" : operationId.trim();
        String status = resultStatus == null ? "" : resultStatus.trim().toLowerCase(Locale.ROOT);
        if (id.isEmpty()) return "invalid";
        if (!("synced".equals(status) || "conflict".equals(status) || "rejected".equals(status) || "discarded".equals(status))) return "invalid";
        JSONArray outbox = state.optJSONArray("outbox");
        if (outbox == null) return "state-error";
        for (int i = 0; i < outbox.length(); i++) {
            JSONObject operation = outbox.optJSONObject(i);
            if (operation != null && id.equals(operation.optString("operationId"))) {
                String previousOutbox = outbox.toString();
                if ("synced".equals(status) || "discarded".equals(status)) {
                    outbox.remove(i);
                } else {
                    try {
                        operation.put("status", status);
                    } catch (JSONException error) {
                        return "state-error";
                    }
                }
                if (!persist()) {
                    try {
                        state.put("outbox", new JSONArray(previousOutbox));
                    } catch (JSONException error) {
                        return "state-error";
                    }
                    return "io-error";
                }
                return "ok";
            }
        }
        return "missing";
    }

    synchronized String clear() {
        if (!enforceOwner()) return "blocked";
        if (!deleteRecursively(root)) return "io-error";
        state = emptyState(currentOwner());
        return persist() ? "ok" : "io-error";
    }

    synchronized String clearForAccountSwitch() {
        if (!enforceOwner()) return "blocked";
        if (!deleteRecursively(root)) return "io-error";
        state = emptyState("");
        sessionOwner = "";
        return "ok";
    }

    private boolean enforceOwner() {
        String owner = currentOwner();
        if (owner.isEmpty()) return false;
        String stored = state.optString("owner", "");
        if (!stored.equals(owner)) {
            if (!deleteRecursively(root)) {
                state = emptyState("");
                return false;
            }
            state = emptyState(owner);
            if (!persist()) {
                state = emptyState("");
                return false;
            }
        }
        return true;
    }

    private String currentOwner() {
        if (sessionOwner.isEmpty()) return "";
        SharedPreferences identity = context.getSharedPreferences(IDENTITY_PREFS, Context.MODE_PRIVATE);
        String email = identity.getString(IDENTITY_EMAIL, "");
        String normalizedEmail = email == null ? "" : email.trim().toLowerCase(Locale.ROOT);
        return "android:" + normalizedEmail + "|user:" + sessionOwner;
    }

    private JSONObject loadState() {
        for (String name : new String[]{INDEX, INDEX + ".bak"}) {
            File file = new File(root, name);
            if (!file.isFile() || file.length() > 1024 * 1024) continue;
            try (FileInputStream input = new FileInputStream(file)) {
                byte[] data = new byte[(int) file.length()];
                int offset = 0;
                while (offset < data.length) {
                    int read = input.read(data, offset, data.length - offset);
                    if (read < 0) break;
                    offset += read;
                }
                if (offset != data.length) continue;
                JSONObject parsed = new JSONObject(new String(data, StandardCharsets.UTF_8));
                if (!(parsed.opt("items") instanceof JSONArray) || !(parsed.opt("outbox") instanceof JSONArray)) {
                    continue;
                }
                return parsed;
            } catch (IOException | JSONException error) {
                // Try the transactional backup before falling back to an empty state.
            }
        }
        return emptyState(currentOwner());
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

    private void recoverInterruptedItemWrites() {
        JSONArray items = state.optJSONArray("items");
        if (items == null) return;
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item == null) continue;
            String id = normalizeId(item.optString("id"));
            String kind = normalizeKind(item.optString("kind"));
            if (id == null || kind == null) continue;
            File target = itemFile(kind, id);
            File backup = new File(root, target.getName() + ".bak");
            File removeBackup = new File(root, target.getName() + ".remove.bak");
            String expectedHash = item.optString("contentSha256", "");

            if (target.isFile() && (expectedHash.isEmpty() || fileMatchesHash(target, expectedHash))) {
                if (!expectedHash.isEmpty()) {
                    backup.delete();
                    removeBackup.delete();
                }
                continue;
            }

            File recovery = null;
            if (!expectedHash.isEmpty()) {
                if (fileMatchesHash(backup, expectedHash)) recovery = backup;
                else if (fileMatchesHash(removeBackup, expectedHash)) recovery = removeBackup;
            } else if (!target.isFile()) {
                if (backup.isFile()) recovery = backup;
                else if (removeBackup.isFile()) recovery = removeBackup;
            }
            if (recovery == null) continue;
            if (target.exists() && !target.delete()) continue;
            recovery.renameTo(target);
        }
    }

    private void cleanupOrphanedItemFiles() {
        JSONArray items = state.optJSONArray("items");
        if (items == null) return;
        Set<String> known = new HashSet<>();
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item == null) continue;
            String id = normalizeId(item.optString("id"));
            String kind = normalizeKind(item.optString("kind"));
            if (id != null && kind != null) known.add(itemFile(kind, id).getName());
        }

        File[] files = root.listFiles();
        if (files == null) return;
        for (File file : files) {
            if (!file.isFile()) continue;
            String name = file.getName();
            if (name.endsWith(".cache")) {
                if (!known.contains(name)) file.delete();
                continue;
            }
            if (name.endsWith(".cache.tmp")) {
                file.delete();
                continue;
            }
            String base = null;
            if (name.endsWith(".cache.remove.bak")) {
                base = name.substring(0, name.length() - ".remove.bak".length());
            } else if (name.endsWith(".cache.bak")) {
                base = name.substring(0, name.length() - ".bak".length());
            }
            if (base == null) continue;
            if (!known.contains(base) || new File(root, base).isFile()) file.delete();
        }
    }

    private long totalBytes() {
        long total = 0L;
        File[] files = root.listFiles();
        if (files == null) return 0L;
        for (File file : files) {
            if (file.isFile() && file.getName().endsWith(".cache")) {
                total += Math.max(0L, file.length());
            }
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
        return "task".equals(normalized) ? normalized : null;
    }

    private String normalizeVersion(String value) {
        String normalized = value == null ? "" : value.trim();
        return normalized.isEmpty() || normalized.length() > 256 ? null : normalized;
    }

    private String normalizeWorkset(String value) {
        String normalized = value == null ? "" : value.trim();
        if (normalized.isEmpty()) normalized = "default";
        return normalized.length() > 80 ? null : normalized;
    }

    private boolean persist() {
        if (!root.exists() && !root.mkdirs()) return false;
        File file = new File(root, INDEX);
        File temporary = new File(root, INDEX + ".tmp");
        File backup = new File(root, INDEX + ".bak");
        try (FileOutputStream output = new FileOutputStream(temporary, false)) {
            byte[] data = state.toString().getBytes(StandardCharsets.UTF_8);
            output.write(data);
            output.getFD().sync();
        } catch (Exception error) {
            temporary.delete();
            return false;
        }
        if (backup.exists() && !backup.delete()) {
            temporary.delete();
            return false;
        }
        if (file.exists() && !file.renameTo(backup)) {
            temporary.delete();
            return false;
        }
        if (!temporary.renameTo(file)) {
            temporary.delete();
            if (backup.isFile()) backup.renameTo(file);
            return false;
        }
        backup.delete();
        return true;
    }

    private static String ownerFingerprint(String owner) {
        return owner == null || owner.isEmpty() ? "" : sha256(owner).substring(0, 12);
    }

    private static String sha256(String value) {
        return sha256Bytes(value.getBytes(StandardCharsets.UTF_8));
    }

    private static String sha256Bytes(byte[] value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return hexDigest(digest.digest(value));
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 unavailable", error);
        }
    }

    private static boolean fileMatchesHash(File file, String expectedHash) {
        if (file == null || !file.isFile() || expectedHash == null || expectedHash.isEmpty()) return false;
        try (FileInputStream input = new FileInputStream(file)) {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                if (read > 0) digest.update(buffer, 0, read);
            }
            return expectedHash.equals(hexDigest(digest.digest()));
        } catch (IOException error) {
            return false;
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 unavailable", error);
        }
    }

    private static String hexDigest(byte[] bytes) {
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) result.append(String.format(Locale.ROOT, "%02x", b & 0xff));
        return result.toString();
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

    private static boolean deleteRecursively(File file) {
        if (file == null || !file.exists()) return true;
        boolean removed = true;
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children == null) return false;
            for (File child : children) removed = deleteRecursively(child) && removed;
        }
        return file.delete() && removed;
    }
}

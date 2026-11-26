package de.simpleoffice4me.android;

import android.content.ContentResolver;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.os.CancellationSignal;
import android.os.OperationCanceledException;
import android.provider.DocumentsContract;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CancellationException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/** Holds one temporary metadata-only snapshot; never persists file names or reads file bytes. */
final class AndroidLocalFolderNames {
    interface Listener {
        void scanned(String requestId, String owner, String status, JSONArray files, boolean limited);
        void open(String requestId, String owner, String status, Uri uri, String mime);
    }

    private static final String[] COLUMNS = {
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
    };
    private static final class Snapshot {
        final String sourceId;
        final String owner;
        final Uri tree;
        final Map<String, String> documents = new HashMap<>();

        Snapshot(String sourceId, String owner, Uri tree) {
            this.sourceId = sourceId;
            this.owner = owner;
            this.tree = tree;
        }
    }

    private final ContentResolver resolver;
    private final AndroidLocalFolderRegistry registry;
    private final Listener listener;
    private final ThreadPoolExecutor executor = new ThreadPoolExecutor(
            1, 1, 0, TimeUnit.MILLISECONDS, new ArrayBlockingQueue<>(1));
    private CancellationSignal pending;
    private Snapshot snapshot;
    private String preparedOpenId = "";

    AndroidLocalFolderNames(Context context, AndroidLocalFolderRegistry registry, Listener listener) {
        this.resolver = context.getApplicationContext().getContentResolver();
        this.registry = registry;
        this.listener = listener;
    }

    synchronized boolean scan(String sourceId, String owner, String requestId) {
        if (!validIdentifier(sourceId) || !validIdentifier(requestId) || !AndroidLocalFolderRegistry.validOwner(owner)
                || executor.isShutdown()) return false;
        clear();
        CancellationSignal signal = new CancellationSignal();
        pending = signal;
        executor.execute(() -> read(sourceId, owner, requestId, signal));
        return true;
    }

    private void read(String sourceId, String owner, String requestId, CancellationSignal signal) {
        try {
            Uri tree = registry.treeUriForSearch(sourceId, owner);
            Snapshot result = new Snapshot(sourceId, owner, tree);
            AndroidFolderFilenameIndex.Snapshot inventory = AndroidFolderFilenameIndex.read(
                    DocumentsContract.getTreeDocumentId(tree), directory -> children(resolver, tree, directory, signal),
                    signal::isCanceled);
            JSONArray files = new JSONArray();
            for (AndroidFolderFilenameIndex.FileName file : inventory.files) {
                String opaqueId = UUID.randomUUID().toString();
                result.documents.put(opaqueId, file.file.id);
                files.put(new JSONObject().put("id", opaqueId).put("name", file.file.name)
                        .put("parent", file.parent));
            }
            if (!tree.equals(registry.treeUriForSearch(sourceId, owner))) return;
            finishScan(signal, requestId, owner, "available", result, files, inventory.limited);
        } catch (OperationCanceledException | CancellationException ignored) {
            // Re-selection or page leave invalidated this result.
        } catch (SecurityException denied) {
            finishScan(signal, requestId, owner, "permission_revoked", null, null, false);
        } catch (RuntimeException | JSONException unavailable) {
            finishScan(signal, requestId, owner, "unavailable", null, null, false);
        }
    }

    static AndroidFolderFilenameIndex.Reader children(
            ContentResolver resolver, Uri tree, String directory, CancellationSignal signal) {
        Uri childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(tree, directory);
        Cursor rows = resolver.query(childrenUri, COLUMNS, null, null, null, signal);
        if (rows == null) return null;
        return new AndroidFolderFilenameIndex.Reader() {
            @Override public AndroidFolderFilenameIndex.Node next() {
                if (!rows.moveToNext()) return null;
                String id = rows.getString(rows.getColumnIndexOrThrow(COLUMNS[0]));
                String name = rows.getString(rows.getColumnIndexOrThrow(COLUMNS[1]));
                String mime = rows.getString(rows.getColumnIndexOrThrow(COLUMNS[2]));
                return new AndroidFolderFilenameIndex.Node(id, name,
                        DocumentsContract.Document.MIME_TYPE_DIR.equals(mime));
            }

            @Override public void close() {
                rows.close();
            }
        };
    }

    private synchronized void finishScan(CancellationSignal signal, String id, String owner, String state,
                                         Snapshot result, JSONArray files, boolean limited) {
        if (pending != signal || signal.isCanceled()) return;
        pending = null;
        snapshot = result;
        listener.scanned(id, owner, state, files == null ? new JSONArray() : files, limited);
    }

    synchronized String open(String opaqueId, String owner, String requestId) {
        if (!validIdentifier(opaqueId) || !validIdentifier(requestId) || executor.isShutdown()) return "missing";
        Snapshot version = snapshot;
        if (version == null || !version.owner.equals(owner)) return "missing";
        if (pending != null) return "busy";
        preparedOpenId = "";
        String document = version.documents.get(opaqueId);
        if (document == null) return "missing";
        CancellationSignal signal = new CancellationSignal();
        pending = signal;
        executor.execute(() -> prepareOpen(version, requestId, document, signal));
        return "pending";
    }

    private void prepareOpen(Snapshot version, String requestId, String document, CancellationSignal signal) {
        try {
            Uri tree = registry.treeUriForSearch(version.sourceId, version.owner);
            if (!tree.equals(version.tree)) {
                finishOpen(signal, requestId, version.owner, "missing", null, null);
                return;
            }
            Uri uri = DocumentsContract.buildDocumentUriUsingTree(tree, document);
            String mime;
            try (Cursor reported = resolver.query(uri, COLUMNS, null, null, null, signal)) {
                if (reported == null || !reported.moveToFirst()
                        || !document.equals(reported.getString(reported.getColumnIndexOrThrow(COLUMNS[0])))) {
                    finishOpen(signal, requestId, version.owner, "missing", null, null);
                    return;
                }
                mime = reported.getString(reported.getColumnIndexOrThrow(COLUMNS[2]));
            }
            if (DocumentsContract.Document.MIME_TYPE_DIR.equals(mime)) {
                finishOpen(signal, requestId, version.owner, "missing", null, null);
                return;
            }
            if (mime == null || !mime.matches("[A-Za-z0-9.+_-]{1,80}/[A-Za-z0-9.+_-]{1,80}")) {
                mime = "application/octet-stream";
            }
            // A provider or user may revoke access while its cursor is open.
            if (!tree.equals(registry.treeUriForSearch(version.sourceId, version.owner))) {
                finishOpen(signal, requestId, version.owner, "missing", null, null);
                return;
            }
            finishOpen(signal, requestId, version.owner, "ready", uri, mime);
        } catch (OperationCanceledException | CancellationException ignored) {
            // The caller navigated away or refreshed the source.
        } catch (SecurityException denied) {
            finishOpen(signal, requestId, version.owner, "permission_revoked", null, null);
        } catch (RuntimeException missing) {
            finishOpen(signal, requestId, version.owner, "missing", null, null);
        }
    }

    private synchronized void finishOpen(CancellationSignal signal, String id, String owner, String state,
                                         Uri uri, String mime) {
        if (pending != signal || signal.isCanceled()) return;
        pending = null;
        if ("permission_revoked".equals(state)) snapshot = null;
        if ("ready".equals(state)) preparedOpenId = id;
        listener.open(id, owner, state, uri, mime);
    }

    synchronized boolean consumePreparedOpen(String requestId, String owner) {
        Snapshot source = snapshot;
        if (source == null || !source.owner.equals(owner) || !preparedOpenId.equals(requestId)) return false;
        preparedOpenId = "";
        try {
            return source.tree.equals(registry.treeUriForSearch(source.sourceId, owner));
        } catch (IllegalArgumentException | SecurityException revoked) {
            snapshot = null;
            return false;
        }
    }

    synchronized void clear() {
        if (pending != null) pending.cancel();
        pending = null;
        snapshot = null;
        preparedOpenId = "";
        executor.getQueue().clear();
    }

    synchronized void close() {
        clear();
        executor.shutdownNow();
    }

    private static boolean validIdentifier(String id) {
        return id != null && id.matches("[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}");
    }
}

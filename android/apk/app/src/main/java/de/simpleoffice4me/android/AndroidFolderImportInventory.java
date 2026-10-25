package de.simpleoffice4me.android;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteConstraintException;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteException;
import android.net.Uri;

import java.io.File;
import java.io.IOException;
import java.text.Normalizer;
import java.util.AbstractList;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Disk-backed SAF traversal and file metadata inventory for large folder imports. */
final class AndroidFolderImportInventory extends AbstractList<AndroidFolderImportInventory.Entry>
        implements AutoCloseable {
    private static final String FILE_PREFIX = "simpleoffice-folder-import-";
    private static final long STALE_AGE_MILLIS = 7L * 24 * 60 * 60 * 1000;

    private final File databaseFile;
    private final SQLiteDatabase database;
    private int entryCount;
    private long totalBytes;
    private boolean closed;
    private boolean scanning;

    AndroidFolderImportInventory(Context context) throws IOException {
        File cacheDirectory = context.getCacheDir();
        if (cacheDirectory == null || (!cacheDirectory.isDirectory() && !cacheDirectory.mkdirs())) {
            throw new IOException("Android konnte keinen temporären Importordner anlegen.");
        }
        removeAbandonedInventories(cacheDirectory);
        databaseFile = File.createTempFile(FILE_PREFIX, ".sqlite", cacheDirectory);
        SQLiteDatabase opened = null;
        try {
            opened = SQLiteDatabase.openOrCreateDatabase(databaseFile, null);
            opened.execSQL("CREATE TABLE files (sort_key TEXT PRIMARY KEY, relative_path TEXT NOT NULL, "
                    + "content_uri TEXT NOT NULL, file_size INTEGER NOT NULL, source_identity TEXT NOT NULL)");
            opened.execSQL("CREATE TABLE directories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    + "tree_uri TEXT NOT NULL, document_id TEXT NOT NULL, relative_path TEXT NOT NULL, "
                    + "source_identity TEXT NOT NULL, tree_key TEXT NOT NULL, depth INTEGER NOT NULL)");
            opened.execSQL("CREATE TABLE visited_directories (tree_key TEXT NOT NULL, "
                    + "document_id TEXT NOT NULL, PRIMARY KEY(tree_key, document_id))");
        } catch (RuntimeException error) {
            if (opened != null) opened.close();
            deleteDatabaseFiles(databaseFile);
            throw error;
        }
        database = opened;
    }

    void beginScan() {
        database.beginTransaction();
        scanning = true;
    }

    void finishScan() {
        database.setTransactionSuccessful();
        database.endTransaction();
        scanning = false;
        touch();
    }

    boolean markVisited(String treeKey, String documentId) {
        ContentValues values = new ContentValues();
        values.put("tree_key", treeKey);
        values.put("document_id", documentId);
        return database.insertWithOnConflict(
                "visited_directories", null, values, SQLiteDatabase.CONFLICT_IGNORE) != -1;
    }

    void enqueueDirectory(Uri treeUri, String documentId, String relativePath,
            String sourceIdentity, String treeKey, int depth) {
        ContentValues values = new ContentValues();
        values.put("tree_uri", treeUri.toString());
        values.put("document_id", documentId);
        values.put("relative_path", relativePath);
        values.put("source_identity", sourceIdentity);
        values.put("tree_key", treeKey);
        values.put("depth", depth);
        database.insertOrThrow("directories", null, values);
    }

    Directory nextDirectory() {
        long id;
        Directory result;
        try (Cursor cursor = database.rawQuery(
                "SELECT id, tree_uri, document_id, relative_path, source_identity, tree_key, depth "
                        + "FROM directories ORDER BY id LIMIT 1", null)) {
            if (!cursor.moveToFirst()) return null;
            id = cursor.getLong(0);
            result = new Directory(cursor.getString(1), cursor.getString(2), cursor.getString(3),
                    cursor.getString(4), cursor.getString(5), cursor.getInt(6));
        }
        database.delete("directories", "id=?", new String[]{Long.toString(id)});
        return result;
    }

    void addEntry(Entry entry) throws AndroidFolderImportTask.ImportFailure {
        String sortKey = collisionKey(entry.relativePath);
        ContentValues values = new ContentValues();
        values.put("sort_key", sortKey);
        values.put("relative_path", entry.relativePath);
        values.put("content_uri", entry.uri.toString());
        values.put("file_size", entry.size);
        values.put("source_identity", entry.sourceIdentity);
        try {
            database.insertOrThrow("files", null, values);
        } catch (SQLiteConstraintException error) {
            throw new AndroidFolderImportTask.ImportFailure("Der Ordner enthält doppelte Dateipfade.");
        }
        entry.sortKey = sortKey;
        entryCount = Math.addExact(entryCount, 1);
        totalBytes = Math.addExact(totalBytes, entry.size);
    }

    List<Entry> readRange(String afterSortKey, int limit) {
        List<Entry> entries = new ArrayList<>(Math.max(0, Math.min(limit, entryCount)));
        try (Cursor cursor = database.rawQuery(
                "SELECT sort_key, relative_path, content_uri, file_size, source_identity FROM files "
                        + "WHERE sort_key>? ORDER BY sort_key LIMIT ?",
                new String[]{afterSortKey, Integer.toString(limit)})) {
            while (cursor.moveToNext()) {
                Entry entry = new Entry(Uri.parse(cursor.getString(2)), cursor.getString(1),
                        cursor.getLong(3), cursor.getString(4));
                entry.sortKey = cursor.getString(0);
                entries.add(entry);
            }
        }
        return entries;
    }

    @Override
    public Entry get(int index) {
        if (index < 0 || index >= entryCount) throw new IndexOutOfBoundsException();
        try (Cursor cursor = database.rawQuery(
                "SELECT sort_key, relative_path, content_uri, file_size, source_identity FROM files "
                        + "ORDER BY sort_key LIMIT 1 OFFSET ?",
                new String[]{Integer.toString(index)})) {
            if (!cursor.moveToFirst()) throw new IndexOutOfBoundsException();
            Entry entry = new Entry(Uri.parse(cursor.getString(2)), cursor.getString(1),
                    cursor.getLong(3), cursor.getString(4));
            entry.sortKey = cursor.getString(0);
            return entry;
        }
    }

    @Override
    public int size() {
        return entryCount;
    }

    long totalBytes() {
        return totalBytes;
    }

    void touch() {
        databaseFile.setLastModified(System.currentTimeMillis());
    }

    @Override
    public void close() {
        if (closed) return;
        closed = true;
        if (scanning) {
            try {
                database.endTransaction();
            } catch (SQLiteException ignored) {
                // Closing a partially scanned inventory rolls its transaction back.
            }
            scanning = false;
        }
        database.close();
        deleteDatabaseFiles(databaseFile);
    }

    private static String collisionKey(String path) {
        return Normalizer.normalize(path, Normalizer.Form.NFC).toLowerCase(Locale.ROOT);
    }

    private static void removeAbandonedInventories(File cacheDirectory) {
        File[] files = cacheDirectory.listFiles();
        if (files == null) return;
        long staleBefore = System.currentTimeMillis() - STALE_AGE_MILLIS;
        for (File file : files) {
            if (file.isFile() && file.getName().startsWith(FILE_PREFIX)
                    && file.lastModified() < staleBefore) {
                file.delete();
            }
        }
    }

    private static void deleteDatabaseFiles(File file) {
        file.delete();
        new File(file.getPath() + "-journal").delete();
        new File(file.getPath() + "-wal").delete();
        new File(file.getPath() + "-shm").delete();
    }

    static final class Directory {
        final String treeUri;
        final String documentId;
        final String relativePath;
        final String sourceIdentity;
        final String treeKey;
        final int depth;

        Directory(String treeUri, String documentId, String relativePath,
                String sourceIdentity, String treeKey, int depth) {
            this.treeUri = treeUri;
            this.documentId = documentId;
            this.relativePath = relativePath;
            this.sourceIdentity = sourceIdentity;
            this.treeKey = treeKey;
            this.depth = depth;
        }
    }

    static final class Entry {
        final Uri uri;
        final String relativePath;
        final long size;
        final String sourceIdentity;
        String sortKey;

        Entry(Uri uri, String relativePath, long size, String sourceIdentity) {
            this.uri = uri;
            this.relativePath = relativePath;
            this.size = size;
            this.sourceIdentity = sourceIdentity;
        }

        String fileName() {
            return relativePath.substring(relativePath.lastIndexOf('/') + 1);
        }
    }
}

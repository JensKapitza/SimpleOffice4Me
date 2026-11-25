package de.simpleoffice4me.android;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.function.BooleanSupplier;

/** Bounded, in-memory inventory of names in one explicitly selected SAF tree. */
final class AndroidFolderFilenameIndex {
    static final int MAX_NODES = 600;
    static final int MAX_RESULTS = 400;
    static final int MAX_DEPTH = 12;
    private static final int MAX_SEGMENT = 160;
    private static final int MAX_DOCUMENT_ID = 2048;

    interface Reader extends AutoCloseable {
        Node next();
        @Override void close();
    }

    interface Tree {
        Reader children(String directoryId);
    }

    static final class Node {
        final String id;
        final String name;
        final boolean directory;

        Node(String id, String name, boolean directory) {
            this.id = id;
            this.name = name;
            this.directory = directory;
        }
    }

    static final class FileName {
        final Node file;
        final String parent;

        FileName(Node file, String parent) {
            this.file = file;
            this.parent = parent;
        }
    }

    static final class Snapshot {
        final List<FileName> files;
        final boolean limited;

        Snapshot(List<FileName> files, boolean limited) {
            this.files = files;
            this.limited = limited;
        }
    }

    private static final class Directory {
        final String id;
        final String path;
        final int depth;

        Directory(String id, String path, int depth) {
            this.id = id;
            this.path = path;
            this.depth = depth;
        }
    }

    private AndroidFolderFilenameIndex() {}

    static Snapshot read(String root, Tree tree, BooleanSupplier cancelled) {
        if (!validDocumentId(root)) throw new IllegalArgumentException("Invalid selected SAF tree");
        final ArrayDeque<Directory> queue = new ArrayDeque<>();
        final Set<String> seen = new HashSet<>();
        final List<FileName> files = new ArrayList<>();
        seen.add(root);
        queue.add(new Directory(root, "", 0));
        int visited = 0;
        boolean limited = false;
        while (!queue.isEmpty()) {
            checkActive(cancelled);
            Directory parent = queue.remove();
            try (Reader children = tree.children(parent.id)) {
                if (children == null) throw new IllegalStateException("SAF source is unavailable");
                while (true) {
                    checkActive(cancelled);
                    // Count rows before validation (including duplicates and bad metadata).
                    // Stop before requesting the next row, so the provider cannot extend work.
                    if (visited == MAX_NODES || files.size() == MAX_RESULTS) {
                        return new Snapshot(files, true);
                    }
                    Node child = children.next();
                    if (child == null) break;
                    ++visited;
                    String name = displayName(child.name);
                    if (name.isEmpty() || !validDocumentId(child.id) || !seen.add(child.id)) continue;
                    if (child.directory) {
                        if (parent.depth == MAX_DEPTH) {
                            limited = true;
                            continue;
                        }
                        queue.add(new Directory(child.id, parent.path.isEmpty() ? name : parent.path + " / " + name,
                                parent.depth + 1));
                    } else {
                        // The caller does not pass MIME from Javascript to Android.
                        files.add(new FileName(new Node(child.id, name, false), parent.path));
                    }
                }
            }
        }
        return new Snapshot(files, limited);
    }

    private static void checkActive(BooleanSupplier cancelled) {
        if (cancelled.getAsBoolean()) throw new java.util.concurrent.CancellationException();
    }

    private static boolean validDocumentId(String id) {
        return id != null && !id.isEmpty() && id.length() <= MAX_DOCUMENT_ID;
    }

    private static String displayName(String supplied) {
        if (supplied == null) return "";
        StringBuilder value = new StringBuilder();
        for (int i = 0; i < supplied.length() && i < MAX_SEGMENT; ++i) {
            char current = supplied.charAt(i);
            if (Character.isISOControl(current) || Character.getType(current) == Character.FORMAT) continue;
            value.append(current == '/' || current == '\\' ? ' ' : current);
        }
        return value.toString().trim();
    }
}

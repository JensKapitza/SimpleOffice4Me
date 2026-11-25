package de.simpleoffice4me.android;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CancellationException;
import java.util.concurrent.atomic.AtomicInteger;

public final class AndroidFolderFilenameIndexTest {
    private static AndroidFolderFilenameIndex.Node node(String id, String name, boolean isDirectory) {
        return new AndroidFolderFilenameIndex.Node(id, name, isDirectory);
    }

    private static AndroidFolderFilenameIndex.Reader reader(List<AndroidFolderFilenameIndex.Node> rows) {
        Iterator<AndroidFolderFilenameIndex.Node> iterator = rows.iterator();
        return new AndroidFolderFilenameIndex.Reader() {
            @Override public AndroidFolderFilenameIndex.Node next() { return iterator.hasNext() ? iterator.next() : null; }
            @Override public void close() {}
        };
    }

    @Test public void onlySelectedTreeAndDistinctDescendantsAreSearched() {
        Map<String, List<AndroidFolderFilenameIndex.Node>> provider = Map.of(
                "tree", List.of(node("child", "Ablage", true), node("a", "Beleg.pdf", false)),
                "child", List.of(node("b", "Zahlung.txt", false), node("tree", "Cycle", true)),
                "ungranted", List.of(node("x", "Unselected.txt", false)));
        List<String> queried = new ArrayList<>();
        AndroidFolderFilenameIndex.Snapshot snapshot = AndroidFolderFilenameIndex.read("tree", id -> {
            queried.add(id);
            return reader(provider.getOrDefault(id, List.of()));
        }, () -> false);
        assertEquals(List.of("tree", "child"), queried);
        assertEquals(2, snapshot.files.size());
        assertEquals("Zahlung.txt", snapshot.files.get(1).file.name);
        assertEquals("Ablage", snapshot.files.get(1).parent);
        assertFalse(snapshot.limited);
    }

    @Test public void malformedRepeatedRowsStillConsumeTheGlobalTraversalBudget() {
        AtomicInteger cursorRows = new AtomicInteger();
        AtomicInteger closed = new AtomicInteger();
        AndroidFolderFilenameIndex.Snapshot snapshot = AndroidFolderFilenameIndex.read("tree", id ->
                new AndroidFolderFilenameIndex.Reader() {
                    @Override public AndroidFolderFilenameIndex.Node next() {
                        cursorRows.incrementAndGet();
                        return node("tree", "/\u0000", true);
                    }
                    @Override public void close() { closed.incrementAndGet(); }
                }, () -> false);
        assertTrue(snapshot.limited);
        assertEquals(AndroidFolderFilenameIndex.MAX_NODES, cursorRows.get());
        assertEquals(1, closed.get());
        assertTrue(snapshot.files.isEmpty());
    }

    @Test public void cancellationDiscardsPartialInventoryAndClosesItsCursor() {
        AtomicInteger reads = new AtomicInteger();
        AtomicInteger closed = new AtomicInteger();
        assertThrows(CancellationException.class, () -> AndroidFolderFilenameIndex.read("tree", id ->
                new AndroidFolderFilenameIndex.Reader() {
                    @Override public AndroidFolderFilenameIndex.Node next() {
                        return node("a", "File " + reads.incrementAndGet(), false);
                    }
                    @Override public void close() { closed.incrementAndGet(); }
                }, () -> reads.get() >= 1));
        assertEquals(1, closed.get());
        assertEquals(1, reads.get());
    }

    @Test public void unavailableProviderDoesNotLookLikeAnEmptyDirectory() {
        assertThrows(IllegalStateException.class, () -> AndroidFolderFilenameIndex.read("tree", id -> null, () -> false));
        assertTrue(AndroidFolderFilenameIndex.read("tree", id -> reader(List.of()), () -> false).files.isEmpty());
    }
}

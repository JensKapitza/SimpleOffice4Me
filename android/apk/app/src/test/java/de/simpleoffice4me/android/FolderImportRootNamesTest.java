package de.simpleoffice4me.android;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;

import org.junit.Test;

public final class FolderImportRootNamesTest {
    @Test
    public void sameNamedRootsGetDistinctStableDestinationNames() {
        Set<String> reserved = new HashSet<>();

        String first = FolderImportRootNames.reserve(
                "Books", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", reserved);
        String second = FolderImportRootNames.reserve(
                "Books", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", reserved);

        assertEquals("Books", first);
        assertEquals("Books (bbbbbbbb)", second);
        assertEquals(2, reserved.size());
    }

    @Test
    public void repeatedSourceIdentityStillGetsCollisionFreeNames() {
        Set<String> reserved = new HashSet<>();
        String identity = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

        FolderImportRootNames.reserve("Books", identity, reserved);
        String second = FolderImportRootNames.reserve("Books", identity, reserved);
        String third = FolderImportRootNames.reserve("Books", identity, reserved);

        assertEquals("Books (aaaaaaaa)", second);
        assertEquals("Books (aaaaaaaa-2)", third);
    }

    @Test
    public void collisionKeyMatchesUnicodeCaseFoldForGermanSharpS() {
        Set<String> reserved = new HashSet<>();
        String first = FolderImportRootNames.reserve(
                "Straße", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", reserved);
        String second = FolderImportRootNames.reserve(
                "STRAẞE", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", reserved);

        assertEquals("Straße", first);
        assertEquals("STRAẞE (bbbbbbbb)", second);
    }

    @Test
    public void collisionSuffixKeepsUtf8SegmentWithinPortableLimit() {
        Set<String> reserved = new HashSet<>();
        String longName = "日".repeat(85);
        String identity = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

        FolderImportRootNames.reserve(longName, identity, reserved);
        String collision = FolderImportRootNames.reserve(longName, identity, reserved);

        assertTrue(collision.getBytes(StandardCharsets.UTF_8).length <= 255);
        assertTrue(collision.startsWith("日".repeat(81)));
        assertTrue(collision.endsWith(" (bbbbbbbb)"));
    }
}

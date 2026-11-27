package de.simpleoffice4me.android;

import org.junit.Test;

import java.io.IOException;

import static org.junit.Assert.*;

public class AndroidManagedSnapshotCursorTest {
    private static final String OWNER = "a".repeat(64);
    private static final String REV = "b".repeat(64);

    @Test
    public void previewAcceptsOnlyTheSameAccountAndRevisionWithinTwoMinutes() {
        assertTrue(AndroidManagedSnapshotCursor.approvalMatches("principal", "revision", 1000, 121000,
                "principal", "revision"));
        assertFalse(AndroidManagedSnapshotCursor.approvalMatches("principal", "revision", 1000, 121001,
                "principal", "revision"));
        assertFalse(AndroidManagedSnapshotCursor.approvalMatches("principal", "revision", 1000, 2000,
                "other", "revision"));
        assertFalse(AndroidManagedSnapshotCursor.approvalMatches("principal", "revision", 1000, 2000,
                "principal", "changed"));
    }

    @Test public void acceptsAFullThousandRowsWithoutOffsetReplay() throws IOException {
        AndroidManagedSnapshotCursor cursor = new AndroidManagedSnapshotCursor();
        assertEquals("", cursor.revision());
        for (int page = 0; page < 40; page++) {
            assertEquals(page * 25, cursor.offset());
            assertEquals(page == 39, cursor.accept(OWNER, REV, 25, page < 39 ? (page + 1) * 25 : null));
        }
        assertEquals(OWNER, cursor.principal());
        assertThrows(IOException.class, () -> cursor.accept(OWNER, REV, 1, null));
    }

    @Test public void rejectsChangedRevisionDifferentAccountAndSkippedOffsets() throws IOException {
        AndroidManagedSnapshotCursor cursor = new AndroidManagedSnapshotCursor();
        assertFalse(cursor.accept(OWNER, REV, 25, 25));
        assertThrows(IOException.class, () -> cursor.accept(OWNER, "c".repeat(64), 25, 50));
        assertThrows(IOException.class, () -> cursor.accept("c".repeat(64), REV, 25, 50));
        assertThrows(IOException.class, () -> cursor.accept(OWNER, REV, 25, 75));
        assertThrows(IOException.class, () -> cursor.accept(OWNER, REV, 0, null));
        assertTrue(cursor.accept(OWNER, REV, 3, null));
    }

    @Test public void acceptsAnEmptyExportWithoutInventingAContinuation() throws IOException {
        AndroidManagedSnapshotCursor cursor = new AndroidManagedSnapshotCursor();
        assertThrows(IOException.class, () -> cursor.accept(OWNER, REV, 0, 25));
        assertTrue(cursor.accept(OWNER, REV, 0, null));
    }
}

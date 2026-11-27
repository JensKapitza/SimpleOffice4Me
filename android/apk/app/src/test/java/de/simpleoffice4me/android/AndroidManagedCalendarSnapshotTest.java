package de.simpleoffice4me.android;

import org.junit.Test;

import java.util.List;

import static org.junit.Assert.assertThrows;

public class AndroidManagedCalendarSnapshotTest {
    private static AndroidManagedCalendarRows.Event event(String id, String original, String recurrence, String name) {
        return new AndroidManagedCalendarRows.Event(id, "demo", name, "UTC", id, "", 0, 86400000,
                "UTC", true, recurrence, "", "", original, 0, false);
    }

    @Test
    public void validatesMasterAcrossTransactionsBeforeAnyProviderWrite() {
        var master = event("master", "", "FREQ=DAILY", "Demo");
        var exception = event("exception", "master", "", "Demo");
        AndroidManagedCalendarRows.validateSnapshot(List.of(List.of(master), List.of(exception)));
        assertThrows(IllegalArgumentException.class, () ->
                AndroidManagedCalendarRows.validateSnapshot(List.of(List.of(exception), List.of(master))));
    }

    @Test
    public void rejectsDuplicatesAndConflictingMetadataAcrossPages() {
        var master = event("master", "", "FREQ=DAILY", "Demo");
        var wrongCalendarName = event("exception", "master", "", "Different metadata");
        assertThrows(IllegalArgumentException.class, () ->
                AndroidManagedCalendarRows.validateSnapshot(List.of(List.of(master), List.of(master))));
        assertThrows(IllegalArgumentException.class, () ->
                AndroidManagedCalendarRows.validateSnapshot(List.of(List.of(master), List.of(wrongCalendarName))));
    }
}

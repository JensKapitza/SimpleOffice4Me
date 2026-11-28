package de.simpleoffice4me.android;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class AndroidManagedCalendarRowsTest {
    @Test
    public void allDayRecurrencesUseWholeUtcDaysIncludingPreEpochDates() {
        long day = 86_400_000L;
        assertTrue(AndroidManagedCalendarRows.midnight(-day));
        assertTrue(AndroidManagedCalendarRows.midnight(0));
        assertFalse(AndroidManagedCalendarRows.midnight(-day + 1));
        assertEquals("P2D", AndroidManagedCalendarRows.encodeDuration(-day, day, true));
    }

    @Test
    public void timedRecurrencesUseSecondsAndDoNotRoundPositiveDurationToZero() {
        assertEquals("PT3600S", AndroidManagedCalendarRows.encodeDuration(100L, 3_600_100L, false));
        assertEquals("PT1S", AndroidManagedCalendarRows.encodeDuration(1L, 2L, false));
    }

    @Test
    public void providerIdIsStableAndIsolatedEvenForEqualSourceIds() {
        String id = AndroidManagedCalendarRows.providerEventId("account-a", "calendar-a", "event-1");
        assertTrue(id.matches("simpleoffice-v1-[0-9a-f]{64}"));
        assertEquals(id, AndroidManagedCalendarRows.providerEventId("account-a", "calendar-a", "event-1"));
        assertNotEquals(id, AndroidManagedCalendarRows.providerEventId("account-b", "calendar-a", "event-1"));
        assertNotEquals(id, AndroidManagedCalendarRows.providerEventId("account-a", "calendar-b", "event-1"));
        assertNotEquals(id, AndroidManagedCalendarRows.providerEventId("account-a", "calendar-a", "event-2"));
    }
}

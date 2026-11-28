package de.simpleoffice4me.android;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertThrows;

import org.junit.Test;

public class AndroidManagedContactsBirthdaysTest {
    @Test
    public void preservesMissingClearedAndYearlessDates() {
        assertNull(AndroidManagedContactsBirthdays.validate(null));
        assertEquals("", AndroidManagedContactsBirthdays.validate(""));
        assertEquals("--01-23", AndroidManagedContactsBirthdays.validate("--01-23"));
        assertEquals("--02-29", AndroidManagedContactsBirthdays.validate("--02-29"));
    }

    @Test
    public void acceptsValidCalendarDatesButNotInventedDates() {
        assertEquals("2000-02-29", AndroidManagedContactsBirthdays.validate("2000-02-29"));
        assertEquals("2026-11-27", AndroidManagedContactsBirthdays.validate("2026-11-27"));
        for (String invalid : new String[]{
                "1900-02-29", "2026-02-29", "2026-04-31", "--04-31", "--00-01", "--13-01",
                "--03-00", "0000-01-01", "20261127", "--11-7", "1980-11-27T00:00:00"
        }) {
            assertThrows(invalid, IllegalArgumentException.class,
                    () -> AndroidManagedContactsBirthdays.validate(invalid));
        }
    }
}

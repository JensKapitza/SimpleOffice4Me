package de.simpleoffice4me.android;

import android.Manifest;
import android.accounts.Account;
import android.app.UiAutomation;
import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.provider.CalendarContract;
import android.provider.CalendarContract.Calendars;
import android.provider.CalendarContract.Events;
import android.provider.CalendarContract.Instances;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

import org.json.JSONArray;
import org.json.JSONObject;

/** Verifies the platform-expanded occurrences, not just stored event columns. */
final class ManagedCalendarProviderAcceptance {
    private static final String ZONE = "Europe/Berlin";
    private static final String SERIES = "provider-test-series";
    private static final String BIRTHDAYS = "provider-test-birthdays";
    private static final String LOCAL_NAME = "Unrelated local calendar";
    private static final String LOCAL_TITLE = "Unrelated local event";
    private record Occurrence(long begin, long eventId, String title) {}
    private record LocalFixture(long calendarId, long eventId) {}
    private static final String LOCAL_START = "2027-03-21T10:00:00Z";
    private static final String LOCAL_END = "2027-03-21T11:00:00Z";

    private ManagedCalendarProviderAcceptance() {}

    static void verify(Context context, UiAutomation shell) throws Exception {
        shell.grantRuntimePermission(context.getPackageName(), Manifest.permission.READ_CALENDAR);
        shell.grantRuntimePermission(context.getPackageName(), Manifest.permission.WRITE_CALENDAR);
        ContentResolver resolver = context.getContentResolver();
        try (Cursor rows = resolver.query(Calendars.CONTENT_URI, new String[]{Calendars._ID}, null, null, null)) {
            require(rows != null && !rows.moveToFirst(), "Android calendar store is not empty");
        }
        LocalFixture local = createUnrelatedEvent(resolver);
        List<Occurrence> baseline = localOccurrences(resolver, local);
        require(baseline.equals(List.of(new Occurrence(ms(LOCAL_START), local.eventId(), LOCAL_TITLE))),
                "Unrelated local event fixture was not visible before sync");
        Account account = AndroidManagedSyncAccount.require(context);

        JSONObject master = row("master", SERIES, "Weekly series", "2027-03-18T09:00:00Z",
                "2027-03-18T10:00:00Z").put("rrule", "FREQ=WEEKLY;COUNT=4");
        JSONObject moved = row("move", SERIES, "Weekly series", "2027-03-26T10:30:00Z",
                "2027-03-26T11:30:00Z").put("original_id", "master")
                .put("original_start_ms", ms("2027-03-25T09:00:00Z"));
        JSONObject cancelled = row("cancel", SERIES, "Weekly series", "2027-04-01T08:00:00Z",
                "2027-04-01T09:00:00Z").put("original_id", "master")
                .put("original_start_ms", ms("2027-04-01T08:00:00Z")).put("cancelled", true);
        JSONObject birthday = row("leap-birthday", BIRTHDAYS, "Birthday: Leap Fixture",
                "2000-02-29T00:00:00Z", "2000-03-01T00:00:00Z").put("all_day", true)
                .put("calendar_timezone", "UTC").put("rrule", "FREQ=YEARLY");

        List<List<AndroidManagedCalendarRows.Event>> pages = AndroidManagedCalendarRows.prepare(List.of(
                new JSONArray().put(master).put(birthday), new JSONArray().put(moved).put(cancelled)));
        List<Occurrence> previous = null;
        List<Occurrence> previousBirthday = null;
        for (int repetition = 0; repetition < 2; repetition++) {
            for (List<AndroidManagedCalendarRows.Event> page : pages) {
                require(AndroidManagedCalendar.sync(context, page) == 2, "Calendar page did not export");
            }
            long seriesId = calendarId(resolver, account, SERIES);
            long birthdaysId = calendarId(resolver, account, BIRTHDAYS);
            List<Occurrence> weekly = instances(resolver, seriesId, "2027-03-17T00:00:00Z", "2027-04-10T00:00:00Z");
            checkStarts(weekly, "2027-03-18T09:00:00Z", "2027-03-26T10:30:00Z", "2027-04-08T08:00:00Z");
            require(instances(resolver, birthdaysId, "2027-01-01T00:00:00Z", "2028-01-01T00:00:00Z")
                    .isEmpty(), "Yearless leap birthday appeared in a non-leap year");
            List<Occurrence> leap = instances(resolver, birthdaysId, "2028-02-28T00:00:00Z", "2028-03-02T00:00:00Z");
            checkStarts(leap, "2028-02-29T00:00:00Z");
            require(previous == null || previous.equals(weekly), "Series provider IDs changed on resync");
            require(previousBirthday == null || previousBirthday.equals(leap), "Birthday provider ID changed on resync");
            previous = weekly;
            previousBirthday = leap;
            require(baseline.equals(localOccurrences(resolver, local)),
                    "Unrelated local calendar event changed after sync " + (repetition + 1));
        }
    }

    private static JSONObject row(String id, String calendar, String title, String start, String end)
            throws Exception {
        return new JSONObject().put("id", id).put("calendar_id", calendar).put("calendar_name", calendar)
                .put("calendar_timezone", ZONE).put("timezone", ZONE).put("title", title)
                .put("start_ms", ms(start)).put("end_ms", ms(end));
    }

    private static long calendarId(ContentResolver resolver, Account account, String source) {
        String selector = Calendars.ACCOUNT_NAME + "=? AND " + Calendars.ACCOUNT_TYPE + "=? AND " + Calendars._SYNC_ID + "=?";
        try (Cursor row = resolver.query(Calendars.CONTENT_URI, new String[]{Calendars._ID, Calendars.VISIBLE},
                selector, new String[]{account.name, account.type, source}, null)) {
            require(row != null && row.moveToFirst(), "Managed calendar did not appear");
            long id = row.getLong(0);
            require(row.getInt(1) == 1 && !row.moveToNext(), "Managed calendar is hidden or duplicated");
            return id;
        }
    }

    private static List<Occurrence> instances(ContentResolver resolver, long calendar, String start, String end) {
        Uri.Builder range = Instances.CONTENT_URI.buildUpon();
        ContentUris.appendId(range, ms(start));
        ContentUris.appendId(range, ms(end));
        ArrayList<Occurrence> results = new ArrayList<>();
        try (Cursor rows = resolver.query(range.build(), new String[]{Instances.BEGIN, Instances.EVENT_ID, Events.TITLE},
                Events.CALENDAR_ID + "=?", new String[]{Long.toString(calendar)}, Instances.BEGIN + " ASC")) {
            require(rows != null, "Could not query expanded Android calendar instances");
            while (rows.moveToNext()) results.add(new Occurrence(rows.getLong(0), rows.getLong(1), rows.getString(2)));
        }
        return results;
    }

    private static void checkStarts(List<Occurrence> actual, String... expected) {
        require(actual.size() == expected.length, "Wrong visible occurrence count: " + actual.size());
        for (int index = 0; index < expected.length; index++) {
            require(actual.get(index).begin() == ms(expected[index]), "Wrong visible start at occurrence " + index);
        }
    }

    private static List<Occurrence> localOccurrences(ContentResolver resolver, LocalFixture fixture) {
        return instances(resolver, fixture.calendarId(), "2027-03-21T00:00:00Z", "2027-03-22T00:00:00Z");
    }

    private static Uri localFixtureScope(Uri endpoint) {
        return endpoint.buildUpon()
                .appendQueryParameter(CalendarContract.CALLER_IS_SYNCADAPTER, "true")
                .appendQueryParameter(Calendars.ACCOUNT_NAME, LOCAL_NAME)
                .appendQueryParameter(Calendars.ACCOUNT_TYPE, CalendarContract.ACCOUNT_TYPE_LOCAL).build();
    }

    private static LocalFixture createUnrelatedEvent(ContentResolver resolver) {
        ContentValues calendar = new ContentValues();
        calendar.put(Calendars.ACCOUNT_NAME, LOCAL_NAME);
        calendar.put(Calendars.ACCOUNT_TYPE, CalendarContract.ACCOUNT_TYPE_LOCAL);
        calendar.put(Calendars.NAME, LOCAL_NAME);
        calendar.put(Calendars.CALENDAR_DISPLAY_NAME, LOCAL_NAME);
        calendar.put(Calendars.CALENDAR_TIME_ZONE, "UTC");
        calendar.put(Calendars.OWNER_ACCOUNT, LOCAL_NAME);
        calendar.put(Calendars.CALENDAR_ACCESS_LEVEL, Calendars.CAL_ACCESS_OWNER);
        calendar.put(Calendars.VISIBLE, 1);
        calendar.put(Calendars.SYNC_EVENTS, 1);
        Uri localCalendar = resolver.insert(localFixtureScope(Calendars.CONTENT_URI), calendar);
        require(localCalendar != null, "Unrelated local calendar fixture was not created");

        ContentValues event = new ContentValues();
        event.put(Events.CALENDAR_ID, ContentUris.parseId(localCalendar));
        event.put(Events.TITLE, LOCAL_TITLE);
        event.put(Events._SYNC_ID, "external-local-event");
        event.put(Events.DTSTART, ms(LOCAL_START));
        event.put(Events.DTEND, ms(LOCAL_END));
        event.put(Events.EVENT_TIMEZONE, "UTC");
        Uri localEvent = resolver.insert(localFixtureScope(Events.CONTENT_URI), event);
        require(localEvent != null, "Unrelated local event fixture was not created");
        return new LocalFixture(ContentUris.parseId(localCalendar), ContentUris.parseId(localEvent));
    }

    private static long ms(String timestamp) { return Instant.parse(timestamp).toEpochMilli(); }

    private static void require(boolean condition, String failure) {
        if (!condition) throw new AssertionError("Calendar: " + failure);
    }
}

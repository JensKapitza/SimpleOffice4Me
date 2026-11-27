package de.simpleoffice4me.android;

import android.accounts.Account;
import android.content.ContentProviderOperation;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.provider.CalendarContract;
import android.provider.CalendarContract.Calendars;
import android.provider.CalendarContract.Events;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** One-way upserts into calendars owned by the current SimpleOffice Android account. */
final class AndroidManagedCalendar {
    private static final int COLOR = 0xff2563eb;
    private static final String CALENDAR_SCOPE =
            Calendars.ACCOUNT_NAME + "=? AND " + Calendars.ACCOUNT_TYPE + "=? AND " + Calendars._SYNC_ID + "=?";
    private static final String EVENT_SCOPE = Events.CALENDAR_ID + "=? AND " + Events._SYNC_ID + "=?";

    private AndroidManagedCalendar() {}

    /**
     * Call on a worker after granting the calendar permissions. Validates the entire page
     * before writing, and sends one non-yielding transaction to CalendarProvider.
     */
    static synchronized int sync(Context context, List<AndroidManagedCalendarRows.Event> events) throws Exception {
        if (events.isEmpty()) return 0;

        Account account = AndroidManagedSyncAccount.require(context);
        if (account == null || !AndroidManagedSyncAccount.TYPE.equals(account.type) || account.name.isEmpty()) {
            throw new IllegalStateException("No managed SimpleOffice Android account");
        }
        ContentResolver resolver = context.getContentResolver();
        Uri calendarUri = scoped(Calendars.CONTENT_URI, account);
        Uri eventUri = scoped(Events.CONTENT_URI, account);
        ArrayList<ContentProviderOperation> writes = new ArrayList<>();
        Map<String, Reference> calendars = new LinkedHashMap<>();
        Map<AndroidManagedCalendarRows.Key, Master> masters = new LinkedHashMap<>();

        for (AndroidManagedCalendarRows.Event event : events) {
            if (!calendars.containsKey(event.calendarId())) {
                calendars.put(event.calendarId(), planCalendar(resolver, calendarUri, account, event, writes));
            }
        }
        // Masters must exist before their exceptions. Separate pages can refer to a
        // master already present in this managed calendar.
        for (AndroidManagedCalendarRows.Event event : events) {
            if (event.originalId().isEmpty()) {
                Reference reference = planEvent(resolver, eventUri, account.name, event, calendars.get(event.calendarId()), null, writes);
                masters.put(event.key(), new Master(reference, event.allDay(), event.repeats()));
            }
        }
        for (AndroidManagedCalendarRows.Event exception : events) {
            if (exception.originalId().isEmpty()) continue;
            Reference calendar = calendars.get(exception.calendarId());
            Master master = masters.get(exception.originalKey());
            if (master == null && calendar.rowId() != null) {
                String masterKey = AndroidManagedCalendarRows.providerEventId(
                        account.name, exception.calendarId(), exception.originalId());
                ExistingEvent old = lookupEvent(resolver, eventUri, calendar.rowId(), masterKey);
                if (old != null) master = new Master(new Reference(old.id(), null), old.allDay(), old.repeats());
            }
            if (master == null || !master.repeats()) {
                throw new IllegalArgumentException("An exception requires a recurring master in its calendar");
            }
            if (master.allDay() && !AndroidManagedCalendarRows.midnight(exception.originalStart())) {
                throw new IllegalArgumentException("All-day exception instance is not at UTC midnight");
            }
            planEvent(resolver, eventUri, account.name, exception, calendar, master, writes);
        }

        resolver.applyBatch(CalendarContract.AUTHORITY, writes);
        return events.size();
    }

    private static Uri scoped(Uri uri, Account account) {
        return uri.buildUpon().appendQueryParameter(CalendarContract.CALLER_IS_SYNCADAPTER, "true")
                .appendQueryParameter(Calendars.ACCOUNT_NAME, account.name)
                .appendQueryParameter(Calendars.ACCOUNT_TYPE, account.type).build();
    }

    private record Reference(Long rowId, Integer operation) {
        void attach(ContentProviderOperation.Builder write, String field) {
            if (rowId != null) write.withValue(field, rowId);
            else write.withValueBackReference(field, operation);
        }
    }

    private record Master(Reference reference, boolean allDay, boolean repeats) {}

    private record ExistingEvent(long id, boolean allDay, boolean repeats) {}

    private static Reference planCalendar(ContentResolver resolver, Uri uri, Account account,
                                           AndroidManagedCalendarRows.Event incoming,
                                           ArrayList<ContentProviderOperation> writes) {
        String[] arguments = {account.name, account.type, incoming.calendarId()};
        String name = "SimpleOffice · " + incoming.calendarName();
        try (Cursor cursor = resolver.query(uri, new String[]{Calendars._ID, Calendars.CALENDAR_DISPLAY_NAME, Calendars.CALENDAR_TIME_ZONE},
                CALENDAR_SCOPE, arguments, null)) {
            if (cursor == null) throw new IllegalStateException("Android Calendar Provider is unavailable");
            if (cursor.moveToFirst()) {
                long id = cursor.getLong(0);
                boolean update = !name.equals(cursor.getString(1))
                        || !incoming.calendarZone().equals(cursor.getString(2));
                if (cursor.moveToNext()) throw new IllegalStateException("Duplicate managed Android calendar");
                String selection = Calendars._ID + "=? AND " + CALENDAR_SCOPE;
                String[] exact = {Long.toString(id), arguments[0], arguments[1], arguments[2]};
                // Guard the account ownership again in the transaction itself; only our
                // calendar gets renamed and its user's existing visibility is preserved.
                ContentProviderOperation.Builder operation = update
                        ? ContentProviderOperation.newUpdate(uri).withValue(Calendars.CALENDAR_DISPLAY_NAME, name)
                                .withValue(Calendars.CALENDAR_TIME_ZONE, incoming.calendarZone())
                        : ContentProviderOperation.newAssertQuery(uri);
                writes.add(operation.withSelection(selection, exact).withExpectedCount(1).build());
                return new Reference(id, null);
            }
        }

        ContentValues values = new ContentValues();
        values.put(Calendars.ACCOUNT_NAME, account.name);
        values.put(Calendars.ACCOUNT_TYPE, account.type);
        values.put(Calendars._SYNC_ID, incoming.calendarId());
        values.put(Calendars.NAME, incoming.calendarId());
        values.put(Calendars.OWNER_ACCOUNT, account.name);
        values.put(Calendars.CALENDAR_DISPLAY_NAME, name);
        values.put(Calendars.CALENDAR_COLOR, COLOR);
        values.put(Calendars.CALENDAR_TIME_ZONE, incoming.calendarZone());
        values.put(Calendars.CALENDAR_ACCESS_LEVEL, Calendars.CAL_ACCESS_READ);
        values.put(Calendars.VISIBLE, 1);
        values.put(Calendars.SYNC_EVENTS, 1);
        int index = writes.size();
        writes.add(ContentProviderOperation.newInsert(uri).withValues(values).build());
        return new Reference(null, index);
    }

    private static ExistingEvent lookupEvent(ContentResolver resolver, Uri uri, long calendar, String key) {
        try (Cursor cursor = resolver.query(uri, new String[]{Events._ID, Events.ALL_DAY, Events.RRULE, Events.RDATE},
                EVENT_SCOPE, new String[]{Long.toString(calendar), key}, null)) {
            if (cursor == null) throw new IllegalStateException("Android Calendar Provider is unavailable");
            if (!cursor.moveToFirst()) return null;
            ExistingEvent event = new ExistingEvent(cursor.getLong(0), cursor.getInt(1) != 0,
                    AndroidManagedCalendarRows.present(cursor.getString(2))
                            || AndroidManagedCalendarRows.present(cursor.getString(3)));
            if (cursor.moveToNext()) throw new IllegalStateException("Duplicate managed Android event");
            return event;
        }
    }

    private static Reference planEvent(ContentResolver resolver, Uri uri, String account,
                                       AndroidManagedCalendarRows.Event incoming, Reference calendar,
                                       Master master, ArrayList<ContentProviderOperation> writes) {
        String key = AndroidManagedCalendarRows.providerEventId(account, incoming.calendarId(), incoming.id());
        ExistingEvent old = calendar.rowId() == null ? null
                : lookupEvent(resolver, uri, calendar.rowId(), key);
        ContentValues values = new ContentValues();
        values.put(Events._SYNC_ID, key);
        values.put(Events.TITLE, incoming.title());
        values.put(Events.DESCRIPTION, incoming.description());
        values.put(Events.DTSTART, incoming.start());
        values.put(Events.ALL_DAY, incoming.allDay() ? 1 : 0);
        values.put(Events.EVENT_TIMEZONE, incoming.zone());
        values.put(Events.EVENT_END_TIMEZONE, incoming.zone());
        values.put(Events.STATUS, incoming.cancelled() ? Events.STATUS_CANCELED : Events.STATUS_CONFIRMED);
        values.put(Events.DIRTY, 0);
        values.put(Events.DELETED, 0);
        putOptional(values, Events.RRULE, incoming.rrule());
        putOptional(values, Events.RDATE, incoming.rdate());
        putOptional(values, Events.EXDATE, incoming.exdate());
        if (incoming.repeats()) {
            values.put(Events.DURATION, incoming.duration());
            values.putNull(Events.DTEND);
        } else {
            values.putNull(Events.DURATION);
            values.put(Events.DTEND, incoming.end());
        }
        if (master == null) {
            values.putNull(Events.ORIGINAL_ID);
            values.putNull(Events.ORIGINAL_SYNC_ID);
            values.putNull(Events.ORIGINAL_INSTANCE_TIME);
            values.putNull(Events.ORIGINAL_ALL_DAY);
        } else {
            values.put(Events.ORIGINAL_SYNC_ID,
                    AndroidManagedCalendarRows.providerEventId(account, incoming.calendarId(), incoming.originalId()));
            values.put(Events.ORIGINAL_INSTANCE_TIME, incoming.originalStart());
            values.put(Events.ORIGINAL_ALL_DAY, master.allDay() ? 1 : 0);
        }
        ContentProviderOperation.Builder write = old == null
                ? ContentProviderOperation.newInsert(uri)
                : ContentProviderOperation.newUpdate(uri)
                    .withSelection(Events._ID + "=? AND " + EVENT_SCOPE,
                            new String[]{Long.toString(old.id()), Long.toString(calendar.rowId()), key})
                    .withExpectedCount(1);
        write.withValues(values);
        // CalendarProvider expects calendar_id on insert. On update it must not be moved.
        if (old == null) calendar.attach(write, Events.CALENDAR_ID);
        if (master != null) master.reference().attach(write, Events.ORIGINAL_ID);
        int index = writes.size();
        writes.add(write.build());
        return old == null ? new Reference(null, index) : new Reference(old.id(), null);
    }

    private static void putOptional(ContentValues values, String column, String text) {
        if (text.isEmpty()) values.putNull(column);
        else values.put(column, text);
    }
}

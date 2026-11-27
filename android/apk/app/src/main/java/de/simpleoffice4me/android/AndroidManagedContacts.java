package de.simpleoffice4me.android;

import android.Manifest;
import android.accounts.Account;
import android.content.ContentProviderOperation;
import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.ContentValues;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.provider.ContactsContract;
import android.provider.ContactsContract.CommonDataKinds.Email;
import android.provider.ContactsContract.CommonDataKinds.Event;
import android.provider.ContactsContract.CommonDataKinds.Phone;
import android.provider.ContactsContract.CommonDataKinds.StructuredName;
import android.provider.ContactsContract.Data;
import android.provider.ContactsContract.RawContacts;
import android.provider.ContactsContract.Settings;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.function.BooleanSupplier;
import java.util.regex.Pattern;

/** Copies SimpleOffice contacts into the dedicated Android account, without modifying other accounts. */
final class AndroidManagedContacts {
    private static final String MANAGED = "simpleoffice4me-contacts-v1";
    private static final String DISPLAY_ONLY = "display-only";
    private static final Pattern SOURCE_ID = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._~-]{0,127}");
    private static final String ACCOUNT_SCOPE = RawContacts.ACCOUNT_NAME + "=? AND "
            + RawContacts.ACCOUNT_TYPE + "=? AND " + RawContacts.DATA_SET + " IS NULL";
    private static final String SOURCE_SCOPE = ACCOUNT_SCOPE + " AND " + RawContacts.SOURCE_ID + "=?";
    private static final String DATA_SCOPE = Data._ID + "=? AND " + Data.RAW_CONTACT_ID + "=? AND "
            + Data.MIMETYPE + "=? AND " + Data.SYNC1 + "=?";
    private static final String[] RAW_PROJECTION = {
            RawContacts._ID, RawContacts.VERSION, RawContacts.DELETED, RawContacts.SYNC1
    };
    private static final String[] DATA_PROJECTION = {
            Data._ID, Data.MIMETYPE, Data.DATA1, Data.DATA2, Data.DATA3, Data.SYNC2
    };

    private AndroidManagedContacts() {}

    // Serialize requests within the APK. Provider assertions also guard against device-side changes.
    static synchronized int sync(Context context, JSONArray rows, BooleanSupplier active) throws Exception {
        if (context.checkSelfPermission(Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED
                || context.checkSelfPermission(Manifest.permission.WRITE_CONTACTS) != PackageManager.PERMISSION_GRANTED) {
            throw new SecurityException("Android contacts permission is required");
        }
        if (rows.length() > AndroidManagedContactCursor.MAX_ROWS) {
            throw new IllegalArgumentException("Too many contacts in one export");
        }

        // Validate the entire payload before any provider write, including account settings.
        ArrayList<Contact> contacts = new ArrayList<>(rows.length());
        Set<String> ids = new HashSet<>();
        for (int i = 0; i < rows.length(); i++) {
            Contact contact = new Contact(rows.getJSONObject(i));
            if (!ids.add(contact.id)) {
                throw new IllegalArgumentException("Duplicate contact identifier");
            }
            contacts.add(contact);
        }
        if (contacts.isEmpty()) {
            return 0;
        }

        if (!active.getAsBoolean()) {
            throw new java.io.IOException("Contact sync cancelled");
        }
        Account account = AndroidManagedSyncAccount.require(context);
        if (!AndroidManagedSyncAccount.TYPE.equals(account.type) || account.name.isEmpty()) {
            throw new IllegalStateException("Invalid SimpleOffice contacts account");
        }
        ContentResolver resolver = context.getContentResolver();
        ensureUngroupedVisibility(resolver, account);
        int exported = 0;
        for (Contact contact : contacts) {
            if (!active.getAsBoolean()) {
                throw new java.io.IOException("Contact sync cancelled");
            }
            if (writeContact(resolver, account, contact)) {
                exported++;
            }
        }
        return exported;
    }

    private static boolean writeContact(ContentResolver resolver, Account account, Contact contact)
            throws Exception {
        String[] scopeArgs = {account.name, account.type, contact.id};
        Uri rawContacts = syncUri(RawContacts.CONTENT_URI, account);
        Uri data = syncUri(Data.CONTENT_URI, account);
        Long rawId = null;
        int rawVersion = 0;
        try (Cursor cursor = requireCursor(resolver.query(rawContacts, RAW_PROJECTION,
                SOURCE_SCOPE, scopeArgs, null))) {
            if (cursor.moveToFirst()) {
                rawId = cursor.getLong(0);
                rawVersion = cursor.getInt(1);
                boolean deleted = cursor.getInt(2) != 0;
                if (!MANAGED.equals(cursor.getString(3)) || cursor.moveToNext()) {
                    throw new IllegalStateException("Ambiguous SimpleOffice contact ownership");
                }
                if (deleted) {
                    return false;
                }
            }
        }

        ArrayList<ContentProviderOperation> batch = new ArrayList<>();
        Map<String, Detail> existing = new HashMap<>();
        int rawInsert = -1;
        if (rawId == null) {
            batch.add(ContentProviderOperation.newAssertQuery(rawContacts)
                    .withSelection(SOURCE_SCOPE, scopeArgs)
                    .withExpectedCount(0)
                    .build());
            rawInsert = batch.size();
            batch.add(ContentProviderOperation.newInsert(rawContacts)
                    .withValue(RawContacts.ACCOUNT_NAME, account.name)
                    .withValue(RawContacts.ACCOUNT_TYPE, account.type)
                    .withValue(RawContacts.SOURCE_ID, contact.id)
                    .withValue(RawContacts.SYNC1, MANAGED)
                    .withValue(RawContacts.RAW_CONTACT_IS_READ_ONLY, 1)
                    .build());
        } else {
            Uri individualRaw = ContentUris.withAppendedId(rawContacts, rawId);
            batch.add(ContentProviderOperation.newAssertQuery(individualRaw)
                    .withSelection(SOURCE_SCOPE, scopeArgs)
                    .withValue(RawContacts.SYNC1, MANAGED)
                    .withValue(RawContacts.DELETED, 0)
                    .withValue(RawContacts.VERSION, rawVersion)
                    .withExpectedCount(1)
                    .build());
            existing = details(resolver, data, rawId);
        }
        addDetail(batch, data, rawId, rawInsert, existing, StructuredName.CONTENT_ITEM_TYPE,
                contact.name, contact.firstName, contact.lastName);
        if (contact.phone != null) {
            addDetail(batch, data, rawId, rawInsert, existing, Phone.CONTENT_ITEM_TYPE,
                    contact.phone, String.valueOf(Phone.TYPE_OTHER), null);
        }
        if (contact.email != null) {
            addDetail(batch, data, rawId, rawInsert, existing, Email.CONTENT_ITEM_TYPE,
                    contact.email, String.valueOf(Email.TYPE_OTHER), null);
        }
        if (contact.birthday != null) {
            String type = String.valueOf(Event.TYPE_BIRTHDAY);
            if (contact.birthday.isEmpty()) {
                Detail previous = existing.get(Event.CONTENT_ITEM_TYPE);
                if (previous != null) {
                    // Android's item DELETE URI ignores the selection; use the collection URI
                    // with explicit predicates so another row can never satisfy this operation.
                    batch.add(ContentProviderOperation.newDelete(data)
                            .withSelection(DATA_SCOPE + " AND " + Event.TYPE + "=?",
                                    new String[]{String.valueOf(previous.id), String.valueOf(rawId),
                                            Event.CONTENT_ITEM_TYPE, MANAGED, type})
                            .withExpectedCount(1)
                            .build());
                }
            } else {
                addDetail(batch, data, rawId, rawInsert, existing, Event.CONTENT_ITEM_TYPE,
                        contact.birthday, type, null);
            }
        }
        // Existing, unchanged contacts must not cause provider mutations or version churn.
        if (batch.size() > 1) {
            resolver.applyBatch(ContactsContract.AUTHORITY, batch);
        }
        return true;
    }

    private static Map<String, Detail> details(ContentResolver resolver, Uri data, long rawId) {
        Map<String, Detail> details = new HashMap<>();
        String selection = Data.RAW_CONTACT_ID + "=? AND " + Data.SYNC1 + "=? AND "
                + Data.MIMETYPE + " IN (?,?,?,?)";
        String[] args = {
                String.valueOf(rawId), MANAGED, StructuredName.CONTENT_ITEM_TYPE, Phone.CONTENT_ITEM_TYPE,
                Email.CONTENT_ITEM_TYPE, Event.CONTENT_ITEM_TYPE
        };
        try (Cursor cursor = requireCursor(resolver.query(data, DATA_PROJECTION, selection, args, null))) {
            while (cursor.moveToNext()) {
                String mime = cursor.getString(1);
                if (Event.CONTENT_ITEM_TYPE.equals(mime)
                        && !String.valueOf(Event.TYPE_BIRTHDAY).equals(cursor.getString(3))) {
                    continue;
                }
                Detail previous = details.put(mime, new Detail(cursor.getLong(0),
                        cursor.getString(2), cursor.getString(3), cursor.getString(4), cursor.getString(5)));
                if (previous != null) {
                    throw new IllegalStateException("Duplicate managed contact detail");
                }
            }
        }
        return details;
    }

    private static void addDetail(ArrayList<ContentProviderOperation> batch, Uri data, Long rawId,
            int rawInsert, Map<String, Detail> existing, String mime, String value1, String value2, String value3) {
        Detail previous = existing.get(mime);
        if (previous == null && empty(value1)) {
            return;
        }
        ContentValues values = new ContentValues();
        values.put(Data.DATA1, nullIfEmpty(value1));
        values.put(Data.DATA2, nullIfEmpty(value2));
        values.put(Data.DATA3, nullIfEmpty(value3));
        boolean structuredName = StructuredName.CONTENT_ITEM_TYPE.equals(mime);
        boolean displayOnly = structuredName && empty(value2) && empty(value3);
        if (structuredName) {
            // Android derives given/family name from a display-only input; remember that
            // mode so inferred parts aren't mistaken for a changed SimpleOffice contact.
            values.put(Data.SYNC2, displayOnly ? DISPLAY_ONLY : null);
        }
        if (previous != null) {
            if (previous.matches(value1, value2, value3, displayOnly)) {
                return;
            }
            batch.add(ContentProviderOperation.newUpdate(data)
                    .withValues(values)
                    .withSelection(DATA_SCOPE,
                            new String[]{String.valueOf(previous.id), String.valueOf(rawId), mime, MANAGED})
                    .withExpectedCount(1)
                    .build());
            return;
        }
        ContentProviderOperation.Builder insert = ContentProviderOperation.newInsert(data)
                .withValues(values)
                .withValue(Data.MIMETYPE, mime)
                .withValue(Data.SYNC1, MANAGED)
                .withValue(Data.IS_READ_ONLY, 1);
        if (rawId == null) {
            insert.withValueBackReference(Data.RAW_CONTACT_ID, rawInsert);
        } else {
            insert.withValue(Data.RAW_CONTACT_ID, rawId);
        }
        batch.add(insert.build());
    }

    private static void ensureUngroupedVisibility(ContentResolver resolver, Account account) throws Exception {
        Uri settings = syncUri(Settings.CONTENT_URI, account);
        String[] args = {account.name, account.type};
        boolean existing;
        try (Cursor cursor = requireCursor(resolver.query(settings,
                new String[]{Settings.UNGROUPED_VISIBLE}, ACCOUNT_SCOPE, args, null))) {
            existing = cursor.moveToFirst();
            if (existing && cursor.getInt(0) == 1) {
                return;
            }
        }
        ArrayList<ContentProviderOperation> batch = new ArrayList<>();
        if (existing) {
            batch.add(ContentProviderOperation.newUpdate(settings)
                    .withSelection(ACCOUNT_SCOPE, args)
                    .withValue(Settings.UNGROUPED_VISIBLE, 1)
                    .withExpectedCount(1)
                    .build());
        } else {
            batch.add(ContentProviderOperation.newInsert(settings)
                    .withValue(Settings.ACCOUNT_NAME, account.name)
                    .withValue(Settings.ACCOUNT_TYPE, account.type)
                    .withValue(Settings.UNGROUPED_VISIBLE, 1)
                    .build());
        }
        resolver.applyBatch(ContactsContract.AUTHORITY, batch);
    }

    private static Cursor requireCursor(Cursor cursor) {
        if (cursor == null) {
            throw new IllegalStateException("Android contacts provider is unavailable");
        }
        return cursor;
    }

    private static Uri syncUri(Uri base, Account account) {
        return base.buildUpon()
                .appendQueryParameter(ContactsContract.CALLER_IS_SYNCADAPTER, "true")
                .appendQueryParameter(RawContacts.ACCOUNT_NAME, account.name)
                .appendQueryParameter(RawContacts.ACCOUNT_TYPE, account.type)
                .build();
    }

    private static boolean empty(String value) {
        return value == null || value.isEmpty();
    }

    private static String nullIfEmpty(String value) {
        return empty(value) ? null : value;
    }

    private static String field(JSONObject row, String key, int maximum) {
        if (!row.has(key)) {
            return null;
        }
        Object value = row.opt(key);
        if (value == JSONObject.NULL) {
            return "";
        }
        if (!(value instanceof String)) {
            throw new IllegalArgumentException("Invalid contact field type");
        }
        String text = ((String) value).trim();
        if (text.length() > maximum || text.codePoints().anyMatch(Character::isISOControl)) {
            throw new IllegalArgumentException("Invalid contact field content");
        }
        return text;
    }

    private static final class Contact {
        final String id;
        final String name;
        final String firstName;
        final String lastName;
        final String phone;
        final String email;
        final String birthday;

        Contact(JSONObject row) {
            id = field(row, "id", 128);
            if (id == null || !SOURCE_ID.matcher(id).matches()) {
                throw new IllegalArgumentException("Invalid contact identifier");
            }
            firstName = field(row, "first_name", 512);
            lastName = field(row, "last_name", 512);
            String display = field(row, "name", 1024);
            if (empty(display)) {
                display = ((firstName == null ? "" : firstName) + " "
                        + (lastName == null ? "" : lastName)).trim();
            }
            if (display.isEmpty()) {
                throw new IllegalArgumentException("Contact display name is required");
            }
            name = display;
            phone = field(row, "phone", 256);
            email = field(row, "email", 512);
            birthday = AndroidManagedContactsBirthdays.validate(field(row, "birthday", 10));
        }
    }

    private static final class Detail {
        final long id;
        final String value1;
        final String value2;
        final String value3;
        final boolean displayOnly;

        Detail(long id, String value1, String value2, String value3, String sourceMode) {
            this.id = id;
            this.value1 = value1;
            this.value2 = value2;
            this.value3 = value3;
            this.displayOnly = DISPLAY_ONLY.equals(sourceMode);
        }

        boolean matches(String first, String second, String third, boolean incomingDisplayOnly) {
            return displayOnly == incomingDisplayOnly
                    && Objects.equals(nullIfEmpty(value1), nullIfEmpty(first))
                    && (displayOnly || (Objects.equals(nullIfEmpty(value2), nullIfEmpty(second))
                            && Objects.equals(nullIfEmpty(value3), nullIfEmpty(third))));
        }
    }
}

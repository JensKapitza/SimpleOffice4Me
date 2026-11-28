package de.simpleoffice4me.android;

import android.accounts.Account;
import android.accounts.AccountManager;
import android.app.Activity;
import android.app.Instrumentation;
import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.ContactsContract.Contacts;
import android.provider.ContactsContract.RawContacts;
import android.provider.ContactsContract.Data;
import android.provider.ContactsContract.CommonDataKinds.StructuredName;

import java.io.IOException;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import org.json.JSONArray;
import org.json.JSONObject;

/** Runs only in a disposable Android emulator, against its real ContactsProvider. */
public final class ManagedContactsProviderAcceptance extends Instrumentation {
    private static final String PRINCIPAL =
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private static final String SOURCE = "so4me-provider-acceptance";
    private static final String LOCAL_NAME = "Local Fixture Outside SimpleOffice";

    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        if (arguments == null || !"yes".equals(arguments.getString("disposable"))
                || !("ranchu".equals(Build.HARDWARE) || "goldfish".equals(Build.HARDWARE))) {
            finish(Activity.RESULT_CANCELED, report("Refused: fresh disposable Android emulator required"));
            return;
        }
        start();
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            verify(getTargetContext());
            result.putString("simpleoffice_contacts_provider", "pass");
            result.putString("simpleoffice_contacts_bulk_resume", "pass");
            ManagedCalendarProviderAcceptance.verify(getTargetContext(), getUiAutomation());
            result.putString("simpleoffice_calendar_provider", "pass");
            finish(Activity.RESULT_OK, result);
        } catch (Exception | AssertionError failure) {
            String stage = result.containsKey("simpleoffice_contacts_provider") ? "calendar" : "contacts";
            result.putAll(report("Android " + stage + " provider acceptance failed: " + describe(failure)));
            finish(Activity.RESULT_CANCELED, result);
        } finally {
            AndroidManagedSyncAccount.clear();
        }
    }

    private static String describe(Throwable failure) {
        // Never dump provider arguments, account identifiers, or arbitrary exception text.
        if (failure instanceof AssertionError) return failure.getMessage();
        StringBuilder reason = new StringBuilder(failure.getClass().getSimpleName());
        if (failure.getMessage() != null) {
            java.util.regex.Matcher count = java.util.regex.Pattern
                    .compile("Expected [0-9]+ rows but actual [0-9]+")
                    .matcher(failure.getMessage());
            if (count.find()) reason.append(": ").append(count.group());
        }
        int frames = 0;
        for (StackTraceElement frame : failure.getStackTrace()) {
            if (!frame.getClassName().startsWith("de.simpleoffice4me.android.")) continue;
            reason.append(" | ").append(frame.getFileName()).append(':').append(frame.getLineNumber())
                    .append(' ').append(frame.getMethodName());
            if (++frames == 4) break;
        }
        return reason.toString();
    }

    private void verify(Context context) throws Exception {
        AndroidManagedSyncAccount.bind(PRINCIPAL);
        require(sync(context, "Office Fixture Before", false) == 0,
                "Expected contacts permission to be denied before grant");
        require(AccountManager.get(context).getAccountsByType(AndroidManagedSyncAccount.TYPE).length == 0,
                "Test requires an unused SimpleOffice account");
        grantContacts(context);
        ContentResolver resolver = context.getContentResolver();
        try (Cursor rows = resolver.query(RawContacts.CONTENT_URI, new String[]{RawContacts._ID},
                RawContacts.DELETED + "=0", null, null)) {
            require(rows != null && !rows.moveToFirst(), "The Android contact store is not empty");
        }
        long localId = localContact(resolver);
        require(sync(context, "Office Fixture Before", true) == 1, "First sync did not export one contact");
        Account account = AndroidManagedSyncAccount.require(context);
        long rawId = managedContact(resolver, account, "Office Fixture Before");
        require(localId != rawId, "Unrelated local contact was adopted");
        for (int repeat = 0; repeat < 2; repeat++) {
            require(sync(context, "Office Fixture After", true) == 1, "Resync did not report one contact");
            require(rawId == managedContact(resolver, account, "Office Fixture After"),
                    "Resync did not preserve the existing raw contact");
            require(LOCAL_NAME.equals(displayName(resolver, localId)), "Unrelated local contact changed");
        }
        verifyBulkResume(context, resolver, account, localId);
    }

    private static void verifyBulkResume(Context context, ContentResolver resolver, Account account, long localId)
            throws Exception {
        final int total = 131;
        JSONArray input = new JSONArray();
        for (int i = 0; i < total; i++) {
            input.put(new JSONObject().put("id", "bulk-" + i).put("name", "Synthetic Bulk Contact " + i));
        }
        var snapshot = AndroidManagedContacts.prepare(input);
        AtomicInteger authorizedChecks = new AtomicInteger();
        boolean interrupted = false;
        try {
            AndroidManagedContacts.sync(context, snapshot, () -> authorizedChecks.incrementAndGet() <= 34);
        } catch (IOException canceled) {
            interrupted = "Contact sync cancelled".equals(canceled.getMessage());
        }
        require(interrupted, "Expected an authorized bulk write to stop when its scope expired");
        Map<String, BulkRow> partial = bulkRows(resolver, account);
        require(!partial.isEmpty() && partial.size() < total, "Cancellation did not leave an incomplete safe prefix");
        require(!partial.containsKey("bulk-130"), "Canceled bulk job continued writing to the final record");
        require(LOCAL_NAME.equals(displayName(resolver, localId)), "Canceled bulk job changed the unrelated local contact");

        require(AndroidManagedContacts.sync(context, snapshot, () -> true) == total,
                "Bulk retry did not account for the complete snapshot");
        Map<String, BulkRow> complete = bulkRows(resolver, account);
        require(complete.size() == total, "Bulk retry missed or duplicated contacts");
        for (int i = 0; i < total; i++) {
            require(complete.containsKey("bulk-" + i), "Bulk retry missed a source identifier");
        }
        for (var entry : partial.entrySet()) {
            require(entry.getValue().equals(complete.get(entry.getKey())), "Retry changed an already correct contact");
        }
        require("Synthetic Bulk Contact 130".equals(displayName(resolver, complete.get("bulk-130").id())),
                "Final bulk contact is not published by Android");
        require(AndroidManagedContacts.sync(context, snapshot, () -> true) == total,
                "Repeated bulk sync did not account for all contacts");
        require(complete.equals(bulkRows(resolver, account)), "Repeated bulk sync changed Android rows or versions");
        require(LOCAL_NAME.equals(displayName(resolver, localId)), "Bulk sync changed the unrelated local contact");
    }

    private record BulkRow(long id, int version) {}

    private static Map<String, BulkRow> bulkRows(ContentResolver resolver, Account account) {
        Map<String, BulkRow> result = new HashMap<>();
        String scope = RawContacts.ACCOUNT_NAME + "=? AND " + RawContacts.ACCOUNT_TYPE + "=? AND "
                + RawContacts.SOURCE_ID + " LIKE ? AND " + RawContacts.DELETED + "=0";
        try (Cursor rows = resolver.query(RawContacts.CONTENT_URI,
                new String[]{RawContacts.SOURCE_ID, RawContacts._ID, RawContacts.VERSION},
                scope, new String[]{account.name, account.type, "bulk-%"}, null)) {
            require(rows != null, "Android bulk contact query did not return a cursor");
            while (rows.moveToNext()) {
                BulkRow prior = result.put(rows.getString(0), new BulkRow(rows.getLong(1), rows.getInt(2)));
                require(prior == null, "Multiple Android raw contacts share a bulk source ID");
            }
        }
        return result;
    }

    private static int sync(Context context, String name, boolean allowed) throws Exception {
        JSONArray input = new JSONArray().put(new JSONObject().put("id", SOURCE).put("name", name));
        try {
            return AndroidManagedContacts.sync(context, AndroidManagedContacts.prepare(input), () -> true);
        } catch (SecurityException denied) {
            if (allowed) throw denied;
            return 0;
        }
    }

    private static Bundle report(String message) {
        Bundle values = new Bundle();
        values.putString("stream", "\n" + message + "\n");
        return values;
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private void grantContacts(Context context) {
        // These permissions are granted only to the instrumented test app on the disposable emulator.
        getUiAutomation().grantRuntimePermission(context.getPackageName(), android.Manifest.permission.READ_CONTACTS);
        getUiAutomation().grantRuntimePermission(context.getPackageName(), android.Manifest.permission.WRITE_CONTACTS);
    }

    private static long localContact(ContentResolver resolver) {
        ContentValues raw = new ContentValues();
        raw.putNull(RawContacts.ACCOUNT_NAME);
        raw.putNull(RawContacts.ACCOUNT_TYPE);
        Uri created = resolver.insert(RawContacts.CONTENT_URI, raw);
        require(created != null, "Local contact could not be created");
        long id = ContentUris.parseId(created);
        ContentValues name = new ContentValues();
        name.put(Data.RAW_CONTACT_ID, id);
        name.put(Data.MIMETYPE, StructuredName.CONTENT_ITEM_TYPE);
        name.put(StructuredName.DISPLAY_NAME, LOCAL_NAME);
        require(resolver.insert(Data.CONTENT_URI, name) != null, "Local contact fixture has no name");
        return id;
    }

    private static long managedContact(ContentResolver resolver, Account account, String name) {
        String scope = RawContacts.ACCOUNT_NAME + "=? AND " + RawContacts.ACCOUNT_TYPE + "=? AND "
                + RawContacts.SOURCE_ID + "=? AND " + RawContacts.DELETED + "=0";
        try (Cursor rows = resolver.query(RawContacts.CONTENT_URI, new String[]{RawContacts._ID},
                scope, new String[]{account.name, account.type, SOURCE}, null)) {
            require(rows != null && rows.moveToFirst(), "Provider does not contain the managed contact");
            long rawId = rows.getLong(0);
            require(!rows.moveToNext(), "Resync duplicated the managed contact");
            require(name.equals(displayName(resolver, rawId)), "Android contact name was not exported");
            return rawId;
        }
    }

    private static String displayName(ContentResolver resolver, long rawId) {
        try (Cursor raw = resolver.query(ContentUris.withAppendedId(RawContacts.CONTENT_URI, rawId),
                new String[]{RawContacts.CONTACT_ID}, null, null, null)) {
            require(raw != null && raw.moveToFirst() && !raw.isNull(0), "Raw contact was not aggregated");
            Uri contact = ContentUris.withAppendedId(Contacts.CONTENT_URI, raw.getLong(0));
            try (Cursor row = resolver.query(contact, new String[]{Contacts.DISPLAY_NAME_PRIMARY},
                    null, null, null)) {
                require(row != null && row.moveToFirst(), "Published Android contact was not found");
                return row.getString(0);
            }
        }
    }
}

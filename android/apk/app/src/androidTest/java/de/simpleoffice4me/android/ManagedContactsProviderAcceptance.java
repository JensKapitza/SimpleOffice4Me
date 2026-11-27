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
        try {
            verify(getTargetContext());
            Bundle result = report("SIMPLEOFFICE_CONTACTS_PROVIDER_PASS");
            result.putString("simpleoffice_contacts_provider", "pass");
            finish(Activity.RESULT_OK, result);
        } catch (Exception | AssertionError failure) {
            String reason = failure instanceof AssertionError
                    ? failure.getMessage() : failure.getClass().getSimpleName();
            finish(Activity.RESULT_CANCELED, report("Contact provider acceptance failed: " + reason));
        } finally {
            AndroidManagedSyncAccount.clear();
        }
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

package de.simpleoffice4me.android;

import android.accounts.Account;
import android.accounts.AccountManager;
import android.content.Context;

/** Provider rows are owned by a distinct account per authenticated local principal. */
final class AndroidManagedSyncAccount {
    static final String TYPE = "de.simpleoffice4me.android.sync";
    private static final ThreadLocal<String> PRINCIPAL = new ThreadLocal<>();

    private AndroidManagedSyncAccount() {}

    static void bind(String principal) {
        if (principal == null || !principal.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException("Invalid account scope");
        }
        PRINCIPAL.set(principal);
    }

    static void clear() {
        PRINCIPAL.remove();
    }

    static Account require(Context context) {
        String principal = PRINCIPAL.get();
        if (principal == null) throw new IllegalStateException("No authenticated account scope");
        Account account = new Account("SimpleOffice · " + principal.substring(0, 24), TYPE);
        AccountManager manager = AccountManager.get(context);
        for (Account item : manager.getAccountsByType(TYPE)) {
            if (!account.equals(item)) continue;
            if (!principal.equals(manager.getUserData(item, "scope"))) {
                throw new IllegalStateException("Local account scope mismatch");
            }
            return item;
        }
        android.os.Bundle extras = new android.os.Bundle();
        extras.putString("scope", principal);
        if (!manager.addAccountExplicitly(account, null, extras)) {
            throw new IllegalStateException("Managed Android account could not be created");
        }
        return account;
    }
}

package de.simpleoffice4me.android;

import android.accounts.AbstractAccountAuthenticator;
import android.accounts.Account;
import android.accounts.AccountAuthenticatorResponse;
import android.accounts.AccountManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.os.IBinder;

/** Local-only account identity. No credentials or generic system add flow. */
public final class AndroidManagedAuthenticatorService extends Service {
    private LocalAuthenticator authenticator;

    @Override public IBinder onBind(Intent intent) {
        if (intent == null || !AccountManager.ACTION_AUTHENTICATOR_INTENT.equals(intent.getAction())) return null;
        if (authenticator == null) authenticator = new LocalAuthenticator(this);
        return authenticator.getIBinder();
    }

    private static final class LocalAuthenticator extends AbstractAccountAuthenticator {
        LocalAuthenticator(Context context) { super(context); }

        private Bundle unsupported() {
            Bundle result = new Bundle();
            result.putInt(AccountManager.KEY_ERROR_CODE, AccountManager.ERROR_CODE_UNSUPPORTED_OPERATION);
            result.putString(AccountManager.KEY_ERROR_MESSAGE, "Set up synchronization inside SimpleOffice");
            return result;
        }

        @Override public Bundle editProperties(AccountAuthenticatorResponse reply, String type) {
            return unsupported();
        }
        @Override public Bundle addAccount(AccountAuthenticatorResponse reply, String type, String tokenType,
                                           String[] features, Bundle options) {
            return unsupported();
        }
        @Override public Bundle confirmCredentials(AccountAuthenticatorResponse reply, Account account, Bundle options) {
            return unsupported();
        }
        @Override public Bundle getAuthToken(AccountAuthenticatorResponse reply, Account account, String type, Bundle options) {
            return unsupported();
        }
        @Override public String getAuthTokenLabel(String type) { return ""; }
        @Override public Bundle updateCredentials(AccountAuthenticatorResponse reply, Account account, String type, Bundle options) {
            return unsupported();
        }
        @Override public Bundle hasFeatures(AccountAuthenticatorResponse reply, Account account, String[] features) {
            Bundle result = new Bundle();
            result.putBoolean(AccountManager.KEY_BOOLEAN_RESULT, false);
            return result;
        }
    }
}

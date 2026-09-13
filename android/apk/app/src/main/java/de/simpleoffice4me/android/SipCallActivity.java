package de.simpleoffice4me.android;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.widget.Toast;

import java.util.Locale;

/**
 * Narrow Android bridge for chat call actions.
 *
 * The WebView deliberately blocks arbitrary custom URI schemes. Chat call links
 * therefore enter this activity through one fixed HTTPS app-link and this class
 * forwards only a validated sip: URI to an installed SIP client. No credentials
 * or arbitrary intents are accepted.
 */
public class SipCallActivity extends Activity {
    private static final String BRIDGE_HOST = "sip.simpleoffice.local";
    private static final String BRIDGE_PATH = "/call";
    private static final int MAX_TARGET_LENGTH = 512;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        handle(getIntent());
        finish();
    }

    private void handle(Intent source) {
        Uri bridge = source == null ? null : source.getData();
        if (!isTrustedBridge(bridge)) {
            Toast.makeText(this, "Ungültiger SimpleOffice-Anruflink.", Toast.LENGTH_SHORT).show();
            return;
        }
        String encodedTarget = bridge.getQueryParameter("target");
        if (encodedTarget == null || encodedTarget.length() > MAX_TARGET_LENGTH) {
            Toast.makeText(this, "SIP-Ziel fehlt oder ist zu lang.", Toast.LENGTH_SHORT).show();
            return;
        }
        Uri target;
        try {
            target = Uri.parse(encodedTarget);
        } catch (RuntimeException error) {
            Toast.makeText(this, "SIP-Ziel ist ungültig.", Toast.LENGTH_SHORT).show();
            return;
        }
        if (!isSafeSipTarget(target)) {
            Toast.makeText(this, "Nur sichere SIP-Ziele werden geöffnet.", Toast.LENGTH_SHORT).show();
            return;
        }
        try {
            Intent intent = new Intent(Intent.ACTION_VIEW, target);
            intent.addCategory(Intent.CATEGORY_BROWSABLE);
            startActivity(intent);
        } catch (ActivityNotFoundException error) {
            Toast.makeText(this, "Keine SIP-/Video-App installiert.", Toast.LENGTH_LONG).show();
        }
    }

    private static boolean isTrustedBridge(Uri uri) {
        return uri != null
                && "https".equalsIgnoreCase(uri.getScheme())
                && BRIDGE_HOST.equalsIgnoreCase(uri.getHost())
                && BRIDGE_PATH.equals(uri.getPath());
    }

    private static boolean isSafeSipTarget(Uri uri) {
        if (uri == null || !"sip".equalsIgnoreCase(uri.getScheme())) return false;
        String text = uri.toString();
        if (text.length() < 5 || text.length() > MAX_TARGET_LENGTH) return false;
        if (text.indexOf('\r') >= 0 || text.indexOf('\n') >= 0) return false;
        String lower = text.toLowerCase(Locale.ROOT);
        // Keep credentials and dangerous URI extensions out of the handoff.
        if (lower.contains("password=") || lower.contains("transport=ws") || lower.contains("transport=wss")) return false;
        String schemeSpecific = uri.getSchemeSpecificPart();
        if (schemeSpecific == null || schemeSpecific.startsWith("//")) return false;
        int at = schemeSpecific.lastIndexOf('@');
        return at > 0 && at < schemeSpecific.length() - 1;
    }
}

package de.simpleoffice4me.android;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.widget.Toast;

import com.chaquo.python.Python;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Locale;
import java.util.UUID;
import java.util.function.BooleanSupplier;

/** Foreground native approval; web content cannot confirm either step. */
final class AndroidPcFolderConsent {
    private final Activity activity;
    private final AndroidLocalFolderRegistry registry;
    private String generation = "";
    private String sharedSource = "";
    private AlertDialog dialog;

    AndroidPcFolderConsent(Activity activity, AndroidLocalFolderRegistry registry) {
        this.activity = activity;
        this.registry = registry;
    }

    private String text(String de, String en) {
        return Locale.getDefault().getLanguage().equals("de") ? de : en;
    }

    private JSONObject call(String action, String owner, String expected, String fingerprint,
                            AndroidPairedFolderSource adapter) {
        if (!Python.isStarted()) return new JSONObject();
        try {
            return new JSONObject(Python.getInstance().getModule("app.android_paired_folder_session")
                    .callAttr("phone_call", action, owner, expected, fingerprint, adapter).toString());
        } catch (RuntimeException | JSONException unavailable) {
            return new JSONObject();
        }
    }

    void stop() {
        if (dialog != null) {
            dialog.dismiss();
            dialog = null;
        }
        String ending = generation;
        generation = "";
        sharedSource = "";
        if (!ending.isEmpty()) call("stop", "", ending, "", null);
    }

    private void notice(boolean ok, String de, String en) {
        Toast.makeText(activity, ok ? text(de, en) : text(
                "Nicht verfügbar. Prüfe Ordner, Kopplung und Netzwerk.",
                "Unavailable. Check the folder, pairing and network."), Toast.LENGTH_LONG).show();
    }

    private String shownCode(String code) {
        StringBuilder display = new StringBuilder();
        for (int i = 0; i < code.length(); i += 8) {
            if (i > 0) display.append(i == 32 ? '\n' : ' ');
            display.append(code, i, i + 8);
        }
        return display.toString();
    }

    void manage(String source, String owner, BooleanSupplier stillPresent) {
        if (!stillPresent.getAsBoolean() || (dialog != null && dialog.isShowing())) return;
        final JSONObject folder;
        try {
            folder = new JSONObject(registry.check(source, owner));
        } catch (RuntimeException | JSONException unavailable) {
            notice(false, "", "");
            return;
        }
        String name = folder.optString("name");
        final String expected = generation;
        JSONObject status = expected.isEmpty() ? new JSONObject() : call("status", owner, expected, "", null);
        String fingerprint = status.optString("fingerprint");
        boolean paired = status.optBoolean("paired");
        AlertDialog.Builder builder = new AlertDialog.Builder(activity)
                .setTitle(text("PC-Zugriff", "PC access"))
                .setNeutralButton(text("Schließen", "Close"), null);
        if (!expected.isEmpty()) builder.setNegativeButton(text("Zugriff beenden", "Stop access"), (unused, which) -> {
            if (expected.equals(generation)) {
                stop();
                notice(true, "PC-Zugriff beendet.", "PC access stopped.");
            }
        });
        if (paired) {
            boolean unshare = source.equals(sharedSource) && status.optBoolean("shared");
            if (unshare) {
                builder.setMessage(text("Freigegeben: ", "Shared: ") + name + "\n\n"
                        + text("Nur die Ordnerfreigabe beenden. Der PC bleibt bestätigt.",
                        "Stop sharing this folder only. The PC stays paired."));
                builder.setPositiveButton(text("Ordnerfreigabe beenden", "Unshare folder"), (unused, which) -> {
                    if (!stillPresent.getAsBoolean() || !expected.equals(generation)) return;
                    boolean ok = call("unshare", owner, expected, fingerprint, null).optBoolean("ok");
                    if (ok) sharedSource = "";
                    notice(ok, "Ordnerfreigabe beendet.", "Folder unshared.");
                });
                dialog = builder.show();
                dialog.getButton(DialogInterface.BUTTON_POSITIVE).setFilterTouchesWhenObscured(true);
                return;
            }
            builder.setMessage(text("Ordner: ", "Folder: ") + name + "\n\n"
                    + text("Nur passende Dateinamen für diesen PC freigeben (max. 10 Minuten). Keine Dateien.",
                    "Share only matching file names with this PC (up to 10 minutes). No files.") + "\n\n"
                    + shownCode(fingerprint));
            builder.setPositiveButton(text("Ordner freigeben", "Share folder"), (unused, which) -> {
                if (!stillPresent.getAsBoolean() || !expected.equals(generation)) return;
                try {
                    AndroidPairedFolderSource adapter = new AndroidPairedFolderSource(activity, registry, source, owner);
                    boolean ok = call("share", owner, expected, fingerprint, adapter).optBoolean("ok");
                    if (ok) sharedSource = source;
                    notice(ok, "Dateinamensuche freigegeben. Lass diese Seite geöffnet.",
                            "Filename search shared. Keep this screen open.");
                } catch (RuntimeException unavailable) {
                    notice(false, "", "");
                }
            });
        } else if (fingerprint.matches("[0-9a-f]{64}")) {
            builder.setMessage(text("Vergleiche den ganzen Code mit dem PC. Das Koppeln allein gibt keine Daten frei.",
                    "Compare the entire code with the PC. Pairing by itself does not share any data.") + "\n\n"
                    + shownCode(fingerprint));
            builder.setPositiveButton(text("PC bestätigen", "Confirm PC"), (unused, which) -> {
                if (stillPresent.getAsBoolean() && expected.equals(generation)) {
                    notice(call("pair", owner, expected, fingerprint, null).optBoolean("ok"),
                            "PC bestätigt. Öffne PC-Zugriff erneut, um einen Ordner freizugeben.",
                            "PC confirmed. Open PC access again to share a folder.");
                }
            });
        } else {
            builder.setMessage(text(
                    "Aktiviere die Kopplung für zwei Minuten. Melde dich am PC über vertrauenswürdiges LAN-HTTPS mit demselben Konto an. Öffne dort Lokale Ordner und starte die Kopplung. Öffne danach hier erneut PC-Zugriff.",
                    "Enable pairing for two minutes. On the PC, sign in to this app over trusted LAN HTTPS with the same account. Open Local folders and start pairing, then reopen PC access here."));
            builder.setPositiveButton(text(expected.isEmpty() ? "Kopplung erlauben" : "Kopplung neu erlauben",
                    expected.isEmpty() ? "Enable pairing" : "Restart pairing"), (unused, which) -> {
                if (!stillPresent.getAsBoolean() || !expected.equals(generation)) return;
                String next = UUID.randomUUID().toString().replace("-", "");
                boolean ok = call("enable", owner, next, "", null).optBoolean("ok");
                if (ok) generation = next;
                notice(ok, "Kopplung erlaubt. Erst am PC starten, dann hier erneut öffnen.",
                        "Pairing enabled. Start on the PC, then reopen this dialog.");
            });
        }
        dialog = builder.show();
        dialog.getButton(DialogInterface.BUTTON_POSITIVE).setFilterTouchesWhenObscured(true);
    }
}

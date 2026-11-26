package de.simpleoffice4me.android;

import android.content.ContentResolver;
import android.content.Context;
import android.net.Uri;
import android.os.CancellationSignal;
import android.provider.DocumentsContract;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.Locale;

/** Chaquopy calls this single metadata-only method for an interactively approved SAF tree. */
public final class AndroidPairedFolderSource {
    private static final int LIMIT = 40;
    private final ContentResolver resolver;
    private final AndroidLocalFolderRegistry registry;
    private final String sourceId;
    private final String profile;
    private final Uri grantedTree;

    AndroidPairedFolderSource(Context context, AndroidLocalFolderRegistry registry, String sourceId, String profile) {
        this.resolver = context.getApplicationContext().getContentResolver();
        this.registry = registry;
        this.sourceId = sourceId;
        this.profile = profile;
        this.grantedTree = registry.treeUriForSearch(sourceId, profile);
    }

    private void requireSameGrant() {
        if (!grantedTree.equals(registry.treeUriForSearch(sourceId, profile))) {
            throw new SecurityException("Selected SAF source changed");
        }
    }

    public String readNamesForPc(String query) {
        if (query == null || query.length() > 240) return "{}";
        try {
            requireSameGrant();
            CancellationSignal signal = new CancellationSignal();
            String root = DocumentsContract.getTreeDocumentId(grantedTree);
            JSONArray names = new JSONArray();
            boolean limited = false;
            if (query.isEmpty()) {
                // Lease heartbeat checks provider availability, without traversing or returning rows.
                try (AndroidFolderFilenameIndex.Reader reader =
                             AndroidLocalFolderNames.children(resolver, grantedTree, root, signal)) {
                    if (reader == null) return "{}";
                }
            } else {
                AndroidFolderFilenameIndex.Snapshot inventory = AndroidFolderFilenameIndex.read(
                        root, directory -> AndroidLocalFolderNames.children(resolver, grantedTree, directory, signal),
                        signal::isCanceled);
                String term = query.toLowerCase(Locale.ROOT);
                limited = inventory.limited;
                for (AndroidFolderFilenameIndex.FileName item : inventory.files) {
                    if (!item.file.name.toLowerCase(Locale.ROOT).contains(term)) continue;
                    if (names.length() == LIMIT) {
                        limited = true;
                        break;
                    }
                    names.put(item.file.name);
                }
            }
            requireSameGrant();
            return new JSONObject().put("status", "ready").put("names", names).put("limited", limited).toString();
        } catch (RuntimeException | JSONException unavailable) {
            // Do not expose path/provider identifiers or Android exception messages.
            return "{}";
        }
    }
}

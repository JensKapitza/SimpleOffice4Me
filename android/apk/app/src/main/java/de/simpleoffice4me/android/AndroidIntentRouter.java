package de.simpleoffice4me.android;

import android.content.ClipData;
import android.content.Intent;
import android.net.Uri;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Keeps Android intents separate from the WebView lifecycle.
 *
 * Incoming files are never copied or imported silently. They are retained as
 * content URIs until the user opens the normal SimpleOffice document upload
 * control, which keeps the existing server-side validation and audit path.
 */
final class AndroidIntentRouter {
    static final int MAX_SHARED_FILES = 20;
    private static final Pattern DOCUMENT_ID = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._-]{0,199}");

    private final ArrayList<Uri> pendingSharedFiles = new ArrayList<>();
    private String pendingNavigationPath = "";
    private boolean navigationDispatched;

    void accept(Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        if (Intent.ACTION_SEND.equals(action) || Intent.ACTION_SEND_MULTIPLE.equals(action)) {
            collectSharedFiles(intent);
            if (!pendingSharedFiles.isEmpty()) setPendingNavigation("/documents");
            return;
        }
        if (Intent.ACTION_VIEW.equals(action)) {
            Uri data = intent.getData();
            if (isContentUri(data)) {
                addIncomingContentUri(data);
                setPendingNavigation("/documents");
                return;
            }
            String path = deepLinkPath(data);
            if (!path.isEmpty()) setPendingNavigation(path);
        }
    }

    boolean hasPendingSharedFiles() {
        return !pendingSharedFiles.isEmpty();
    }

    int pendingSharedFileCount() {
        return pendingSharedFiles.size();
    }

    Uri[] consumeSharedFiles() {
        Uri[] values = pendingSharedFiles.toArray(new Uri[0]);
        pendingSharedFiles.clear();
        return values;
    }

    boolean hasPendingNavigation() {
        return !pendingNavigationPath.isEmpty();
    }

    String pendingNavigationPath() {
        return pendingNavigationPath;
    }

    /**
     * MainActivity calls this once when dispatching a target and again after
     * the target page is reached. Keeping the first call armed preserves the
     * target across an intermediate /auth/login redirect.
     */
    void clearPendingNavigation() {
        if (pendingNavigationPath.isEmpty()) return;
        if (!navigationDispatched) {
            navigationDispatched = true;
            return;
        }
        pendingNavigationPath = "";
        navigationDispatched = false;
    }

    private void setPendingNavigation(String path) {
        pendingNavigationPath = path;
        navigationDispatched = false;
    }

    private void addIncomingContentUri(Uri uri) {
        Set<Uri> unique = new LinkedHashSet<>(pendingSharedFiles);
        addUri(unique, uri);
        pendingSharedFiles.clear();
        for (Uri value : unique) {
            if (pendingSharedFiles.size() >= MAX_SHARED_FILES) break;
            pendingSharedFiles.add(value);
        }
    }

    private void collectSharedFiles(Intent intent) {
        Set<Uri> unique = new LinkedHashSet<>(pendingSharedFiles);
        if (Intent.ACTION_SEND.equals(intent.getAction())) {
            addUri(unique, intent.getParcelableExtra(Intent.EXTRA_STREAM));
        } else {
            ArrayList<Uri> streams = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (streams != null) {
                for (Uri uri : streams) addUri(unique, uri);
            }
        }
        ClipData clipData = intent.getClipData();
        if (clipData != null) {
            for (int index = 0; index < clipData.getItemCount(); index++) {
                addUri(unique, clipData.getItemAt(index).getUri());
            }
        }
        pendingSharedFiles.clear();
        for (Uri uri : unique) {
            if (pendingSharedFiles.size() >= MAX_SHARED_FILES) break;
            pendingSharedFiles.add(uri);
        }
    }

    private static boolean isContentUri(Uri uri) {
        return uri != null && "content".equalsIgnoreCase(uri.getScheme());
    }

    private static void addUri(Set<Uri> values, Uri uri) {
        if (uri == null || values.size() >= MAX_SHARED_FILES) return;
        // Android's content:// grant is the only supported cross-app file
        // boundary. file:// paths from other apps are intentionally rejected.
        if (isContentUri(uri)) values.add(uri);
    }

    static String deepLinkPath(Uri uri) {
        if (uri == null
                || !"simpleoffice4me".equalsIgnoreCase(uri.getScheme())
                || !"open".equalsIgnoreCase(uri.getHost())) return "";
        List<String> segments = uri.getPathSegments();
        if (segments.isEmpty()) return "/";
        String first = segments.get(0);
        if ("documents".equals(first)) {
            if (segments.size() == 1) return "/documents";
            String documentId = segments.get(1);
            return segments.size() == 2 && DOCUMENT_ID.matcher(documentId).matches()
                    ? "/documents/" + documentId : "";
        }
        if (segments.size() != 1) return "";
        if ("inventory".equals(first)) return "/inventory";
        if ("images".equals(first)) return "/documents/images";
        if ("calendar".equals(first)) return "/documents/calendar";
        if ("contacts".equals(first)) return "/documents/contacts";
        if ("drive".equals(first)) return "/settings/google-drive";
        return "";
    }
}

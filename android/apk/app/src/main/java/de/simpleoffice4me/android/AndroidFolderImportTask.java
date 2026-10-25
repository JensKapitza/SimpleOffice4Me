package de.simpleoffice4me.android;

import android.content.ContentResolver;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.os.CancellationSignal;
import android.provider.DocumentsContract;
import android.util.Log;
import android.webkit.CookieManager;

import org.json.JSONArray;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.NoSuchAlgorithmException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;
import java.util.concurrent.atomic.AtomicBoolean;

/** Streams a user-selected SAF tree through the authenticated document upload route. */
final class AndroidFolderImportTask implements Runnable {
    interface Listener {
        void onUpdate(String state, int completedFiles, int totalFiles, String message);
    }

    private static final String LOCAL_URL = "http://127.0.0.1:8765/";
    private static final String UPLOAD_URL = LOCAL_URL + "documents/upload";
    static final int MAX_TREES = 20;
    private static final int MAX_DEPTH = 64;
    private static final int MAX_PATH_BYTES = 4096;
    private static final int MAX_MANIFEST_BYTES = 400000;
    private static final int MAX_SOURCE_IDS_BYTES = 100000;
    private static final int BUFFER_SIZE = 64 * 1024;
    private static final Pattern RESERVED_NAME = Pattern.compile(
            "^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\\..*)?$", Pattern.CASE_INSENSITIVE);

    private final Context context;
    private final List<Uri> treeUris;
    private final List<Uri> ownedGrantUris;
    private final String csrfToken;
    private final long maxRequestBytes;
    private final Listener listener;
    private volatile HttpURLConnection connection;
    private volatile InputStream activeInput;
    private volatile CancellationSignal activeQuery;
    private volatile boolean cancelled;
    private volatile boolean requestBodySent;
    private volatile boolean running;
    private volatile boolean confirmed;
    private volatile boolean finished;
    private volatile PreparedUpload preparedUpload;
    private final AtomicBoolean grantsReleased = new AtomicBoolean();

    AndroidFolderImportTask(Context context, List<Uri> treeUris, List<Uri> ownedGrantUris, String csrfToken,
            long maxRequestBytes, Listener listener) {
        this.context = context.getApplicationContext();
        this.treeUris = Collections.unmodifiableList(new ArrayList<>(treeUris));
        this.ownedGrantUris = Collections.unmodifiableList(new ArrayList<>(ownedGrantUris));
        this.csrfToken = csrfToken == null ? "" : csrfToken;
        this.maxRequestBytes = maxRequestBytes;
        this.listener = listener;
    }

    synchronized boolean cancel() {
        if (requestBodySent) return false;
        cancelled = true;
        HttpURLConnection active = connection;
        if (active != null) active.disconnect();
        CancellationSignal query = activeQuery;
        if (query != null) query.cancel();
        InputStream input = activeInput;
        if (input != null) {
            try {
                input.close();
            } catch (IOException ignored) {
                // Closing an active SAF stream is best-effort cancellation cleanup.
            }
        }
        if (preparedUpload != null && !running && !finished) {
            finished = true;
            releaseOwnedGrants();
            notifyListener("cancelled", 0, 0, "Ordnerimport abgebrochen.");
        }
        return true;
    }

    synchronized boolean confirm() {
        if (preparedUpload == null || running || confirmed || finished || cancelled) return false;
        confirmed = true;
        return true;
    }

    boolean isPrepared() {
        return preparedUpload != null && !finished && !cancelled;
    }

    int getTreeCount() {
        return treeUris.size();
    }

    List<Uri> getOwnedGrantUris() {
        return ownedGrantUris;
    }

    String getPreviewMessage() {
        PreparedUpload batch = preparedUpload;
        return batch == null ? "" : batch.previewMessage;
    }

    boolean matchesCsrf(String value) {
        return value != null && MessageDigest.isEqual(
                csrfToken.getBytes(StandardCharsets.UTF_8), value.getBytes(StandardCharsets.UTF_8));
    }

    @Override
    public void run() {
        synchronized (this) {
            if (finished) return;
            running = true;
        }
        boolean terminal = false;
        try {
            checkCancelled();
            if (treeUris.isEmpty() || treeUris.size() > MAX_TREES) {
                throw new ImportFailure("Bitte höchstens 20 Ordner pro Import auswählen.");
            }
            for (Uri uri : treeUris) {
                if (!uri.isHierarchical() || !"content".equalsIgnoreCase(uri.getScheme())
                        || !hasPersistedReadGrant(uri)) {
                    throw new ImportFailure("Eine Ordnerfreigabe ist ungültig oder wurde widerrufen.");
                }
            }
            if (preparedUpload == null) {
                notifyListener("scanning", 0, 0, "Ordner und Dateigrößen werden geprüft …");
                preparedUpload = prepareUpload();
                checkCancelled();
                // Publish the actionable preview only after run() has released its running state.
                return;
            }
            if (!confirmed) return;
            PreparedUpload batch = preparedUpload;
            notifyListener("uploading", 0, batch.files.size(),
                    batch.files.size() + " Datei(en) werden importiert …");
            String importId = java.util.UUID.randomUUID().toString().replace("-", "");
            int completed = 0;
            while (completed < batch.files.size()) {
                checkCancelled();
                int end = completed;
                long bytes = 0;
                int pathBytes = 2;
                while (end < batch.files.size()) {
                    Entry entry = batch.files.get(end);
                    int nextPathBytes = entry.relativePath.getBytes(StandardCharsets.UTF_8).length * 2 + 8;
                    long nextBytes = entry.size + nextPathBytes + 4096;
                    if (end > completed && (bytes + nextBytes > maxRequestBytes
                            || pathBytes + nextPathBytes > MAX_MANIFEST_BYTES
                            || end - completed >= 100)) break;
                    if (nextBytes > maxRequestBytes || nextPathBytes > MAX_MANIFEST_BYTES) {
                        throw new ImportFailure("Eine einzelne Datei überschreitet das Upload-Limit.");
                    }
                    bytes += nextBytes;
                    pathBytes += nextPathBytes;
                    end++;
                }
                PreparedUpload part = prepareUpload(batch.files.subList(completed, end), importId);
                upload(part.boundary, part.files, part.fileHeaders, part.folderField,
                        part.manifestField, part.sourceIdsField, part.closing, part.bodyLength);
                        part.manifestField, part.sourceIdsField, part.closing, part.bodyLength);
                requestBodySent = false;
                completed = end;
                notifyListener("uploading", completed, batch.files.size(),
                        completed + " von " + batch.files.size() + " Dateien verarbeitet.");
            }
            finished = true;
            terminal = true;
            notifyListener("done", batch.files.size(), batch.files.size(), "Ordnerimport abgeschlossen.");
        } catch (ImportFailure error) {
            finished = true;
            terminal = true;
            notifyListener(cancelled ? "cancelled" : "error", 0, 0, error.getMessage());
        } catch (ArithmeticException error) {
            finished = true;
            terminal = true;
            notifyListener("error", 0, 0, "Der Ordnerstapel überschreitet das zulässige Größenlimit.");
        } catch (SecurityException error) {
            finished = true;
            terminal = true;
            notifyListener("error", 0, 0, "Der Zugriff auf den ausgewählten Ordner wurde widerrufen.");
        } catch (IOException error) {
            finished = true;
            terminal = true;
            notifyListener(cancelled ? "cancelled" : "error", 0, 0,
                    cancelled ? "Ordnerimport abgebrochen." : "Ordnerimport fehlgeschlagen. Bitte Verbindung und Ordnerzugriff prüfen.");
        } catch (RuntimeException error) {
            finished = true;
            terminal = true;
            Log.e("AndroidFolderImport", "Folder import failed: " + error.getClass().getSimpleName());
            notifyListener(cancelled ? "cancelled" : "error", 0, 0,
                    cancelled ? "Ordnerimport abgebrochen." : "Ordnerimport fehlgeschlagen. Bitte Ordnerzugriff prüfen.");
        } finally {
            running = false;
            HttpURLConnection active = connection;
            if (active != null) active.disconnect();
            if (terminal) releaseOwnedGrants();
            if (!terminal && preparedUpload != null && !confirmed && !cancelled) {
                notifyListener("preview", preparedUpload.files.size(), preparedUpload.files.size(),
                        preparedUpload.previewMessage);
            }
        }
    }

    private PreparedUpload prepareUpload() throws ImportFailure, IOException {
        List<Entry> files = collectFiles();
        checkCancelled();
        long totalBytes = 0;
        for (Entry entry : files) totalBytes = Math.addExact(totalBytes, entry.size);
        String preview = files.size() + " Datei(en), "
                + String.format(Locale.ROOT, "%.1f", totalBytes / 1048576.0)
                + " MiB aus " + treeUris.size()
                + " Ordner(n). Import erfolgt in Teilpaketen. Bitte bestätigen.";
        return new PreparedUpload("", files, Collections.emptyList(), new byte[0],
                new byte[0], new byte[0], 0, totalBytes, preview);
    }

    private PreparedUpload prepareUpload(List<Entry> files, String importId) throws ImportFailure, IOException {
        if (files.isEmpty()) throw new ImportFailure("Der ausgewählte Ordner enthält keine Dateien.");
        String boundary = "----SimpleOfficeFolder" + java.util.UUID.randomUUID().toString().replace("-", "");
        byte[] folderField = formField(boundary, "folder_import", "1");
        if (!importId.isEmpty()) {
            byte[] idField = formField(boundary, "import_id", importId);
            byte[] combined = new byte[folderField.length + idField.length];
            System.arraycopy(folderField, 0, combined, 0, folderField.length);
            System.arraycopy(idField, 0, combined, folderField.length, idField.length);
            folderField = combined;
        }
        JSONArray paths = new JSONArray();
        JSONArray sourceIds = new JSONArray();
        long bodyLength = folderField.length;
        long totalBytes = 0;
        List<byte[]> fileHeaders = new ArrayList<>(files.size());
        for (Entry entry : files) {
            paths.put(entry.relativePath);
            sourceIds.put(entry.sourceIdentity);
            byte[] header = fileHeader(boundary, entry.fileName());
            fileHeaders.add(header);
            bodyLength = Math.addExact(bodyLength, header.length);
            bodyLength = Math.addExact(bodyLength, entry.size);
            bodyLength = Math.addExact(bodyLength, 2); // CRLF after the file body.
            totalBytes = Math.addExact(totalBytes, entry.size);
        }
        byte[] manifestField = formField(boundary, "relative_paths", paths.toString());
        byte[] sourceIdsField = formField(boundary, "source_ids", sourceIds.toString());
        byte[] closing = ascii("--" + boundary + "--\r\n");
        bodyLength = Math.addExact(bodyLength, manifestField.length);
        bodyLength = Math.addExact(bodyLength, sourceIdsField.length);
        bodyLength = Math.addExact(bodyLength, closing.length);
        if (paths.toString().getBytes(StandardCharsets.UTF_8).length > MAX_MANIFEST_BYTES) {
            throw new ImportFailure("Die Pfadliste ist zu groß. Bitte einen kleineren Ordnerstapel auswählen.");
        }
        if (sourceIds.toString().getBytes(StandardCharsets.UTF_8).length > MAX_SOURCE_IDS_BYTES) {
            throw new ImportFailure("Die Herkunfts-ID-Liste ist zu groß. Bitte einen kleineren Ordnerstapel auswählen.");
        }
        if (maxRequestBytes <= 0 || bodyLength > maxRequestBytes) {
            throw new ImportFailure("Der Ordnerstapel überschreitet das konfigurierte Upload-Limit.");
        }
        String preview = files.size() + " Datei(en), "
                + String.format(Locale.ROOT, "%.1f", totalBytes / 1048576.0)
                + " MiB aus " + treeUris.size()
                + " Ordner(n). Ziel: neuer eindeutiger Importordner. Bitte bestätigen.";
        return new PreparedUpload(boundary, files, fileHeaders, folderField,
                manifestField, sourceIdsField, closing, bodyLength, totalBytes, preview);
    }

    private void releaseOwnedGrants() {
        if (!grantsReleased.compareAndSet(false, true)) return;
        for (Uri uri : ownedGrantUris) {
            try {
                context.getContentResolver().releasePersistableUriPermission(
                        uri, android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION);
            } catch (SecurityException ignored) {
                // A provider may already have revoked this temporary import grant.
            } finally {
                AndroidFolderImportGrantStore.forget(context, uri);
            }
        }
    }

    private List<Entry> collectFiles() throws ImportFailure, IOException {
        ContentResolver resolver = context.getContentResolver();
        List<Entry> result = new ArrayList<>();
        int[] entryCount = {0};
        Set<String> rootNames = new HashSet<>();
        for (Uri treeUri : treeUris) {
            checkCancelled();
            String rootId;
            try {
                rootId = DocumentsContract.getTreeDocumentId(treeUri);
            } catch (IllegalArgumentException error) {
                throw new ImportFailure("Ein ausgewählter Ordner ist ungültig.");
            }
            String rootName = validateSegment(queryDisplayName(
                    DocumentsContract.buildDocumentUriUsingTree(treeUri, rootId), "Ordner"));
            String sourceIdentity = stableSourceIdentity(context, treeUri);
            if (!rootNames.add(rootName.toLowerCase(Locale.ROOT))) {
                throw new ImportFailure("Mehrere ausgewählte Ordner haben denselben Namen. Bitte gemeinsam über einen übergeordneten Ordner importieren.");
            }
            Set<String> visitedDirectories = new HashSet<>();
            visitedDirectories.add(rootId);
            collectDirectory(resolver, treeUri, rootId, rootName, sourceIdentity, 0,
                    visitedDirectories, entryCount, result);
        }
        Set<String> destinationPaths = new HashSet<>();
        for (Entry entry : result) {
            if (!destinationPaths.add(entry.relativePath.toLowerCase(Locale.ROOT))) {
                throw new ImportFailure("Der Ordner enthält doppelte Dateipfade.");
            }
        }
        return result;
    }

    private void collectDirectory(ContentResolver resolver, Uri treeUri, String directoryId, String parentPath,
            String sourceIdentity, int depth, Set<String> visitedDirectories, int[] entryCount, List<Entry> result)
            throws ImportFailure, IOException {
        checkCancelled();
        if (depth > MAX_DEPTH) throw new ImportFailure("Der Ordner ist zu tief verschachtelt.");
        Uri childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, directoryId);
        List<Child> children = new ArrayList<>();
        CancellationSignal querySignal = new CancellationSignal();
        activeQuery = querySignal;
        if (cancelled) querySignal.cancel();
        try (Cursor cursor = resolver.query(childrenUri,
                new String[]{DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                        DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                        DocumentsContract.Document.COLUMN_MIME_TYPE,
                        DocumentsContract.Document.COLUMN_SIZE},
                null, null, null, querySignal)) {
            if (cursor == null) throw new ImportFailure("Android konnte den Ordnerinhalt nicht lesen.");
            int idColumn = cursor.getColumnIndexOrThrow(DocumentsContract.Document.COLUMN_DOCUMENT_ID);
            int nameColumn = cursor.getColumnIndexOrThrow(DocumentsContract.Document.COLUMN_DISPLAY_NAME);
            int mimeColumn = cursor.getColumnIndexOrThrow(DocumentsContract.Document.COLUMN_MIME_TYPE);
            int sizeColumn = cursor.getColumnIndexOrThrow(DocumentsContract.Document.COLUMN_SIZE);
            while (cursor.moveToNext()) {
                String childId = cursor.getString(idColumn);
                String name = validateSegment(cursor.getString(nameColumn));
                String mime = cursor.getString(mimeColumn);
                long size = cursor.isNull(sizeColumn) ? -1 : cursor.getLong(sizeColumn);
                if (childId == null || childId.isEmpty()) {
                    throw new ImportFailure("Android hat einen ungültigen Dateieintrag geliefert.");
                }
                children.add(new Child(childId, name, mime, size));
            }
        } catch (IllegalArgumentException error) {
            throw new ImportFailure("Android konnte den Ordnerinhalt nicht lesen.");
        } finally {
            activeQuery = null;
        }
        Collections.sort(children, Comparator.comparing(child -> child.name.toLowerCase(Locale.ROOT)));
        for (Child child : children) {
            checkCancelled();
            String path = parentPath + "/" + child.name;
            if (path.getBytes(StandardCharsets.UTF_8).length > MAX_PATH_BYTES) {
                throw new ImportFailure("Ein relativer Ordnerpfad ist zu lang.");
            }
            if (DocumentsContract.Document.MIME_TYPE_DIR.equals(child.mimeType)) {
                if (!visitedDirectories.add(child.documentId)) {
                    throw new ImportFailure("Der Ordneranbieter lieferte eine zyklische Verzeichnisstruktur.");
                }
                collectDirectory(resolver, treeUri, child.documentId, path, sourceIdentity, depth + 1,
                        visitedDirectories, entryCount, result);
                continue;
            }
            if (child.size < 0) {
                throw new ImportFailure("Android kann die Größe einer Datei nicht bestimmen. Bitte einen anderen Ordneranbieter verwenden.");
            }
            result.add(new Entry(
                    DocumentsContract.buildDocumentUriUsingTree(treeUri, child.documentId),
                    path, child.size, sourceIdentity
            ));
        }
    }

    private static String stableSourceIdentity(Context context, Uri treeUri) throws ImportFailure {
        try {
            MessageDigest sha256 = MessageDigest.getInstance("SHA-256");
            sha256.update(AndroidFolderImportGrantStore.sourceIdentitySalt(context));
            sha256.update((byte) 0);
            byte[] digest = sha256.digest(treeUri.toString().getBytes(StandardCharsets.UTF_8));
            char[] hex = "0123456789abcdef".toCharArray();
            StringBuilder result = new StringBuilder(digest.length * 2);
            for (byte item : digest) {
                int value = item & 0xff;
                result.append(hex[value >>> 4]).append(hex[value & 0x0f]);
            }
            return result.toString();
        } catch (NoSuchAlgorithmException error) {
            throw new ImportFailure("Die Herkunft des Android-Ordners konnte nicht sicher bestimmt werden.");
        }
    }

    private boolean hasPersistedReadGrant(Uri uri) {
        for (android.content.UriPermission permission
                : context.getContentResolver().getPersistedUriPermissions()) {
            if (uri.equals(permission.getUri()) && permission.isReadPermission()) return true;
        }
        return false;
    }

    private String queryDisplayName(Uri documentUri, String fallback) throws ImportFailure {
        CancellationSignal querySignal = new CancellationSignal();
        activeQuery = querySignal;
        if (cancelled) querySignal.cancel();
        try (Cursor cursor = context.getContentResolver().query(documentUri,
                new String[]{DocumentsContract.Document.COLUMN_DISPLAY_NAME}, null, null, null, querySignal)) {
            if (cursor != null && cursor.moveToFirst()) {
                String value = cursor.getString(0);
                if (value != null && !value.trim().isEmpty()) return value;
            }
            return fallback;
        } catch (RuntimeException error) {
            throw new ImportFailure("Android konnte den ausgewählten Ordner nicht lesen.");
        } finally {
            activeQuery = null;
        }
    }

    private static String validateSegment(String source) throws ImportFailure {
        if (source == null) throw new ImportFailure("Android hat einen leeren Dateinamen geliefert.");
        String value = Normalizer.normalize(source, Normalizer.Form.NFC);
        if (value.isEmpty() || value.equals(".") || value.equals("..")
                || value.indexOf('/') >= 0 || value.indexOf('\\') >= 0 || value.indexOf(':') >= 0
                || value.endsWith(" ") || value.endsWith(".")
                || value.getBytes(StandardCharsets.UTF_8).length > 255 || value.length() > 255
                || RESERVED_NAME.matcher(value).matches()) {
            throw new ImportFailure("Ein Datei- oder Ordnername ist ungültig oder nicht portabel.");
        }
        for (int index = 0; index < value.length(); index++) {
            if (value.charAt(index) < 32) {
                throw new ImportFailure("Ein Datei- oder Ordnername enthält Steuerzeichen.");
            }
        }
        return value;
    }

    private void upload(String boundary, List<Entry> files, List<byte[]> headers,
            byte[] folderField, byte[] manifestField, byte[] sourceIdsField,
            byte[] closing, long bodyLength)
            throws IOException, ImportFailure {
        checkCancelled();
        requestBodySent = false;
        String cookie = CookieManager.getInstance().getCookie(LOCAL_URL);
        if (cookie == null || cookie.trim().isEmpty()) {
            throw new ImportFailure("Sitzung abgelaufen. Bitte die Dokumentseite neu laden.");
        }
        HttpURLConnection request = (HttpURLConnection) new URL(UPLOAD_URL).openConnection();
        connection = request;
        request.setRequestMethod("POST");
        request.setConnectTimeout(15000);
        request.setReadTimeout(60000);
        request.setInstanceFollowRedirects(false);
        request.setDoOutput(true);
        request.setRequestProperty("Cookie", cookie);
        request.setRequestProperty("X-CSRF-Token", csrfToken);
        request.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        request.setFixedLengthStreamingMode(bodyLength);
        long uploaded = 0;
        try (OutputStream output = new BufferedOutputStream(request.getOutputStream())) {
            output.write(folderField);
            output.write(manifestField);
            output.write(sourceIdsField);
            byte[] buffer = new byte[BUFFER_SIZE];
            for (int index = 0; index < files.size(); index++) {
                checkCancelled();
                Entry entry = files.get(index);
                output.write(headers.get(index));
                long fileBytes = 0;
                InputStream opened = context.getContentResolver().openInputStream(entry.uri);
                if (opened == null) throw new ImportFailure("Eine Datei konnte nicht geöffnet werden.");
                activeInput = opened;
                try (InputStream input = new BufferedInputStream(opened)) {
                    checkCancelled();
                    int count;
                    while ((count = input.read(buffer)) != -1) {
                        checkCancelled();
                        fileBytes += count;
                        if (fileBytes > entry.size) {
                            throw new ImportFailure("Eine Datei wurde während des Imports verändert.");
                        }
                        output.write(buffer, 0, count);
                        uploaded += count;
                        if ((uploaded & 0x3ffff) < count) {
                            notifyListener("uploading", index, files.size(),
                                    "Datei " + (index + 1) + " von " + files.size() + " wird übertragen …");
                        }
                    }
                } finally {
                    activeInput = null;
                }
                if (fileBytes != entry.size) {
                    throw new ImportFailure("Eine Datei wurde während des Imports verändert.");
                }
                output.write(ascii("\r\n"));
                notifyListener("uploading", index + 1, files.size(),
                        (index + 1) + " von " + files.size() + " Dateien übertragen.");
            }
            output.write(closing);
            output.flush();
        } catch (IOException error) {
            if (cancelled) throw new ImportFailure("Ordnerimport abgebrochen.");
            throw error;
        }
        requestBodySent = true;
        notifyListener("finishing", files.size(), files.size(),
                "Dateien übertragen; der Server schließt den Import ab …");
        checkCancelled();
        int responseCode = request.getResponseCode();
        updateCookies(request);
        String location = request.getHeaderField("Location");
        if (isLoginRedirect(location)) {
            throw new ImportFailure("Sitzung abgelaufen. Bitte erneut anmelden.");
        }
        if ((responseCode == HttpURLConnection.HTTP_MOVED_TEMP
                || responseCode == HttpURLConnection.HTTP_SEE_OTHER)
                && isDocumentsRedirect(location)) return;
        if (responseCode == 409) {
            throw new ImportFailure("Ein Upload-Teilpaket wurde nicht vollständig gespeichert. Import gestoppt.");
        }
        if (responseCode == 413) {
            throw new ImportFailure("Der Upload ist größer als das konfigurierte Limit.");
        }
        if (responseCode == 401) {
            throw new ImportFailure("Sitzung abgelaufen. Bitte erneut anmelden.");
        }
        if (responseCode == 403) {
            throw new ImportFailure("Sitzung oder Sicherheitsprüfung abgelaufen. Bitte die Seite neu laden.");
        }
        if (responseCode < 200 || responseCode >= 300) {
            throw new ImportFailure("Der Server hat den Ordnerimport abgelehnt (HTTP " + responseCode + ").");
        }
    }

    private static byte[] fileHeader(String boundary, String filename) {
        return ascii("--" + boundary + "\r\nContent-Disposition: form-data; name=\"files\"; filename*=UTF-8''"
                + encodeFilename(filename) + "\r\nContent-Type: application/octet-stream\r\n\r\n");
    }

    private static byte[] formField(String boundary, String name, String value) {
        return ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name
                + "\"\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n" + value + "\r\n")
                .getBytes(StandardCharsets.UTF_8);
    }

    private static String encodeFilename(String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        StringBuilder result = new StringBuilder(bytes.length * 3);
        final char[] hex = "0123456789ABCDEF".toCharArray();
        for (byte item : bytes) {
            int valueByte = item & 0xff;
            if ((valueByte >= 'a' && valueByte <= 'z') || (valueByte >= 'A' && valueByte <= 'Z')
                    || (valueByte >= '0' && valueByte <= '9') || "!#$&+-.^_`|~".indexOf(valueByte) >= 0) {
                result.append((char) valueByte);
            } else {
                result.append('%').append(hex[valueByte >>> 4]).append(hex[valueByte & 15]);
            }
        }
        return result.toString();
    }

    private static byte[] ascii(String value) {
        return value.getBytes(StandardCharsets.US_ASCII);
    }

    private static void updateCookies(HttpURLConnection request) {
        List<String> cookies = null;
        for (java.util.Map.Entry<String, List<String>> header : request.getHeaderFields().entrySet()) {
            if (header.getKey() != null && "Set-Cookie".equalsIgnoreCase(header.getKey())) {
                cookies = header.getValue();
                break;
            }
        }
        if (cookies == null) return;
        CookieManager manager = CookieManager.getInstance();
        for (String cookie : cookies) manager.setCookie(LOCAL_URL, cookie);
        manager.flush();
    }

    private static boolean isLoginRedirect(String location) {
        if (location == null) return false;
        Uri uri = Uri.parse(location);
        return uri.getPath() != null && uri.getPath().startsWith("/auth/");
    }

    private static boolean isDocumentsRedirect(String location) {
        if (location == null) return false;
        Uri uri = Uri.parse(location);
        if (uri.getScheme() != null && (!"http".equalsIgnoreCase(uri.getScheme())
                || !"127.0.0.1".equals(uri.getHost()) || uri.getPort() != 8765)) return false;
        String path = uri.getPath();
        return "/documents".equals(path) || "/documents/".equals(path);
    }

    private void checkCancelled() throws ImportFailure {
        if (cancelled) throw new ImportFailure("Ordnerimport abgebrochen.");
    }

    private void notifyListener(String state, int completed, int total, String message) {
        if (listener != null) listener.onUpdate(state, completed, total, message);
    }

    private static final class Child {
        final String documentId;
        final String name;
        final String mimeType;
        final long size;

        Child(String documentId, String name, String mimeType, long size) {
            this.documentId = documentId;
            this.name = name;
            this.mimeType = mimeType;
            this.size = size;
        }
    }

    private static final class PreparedUpload {
        final String boundary;
        final List<Entry> files;
        final List<byte[]> fileHeaders;
        final byte[] folderField;
        final byte[] manifestField;
        final byte[] sourceIdsField;
        final byte[] closing;
        final long bodyLength;
        final long totalBytes;
        final String previewMessage;

        PreparedUpload(String boundary, List<Entry> files, List<byte[]> fileHeaders,
                byte[] folderField, byte[] manifestField, byte[] sourceIdsField, byte[] closing,
                long bodyLength, long totalBytes, String previewMessage) {
            this.boundary = boundary;
            this.files = files;
            this.fileHeaders = fileHeaders;
            this.folderField = folderField;
            this.manifestField = manifestField;
            this.sourceIdsField = sourceIdsField;
            this.closing = closing;
            this.bodyLength = bodyLength;
            this.totalBytes = totalBytes;
            this.previewMessage = previewMessage;
        }
    }

    private static final class Entry {
        final Uri uri;
        final String relativePath;
        final long size;
        final String sourceIdentity;

        Entry(Uri uri, String relativePath, long size, String sourceIdentity) {
            this.uri = uri;
            this.relativePath = relativePath;
            this.size = size;
            this.sourceIdentity = sourceIdentity;
        }

        String fileName() {
            return relativePath.substring(relativePath.lastIndexOf('/') + 1);
        }
    }

    private static final class ImportFailure extends IOException {
        ImportFailure(String message) {
            super(message);
        }
    }
}

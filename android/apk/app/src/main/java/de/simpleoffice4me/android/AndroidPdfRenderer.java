package de.simpleoffice4me.android;

import android.graphics.Bitmap;
import android.graphics.pdf.PdfRenderer;
import android.os.ParcelFileDescriptor;
import android.util.Base64;
import android.webkit.CookieManager;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/** Bounded native PDF rendering for authenticated loopback reader URLs. */
final class AndroidPdfRenderer {
    private static final long MAX_PDF_BYTES = 200L * 1024L * 1024L;
    private static final int MAX_WIDTH = 2048;
    private static final int MIN_WIDTH = 320;

    private AndroidPdfRenderer() {}

    static String render(File cacheDir, String urlValue, int pageIndex, int requestedWidth) throws Exception {
        URL url = new URL(urlValue);
        String host = url.getHost();
        if (!"http".equalsIgnoreCase(url.getProtocol())
                || !("127.0.0.1".equals(host) || "localhost".equalsIgnoreCase(host))
                || url.getPort() != 8765
                || !url.getPath().matches("^/documents/[A-Za-z0-9._-]{1,200}/preview$")) {
            throw new SecurityException("PDF reader URL is not a trusted local document URL");
        }

        File target = File.createTempFile("simpleoffice-reader-", ".pdf", cacheDir);
        try {
            download(url, target);
            int width = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, requestedWidth));
            try (ParcelFileDescriptor descriptor = ParcelFileDescriptor.open(
                         target, ParcelFileDescriptor.MODE_READ_ONLY);
                 PdfRenderer renderer = new PdfRenderer(descriptor)) {
                int pageCount = renderer.getPageCount();
                if (pageIndex < 0 || pageIndex >= pageCount) {
                    throw new IllegalArgumentException("PDF page is outside the document");
                }
                try (PdfRenderer.Page page = renderer.openPage(pageIndex)) {
                    int height = Math.max(1, Math.round(
                            ((float) page.getHeight() / Math.max(1, page.getWidth())) * width));
                    Bitmap bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
                    try {
                        bitmap.eraseColor(0xffffffff);
                        page.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY);
                        ByteArrayOutputStream encoded = new ByteArrayOutputStream();
                        if (!bitmap.compress(Bitmap.CompressFormat.JPEG, 90, encoded)) {
                            throw new IllegalStateException("PDF bitmap could not be encoded");
                        }
                        JSONObject result = new JSONObject();
                        result.put("page", pageIndex);
                        result.put("pageCount", pageCount);
                        result.put("width", width);
                        result.put("height", height);
                        result.put("dataUrl", "data:image/jpeg;base64,"
                                + Base64.encodeToString(encoded.toByteArray(), Base64.NO_WRAP));
                        return result.toString();
                    } finally {
                        bitmap.recycle();
                    }
                }
            }
        } finally {
            target.delete();
        }
    }

    private static void download(URL url, File target) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        try {
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(30000);
            connection.setInstanceFollowRedirects(false);
            connection.setUseCaches(false);
            connection.setRequestProperty("Accept", "application/pdf,application/octet-stream");
            String cookie = CookieManager.getInstance().getCookie(url.toString());
            if (cookie != null && !cookie.trim().isEmpty()) connection.setRequestProperty("Cookie", cookie);
            int status = connection.getResponseCode();
            if (status != HttpURLConnection.HTTP_OK) {
                throw new IllegalStateException("PDF download returned HTTP " + status);
            }
            String contentType = connection.getContentType();
            if (contentType == null || !contentType.toLowerCase(java.util.Locale.ROOT).startsWith("application/pdf")) {
                throw new IllegalStateException("PDF reader endpoint returned an unexpected content type");
            }
            long declared = connection.getContentLengthLong();
            if (declared > MAX_PDF_BYTES) throw new IllegalArgumentException("PDF exceeds reader size limit");
            long total = 0L;
            try (InputStream input = connection.getInputStream();
                 FileOutputStream output = new FileOutputStream(target, false)) {
                byte[] buffer = new byte[64 * 1024];
                int read;
                while ((read = input.read(buffer)) >= 0) {
                    total += read;
                    if (total > MAX_PDF_BYTES) throw new IllegalArgumentException("PDF exceeds reader size limit");
                    output.write(buffer, 0, read);
                }
                output.getFD().sync();
            }
            if (total < 5L || (declared >= 0L && total != declared)) {
                throw new IllegalStateException("PDF download is incomplete");
            }
        } finally {
            connection.disconnect();
        }
    }
}

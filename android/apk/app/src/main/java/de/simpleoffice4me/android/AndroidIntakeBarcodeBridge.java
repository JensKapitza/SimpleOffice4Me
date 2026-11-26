package de.simpleoffice4me.android;

import android.content.Context;
import android.net.Uri;
import android.os.Handler;
import android.webkit.WebMessage;
import android.webkit.WebMessagePort;
import android.webkit.WebView;

import com.google.android.gms.common.moduleinstall.ModuleInstall;
import com.google.mlkit.vision.barcode.common.Barcode;
import com.google.mlkit.vision.codescanner.GmsBarcodeScanner;
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions;
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning;

/** Main-frame port for mixed intake only; does not expose a global JavaScript interface. */
final class AndroidIntakeBarcodeBridge {
    private final Context context;
    private final Handler ui;
    private final AndroidIntakeBarcodeSession session = new AndroidIntakeBarcodeSession();
    private WebView view;
    private WebMessagePort port;
    private String pageUrl;
    private AndroidIntakeBarcodeSession.Request checkingModules;
    private AndroidIntakeBarcodeSession.Request ready;
    private String readyMessage;
    private boolean resumed;
    private boolean loadFailed;
    private boolean closed;

    AndroidIntakeBarcodeBridge(Context context, Handler ui) {
        this.context = context;
        this.ui = ui;
    }

    void onLoadStarted() {
        closePort();
        loadFailed = false;
    }

    void onLoadFailed() {
        loadFailed = true;
        closePort();
    }

    void onHistory(String url, boolean reloading) {
        if (reloading || !AndroidIntakeBarcodeSession.isPage(url)) {
            closePort();
        } else if (port != null && !url.equals(pageUrl)) {
            invalidatePending();
            pageUrl = url;
        }
    }

    void onLoadFinished(WebView current, String url) {
        if (closed || loadFailed || !AndroidIntakeBarcodeSession.isPage(url) || !url.equals(current.getUrl())) return;
        if (port != null && current == view && url.equals(pageUrl)) return;
        closePort();
        WebMessagePort[] pair = null;
        try {
            pair = current.createWebMessageChannel();
            view = current;
            port = pair[0];
            pageUrl = url;
            port.setWebMessageCallback(new WebMessagePort.WebMessageCallback() {
                @Override
                public void onMessage(WebMessagePort sender, WebMessage message) {
                    handleMessage(sender, message.getData());
                }
            }, ui);
            current.postWebMessage(new WebMessage(AndroidIntakeBarcodeSession.OFFER,
                    new WebMessagePort[]{pair[1]}), Uri.parse(AndroidIntakeBarcodeSession.ORIGIN));
        } catch (RuntimeException error) {
            closePort();
            if (pair != null) closeUntransferred(pair[1]);
        }
    }

    private void handleMessage(WebMessagePort sender, String message) {
        if (sender != port || view == null || !pageUrl.equals(view.getUrl())) return;
        String cancel = AndroidIntakeBarcodeSession.cancelId(message);
        if (!cancel.isEmpty()) {
            session.cancel(cancel);
            AndroidIntakeBarcodeSession.Request checking = checkingModules;
            if (checking != null && checking.id.equals(cancel)) {
                checkingModules = null;
                session.finish(checking, view.getUrl());
            }
            return;
        }
        String id = AndroidIntakeBarcodeSession.requestId(message);
        if (id.isEmpty() || !resumed) return;
        AndroidIntakeBarcodeSession.Request request = session.begin(message, pageUrl);
        if (request == null) {
            send(id + ":busy:");
            return;
        }
        try {
            GmsBarcodeScannerOptions options = new GmsBarcodeScannerOptions.Builder()
                    .setBarcodeFormats(Barcode.FORMAT_EAN_8, Barcode.FORMAT_EAN_13, Barcode.FORMAT_UPC_A)
                    .build();
            GmsBarcodeScanner scanner = GmsBarcodeScanning.getClient(context, options);
            checkingModules = request;
            // Check only. This flow never requests a Play services module installation.
            ModuleInstall.getClient(context).areModulesAvailable(scanner)
                    .addOnSuccessListener(result -> ui.post(() -> modulesChecked(
                            request, scanner, result.areModulesAvailable())))
                    .addOnCanceledListener(() -> ui.post(() -> modulesChecked(request, scanner, false)))
                    .addOnFailureListener(error -> ui.post(() -> modulesChecked(request, scanner, false)));
            ui.postDelayed(() -> modulesChecked(request, scanner, false), 5000);
        } catch (RuntimeException error) {
            if (checkingModules == request) checkingModules = null;
            finish(request, "unavailable", "");
        }
    }

    private void modulesChecked(AndroidIntakeBarcodeSession.Request request,
                                GmsBarcodeScanner scanner, boolean available) {
        if (checkingModules != request) return;
        checkingModules = null;
        if (!available) {
            finish(request, "unavailable", "");
            return;
        }
        if (!resumed || view == null || !session.isCurrent(request, view.getUrl())) {
            finish(request, "cancelled", "");
            return;
        }
        try {
            scanner.startScan()
                    .addOnSuccessListener(barcode -> ui.post(() -> finishScan(request, barcode)))
                    .addOnCanceledListener(() -> ui.post(() -> finish(request, "cancelled", "")))
                    .addOnFailureListener(error -> ui.post(() -> finish(request, "error", "")));
        } catch (RuntimeException error) {
            finish(request, "error", "");
        }
    }

    private void finishScan(AndroidIntakeBarcodeSession.Request request, Barcode barcode) {
        if (barcode == null) {
            finish(request, "error", "");
            return;
        }
        int digits;
        switch (barcode.getFormat()) {
            case Barcode.FORMAT_EAN_8: digits = 8; break;
            case Barcode.FORMAT_EAN_13: digits = 13; break;
            case Barcode.FORMAT_UPC_A: digits = 12; break;
            default: digits = 0;
        }
        String value = AndroidIntakeBarcodeSession.checkedValue(barcode.getRawValue(), digits);
        finish(request, value.isEmpty() ? "unsupported" : "ok", value);
    }

    private void finish(AndroidIntakeBarcodeSession.Request request, String state, String value) {
        if (!session.finish(request, view == null ? null : view.getUrl()) || port == null) return;
        ready = request;
        readyMessage = request.id + ":" + state + ":" + value;
        deliver();
    }

    private void send(String message) {
        try {
            port.postMessage(new WebMessage(message));
        } catch (RuntimeException error) {
            closePort();
        }
    }

    private void deliver() {
        if (!resumed || ready == null) return;
        AndroidIntakeBarcodeSession.Request result = ready;
        String message = readyMessage;
        ready = null;
        readyMessage = null;
        if (port != null && result.url.equals(view.getUrl())) send(message);
    }

    void setResumed(boolean value) {
        resumed = value;
        deliver();  // The scanner Activity normally pauses the WebView until return.
    }

    void close() {
        closed = true;
        closePort();
    }

    private void invalidatePending() {
        session.invalidate();
        if (checkingModules != null) {
            session.finish(checkingModules, null);
            checkingModules = null;
        }
        ready = null;
        readyMessage = null;
    }

    private void closePort() {
        invalidatePending();
        if (port != null) closeUntransferred(port);
        port = null;
        view = null;
        pageUrl = null;
    }

    private static void closeUntransferred(WebMessagePort channel) {
        try {
            channel.close();
        } catch (IllegalStateException ignored) {
            // The endpoint was already transferred or disposed by the WebView.
        }
    }
}

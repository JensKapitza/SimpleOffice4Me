package de.simpleoffice4me.android;

import java.net.URI;

/** Single ordinary Google Code Scanner request, independent of the legacy UI. All calls are on the UI thread. */
final class AndroidIntakeBarcodeSession {
    static final String ORIGIN = "http://127.0.0.1:8765";
    private static final String PATH = "/inventory/capture/intake";
    static final String OFFER = "simpleoffice:intake-barcode/v1";

    static final class Request {
        final String id;
        final String url;

        Request(String id, String url) {
            this.id = id;
            this.url = url;
        }
    }

    private Request active;
    private boolean deliver;

    static boolean isPage(String value) {
        if (value == null) return false;
        try {
            URI url = URI.create(value);
            return "http".equals(url.getScheme()) && "127.0.0.1:8765".equals(url.getRawAuthority())
                    && PATH.equals(url.getRawPath()) && url.getRawQuery() == null;
        } catch (IllegalArgumentException error) {
            return false;
        }
    }

    static String requestId(String message) {
        return messageId(message, "scan:");
    }

    static String cancelId(String message) {
        return messageId(message, "cancel:");
    }

    private static String messageId(String message, String action) {
        if (message == null || message.length() != action.length() + 32 || !message.startsWith(action)) return "";
        String id = message.substring(action.length());
        return id.matches("[0-9a-f]{32}") ? id : "";
    }

    Request begin(String message, String url) {
        String id = requestId(message);
        if (active != null || id.isEmpty() || !isPage(url)) return null;
        active = new Request(id, url);
        deliver = true;
        return active;
    }

    void invalidate() {
        // The launcher is still in flight; do not launch a second native task.
        deliver = false;
    }

    void cancel(String id) {
        if (active != null && active.id.equals(id)) invalidate();
    }

    boolean isCurrent(Request request, String currentUrl) {
        return active == request && deliver && request.url.equals(currentUrl);
    }

    boolean finish(Request request, String currentUrl) {
        if (active != request) return false;
        boolean allowed = isCurrent(request, currentUrl);
        active = null;
        deliver = false;
        return allowed;
    }

    static String checkedValue(String value, int digits) {
        if (value == null || (digits != 8 && digits != 12 && digits != 13) || value.length() != digits
                || !value.matches("[0-9]+") || value.matches("0+")) return "";
        int sum = value.charAt(digits - 1) - '0';
        for (int i = digits - 2, weight = 3; i >= 0; i--, weight = 4 - weight) {
            sum += (value.charAt(i) - '0') * weight;
        }
        return sum % 10 == 0 ? value : "";
    }
}

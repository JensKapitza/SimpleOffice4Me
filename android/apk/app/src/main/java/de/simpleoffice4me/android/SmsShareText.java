package de.simpleoffice4me.android;

import java.nio.charset.StandardCharsets;

/** One foreground, in-memory draft, never a stored or automatically submitted SMS. */
final class SmsShareText {
    static final String PATH = "/communication/sms";
    static final String URL = "http://127.0.0.1:8765" + PATH;
    private static final int MAX_BYTES = 16000;
    private static final char[] HEX = "0123456789abcdef".toCharArray();

    private SmsShareText() {}

    static String accept(String mime, CharSequence value) {
        if (!"text/plain".equalsIgnoreCase(mime) || value == null || value.length() > MAX_BYTES) return "";
        String text = value.toString();
        if (text.trim().isEmpty() || !StandardCharsets.UTF_8.newEncoder().canEncode(text)
                || text.getBytes(StandardCharsets.UTF_8).length > MAX_BYTES) return "";
        return text;
    }

    static String fillScript(String text) {
        StringBuilder escaped = new StringBuilder(text.length() * 6);
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            escaped.append("\\u").append(HEX[ch >>> 12]).append(HEX[(ch >>> 8) & 15])
                    .append(HEX[(ch >>> 4) & 15]).append(HEX[ch & 15]);
        }
        // Check the document in the renderer too: navigation can happen after Java's URL check.
        return "(function(text){if(location.origin!=='http://127.0.0.1:8765'||location.pathname!=='"
                + PATH + "')return false;const box=document.querySelector('textarea[data-android-sms-share]');"
                + "if(!box||box.value)return false;box.value=text;box.focus();"
                + "return true;})(\"" + escaped + "\");";
    }
}

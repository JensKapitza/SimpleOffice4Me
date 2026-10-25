package de.simpleoffice4me.android;

import java.nio.charset.StandardCharsets;
import java.text.Normalizer;
import java.util.Locale;
import java.util.Set;

/** Allocates portable, collision-free destination names for selected SAF roots. */
final class FolderImportRootNames {
    private FolderImportRootNames() {}

    static String reserve(String requestedName, String sourceIdentity, Set<String> reservedNames) {
        if (reservedNames.add(collisionKey(requestedName))) return requestedName;
        String identityPrefix = sourceIdentity.substring(0, 8);
        for (int duplicate = 1; ; duplicate++) {
            String suffix = " (" + identityPrefix + (duplicate == 1 ? "" : "-" + duplicate) + ")";
            String candidate = truncateUtf8(requestedName, 255 - suffix.length()) + suffix;
            if (reservedNames.add(collisionKey(candidate))) return candidate;
        }
    }

    private static String collisionKey(String value) {
        return Normalizer.normalize(
                value.toUpperCase(Locale.ROOT).toLowerCase(Locale.ROOT).replace("\u00df", "ss"),
                Normalizer.Form.NFC);
    }

    private static String truncateUtf8(String value, int maxBytes) {
        StringBuilder truncated = new StringBuilder();
        int usedBytes = 0;
        for (int offset = 0; offset < value.length();) {
            int codePoint = value.codePointAt(offset);
            String part = new String(Character.toChars(codePoint));
            int partBytes = part.getBytes(StandardCharsets.UTF_8).length;
            if (usedBytes + partBytes > maxBytes) break;
            truncated.append(part);
            usedBytes += partBytes;
            offset += Character.charCount(codePoint);
        }
        return truncated.toString();
    }
}

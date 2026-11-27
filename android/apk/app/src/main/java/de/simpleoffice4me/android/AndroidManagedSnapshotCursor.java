package de.simpleoffice4me.android;

import java.io.IOException;
import java.util.regex.Pattern;

/** Validate the whole read-only pagination contract before any Android write. */
final class AndroidManagedSnapshotCursor {
    static final int PAGE_SIZE = 25;
    static final int MAX_ROWS = 1000;
    private static final Pattern HEX = Pattern.compile("[a-f0-9]{64}");
    private int offset;
    private String principal;
    private String revision = "";
    private boolean finished;

    int offset() { return offset; }
    String revision() { return revision; }
    String principal() { return principal; }

    static boolean approvalMatches(String principal, String revision, long approvedAt, long now,
                                   String currentPrincipal, String currentRevision) {
        return principal != null && revision != null && now >= approvedAt && now - approvedAt <= 120_000
                && principal.equals(currentPrincipal) && revision.equals(currentRevision);
    }

    boolean accept(String receivedPrincipal, String receivedRevision, int size, Integer next) throws IOException {
        if (finished || !HEX.matcher(receivedPrincipal).matches() || !HEX.matcher(receivedRevision).matches()
                || (principal != null && (!principal.equals(receivedPrincipal) || !revision.equals(receivedRevision)))
                || size < 0 || size > PAGE_SIZE || offset + size > MAX_ROWS
                || (offset > 0 && size == 0)
                || (next != null && (size != PAGE_SIZE || next != offset + size || next >= MAX_ROWS))) {
            throw new IOException("Inconsistent managed export page");
        }
        principal = receivedPrincipal;
        revision = receivedRevision;
        finished = next == null;
        if (!finished) offset = next;
        return finished;
    }
}

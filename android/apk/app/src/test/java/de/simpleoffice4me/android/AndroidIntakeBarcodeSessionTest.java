package de.simpleoffice4me.android;

import static org.junit.Assert.*;

import org.junit.Test;

public class AndroidIntakeBarcodeSessionTest {
    private static final String PAGE = AndroidIntakeBarcodeSession.ORIGIN + "/inventory/capture/intake";
    private static final String FIRST = "scan:0123456789abcdef0123456789abcdef";
    private static final String NEXT = "scan:abcdef0123456789abcdef0123456789";

    @Test
    public void onlyTheBundledIntakeRouteQualifies() {
        assertTrue(AndroidIntakeBarcodeSession.isPage(PAGE));
        assertTrue(AndroidIntakeBarcodeSession.isPage(PAGE + "#draft-page"));
        assertFalse(AndroidIntakeBarcodeSession.isPage("http://localhost:8765/inventory/capture/intake"));
        assertFalse(AndroidIntakeBarcodeSession.isPage("http://127.0.0.1:8766/inventory/capture/intake"));
        assertFalse(AndroidIntakeBarcodeSession.isPage(PAGE + "/existing"));
        assertFalse(AndroidIntakeBarcodeSession.isPage(PAGE + "?view=review"));
        assertFalse(AndroidIntakeBarcodeSession.isPage(null));
        assertEquals("", AndroidIntakeBarcodeSession.requestId("camera denied"));
    }

    @Test
    public void lifecycleInvalidatesOldResultsButKeepsTheNativeScannerSingleFlight() {
        AndroidIntakeBarcodeSession session = new AndroidIntakeBarcodeSession();
        AndroidIntakeBarcodeSession.Request first = session.begin(FIRST, PAGE);
        assertNotNull(first);
        assertNull(session.begin(NEXT, PAGE));
        session.invalidate();  // Main-frame reload, history change, or teardown.
        assertNull(session.begin(NEXT, PAGE));
        assertFalse(session.finish(first, PAGE));
        AndroidIntakeBarcodeSession.Request next = session.begin(NEXT, PAGE);
        assertNotNull(next);
        assertFalse(session.finish(first, PAGE));
        assertTrue(session.finish(next, PAGE));
        assertFalse(session.finish(next, PAGE));
    }

    @Test
    public void resultCannotFollowTheUserToADifferentRouteOrAnchor() {
        AndroidIntakeBarcodeSession session = new AndroidIntakeBarcodeSession();
        AndroidIntakeBarcodeSession.Request request = session.begin(FIRST, PAGE);
        assertFalse(session.finish(request, PAGE + "#draft-new"));
        assertNotNull(session.begin(NEXT, PAGE));
    }

    @Test
    public void onlyTheMatchingCancellationInvalidatesThePendingRequest() {
        AndroidIntakeBarcodeSession session = new AndroidIntakeBarcodeSession();
        AndroidIntakeBarcodeSession.Request request = session.begin(FIRST, PAGE);
        assertEquals("", AndroidIntakeBarcodeSession.cancelId(FIRST));
        assertEquals(request.id, AndroidIntakeBarcodeSession.cancelId("cancel:" + request.id));
        session.cancel(NEXT.substring(5));
        assertTrue(session.isCurrent(request, PAGE));
        session.cancel(request.id);
        assertFalse(session.isCurrent(request, PAGE));
        assertFalse(session.finish(request, PAGE));
    }

    @Test
    public void validatesKnownSupportedChecksumsAndSymbologyLengths() {
        assertEquals("9780140328721", AndroidIntakeBarcodeSession.checkedValue("9780140328721", 13));
        assertEquals("4006381333931", AndroidIntakeBarcodeSession.checkedValue("4006381333931", 13));
        assertEquals("96385074", AndroidIntakeBarcodeSession.checkedValue("96385074", 8));
        assertEquals("036000291452", AndroidIntakeBarcodeSession.checkedValue("036000291452", 12));
        assertEquals("", AndroidIntakeBarcodeSession.checkedValue("9780140328722", 13));
        assertEquals("", AndroidIntakeBarcodeSession.checkedValue("9780140328721", 12));
        assertEquals("", AndroidIntakeBarcodeSession.checkedValue("00000000", 8));
        assertEquals("", AndroidIntakeBarcodeSession.checkedValue("ISBN 0140328726", 10));
    }
}

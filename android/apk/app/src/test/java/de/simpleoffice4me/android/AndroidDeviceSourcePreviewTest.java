package de.simpleoffice4me.android;

import org.junit.Test;

import static org.junit.Assert.*;

public class AndroidDeviceSourcePreviewTest {
    @Test
    public void pageScopeAcceptsOnlyTheCanonicalLocalOverview() {
        assertTrue(AndroidDeviceSourcePreview.isPage(AndroidDeviceSourcePreview.PAGE));
        assertFalse(AndroidDeviceSourcePreview.isPage(AndroidDeviceSourcePreview.PAGE + "authorize/contacts"));
        assertFalse(AndroidDeviceSourcePreview.isPage(AndroidDeviceSourcePreview.PAGE + "?source=contacts"));
        assertFalse(AndroidDeviceSourcePreview.isPage("http://localhost:8765/android/device-sources/"));
        assertFalse(AndroidDeviceSourcePreview.isPage("https://example.invalid/android/device-sources/"));
        assertFalse(AndroidDeviceSourcePreview.isPage(null));
    }

    @Test
    public void sourceAndCsrfAcceptOnlyExpectedSymbols() {
        assertSame(AndroidDeviceSourcePreview.Source.CALENDAR, AndroidDeviceSourcePreview.Source.parse("calendar"));
        assertSame(AndroidDeviceSourcePreview.Source.CONTACTS, AndroidDeviceSourcePreview.Source.parse("contacts"));
        assertNull(AndroidDeviceSourcePreview.Source.parse("../contacts"));
        assertNull(AndroidDeviceSourcePreview.Source.parse(null));
        assertTrue(AndroidDeviceSourcePreview.isCsrf("12abcdef3456789_12abcdef3456789-"));
        assertFalse(AndroidDeviceSourcePreview.isCsrf("12abcdef3456789_12abcdef3456789\n"));
        assertFalse(AndroidDeviceSourcePreview.isCsrf("short"));
    }

    @Test
    public void nativeTextCannotInjectAnotherLineOrDirectionalOverride() {
        assertEquals("Kontakt Frage Name", AndroidDeviceSourcePreview.safeText("Kontakt\nFrage\u202eName", "unnamed"));
        assertEquals("unnamed", AndroidDeviceSourcePreview.safeText("\t\n", "unnamed"));
        assertEquals(100, AndroidDeviceSourcePreview.safeText("x".repeat(120), "unnamed").length());
    }
}

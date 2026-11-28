package de.simpleoffice4me.android;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

public class AndroidSmsDeviceImportTest {
    @Test
    public void countsUtf8BytesAndRejectsBrokenUnicode() {
        assertEquals(0, AndroidSmsDeviceImport.utf8Length("", 0));
        assertEquals(6, AndroidSmsDeviceImport.utf8Length("ü\uD83D\uDE00", 6));
        assertEquals(-1, AndroidSmsDeviceImport.utf8Length("ü\uD83D\uDE00", 5));
        assertEquals(-1, AndroidSmsDeviceImport.utf8Length("bad\uD800", 20));
        assertEquals(-1, AndroidSmsDeviceImport.utf8Length("bad\uDC00", 20));
    }

    @Test
    public void neverReadsWithoutAnActiveSingleUseNativeLease() {
        assertEquals("", AndroidSmsDeviceImport.begin());
        assertEquals(false, AndroidSmsDeviceImport.active("0".repeat(32)));
        assertEquals("{\"state\":\"unauthorized\"}",
                AndroidSmsDeviceImport.read("0".repeat(32), 0, Long.MAX_VALUE));
    }
}

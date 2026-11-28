package de.simpleoffice4me.android;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class AndroidPhotoOcrTest {
    @Test
    public void usesExactlyOneGrayscaleBytePerPixel() {
        assertTrue(AndroidPhotoOcr.validRaster(new byte[24], 6, 4));
        assertFalse(AndroidPhotoOcr.validRaster(null, 6, 4));
        assertFalse(AndroidPhotoOcr.validRaster(new byte[23], 6, 4));
        assertFalse(AndroidPhotoOcr.validRaster(new byte[25], 6, 4));
        assertFalse(AndroidPhotoOcr.validRaster(new byte[0], 0, 0));
        assertFalse(AndroidPhotoOcr.validRaster(new byte[2801], 2801, 1));
        assertFalse(AndroidPhotoOcr.validRaster(new byte[1], Integer.MAX_VALUE, Integer.MAX_VALUE));
    }
}

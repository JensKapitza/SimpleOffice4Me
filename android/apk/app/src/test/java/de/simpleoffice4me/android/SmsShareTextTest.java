package de.simpleoffice4me.android;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class SmsShareTextTest {
    @Test
    public void onlyBoundedPlainTextIsAccepted() {
        assertEquals("", SmsShareText.accept("text/html", "not plain"));
        assertEquals("", SmsShareText.accept("text/plain", null));
        assertEquals("", SmsShareText.accept("text/plain", " \n "));
        assertEquals("", SmsShareText.accept("text/plain", "\uD800"));
        assertEquals("", SmsShareText.accept("text/plain", "ä".repeat(8001)));
        assertEquals("ä".repeat(8000), SmsShareText.accept("text/plain", "ä".repeat(8000)));
        assertEquals("hello\n😀", SmsShareText.accept("TEXT/PLAIN", "hello\n😀"));
    }

    @Test
    public void rendererChecksExactOriginPathAndEmptyDraftAndUsesOnlyText() {
        String script = SmsShareText.fillScript("\" ); alert('synthetic');\n</script> 😀");
        assertTrue(script.contains("location.origin!=='http://127.0.0.1:8765'"));
        assertTrue(script.contains("location.pathname!=='/communication/sms'"));
        assertTrue(script.contains("if(!box||box.value)return false;box.value=text"));
        assertTrue(script.contains("\\ud83d\\ude00"));
        assertFalse(script.contains("</script>"));
        assertFalse(script.contains("alert("));
        assertFalse(script.contains(".submit"));
        assertFalse(script.contains("fetch("));
    }
}

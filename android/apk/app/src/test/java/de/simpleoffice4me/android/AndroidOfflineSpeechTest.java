package de.simpleoffice4me.android;

import android.speech.tts.TextToSpeech;

import org.junit.Test;

import java.util.Collections;
import java.util.Locale;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;

public class AndroidOfflineSpeechTest {
    @Test public void refusesNetworkNotInstalledAndUnknownVoices() {
        assertFalse(AndroidOfflineSpeech.permitsVoice(true, null));
        assertFalse(AndroidOfflineSpeech.permitsVoice(false, null));
        assertFalse(AndroidOfflineSpeech.permitsVoice(false, Collections.singleton(
                TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED)));
        assertTrue(AndroidOfflineSpeech.permitsVoice(false, Collections.emptySet()));
    }

    @Test public void selectedVoiceKeysAreOpaqueStableAndEngineScoped() {
        String first = AndroidOfflineSpeech.voiceId("engine.one", "local name");
        assertTrue(first.matches("[0-9a-f]{64}"));
        assertEquals(first, AndroidOfflineSpeech.voiceId("engine.one", "local name"));
        assertNotEquals(first, AndroidOfflineSpeech.voiceId("engine.two", "local name"));
        assertNotEquals(first, AndroidOfflineSpeech.voiceId("engine.one", "other"));
    }

    @Test public void ratePitchAndLanguageAreStrictBeforeEngineUse() {
        assertTrue(AndroidOfflineSpeech.validOptions(Locale.GERMANY, "", 1.0, 1.0));
        assertTrue(AndroidOfflineSpeech.validOptions(Locale.UK, "a".repeat(64), 0.5, 2.0));
        assertFalse(AndroidOfflineSpeech.validOptions(Locale.FRANCE, "", 1.0, 1.0));
        assertFalse(AndroidOfflineSpeech.validOptions(Locale.GERMANY, "vendor raw id", 1.0, 1.0));
        assertFalse(AndroidOfflineSpeech.validOptions(Locale.GERMANY, "", Double.NaN, 1.0));
        assertFalse(AndroidOfflineSpeech.validOptions(Locale.UK, "", 1.0, Double.POSITIVE_INFINITY));
        assertFalse(AndroidOfflineSpeech.validOptions(Locale.GERMANY, "", 0.49, 1.0));
        assertFalse(AndroidOfflineSpeech.validOptions(Locale.GERMANY, "", 1.0, 2.01));
    }
}

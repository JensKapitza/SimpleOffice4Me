package de.simpleoffice4me.android;

import android.speech.tts.TextToSpeech;

import org.junit.Test;

import java.util.Collections;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class AndroidOfflineSpeechTest {
    @Test public void refusesNetworkAndNotInstalledVoices() {
        assertFalse(AndroidOfflineSpeech.permitsVoice(true, null));
        assertFalse(AndroidOfflineSpeech.permitsVoice(false, Collections.singleton(
                TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED)));
        assertTrue(AndroidOfflineSpeech.permitsVoice(false, Collections.emptySet()));
    }
}

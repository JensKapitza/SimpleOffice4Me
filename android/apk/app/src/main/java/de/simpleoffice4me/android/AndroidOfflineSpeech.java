package de.simpleoffice4me.android;

import android.os.Bundle;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.speech.tts.Voice;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.IOException;
import java.util.Comparator;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.locks.ReentrantLock;

/** Internal Python/Java bridge. Not exposed to the WebView JavaScript interface. */
public final class AndroidOfflineSpeech {
    private static final ReentrantLock SLOT = new ReentrantLock();

    private AndroidOfflineSpeech() {}

    public static String cacheDirectory() {
        if (Python.getPlatform() instanceof AndroidPlatform platform) {
            return platform.getApplication().getCacheDir().getAbsolutePath();
        }
        throw new IllegalStateException("Android application context is unavailable");
    }

    public static boolean render(String text, String language, String outputPath) throws IOException {
        if (text == null || text.trim().isEmpty() || text.length() > TextToSpeech.getMaxSpeechInputLength()
                || !SLOT.tryLock()) {
            return false;
        }
        TextToSpeech engine = null;
        try {
            if (!(Python.getPlatform() instanceof AndroidPlatform platform)) return false;
            File appCache = platform.getApplication().getCacheDir().getCanonicalFile();
            File output = new File(outputPath).getCanonicalFile();
            if (!output.getPath().startsWith(appCache.getPath() + File.separator) || output.exists()) return false;

            CountDownLatch initialized = new CountDownLatch(1);
            AtomicInteger initStatus = new AtomicInteger(TextToSpeech.ERROR);
            engine = new TextToSpeech(platform.getApplication(), status -> {
                initStatus.set(status);
                initialized.countDown();
            });
            if (!initialized.await(4, TimeUnit.SECONDS) || initStatus.get() != TextToSpeech.SUCCESS) return false;
            Locale locale = Locale.forLanguageTag(language);
            Voice voice = chooseVoice(engine.getVoices(), locale);
            if (voice == null || engine.setVoice(voice) != TextToSpeech.SUCCESS) return false;
            Voice active = engine.getVoice();
            if (active == null || !voice.getName().equals(active.getName()) || !isInstalledOffline(active)) return false;

            String id = UUID.randomUUID().toString();
            CountDownLatch finished = new CountDownLatch(1);
            AtomicBoolean success = new AtomicBoolean();
            if (engine.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                @Override public void onStart(String utteranceId) {}
                @Override public void onDone(String utteranceId) {
                    if (!id.equals(utteranceId)) return;
                    success.set(true);
                    finished.countDown();
                }
                @Override @Deprecated public void onError(String utteranceId) {
                    if (id.equals(utteranceId)) finished.countDown();
                }
                @Override public void onStop(String utteranceId, boolean interrupted) {
                    if (id.equals(utteranceId)) finished.countDown();
                }
            }) != TextToSpeech.SUCCESS) return false;
            if (engine.synthesizeToFile(text, new Bundle(), output, id) != TextToSpeech.SUCCESS) return false;
            return finished.await(20, TimeUnit.SECONDS) && success.get();
        } catch (InterruptedException exc) {
            Thread.currentThread().interrupt();
            return false;
        } finally {
            try {
                if (engine != null) {
                    try {
                        engine.stop();
                    } finally {
                        engine.shutdown();
                    }
                }
            } finally {
                SLOT.unlock();
            }
        }
    }

    static Voice chooseVoice(Set<Voice> available, Locale locale) {
        if (available == null || locale.getLanguage().isEmpty()) return null;
        return available.stream()
                .filter(AndroidOfflineSpeech::isInstalledOffline)
                .filter(voice -> locale.getLanguage().equals(voice.getLocale().getLanguage()))
                .max(Comparator.comparing((Voice voice) -> locale.equals(voice.getLocale()))
                        .thenComparingInt(Voice::getQuality)
                        .thenComparing(Voice::getName))
                .orElse(null);
    }

    private static boolean isInstalledOffline(Voice voice) {
        return permitsVoice(voice.isNetworkConnectionRequired(), voice.getFeatures());
    }

    static boolean permitsVoice(boolean networkRequired, Set<String> features) {
        return !networkRequired && (features == null
                || !features.contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED));
    }
}

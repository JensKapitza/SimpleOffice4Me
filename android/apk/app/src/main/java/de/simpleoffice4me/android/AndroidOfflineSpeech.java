package de.simpleoffice4me.android;

import android.content.Context;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.speech.tts.Voice;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.Semaphore;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/** App-local Python/Java bridge, never exposed to WebView JavaScript. */
public final class AndroidOfflineSpeech {
    private static final Semaphore ADMISSION = new Semaphore(2);
    private static final AtomicLong REQUESTS = new AtomicLong();
    private static final int MAX_VOICES = 96;
    private static final long INIT_TIMEOUT_MS = 4000;

    private static final class MainThread {
        static final Handler HANDLER = new Handler(Looper.getMainLooper());
    }

    private static final class Choice {
        final String id;
        final Voice voice;

        Choice(String id, Voice voice) {
            this.id = id;
            this.voice = voice;
        }
    }

    private static final class VoiceSnapshot {
        final String status;
        final String json;
        final List<Choice> choices;
        final Voice systemDefault;

        VoiceSnapshot(String status, String json, List<Choice> choices, Voice systemDefault) {
            this.status = status;
            this.json = json;
            this.choices = choices;
            this.systemDefault = systemDefault;
        }

        static VoiceSnapshot state(String status) {
            return new VoiceSnapshot(status, "[]", java.util.Collections.emptyList(), null);
        }
    }

    private static final class Render {
        final String id = UUID.randomUUID().toString();
        final CountDownLatch finished = new CountDownLatch(1);
        final AtomicBoolean abandoned = new AtomicBoolean();
        final long epoch;
        final long serial;
        final String text;
        final Locale locale;
        final File output;
        final String voice;
        final float rate;
        final float pitch;
        boolean success;

        Render(long epoch, long serial, String text, Locale locale, File output, String voice, float rate, float pitch) {
            this.epoch = epoch;
            this.serial = serial;
            this.text = text;
            this.locale = locale;
            this.output = output;
            this.voice = voice;
            this.rate = rate;
            this.pitch = pitch;
        }

        void complete(boolean success) {
            this.success = success;
            finished.countDown();
        }
    }

    // Lifecycle and all engine operations are serialized on the main looper.
    // Python request threads use only the immutable snapshot and a bounded wait.
    private static Object activityOwner;
    private static Context engineContext;
    private static TextToSpeech engine;
    private static Render active;
    private static volatile long epoch;
    private static volatile long latestStarted;
    private static volatile boolean foreground;
    private static volatile VoiceSnapshot snapshot = VoiceSnapshot.state("unavailable");

    private AndroidOfflineSpeech() {}

    private static Context context() {
        if (Python.getPlatform() instanceof AndroidPlatform platform) {
            return platform.getApplication();
        }
        throw new IllegalStateException("Android application context is unavailable");
    }

    public static String cacheDirectory() {
        return context().getCacheDir().getAbsolutePath();
    }

    public static String voicesJson() {
        return snapshot.json;
    }

    public static String status() {
        return snapshot.status;
    }

    /** Explicit retry or refresh, with no document or utterance input. */
    public static boolean refresh() {
        if (!foreground) return false;
        long requested = epoch;
        return MainThread.HANDLER.post(() -> {
            if (foreground && requested == epoch && active == null) initialize();
        });
    }

    static void onResume(Object owner, Context applicationContext) {
        if (activityOwner == owner && foreground) return;
        activityOwner = owner;
        engineContext = applicationContext;
        foreground = true;
        initialize();
    }

    static void onPause(Object owner) {
        if (activityOwner != owner || !foreground) return;
        foreground = false;
        epoch++;
        releaseEngine();
        snapshot = VoiceSnapshot.state("unavailable");
    }

    static void onStop(Object owner) {
        onPause(owner);
        if (activityOwner == owner) {
            activityOwner = null;
            engineContext = null;
        }
    }

    private static void initialize() {
        long check = ++epoch;
        releaseEngine();
        snapshot = VoiceSnapshot.state("loading");
        try {
            engine = new TextToSpeech(engineContext,
                    state -> MainThread.HANDLER.post(() -> initialized(check, state)));
            MainThread.HANDLER.postDelayed(() -> {
                if (check == epoch && "loading".equals(snapshot.status)) initializationFailed();
            }, INIT_TIMEOUT_MS);
        } catch (RuntimeException error) {
            initializationFailed();
        }
    }

    private static void initialized(long check, int status) {
        if (check != epoch || !foreground || engine == null || !"loading".equals(snapshot.status)) return;
        if (status != TextToSpeech.SUCCESS) {
            initializationFailed();
            return;
        }
        try {
            snapshot = collectVoices(engine);
        } catch (JSONException | RuntimeException error) {
            initializationFailed();
        }
    }

    private static void initializationFailed() {
        snapshot = VoiceSnapshot.state("unavailable");
        releaseEngine();
    }

    private static VoiceSnapshot collectVoices(TextToSpeech current) throws JSONException {
        Set<Voice> available = current.getVoices();
        if (available == null) return VoiceSnapshot.state("unavailable");
        List<Choice> choices = new ArrayList<>();
        JSONArray rows = new JSONArray();
        String engineName = current.getDefaultEngine();
        List<Voice> sorted = new ArrayList<>();
        for (String language : new String[]{"de", "en"}) {
            available.stream().filter(AndroidOfflineSpeech::safeVoice)
                    .filter(voice -> language.equals(voice.getLocale().getLanguage()))
                    .sorted(Comparator.comparing((Voice voice) -> voice.getLocale().toLanguageTag())
                            .thenComparing(Voice::getName))
                    .limit(MAX_VOICES / 2).forEachOrdered(sorted::add);
        }
        for (Voice voice : sorted) {
            String id = voiceId(engineName, voice.getName() + '\0' + voice.getLocale().toLanguageTag());
            choices.add(new Choice(id, voice));
            String name = voice.getLocale().getDisplayName(Locale.getDefault()) + " · " + voice.getName();
            rows.put(new JSONObject().put("id", id).put("language", voice.getLocale().toLanguageTag())
                    .put("label", name.length() <= 180 ? name : name.substring(0, 180)));
        }
        return new VoiceSnapshot(choices.isEmpty() ? "unavailable" : "ready", rows.toString(),
                java.util.Collections.unmodifiableList(choices), current.getDefaultVoice());
    }

    static String voiceId(String engineName, String voiceName) {
        try {
            MessageDigest hash = MessageDigest.getInstance("SHA-256");
            byte[] digest = hash.digest(((engineName == null ? "" : engineName) + '\0' + voiceName)
                    .getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder(64);
            for (byte value : digest) {
                result.append(Character.forDigit((value >>> 4) & 15, 16));
                result.append(Character.forDigit(value & 15, 16));
            }
            return result.toString();
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("Android SHA-256 is unavailable", error);
        }
    }

    public static boolean render(String text, String language, String outputPath) throws IOException {
        return render(text, language, outputPath, "", 1f, 1f);
    }

    public static boolean render(String text, String language, String outputPath, String voice,
                                 double rate, double pitch) throws IOException {
        String selected = voice == null ? "" : voice;
        Locale locale = Locale.forLanguageTag(language == null ? "" : language);
        if (!foreground || !validOptions(locale, selected, rate, pitch) || text == null || text.trim().isEmpty()
                || text.length() > TextToSpeech.getMaxSpeechInputLength()
                || outputPath == null || !ADMISSION.tryAcquire()) return false;
        Render job = null;
        try {
            long requested = epoch;
            long serial = REQUESTS.incrementAndGet();
            File appCache = context().getCacheDir().getCanonicalFile();
            File output = new File(outputPath).getCanonicalFile();
            if (!output.getPath().startsWith(appCache.getPath() + File.separator) || output.exists()) return false;
            job = new Render(requested, serial, text, locale, output, selected, (float) rate, (float) pitch);
            Render queued = job;
            if (!MainThread.HANDLER.post(() -> begin(queued))) return false;
            return job.finished.await(20, TimeUnit.SECONDS) && job.success && foreground
                    && epoch == requested && latestStarted == serial;
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            return false;
        } finally {
            if (job != null && job.finished.getCount() != 0) {
                job.abandoned.set(true);
                Render obsolete = job;
                MainThread.HANDLER.post(() -> cancel(obsolete));
            }
            ADMISSION.release();
        }
    }

    static boolean validOptions(Locale locale, String voice, double rate, double pitch) {
        return supported(locale) && (voice.isEmpty() || voice.matches("[0-9a-f]{64}"))
                && Double.isFinite(rate) && 0.5f <= rate && rate <= 2f
                && Double.isFinite(pitch) && 0.5f <= pitch && pitch <= 2f;
    }

    private static void begin(Render job) {
        if (job.abandoned.get() || !foreground || job.epoch != epoch || engine == null
                || job.serial < latestStarted || !"ready".equals(snapshot.status)) {
            job.complete(false);
            return;
        }
        try {
            Voice chosen = currentVoice(engine.getVoices(), select(snapshot, job));
            if (chosen == null) {
                job.complete(false);
                return;
            }
            if (active != null && !cancel(active)) {
                job.complete(false);
                return;
            }
            latestStarted = job.serial;
            if (engine.setVoice(chosen) != TextToSpeech.SUCCESS
                    || engine.setSpeechRate(job.rate) != TextToSpeech.SUCCESS
                    || engine.setPitch(job.pitch) != TextToSpeech.SUCCESS) {
                job.complete(false);
                return;
            }
            Voice installed = engine.getVoice();
            if (!safeVoice(installed) || !chosen.getName().equals(installed.getName())
                    || !chosen.getLocale().equals(installed.getLocale())) {
                job.complete(false);
                return;
            }
            active = job;
            String callbackId = job.id;
            long callbackEpoch = job.epoch;
            if (engine.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                @Override public void onStart(String utteranceId) {}
                @Override public void onDone(String utteranceId) { callback(callbackId, callbackEpoch, utteranceId, true); }
                @Override @Deprecated public void onError(String utteranceId) { callback(callbackId, callbackEpoch, utteranceId, false); }
                @Override public void onStop(String utteranceId, boolean interrupted) { callback(callbackId, callbackEpoch, utteranceId, false); }
            }) != TextToSpeech.SUCCESS
                    || engine.synthesizeToFile(job.text, new Bundle(), job.output, job.id) != TextToSpeech.SUCCESS) {
                cancel(job);
            }
        } catch (RuntimeException error) {
            if (active == job) cancel(job);
            else job.complete(false);
        }
    }

    private static void callback(String expectedId, long expectedEpoch, String utteranceId, boolean success) {
        // The reusable engine can retain its listener after completion.
        // Capture an opaque ID and epoch only, never a Render holding text.
        if (!expectedId.equals(utteranceId)) return;
        MainThread.HANDLER.post(() -> {
            Render job = active;
            if (job == null || job.epoch != expectedEpoch || !job.id.equals(expectedId)) return;
            active = null;
            job.complete(success && !job.abandoned.get() && foreground && job.epoch == epoch);
        });
    }

    private static boolean cancel(Render job) {
        if (active != job) return true;
        active = null;
        boolean stopped = engine == null || stopQuietly(engine);
        if (!stopped) initializationFailed(); // Do not reuse an unconfirmed engine after cancellation.
        job.complete(false);
        return stopped;
    }

    private static void releaseEngine() {
        TextToSpeech previous = engine;
        engine = null;
        Render pending = active;
        active = null;
        if (pending != null) pending.complete(false);
        if (previous != null) {
            stopQuietly(previous);
            try {
                previous.shutdown();
            } catch (RuntimeException ignored) {
                // Disconnected vendor engines have no further callback to consume.
            }
        }
    }

    private static boolean stopQuietly(TextToSpeech previous) {
        try {
            return previous.stop() == TextToSpeech.SUCCESS;
        } catch (RuntimeException ignored) {
            return false;
        }
    }

    private static Voice currentVoice(Set<Voice> voices, Voice expected) {
        // Query the installed metadata immediately before setVoice: Android may
        // download automatically if setVoice sees a voice marked notInstalled.
        if (voices == null || expected == null) return null;
        for (Voice voice : voices) {
            if (safeVoice(voice) && expected.getName().equals(voice.getName())
                    && expected.getLocale().equals(voice.getLocale())) return voice;
        }
        return null;
    }

    private static Voice select(VoiceSnapshot state, Render job) {
        if (!job.voice.isEmpty()) {
            for (Choice choice : state.choices) {
                if (choice.id.equals(job.voice) && sameLanguage(choice.voice, job.locale)) return choice.voice;
            }
            return null;  // No silently substituted language or voice.
        }
        Voice preferred = state.systemDefault;
        Voice best = null;
        for (Choice choice : state.choices) {
            Voice candidate = choice.voice;
            if (!sameLanguage(candidate, job.locale)) continue;
            if (preferred != null && sameLanguage(preferred, job.locale)
                    && candidate.getName().equals(preferred.getName())
                    && candidate.getLocale().equals(preferred.getLocale())) return candidate;
            if (best == null || preference(job.locale).compare(candidate, best) > 0) best = candidate;
        }
        return best;
    }

    private static Comparator<Voice> preference(Locale locale) {
        return Comparator.comparing((Voice voice) -> locale.equals(voice.getLocale()))
                .thenComparingInt(Voice::getQuality).thenComparing(Voice::getName);
    }

    private static boolean sameLanguage(Voice voice, Locale locale) {
        return safeVoice(voice) && locale.getLanguage().equals(voice.getLocale().getLanguage());
    }

    private static boolean supported(Locale locale) {
        return locale != null && ("de".equals(locale.getLanguage()) || "en".equals(locale.getLanguage()));
    }

    private static boolean safeVoice(Voice voice) {
        if (voice == null || !supported(voice.getLocale())
                || !permitsVoice(voice.isNetworkConnectionRequired(), voice.getFeatures())) return false;
        String name = voice.getName();
        return name != null && !name.isEmpty() && name.length() <= 128
                && name.codePoints().noneMatch(code -> Character.isISOControl(code)
                        || Character.getType(code) == Character.FORMAT);
    }

    static boolean permitsVoice(boolean networkRequired, Set<String> features) {
        return !networkRequired && features != null
                && !features.contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED);
    }
}

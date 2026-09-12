package de.simpleoffice4me.android;

import android.media.MediaCodecList;
import android.media.MediaFormat;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** Coordinates native Android sender/receiver sessions and safe UI status. */
final class AndroidAudioStreamer {
    private static final int MAX_TARGETS = 16;
    private final Object lock = new Object();
    private AndroidAudioSender sender;
    private AndroidAudioReceiver receiver;

    static boolean opusEncoderAvailable() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return false;
        try {
            MediaFormat format = MediaFormat.createAudioFormat(
                    MediaFormat.MIMETYPE_AUDIO_OPUS,
                    AndroidAudioSender.SAMPLE_RATE,
                    AndroidAudioSender.CHANNELS);
            format.setInteger(MediaFormat.KEY_BIT_RATE, 64000);
            return new MediaCodecList(MediaCodecList.ALL_CODECS).findEncoderForFormat(format) != null;
        } catch (RuntimeException error) {
            return false;
        }
    }

    String startSender(String destinationsJson, int bitrateKbps) {
        if (!opusEncoderAvailable()) return "unsupported";
        final List<AndroidAudioSender.Target> targets;
        try {
            targets = parseTargets(destinationsJson);
        } catch (Exception error) {
            return "invalid-target";
        }
        synchronized (lock) {
            stopSenderLocked();
            sender = new AndroidAudioSender(targets, bitrateKbps);
            sender.start();
        }
        return "ok";
    }

    String startReceiver(int port) {
        if (port < 1024 || port > 65535) return "invalid-port";
        synchronized (lock) {
            stopReceiverLocked();
            receiver = new AndroidAudioReceiver(port);
            receiver.start();
        }
        return "ok";
    }

    void stopSender() {
        synchronized (lock) {
            stopSenderLocked();
        }
    }

    void stopReceiver() {
        synchronized (lock) {
            stopReceiverLocked();
        }
    }

    void stopAll() {
        synchronized (lock) {
            stopSenderLocked();
            stopReceiverLocked();
        }
    }

    String statusJson() {
        synchronized (lock) {
            try {
                JSONObject result = new JSONObject();
                result.put("platform", "android");
                result.put("opus_encoder", opusEncoderAvailable());
                result.put("last_error", currentError());
                result.put("sender", sender != null && sender.isRunning()
                        ? new JSONObject()
                                .put("running", true)
                                .put("source", "Android Systemmikrofon")
                                .put("backend", "android")
                        : JSONObject.NULL);
                result.put("receiver", receiver != null && receiver.isRunning()
                        ? new JSONObject()
                                .put("running", true)
                                .put("port", receiver.port())
                                .put("speaker_devices", new JSONArray().put("Android Systemausgabe"))
                                .put("virtual_microphone", "")
                        : JSONObject.NULL);
                return result.toString();
            } catch (Exception error) {
                return "{\"platform\":\"android\",\"last_error\":\"Status konnte nicht gelesen werden.\"}";
            }
        }
    }

    private String currentError() {
        if (sender != null && !sender.error().isEmpty()) return sender.error();
        if (receiver != null && !receiver.error().isEmpty()) return receiver.error();
        return "";
    }

    private void stopSenderLocked() {
        if (sender != null) sender.stop();
        sender = null;
    }

    private void stopReceiverLocked() {
        if (receiver != null) receiver.stop();
        receiver = null;
    }

    private static List<AndroidAudioSender.Target> parseTargets(String json) throws Exception {
        JSONArray array = new JSONArray(json == null ? "[]" : json);
        if (array.length() < 1 || array.length() > MAX_TARGETS) {
            throw new IllegalArgumentException("targets");
        }
        List<AndroidAudioSender.Target> result = new ArrayList<>();
        for (int index = 0; index < array.length(); index++) {
            JSONObject item = array.getJSONObject(index);
            String host = item.optString("host", "").trim();
            int port = item.optInt("port", 5004);
            if (host.isEmpty() || host.length() > 253 || port < 1024 || port > 65535) {
                throw new IllegalArgumentException("target");
            }
            result.add(new AndroidAudioSender.Target(host, port));
        }
        return result;
    }
}

package de.simpleoffice4me.android;

import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.media.MediaFormat;
import android.media.MediaRecorder;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

/** Native low-latency RTP/Opus audio for the Android APK. */
final class AndroidAudioStreamer {
    private static final int SAMPLE_RATE = 48000;
    private static final int CHANNELS = 2;
    private static final int RTP_PAYLOAD_TYPE = 111;
    private static final int MAX_TARGETS = 16;
    private static final int FRAME_SAMPLES = 960; // 20 ms at 48 kHz
    private static final int MONO_FRAME_BYTES = FRAME_SAMPLES * 2;
    private static final int STEREO_FRAME_BYTES = FRAME_SAMPLES * CHANNELS * 2;

    private final Object lock = new Object();
    private final AtomicBoolean senderRunning = new AtomicBoolean(false);
    private final AtomicBoolean receiverRunning = new AtomicBoolean(false);
    private Thread senderThread;
    private Thread receiverThread;
    private DatagramSocket senderSocket;
    private DatagramSocket receiverSocket;
    private volatile String lastError = "";
    private volatile int receiverPort;

    static boolean opusEncoderAvailable() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return false;
        try {
            MediaFormat format = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, SAMPLE_RATE, CHANNELS);
            format.setInteger(MediaFormat.KEY_BIT_RATE, 64000);
            return new MediaCodecList(MediaCodecList.ALL_CODECS).findEncoderForFormat(format) != null;
        } catch (RuntimeException error) {
            return false;
        }
    }

    String startSender(String destinationsJson, int bitrateKbps) {
        if (!opusEncoderAvailable()) return "unsupported";
        final List<Target> targets;
        try {
            targets = parseTargets(destinationsJson);
        } catch (Exception error) {
            return "invalid-target";
        }
        int bitrate = Math.max(16, Math.min(bitrateKbps, 256));
        stopSender();
        lastError = "";
        senderRunning.set(true);
        senderThread = new Thread(() -> runSender(targets, bitrate), "simpleoffice-android-audio-send");
        senderThread.start();
        return "ok";
    }

    String startReceiver(int port) {
        if (port < 1024 || port > 65535) return "invalid-port";
        stopReceiver();
        lastError = "";
        receiverPort = port;
        receiverRunning.set(true);
        receiverThread = new Thread(() -> runReceiver(port), "simpleoffice-android-audio-receive");
        receiverThread.start();
        return "ok";
    }

    void stopSender() {
        senderRunning.set(false);
        DatagramSocket socket;
        Thread thread;
        synchronized (lock) {
            socket = senderSocket;
            senderSocket = null;
            thread = senderThread;
            senderThread = null;
        }
        if (socket != null) socket.close();
        if (thread != null) thread.interrupt();
    }

    void stopReceiver() {
        receiverRunning.set(false);
        DatagramSocket socket;
        Thread thread;
        synchronized (lock) {
            socket = receiverSocket;
            receiverSocket = null;
            thread = receiverThread;
            receiverThread = null;
        }
        if (socket != null) socket.close();
        if (thread != null) thread.interrupt();
        receiverPort = 0;
    }

    void stopAll() {
        stopSender();
        stopReceiver();
    }

    String statusJson() {
        try {
            JSONObject result = new JSONObject();
            result.put("platform", "android");
            result.put("opus_encoder", opusEncoderAvailable());
            result.put("last_error", lastError);
            result.put("defaults", new JSONObject()
                    .put("source", "Android Systemmikrofon")
                    .put("backend", "android")
                    .put("bitrate_kbps", 64)
                    .put("port", 5004)
                    .put("speaker_device", "Android Systemausgabe")
                    .put("virtual_microphone", false));
            result.put("sender", senderRunning.get()
                    ? new JSONObject().put("running", true).put("source", "Android Systemmikrofon").put("backend", "android")
                    : JSONObject.NULL);
            result.put("receiver", receiverRunning.get()
                    ? new JSONObject().put("running", true).put("port", receiverPort)
                            .put("speaker_devices", new JSONArray().put("Android Systemausgabe"))
                            .put("virtual_microphone", "")
                    : JSONObject.NULL);
            return result.toString();
        } catch (Exception error) {
            return "{\"platform\":\"android\",\"last_error\":\"status\"}";
        }
    }

    private void runSender(List<Target> targets, int bitrateKbps) {
        AudioRecord recorder = null;
        MediaCodec encoder = null;
        DatagramSocket socket = null;
        try {
            int minBuffer = AudioRecord.getMinBufferSize(
                    SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
            if (minBuffer <= 0) throw new IllegalStateException("audio-record-buffer");
            recorder = new AudioRecord(
                    MediaRecorder.AudioSource.MIC,
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    Math.max(minBuffer, MONO_FRAME_BYTES * 4));
            if (recorder.getState() != AudioRecord.STATE_INITIALIZED) throw new IllegalStateException("audio-record-init");

            MediaFormat format = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, SAMPLE_RATE, CHANNELS);
            format.setInteger(MediaFormat.KEY_BIT_RATE, bitrateKbps * 1000);
            format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, STEREO_FRAME_BYTES);
            encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS);
            encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            encoder.start();

            socket = new DatagramSocket();
            synchronized (lock) { senderSocket = socket; }
            List<ResolvedTarget> resolvedTargets = resolveTargets(targets);
            recorder.startRecording();

            byte[] mono = new byte[MONO_FRAME_BYTES];
            long samplesSubmitted = 0;
            int sequence = new SecureRandom().nextInt(0x10000);
            int ssrc = new SecureRandom().nextInt();
            boolean firstPacket = true;
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();

            while (senderRunning.get() && !Thread.currentThread().isInterrupted()) {
                int read = recorder.read(mono, 0, mono.length, AudioRecord.READ_BLOCKING);
                if (read <= 0) continue;
                read -= read % 2;
                int inputIndex = encoder.dequeueInputBuffer(10000);
                if (inputIndex >= 0) {
                    ByteBuffer input = encoder.getInputBuffer(inputIndex);
                    if (input != null) {
                        input.clear();
                        for (int offset = 0; offset < read; offset += 2) {
                            input.put(mono[offset]).put(mono[offset + 1]);
                            input.put(mono[offset]).put(mono[offset + 1]);
                        }
                        long ptsUs = samplesSubmitted * 1_000_000L / SAMPLE_RATE;
                        int monoSamples = read / 2;
                        encoder.queueInputBuffer(inputIndex, 0, monoSamples * 4, ptsUs, 0);
                        samplesSubmitted += monoSamples;
                    }
                }
                int outputIndex;
                while ((outputIndex = encoder.dequeueOutputBuffer(info, 0)) >= 0) {
                    if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0 && info.size > 0) {
                        ByteBuffer output = encoder.getOutputBuffer(outputIndex);
                        if (output != null) {
                            byte[] opus = new byte[info.size];
                            output.position(info.offset);
                            output.limit(info.offset + info.size);
                            output.get(opus);
                            long timestamp = (info.presentationTimeUs * SAMPLE_RATE / 1_000_000L) & 0xffffffffL;
                            byte[] rtp = rtpPacket(opus, sequence++, timestamp, ssrc, firstPacket);
                            firstPacket = false;
                            for (ResolvedTarget target : resolvedTargets) {
                                socket.send(new DatagramPacket(rtp, rtp.length, target.address, target.port));
                            }
                        }
                    }
                    encoder.releaseOutputBuffer(outputIndex, false);
                }
            }
        } catch (SecurityException error) {
            lastError = "Mikrofonberechtigung fehlt.";
        } catch (Exception error) {
            lastError = "Android-Audiosender konnte nicht gestartet werden.";
        } finally {
            senderRunning.set(false);
            if (recorder != null) {
                try { recorder.stop(); } catch (IllegalStateException ignored) {}
                recorder.release();
            }
            stopCodec(encoder);
            if (socket != null) socket.close();
            synchronized (lock) { senderSocket = null; senderThread = null; }
        }
    }

    private void runReceiver(int port) {
        MediaCodec decoder = null;
        AudioTrack track = null;
        DatagramSocket socket = null;
        try {
            MediaFormat format = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, SAMPLE_RATE, CHANNELS);
            format.setByteBuffer("csd-0", opusHead());
            format.setByteBuffer("csd-1", nativeLong(0));
            format.setByteBuffer("csd-2", nativeLong(80_000_000L));
            decoder = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS);
            decoder.configure(format, null, null, 0);
            decoder.start();

            int minBuffer = AudioTrack.getMinBufferSize(
                    SAMPLE_RATE, AudioFormat.CHANNEL_OUT_STEREO, AudioFormat.ENCODING_PCM_16BIT);
            if (minBuffer <= 0) throw new IllegalStateException("audio-track-buffer");
            track = new AudioTrack(
                    AudioManager.STREAM_MUSIC,
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_OUT_STEREO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    Math.max(minBuffer, STEREO_FRAME_BYTES * 4),
                    AudioTrack.MODE_STREAM);
            if (track.getState() != AudioTrack.STATE_INITIALIZED) throw new IllegalStateException("audio-track-init");
            track.play();

            socket = new DatagramSocket(port);
            socket.setSoTimeout(200);
            synchronized (lock) { receiverSocket = socket; }
            byte[] packetBuffer = new byte[4096];
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            long firstTimestamp = -1;

            while (receiverRunning.get() && !Thread.currentThread().isInterrupted()) {
                try {
                    DatagramPacket datagram = new DatagramPacket(packetBuffer, packetBuffer.length);
                    socket.receive(datagram);
                    RtpPayload payload = parseRtp(datagram.getData(), datagram.getOffset(), datagram.getLength());
                    if (payload != null) {
                        if (firstTimestamp < 0) firstTimestamp = payload.timestamp;
                        long delta = (payload.timestamp - firstTimestamp) & 0xffffffffL;
                        queueDecoder(decoder, payload.data, delta * 1_000_000L / SAMPLE_RATE);
                    }
                } catch (SocketTimeoutException ignored) {
                }
                drainDecoder(decoder, track, info);
            }
        } catch (Exception error) {
            if (receiverRunning.get()) lastError = "Android-Audioempfang konnte nicht gestartet werden.";
        } finally {
            receiverRunning.set(false);
            if (socket != null) socket.close();
            stopCodec(decoder);
            if (track != null) {
                try { track.stop(); } catch (IllegalStateException ignored) {}
                track.release();
            }
            synchronized (lock) { receiverSocket = null; receiverThread = null; }
        }
    }

    private static void queueDecoder(MediaCodec decoder, byte[] payload, long ptsUs) throws IOException {
        int inputIndex = decoder.dequeueInputBuffer(10000);
        if (inputIndex < 0) return;
        ByteBuffer input = decoder.getInputBuffer(inputIndex);
        if (input == null || input.capacity() < payload.length) {
            decoder.queueInputBuffer(inputIndex, 0, 0, ptsUs, 0);
            return;
        }
        input.clear();
        input.put(payload);
        decoder.queueInputBuffer(inputIndex, 0, payload.length, ptsUs, 0);
    }

    private static void drainDecoder(MediaCodec decoder, AudioTrack track, MediaCodec.BufferInfo info) {
        int outputIndex;
        while ((outputIndex = decoder.dequeueOutputBuffer(info, 0)) >= 0) {
            ByteBuffer output = decoder.getOutputBuffer(outputIndex);
            if (output != null && info.size > 0) {
                byte[] pcm = new byte[info.size];
                output.position(info.offset);
                output.limit(info.offset + info.size);
                output.get(pcm);
                track.write(pcm, 0, pcm.length, AudioTrack.WRITE_BLOCKING);
            }
            decoder.releaseOutputBuffer(outputIndex, false);
        }
    }

    private static List<Target> parseTargets(String json) throws Exception {
        JSONArray array = new JSONArray(json == null ? "[]" : json);
        if (array.length() < 1 || array.length() > MAX_TARGETS) throw new IllegalArgumentException("targets");
        List<Target> result = new ArrayList<>();
        for (int index = 0; index < array.length(); index++) {
            JSONObject item = array.getJSONObject(index);
            String host = item.optString("host", "").trim();
            int port = item.optInt("port", 5004);
            if (host.isEmpty() || host.length() > 253 || port < 1024 || port > 65535) throw new IllegalArgumentException("target");
            result.add(new Target(host, port));
        }
        return result;
    }

    private static List<ResolvedTarget> resolveTargets(List<Target> targets) throws Exception {
        List<ResolvedTarget> result = new ArrayList<>();
        for (Target target : targets) result.add(new ResolvedTarget(InetAddress.getByName(target.host), target.port));
        return result;
    }

    private static byte[] rtpPacket(byte[] payload, int sequence, long timestamp, int ssrc, boolean marker) {
        ByteBuffer packet = ByteBuffer.allocate(12 + payload.length).order(ByteOrder.BIG_ENDIAN);
        packet.put((byte) 0x80);
        packet.put((byte) ((marker ? 0x80 : 0) | RTP_PAYLOAD_TYPE));
        packet.putShort((short) sequence);
        packet.putInt((int) timestamp);
        packet.putInt(ssrc);
        packet.put(payload);
        return packet.array();
    }

    private static RtpPayload parseRtp(byte[] packet, int offset, int length) {
        if (length < 12 || (packet[offset] & 0xc0) != 0x80) return null;
        int payloadType = packet[offset + 1] & 0x7f;
        if (payloadType != RTP_PAYLOAD_TYPE) return null;
        int csrcCount = packet[offset] & 0x0f;
        boolean extension = (packet[offset] & 0x10) != 0;
        boolean padding = (packet[offset] & 0x20) != 0;
        int header = 12 + csrcCount * 4;
        if (header > length) return null;
        if (extension) {
            if (header + 4 > length) return null;
            int words = ((packet[offset + header + 2] & 0xff) << 8) | (packet[offset + header + 3] & 0xff);
            header += 4 + words * 4;
            if (header > length) return null;
        }
        int payloadLength = length - header;
        if (padding) {
            int paddingBytes = packet[offset + length - 1] & 0xff;
            if (paddingBytes < 1 || paddingBytes > payloadLength) return null;
            payloadLength -= paddingBytes;
        }
        if (payloadLength <= 0) return null;
        long timestamp = ((long) (packet[offset + 4] & 0xff) << 24)
                | ((long) (packet[offset + 5] & 0xff) << 16)
                | ((long) (packet[offset + 6] & 0xff) << 8)
                | (long) (packet[offset + 7] & 0xff);
        byte[] payload = new byte[payloadLength];
        System.arraycopy(packet, offset + header, payload, 0, payloadLength);
        return new RtpPayload(payload, timestamp);
    }

    private static ByteBuffer opusHead() {
        ByteBuffer buffer = ByteBuffer.allocate(19).order(ByteOrder.LITTLE_ENDIAN);
        buffer.put("OpusHead".getBytes(StandardCharsets.US_ASCII));
        buffer.put((byte) 1).put((byte) CHANNELS).putShort((short) 0);
        buffer.putInt(SAMPLE_RATE).putShort((short) 0).put((byte) 0);
        buffer.flip();
        return buffer;
    }

    private static ByteBuffer nativeLong(long value) {
        ByteBuffer buffer = ByteBuffer.allocate(8).order(ByteOrder.nativeOrder());
        buffer.putLong(value);
        buffer.flip();
        return buffer;
    }

    private static void stopCodec(MediaCodec codec) {
        if (codec == null) return;
        try { codec.stop(); } catch (IllegalStateException ignored) {}
        codec.release();
    }

    private static final class Target {
        final String host;
        final int port;
        Target(String host, int port) { this.host = host; this.port = port; }
    }

    private static final class ResolvedTarget {
        final InetAddress address;
        final int port;
        ResolvedTarget(InetAddress address, int port) { this.address = address; this.port = port; }
    }

    private static final class RtpPayload {
        final byte[] data;
        final long timestamp;
        RtpPayload(byte[] data, long timestamp) { this.data = data; this.timestamp = timestamp; }
    }
}

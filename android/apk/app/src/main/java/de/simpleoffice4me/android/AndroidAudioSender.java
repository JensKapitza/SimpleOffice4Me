package de.simpleoffice4me.android;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaCodec;
import android.media.MediaFormat;
import android.media.MediaRecorder;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

/** Captures Android microphone audio and sends Opus packets over RTP/UDP. */
final class AndroidAudioSender {
    static final int SAMPLE_RATE = 48000;
    static final int CHANNELS = 2;
    static final int RTP_PAYLOAD_TYPE = 111;
    private static final int FRAME_SAMPLES = 960;
    private static final int MONO_FRAME_BYTES = FRAME_SAMPLES * 2;
    private static final int STEREO_FRAME_BYTES = FRAME_SAMPLES * CHANNELS * 2;

    static final class Target {
        final String host;
        final int port;

        Target(String host, int port) {
            this.host = host;
            this.port = port;
        }
    }

    private static final class ResolvedTarget {
        final InetAddress address;
        final int port;

        ResolvedTarget(InetAddress address, int port) {
            this.address = address;
            this.port = port;
        }
    }

    private final AtomicBoolean running = new AtomicBoolean(false);
    private final List<Target> targets;
    private final int bitrateKbps;
    private volatile DatagramSocket socket;
    private volatile Thread thread;
    private volatile String error = "";

    AndroidAudioSender(List<Target> targets, int bitrateKbps) {
        this.targets = new ArrayList<>(targets);
        this.bitrateKbps = Math.max(16, Math.min(bitrateKbps, 256));
    }

    boolean isRunning() {
        return running.get();
    }

    String error() {
        return error;
    }

    void start() {
        if (!running.compareAndSet(false, true)) return;
        error = "";
        thread = new Thread(this::run, "simpleoffice-android-audio-send");
        thread.start();
    }

    void stop() {
        running.set(false);
        DatagramSocket activeSocket = socket;
        if (activeSocket != null) activeSocket.close();
        Thread activeThread = thread;
        if (activeThread != null) activeThread.interrupt();
    }

    private void run() {
        AudioRecord recorder = null;
        MediaCodec encoder = null;
        DatagramSocket datagramSocket = null;
        try {
            recorder = createRecorder();
            encoder = createEncoder();
            datagramSocket = new DatagramSocket();
            socket = datagramSocket;
            List<ResolvedTarget> resolvedTargets = resolveTargets(targets);
            recorder.startRecording();
            stream(recorder, encoder, datagramSocket, resolvedTargets);
        } catch (SecurityException exception) {
            error = "Mikrofonberechtigung fehlt.";
        } catch (Exception exception) {
            if (running.get()) error = "Android-Audiosender konnte nicht gestartet werden.";
        } finally {
            running.set(false);
            if (recorder != null) {
                try { recorder.stop(); } catch (IllegalStateException ignored) { }
                recorder.release();
            }
            stopCodec(encoder);
            if (datagramSocket != null) datagramSocket.close();
            socket = null;
            thread = null;
        }
    }

    private static AudioRecord createRecorder() {
        int minBuffer = AudioRecord.getMinBufferSize(
                SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (minBuffer <= 0) throw new IllegalStateException("audio-record-buffer");
        AudioRecord recorder = new AudioRecord(
                MediaRecorder.AudioSource.MIC,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                Math.max(minBuffer, MONO_FRAME_BYTES * 4));
        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
            recorder.release();
            throw new IllegalStateException("audio-record-init");
        }
        return recorder;
    }

    private MediaCodec createEncoder() throws Exception {
        MediaFormat format = MediaFormat.createAudioFormat(
                MediaFormat.MIMETYPE_AUDIO_OPUS, SAMPLE_RATE, CHANNELS);
        format.setInteger(MediaFormat.KEY_BIT_RATE, bitrateKbps * 1000);
        format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, STEREO_FRAME_BYTES);
        MediaCodec encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS);
        encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
        encoder.start();
        return encoder;
    }

    private void stream(
            AudioRecord recorder,
            MediaCodec encoder,
            DatagramSocket datagramSocket,
            List<ResolvedTarget> resolvedTargets) throws Exception {
        byte[] mono = new byte[MONO_FRAME_BYTES];
        long samplesSubmitted = 0;
        SecureRandom random = new SecureRandom();
        int sequence = random.nextInt(0x10000);
        int ssrc = random.nextInt();
        boolean firstPacket = true;
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();

        while (running.get() && !Thread.currentThread().isInterrupted()) {
            int read = recorder.read(mono, 0, mono.length, AudioRecord.READ_BLOCKING);
            if (read <= 0) continue;
            read -= read % 2;
            samplesSubmitted = queuePcm(encoder, mono, read, samplesSubmitted);
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
                            datagramSocket.send(new DatagramPacket(rtp, rtp.length, target.address, target.port));
                        }
                    }
                }
                encoder.releaseOutputBuffer(outputIndex, false);
            }
        }
    }

    private static long queuePcm(MediaCodec encoder, byte[] mono, int read, long samplesSubmitted) {
        int inputIndex = encoder.dequeueInputBuffer(10000);
        if (inputIndex < 0) return samplesSubmitted;
        ByteBuffer input = encoder.getInputBuffer(inputIndex);
        if (input == null) {
            encoder.queueInputBuffer(inputIndex, 0, 0, 0, 0);
            return samplesSubmitted;
        }
        input.clear();
        for (int offset = 0; offset < read; offset += 2) {
            input.put(mono[offset]).put(mono[offset + 1]);
            input.put(mono[offset]).put(mono[offset + 1]);
        }
        long ptsUs = samplesSubmitted * 1_000_000L / SAMPLE_RATE;
        int monoSamples = read / 2;
        encoder.queueInputBuffer(inputIndex, 0, monoSamples * 4, ptsUs, 0);
        return samplesSubmitted + monoSamples;
    }

    private static List<ResolvedTarget> resolveTargets(List<Target> targets) throws Exception {
        List<ResolvedTarget> result = new ArrayList<>();
        for (Target target : targets) {
            result.add(new ResolvedTarget(InetAddress.getByName(target.host), target.port));
        }
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

    private static void stopCodec(MediaCodec codec) {
        if (codec == null) return;
        try { codec.stop(); } catch (IllegalStateException ignored) { }
        codec.release();
    }
}

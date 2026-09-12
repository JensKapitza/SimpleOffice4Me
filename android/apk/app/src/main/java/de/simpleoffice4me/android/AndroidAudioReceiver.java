package de.simpleoffice4me.android;

import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.media.MediaCodec;
import android.media.MediaFormat;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.SocketTimeoutException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;

/** Receives RTP/Opus over UDP and plays it through the Android system output. */
final class AndroidAudioReceiver {
    private static final int SAMPLE_RATE = AndroidAudioSender.SAMPLE_RATE;
    private static final int CHANNELS = AndroidAudioSender.CHANNELS;
    private static final int RTP_PAYLOAD_TYPE = AndroidAudioSender.RTP_PAYLOAD_TYPE;
    private static final int STEREO_FRAME_BYTES = 960 * CHANNELS * 2;

    private static final class RtpPayload {
        final byte[] data;
        final long timestamp;

        RtpPayload(byte[] data, long timestamp) {
            this.data = data;
            this.timestamp = timestamp;
        }
    }

    private final AtomicBoolean running = new AtomicBoolean(false);
    private final int port;
    private volatile DatagramSocket socket;
    private volatile Thread thread;
    private volatile String error = "";

    AndroidAudioReceiver(int port) {
        this.port = port;
    }

    int port() {
        return port;
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
        thread = new Thread(this::run, "simpleoffice-android-audio-receive");
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
        MediaCodec decoder = null;
        AudioTrack track = null;
        DatagramSocket datagramSocket = null;
        try {
            decoder = createDecoder();
            track = createTrack();
            datagramSocket = new DatagramSocket(port);
            datagramSocket.setSoTimeout(200);
            socket = datagramSocket;
            receive(decoder, track, datagramSocket);
        } catch (Exception exception) {
            if (running.get()) error = "Android-Audioempfang konnte nicht gestartet werden.";
        } finally {
            running.set(false);
            if (datagramSocket != null) datagramSocket.close();
            stopCodec(decoder);
            if (track != null) {
                try { track.stop(); } catch (IllegalStateException ignored) { }
                track.release();
            }
            socket = null;
            thread = null;
        }
    }

    private static MediaCodec createDecoder() throws Exception {
        MediaFormat format = MediaFormat.createAudioFormat(
                MediaFormat.MIMETYPE_AUDIO_OPUS, SAMPLE_RATE, CHANNELS);
        format.setByteBuffer("csd-0", opusHead());
        format.setByteBuffer("csd-1", nativeLong(0));
        format.setByteBuffer("csd-2", nativeLong(80_000_000L));
        MediaCodec decoder = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS);
        decoder.configure(format, null, null, 0);
        decoder.start();
        return decoder;
    }

    private static AudioTrack createTrack() {
        int minBuffer = AudioTrack.getMinBufferSize(
                SAMPLE_RATE, AudioFormat.CHANNEL_OUT_STEREO, AudioFormat.ENCODING_PCM_16BIT);
        if (minBuffer <= 0) throw new IllegalStateException("audio-track-buffer");
        AudioTrack track = new AudioTrack(
                AudioManager.STREAM_MUSIC,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_OUT_STEREO,
                AudioFormat.ENCODING_PCM_16BIT,
                Math.max(minBuffer, STEREO_FRAME_BYTES * 4),
                AudioTrack.MODE_STREAM);
        if (track.getState() != AudioTrack.STATE_INITIALIZED) {
            track.release();
            throw new IllegalStateException("audio-track-init");
        }
        track.play();
        return track;
    }

    private void receive(MediaCodec decoder, AudioTrack track, DatagramSocket datagramSocket) throws Exception {
        byte[] packetBuffer = new byte[4096];
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        long firstTimestamp = -1;

        while (running.get() && !Thread.currentThread().isInterrupted()) {
            try {
                DatagramPacket datagram = new DatagramPacket(packetBuffer, packetBuffer.length);
                datagramSocket.receive(datagram);
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
    }

    private static void queueDecoder(MediaCodec decoder, byte[] payload, long ptsUs) {
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

    private static RtpPayload parseRtp(byte[] packet, int offset, int length) {
        if (length < 12 || (packet[offset] & 0xc0) != 0x80) return null;
        if ((packet[offset + 1] & 0x7f) != RTP_PAYLOAD_TYPE) return null;
        int header = 12 + (packet[offset] & 0x0f) * 4;
        if (header > length) return null;
        if ((packet[offset] & 0x10) != 0) {
            if (header + 4 > length) return null;
            int words = ((packet[offset + header + 2] & 0xff) << 8)
                    | (packet[offset + header + 3] & 0xff);
            header += 4 + words * 4;
            if (header > length) return null;
        }
        int payloadLength = length - header;
        if ((packet[offset] & 0x20) != 0) {
            int padding = packet[offset + length - 1] & 0xff;
            if (padding < 1 || padding > payloadLength) return null;
            payloadLength -= padding;
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
        try { codec.stop(); } catch (IllegalStateException ignored) { }
        codec.release();
    }
}

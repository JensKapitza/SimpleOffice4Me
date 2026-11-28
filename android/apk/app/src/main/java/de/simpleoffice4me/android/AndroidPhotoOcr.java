package de.simpleoffice4me.android;

import android.content.Context;
import android.util.Log;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.FutureTask;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/** App-private photo OCR for the embedded Python server; not a WebView bridge. */
public final class AndroidPhotoOcr {
    private static final String TAG = "SimpleOfficeImageOcr";
    private static final String MODEL_DIRECTORY = "tessdata-fast-4.1.0";
    private static final String TESSDATA = "tessdata";
    private static final int MAX_EDGE = 2800;
    private static final int MAX_TEXT = 100000;
    private static final int TIMEOUT_MILLIS = 25000;
    private static final Semaphore SLOT = new Semaphore(1);

    private AndroidPhotoOcr() {}

    public static String recognize(byte[] raster, int width, int height) {
        if (!validRaster(raster, width, height)
                || !SLOT.tryAcquire()) return null;
        FutureTask<String> task = new FutureTask<>(() -> {
            try {
                Context context = ((AndroidPlatform) Python.getPlatform()).getApplication();
                return recognizeLocally(context, raster, width, height);
            } finally {
                SLOT.release();
            }
        });
        try {
            new Thread(task, "simpleoffice-image-ocr").start();
        } catch (RuntimeException | Error failure) {
            SLOT.release();
            throw failure;
        }
        try {
            // A timeout releases the caller, not the sole slot. Even if the
            // engine takes longer to stop, a second native job cannot overlap.
            return task.get(TIMEOUT_MILLIS, TimeUnit.MILLISECONDS);
        } catch (InterruptedException exc) {
            Thread.currentThread().interrupt();
            return null;
        } catch (TimeoutException exc) {
            Log.w(TAG, "On-device image OCR timed out");
            return null;
        } catch (ExecutionException exc) {
            Log.w(TAG, "On-device image OCR failed: " + exc.getCause().getClass().getSimpleName());
            return null;
        }
    }

    static boolean validRaster(byte[] raster, int width, int height) {
        return raster != null && width > 0 && width <= MAX_EDGE && height > 0 && height <= MAX_EDGE
                && raster.length == width * height;
    }

    private static class NativeLibrary {
        static {
            System.loadLibrary("simpleoffice_ocr");
        }

        static void load() {}
    }

    private static native String recognizeNative(
            String dataPath, byte[] pixels, int width, int height, int timeoutMillis);

    private static String recognizeLocally(Context context, byte[] raster, int width, int height) throws IOException {
        File modelDirectory = installModels(context);
        NativeLibrary.load();
        String text = recognizeNative(modelDirectory.getAbsolutePath(), raster, width, height, TIMEOUT_MILLIS);
        return text != null && text.length() <= MAX_TEXT ? text : null;
    }

    private static File installModels(Context context) throws IOException {
        File root = new File(context.getNoBackupFilesDir(), MODEL_DIRECTORY);
        File directory = new File(root, TESSDATA);
        if (!directory.isDirectory() && !directory.mkdirs()) {
            throw new IOException("OCR model directory is unavailable");
        }
        copyModel(context, directory, "deu.traineddata", 1525436,
                "19d219bbb6672c869d20a9636c6816a81eb9a71796cb93ebe0cb1530e2cdb22d");
        copyModel(context, directory, "eng.traineddata", 4113088,
                "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2");
        return directory;
    }

    private static void copyModel(Context context, File directory, String name, int size, String sha256)
            throws IOException {
        File target = new File(directory, name);
        if (matchesModel(target, size, sha256)) return;
        // The one OCR worker is the only writer. A fixed staging name also
        // bounds leftovers across an interrupted process or device restart.
        File staging = new File(directory, "." + name + ".partial");
        try {
            int total = 0;
            try (InputStream input = context.getAssets().open("android-ocr/tessdata/" + name);
                 OutputStream output = new java.io.FileOutputStream(staging)) {
                byte[] buffer = new byte[32768];
                for (int count; (count = input.read(buffer)) != -1;) {
                    total += count;
                    if (total > size) throw new IOException("Invalid packaged OCR model size");
                    output.write(buffer, 0, count);
                }
            }
            if (!matchesModel(staging, size, sha256) || !staging.renameTo(target)) {
                throw new IOException("Could not install verified packaged OCR model");
            }
        } finally {
            if (staging.exists() && !staging.delete()) Log.w(TAG, "OCR model staging cleanup failed");
        }
    }

    private static boolean matchesModel(File file, int size, String sha256) throws IOException {
        if (!file.isFile() || file.length() != size) return false;
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException exc) {
            throw new IOException("SHA-256 is unavailable", exc);
        }
        try (InputStream input = new java.io.FileInputStream(file)) {
            byte[] buffer = new byte[32768];
            for (int count; (count = input.read(buffer)) != -1;) digest.update(buffer, 0, count);
        }
        StringBuilder hex = new StringBuilder(64);
        for (byte value : digest.digest()) {
            hex.append(Character.forDigit((value >>> 4) & 15, 16));
            hex.append(Character.forDigit(value & 15, 16));
        }
        return sha256.contentEquals(hex);
    }
}

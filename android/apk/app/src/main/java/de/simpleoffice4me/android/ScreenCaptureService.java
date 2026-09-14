package de.simpleoffice4me.android;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.util.Base64;
import android.util.DisplayMetrics;
import android.view.WindowManager;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.concurrent.atomic.AtomicBoolean;

/** User-approved MediaProjection capture exposed as bounded JPEG frames to the local WebView. */
public final class ScreenCaptureService extends Service {
    interface Listener {
        void onScreenEvent(String state, int width, int height, String data, String message);
    }

    private static final String ACTION_START = "de.simpleoffice4me.screen.START";
    private static final String ACTION_STOP = "de.simpleoffice4me.screen.STOP";
    private static final String EXTRA_RESULT_CODE = "result-code";
    private static final String EXTRA_RESULT_DATA = "result-data";
    private static final String CHANNEL_ID = "screen-share";
    private static final int NOTIFICATION_ID = 4102;
    private static final AtomicBoolean RUNNING = new AtomicBoolean(false);
    private static volatile Listener listener;

    private HandlerThread captureThread;
    private Handler captureHandler;
    private MediaProjection projection;
    private VirtualDisplay virtualDisplay;
    private ImageReader imageReader;
    private long lastFrameAt;
    private int captureWidth;
    private int captureHeight;

    static void setListener(Listener value) { listener = value; }
    static boolean isRunning() { return RUNNING.get(); }

    static Intent startIntent(Context context, int resultCode, Intent resultData) {
        return new Intent(context, ScreenCaptureService.class).setAction(ACTION_START)
                .putExtra(EXTRA_RESULT_CODE, resultCode).putExtra(EXTRA_RESULT_DATA, resultData);
    }

    static Intent stopIntent(Context context) {
        return new Intent(context, ScreenCaptureService.class).setAction(ACTION_STOP);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) return START_NOT_STICKY;
        if (ACTION_STOP.equals(intent.getAction())) {
            stopCapture(); stopSelf(); return START_NOT_STICKY;
        }
        if (!ACTION_START.equals(intent.getAction()) || RUNNING.get()) return START_NOT_STICKY;
        createNotificationChannel();
        startForeground(NOTIFICATION_ID, notification());
        Intent data = intent.getParcelableExtra(EXTRA_RESULT_DATA);
        MediaProjectionManager manager = getSystemService(MediaProjectionManager.class);
        if (manager == null || data == null) {
            emit("error", "MediaProjection ist nicht verfügbar."); stopSelf(); return START_NOT_STICKY;
        }
        try {
            projection = manager.getMediaProjection(intent.getIntExtra(EXTRA_RESULT_CODE, 0), data);
            if (projection == null) throw new IllegalStateException("Keine MediaProjection erhalten");
            startCapture();
        } catch (RuntimeException error) {
            emit("error", "Bildschirmaufnahme konnte nicht gestartet werden."); stopCapture(); stopSelf();
        }
        return START_NOT_STICKY;
    }

    private void startCapture() {
        DisplayMetrics metrics = new DisplayMetrics();
        WindowManager windowManager = getSystemService(WindowManager.class);
        if (windowManager == null) throw new IllegalStateException("WindowManager fehlt");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            android.graphics.Rect bounds = windowManager.getCurrentWindowMetrics().getBounds();
            metrics.widthPixels = bounds.width(); metrics.heightPixels = bounds.height();
            metrics.densityDpi = getResources().getDisplayMetrics().densityDpi;
        } else {
            windowManager.getDefaultDisplay().getRealMetrics(metrics);
        }
        double scale = Math.min(1.0, 1280.0 / Math.max(metrics.widthPixels, metrics.heightPixels));
        captureWidth = Math.max(2, ((int) (metrics.widthPixels * scale)) & ~1);
        captureHeight = Math.max(2, ((int) (metrics.heightPixels * scale)) & ~1);
        captureThread = new HandlerThread("SimpleOfficeScreenCapture"); captureThread.start();
        captureHandler = new Handler(captureThread.getLooper());
        imageReader = ImageReader.newInstance(captureWidth, captureHeight, PixelFormat.RGBA_8888, 2);
        imageReader.setOnImageAvailableListener(this::onImageAvailable, captureHandler);
        projection.registerCallback(new MediaProjection.Callback() {
            @Override public void onStop() { stopCapture(); stopSelf(); }
        }, captureHandler);
        virtualDisplay = projection.createVirtualDisplay("SimpleOffice Screen Share", captureWidth, captureHeight,
                metrics.densityDpi, DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                imageReader.getSurface(), null, captureHandler);
        RUNNING.set(true);
        Listener current = listener;
        if (current != null) current.onScreenEvent("active", captureWidth, captureHeight, "", "");
    }

    private void onImageAvailable(ImageReader reader) {
        try (Image image = reader.acquireLatestImage()) {
            long now = System.currentTimeMillis();
            if (image == null || now - lastFrameAt < 80) return;
            lastFrameAt = now;
            Image.Plane plane = image.getPlanes()[0];
            int pixelStride = plane.getPixelStride();
            int rowStride = plane.getRowStride();
            int rowPadding = rowStride - pixelStride * captureWidth;
            Bitmap padded = Bitmap.createBitmap(captureWidth + rowPadding / pixelStride, captureHeight, Bitmap.Config.ARGB_8888);
            ByteBuffer buffer = plane.getBuffer(); padded.copyPixelsFromBuffer(buffer);
            Bitmap frame = Bitmap.createBitmap(padded, 0, 0, captureWidth, captureHeight); padded.recycle();
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(); frame.compress(Bitmap.CompressFormat.JPEG, 70, bytes); frame.recycle();
            Listener current = listener;
            if (current != null) current.onScreenEvent("frame", captureWidth, captureHeight,
                    Base64.encodeToString(bytes.toByteArray(), Base64.NO_WRAP), "");
        } catch (RuntimeException ignored) {
            emit("error", "Ein Bildschirmbild konnte nicht verarbeitet werden.");
        }
    }

    private void stopCapture() {
        boolean wasRunning = RUNNING.getAndSet(false);
        if (virtualDisplay != null) { virtualDisplay.release(); virtualDisplay = null; }
        if (imageReader != null) { imageReader.close(); imageReader = null; }
        MediaProjection oldProjection = projection; projection = null;
        if (oldProjection != null) oldProjection.stop();
        if (captureThread != null) { captureThread.quitSafely(); captureThread = null; captureHandler = null; }
        if (wasRunning) emit("stopped", "");
        stopForeground(STOP_FOREGROUND_REMOVE);
    }

    private void emit(String state, String message) {
        Listener current = listener;
        if (current != null) current.onScreenEvent(state, captureWidth, captureHeight, "", message);
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationChannel channel = new NotificationChannel(CHANNEL_ID, "Bildschirmfreigabe", NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("Zeigt eine aktive SimpleOffice-Bildschirmfreigabe an");
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
    }

    private Notification notification() {
        Intent stop = stopIntent(this);
        PendingIntent stopAction = PendingIntent.getService(this, 0, stop, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL_ID) : new Notification.Builder(this);
        return builder.setSmallIcon(android.R.drawable.ic_menu_view).setContentTitle("Bildschirmfreigabe aktiv")
                .setContentText("SimpleOffice überträgt den Bildschirm").setOngoing(true)
                .addAction(new Notification.Action.Builder(android.R.drawable.ic_menu_close_clear_cancel, "Beenden", stopAction).build()).build();
    }

    @Override public void onDestroy() { stopCapture(); super.onDestroy(); }
    @Override public IBinder onBind(Intent intent) { return null; }
}

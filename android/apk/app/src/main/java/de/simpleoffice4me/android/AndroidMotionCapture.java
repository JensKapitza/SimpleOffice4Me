package de.simpleoffice4me.android;

import android.content.Context;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import java.util.Objects;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/** One-shot foreground acquisition; the Activity must cancel on pause or navigation. */
final class AndroidMotionCapture {
    enum Status { CAPTURED, BLOCKED, BUSY, UNSUPPORTED, UNAVAILABLE, TIMEOUT }
    record Result(Status status, AndroidMotionSample sample) {}

    private static final long TIMEOUT_MS = 3_000L;
    private final SensorManager sensors;
    private final Handler main = new Handler(Looper.getMainLooper());
    private Session pending;

    AndroidMotionCapture(Context context) {
        sensors = context.getApplicationContext().getSystemService(SensorManager.class);
    }

    boolean available(AndroidMotionSample.Kind kind) {
        requireMain();
        return kind != null && sensor(kind) != null;
    }

    void capture(AndroidMotionSample.Kind kind, BooleanSupplier authorized, Consumer<Result> result) {
        requireMain();
        Objects.requireNonNull(authorized);
        Objects.requireNonNull(result);
        if (kind == null || !authorized.getAsBoolean()) {
            result.accept(new Result(Status.BLOCKED, null));
            return;
        }
        if (pending != null) {
            result.accept(new Result(Status.BUSY, null));
            return;
        }
        Sensor sensor = sensor(kind);
        if (sensor == null) {
            result.accept(new Result(Status.UNSUPPORTED, null));
            return;
        }
        Session own = new Session(kind, sensor, authorized, result);
        pending = own;
        try {
            if (!sensors.registerListener(own, sensor, SensorManager.SENSOR_DELAY_NORMAL, main)) {
                finish(own, Status.UNAVAILABLE, null);
                return;
            }
        } catch (IllegalArgumentException | SecurityException failure) {
            finish(own, Status.UNAVAILABLE, null);
            return;
        }
        if (!main.postDelayed(own.timeout, TIMEOUT_MS)) finish(own, Status.UNAVAILABLE, null);
    }

    void cancel() {
        requireMain();
        if (pending != null) cleanup(pending); // A closed or switched page receives no callback.
    }

    private Sensor sensor(AndroidMotionSample.Kind kind) {
        if (sensors == null) return null;
        return sensors.getDefaultSensor(kind == AndroidMotionSample.Kind.ACCELERATION
                ? Sensor.TYPE_ACCELEROMETER : Sensor.TYPE_GYROSCOPE);
    }

    private void finish(Session own, Status status, AndroidMotionSample sample) {
        if (pending != own) return;
        cleanup(own);
        if (own.authorized.getAsBoolean()) own.result.accept(new Result(status, sample));
    }

    private void cleanup(Session own) {
        pending = null;
        main.removeCallbacks(own.timeout);
        sensors.unregisterListener(own);
    }

    private static void requireMain() {
        if (Looper.myLooper() != Looper.getMainLooper()) throw new IllegalStateException("Motion UI thread required");
    }

    private final class Session implements SensorEventListener {
        final AndroidMotionSample.Kind kind;
        final Sensor sensor;
        final BooleanSupplier authorized;
        final Consumer<Result> result;
        final long startedAt = SystemClock.elapsedRealtimeNanos();
        final Runnable timeout = () -> finish(this, Status.TIMEOUT, null);

        Session(AndroidMotionSample.Kind kind, Sensor sensor, BooleanSupplier authorized, Consumer<Result> result) {
            this.kind = kind;
            this.sensor = sensor;
            this.authorized = authorized;
            this.result = result;
        }

        @Override public void onAccuracyChanged(Sensor sensor, int accuracy) {}

        @Override public void onSensorChanged(SensorEvent event) {
            if (pending != this || event.sensor != sensor) return;
            if (!authorized.getAsBoolean()) {
                cleanup(this);
                return;
            }
            long now = SystemClock.elapsedRealtimeNanos();
            if (now - startedAt >= TIMEOUT_MS * 1_000_000L) {
                finish(this, Status.TIMEOUT, null);
                return;
            }
            AndroidMotionSample sample = AndroidMotionSample.from(kind, event.values, startedAt, event.timestamp, now);
            if (sample != null) finish(this, Status.CAPTURED, sample);
        }
    }
}

package de.simpleoffice4me.android;

/** Frozen scalar from a fresh 3-axis reading. No coordinates or raw trace. */
record AndroidMotionSample(Kind kind, double magnitude) {
    enum Kind { ACCELERATION, ANGULAR_SPEED }

    static AndroidMotionSample from(Kind kind, float[] values, long startedAt, long eventAt, long now) {
        if (kind == null || values == null || values.length < 3 || startedAt <= 0
                || eventAt < startedAt || eventAt > now || now - eventAt > 750_000_000L) return null;
        double x = values[0], y = values[1], z = values[2];
        if (!Double.isFinite(x) || !Double.isFinite(y) || !Double.isFinite(z)) return null;
        double value = Math.hypot(Math.hypot(x, y), z);
        double bound = kind == Kind.ACCELERATION ? 2000.0 : 100.0;
        return value <= bound ? new AndroidMotionSample(kind, value) : null;
    }

    String unit() { return kind == Kind.ACCELERATION ? "m/s²" : "rad/s"; }
}

package de.simpleoffice4me.android;

import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.Set;

/** Owns the WebView request independently of the Android system dialog. UI thread only. */
final class AndroidWebPermissionFlow<T> {
    interface Host<T> {
        boolean canHandle(T request);
        String permissionFor(String resource);
        boolean hasPermission(String permission);
        void requestPermissions(String[] permissions);
        void grant(T request, String[] resources);
        void deny(T request);
    }

    private record Pending<T>(T request, String[] resources, Set<String> permissions) {}

    private final Host<T> host;
    private boolean resumed;
    private boolean dialogInFlight;
    private Pending<T> pending;

    AndroidWebPermissionFlow(Host<T> host) {
        this.host = host;
    }

    void request(T request, String[] resources) {
        if (!resumed || pending != null || dialogInFlight || !host.canHandle(request)
                || resources.length == 0) {
            host.deny(request);
            return;
        }
        String[] snapshot = resources.clone();
        Set<String> missing = new LinkedHashSet<>();
        for (String resource : snapshot) {
            String permission = host.permissionFor(resource);
            if (permission == null) {
                host.deny(request);
                return;
            }
            if (!host.hasPermission(permission)) missing.add(permission);
        }
        if (missing.isEmpty()) {
            host.grant(request, snapshot);
            return;
        }
        pending = new Pending<>(request, snapshot, missing);
        dialogInFlight = true;
        host.requestPermissions(missing.toArray(new String[0]));
    }

    void onPermissionResult(String[] permissions, boolean allGranted) {
        if (!dialogInFlight) return;
        dialogInFlight = false;
        if (pending == null) return;
        if (!allGranted || permissions.length != pending.permissions().size()
                || !pending.permissions().equals(new LinkedHashSet<>(Arrays.asList(permissions)))) {
            denyPending();
            return;
        }
        completeIfResumed();
    }

    void setResumed(boolean resumed) {
        this.resumed = resumed;
        if (resumed) completeIfResumed();
    }

    void cancel(T request) {
        if (pending != null && pending.request() == request) pending = null;
    }

    void denyPending() {
        Pending<T> abandoned = pending;
        pending = null;
        // Only the OS result retires the dialog slot, even if the WebView request was abandoned.
        if (abandoned != null) host.deny(abandoned.request());
    }

    private void completeIfResumed() {
        if (!resumed || dialogInFlight || pending == null) return;
        Pending<T> resolved = pending;
        pending = null;
        if (host.canHandle(resolved.request())) {
            for (String resource : resolved.resources()) {
                String permission = host.permissionFor(resource);
                if (permission == null || !host.hasPermission(permission)) {
                    host.deny(resolved.request());
                    return;
                }
            }
            host.grant(resolved.request(), resolved.resources());
        } else {
            host.deny(resolved.request());
        }
    }
}

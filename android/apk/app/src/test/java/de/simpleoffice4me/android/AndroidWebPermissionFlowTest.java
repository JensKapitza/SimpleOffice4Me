package de.simpleoffice4me.android;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.junit.Before;
import org.junit.Test;

public class AndroidWebPermissionFlowTest {
    private final FakeHost host = new FakeHost();
    private final AndroidWebPermissionFlow<Object> flow = new AndroidWebPermissionFlow<>(host);

    @Before
    public void resumeActivity() {
        flow.setResumed(true);
    }

    @Test
    public void firstMicrophoneGrantIsDeliveredOnlyAfterResume() {
        Object request = new Object();
        flow.request(request, new String[]{"mic"});
        assertArrayEquals(new String[]{"RECORD_AUDIO"}, host.dialogs.get(0));
        flow.setResumed(false);
        host.allowed.add("RECORD_AUDIO");
        flow.onPermissionResult(new String[]{"RECORD_AUDIO"}, true);
        assertEquals(List.of(), host.granted);
        assertEquals(List.of(), host.denied);

        flow.setResumed(true);
        flow.setResumed(true);
        assertEquals(List.of(request), host.granted);
        assertEquals(List.of(), host.denied);
        assertArrayEquals(new String[]{"mic"}, host.resources);
    }

    @Test
    public void cameraOnlyRequestsOnlyCameraAndPreviouslyGrantedCameraDoesNotPromptAgain() {
        Object first = new Object();
        Object next = new Object();
        flow.request(first, new String[]{"camera"});
        assertArrayEquals(new String[]{"CAMERA"}, host.dialogs.get(0));
        host.allowed.add("CAMERA");
        flow.onPermissionResult(new String[]{"CAMERA"}, true);
        flow.request(next, new String[]{"camera"});
        assertEquals(List.of(first, next), host.granted);
        assertEquals(1, host.dialogs.size());
        assertArrayEquals(new String[]{"camera"}, host.resources);
    }

    @Test
    public void combinedCaptureAsksOnlyForMissingPermissionAndRechecksBoth() {
        Object request = new Object();
        host.allowed.add("CAMERA");
        flow.request(request, new String[]{"camera", "mic"});
        assertArrayEquals(new String[]{"RECORD_AUDIO"}, host.dialogs.get(0));
        flow.setResumed(false);
        host.allowed.add("RECORD_AUDIO");
        flow.onPermissionResult(new String[]{"RECORD_AUDIO"}, true);
        host.allowed.remove("CAMERA");
        flow.setResumed(true);
        assertEquals(List.of(request), host.denied);
        assertEquals(List.of(), host.granted);
    }

    @Test
    public void emptyPartialOrUnsuccessfulAndroidResultsDenyTheRequest() {
        Object empty = new Object();
        Object partial = new Object();
        Object denied = new Object();
        flow.request(empty, new String[]{"mic"});
        flow.onPermissionResult(new String[0], false);
        flow.request(partial, new String[]{"camera", "mic"});
        flow.onPermissionResult(new String[]{"CAMERA"}, true);
        flow.request(denied, new String[]{"camera", "mic"});
        flow.onPermissionResult(new String[]{"CAMERA", "RECORD_AUDIO"}, false);
        assertEquals(List.of(empty, partial, denied), host.denied);
        assertEquals(List.of(), host.granted);
    }

    @Test
    public void newerRequestCannotTakeOverCanceledOutstandingDialog() {
        Object original = new Object();
        Object overlapping = new Object();
        Object after = new Object();
        flow.request(original, new String[]{"mic"});
        flow.cancel(original);
        flow.request(overlapping, new String[]{"mic"});
        assertEquals(1, host.dialogs.size());
        assertEquals(List.of(overlapping), host.denied);
        host.allowed.add("RECORD_AUDIO");
        flow.onPermissionResult(new String[]{"RECORD_AUDIO"}, true);
        assertEquals(List.of(), host.granted);
        flow.request(after, new String[]{"mic"});
        assertEquals(List.of(after), host.granted);
        assertEquals(1, host.dialogs.size());
    }

    @Test
    public void navigationOrStopDiscardsApprovedButUndeliveredRequest() {
        Object request = new Object();
        flow.request(request, new String[]{"mic"});
        flow.setResumed(false);
        host.allowed.add("RECORD_AUDIO");
        flow.onPermissionResult(new String[]{"RECORD_AUDIO"}, true);
        flow.denyPending();
        flow.setResumed(true);
        assertEquals(List.of(request), host.denied);
        assertEquals(List.of(), host.granted);
    }

    @Test
    public void disallowedOriginAndUnknownResourceNeverOpenSystemDialog() {
        Object offline = new Object();
        Object unknown = new Object();
        host.trusted = false;
        flow.request(offline, new String[]{"mic"});
        host.trusted = true;
        flow.request(unknown, new String[]{"camera", "unknown"});
        assertEquals(List.of(offline, unknown), host.denied);
        assertEquals(List.of(), host.dialogs);
        assertEquals(List.of(), host.granted);
    }

    private static final class FakeHost implements AndroidWebPermissionFlow.Host<Object> {
        final Set<String> allowed = new HashSet<>();
        final List<String[]> dialogs = new ArrayList<>();
        final List<Object> granted = new ArrayList<>();
        final List<Object> denied = new ArrayList<>();
        String[] resources;
        boolean trusted = true;

        @Override
        public boolean canHandle(Object request) {
            return trusted;
        }

        @Override
        public String permissionFor(String resource) {
            return switch (resource) {
                case "camera" -> "CAMERA";
                case "mic" -> "RECORD_AUDIO";
                default -> null;
            };
        }

        @Override
        public boolean hasPermission(String permission) {
            return allowed.contains(permission);
        }

        @Override
        public void requestPermissions(String[] permissions) {
            dialogs.add(permissions);
        }

        @Override
        public void grant(Object request, String[] grantedResources) {
            granted.add(request);
            resources = grantedResources;
        }

        @Override
        public void deny(Object request) {
            denied.add(request);
        }
    }
}

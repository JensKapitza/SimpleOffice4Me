# Mini Services external acceptance protocol

This protocol covers evidence that cannot be created by CI, mocks or loopback
tests. A row is accepted only after a real run records the exact commit,
platform/device, procedure, expected result, actual result and remaining limit.

Do not copy credentials, private addresses, serial numbers or other operational
secrets into the public repository. Use generic device labels and redact local
network details.

## Evidence record

For every run record:

- date and tested Git commit;
- operating system/version or Android version/device class;
- SimpleOffice installation/start method;
- service and scenario;
- expected result;
- actual result;
- pass/fail/not-applicable;
- sanitized log or screenshot reference when useful;
- observed limitation and follow-up issue, if any.

A build succeeding is not a substitute for a runtime result.

## Linux

Run on a clean supported installation:

1. install/start the web application and Mini Services worker;
2. verify status, stop, restart and autostart for enabled services;
3. move a real LAN link down/up and verify bounded waiting/recovery without
   restarting unrelated services;
4. repeat with a usable IPv6 address for services that advertise IPv6 binding;
5. apply the Gateway on a disposable test network and verify actual packet
   forwarding/NAT plus recovery after link loss;
6. change the real audio device while sender/receiver/output functions run and
   verify the UI does not claim playback/health when the device is unavailable;
7. verify nftables rules are limited to the documented SimpleOffice-owned
   tables and survive the intended reload/stop sequence.

## Windows

On a clean supported Windows installation:

1. verify install/start/status/stop/restart/autostart from the documented
   terminal path;
2. exercise DirectShow microphone capture and FFplay receiver playback on real
   hardware;
3. verify the UI truthfully reports system-default output when no targeted
   output selection is available;
4. apply and remove the configured NAT on a disposable network;
5. force a failed apply and verify the previous owned configuration is not
   silently reported as healthy;
6. change network adapters while services are running and verify bounded
   recovery.

A virtual Windows microphone is outside the accepted scope while the explicitly
excluded driver approaches remain excluded.

## Android

On a physical device:

1. install the produced APK;
2. verify LAN discovery and receive against a real SimpleOffice peer;
3. start/stop audio sender and receiver and verify permission denial/regrant;
4. verify screen capture/system-cast behavior where the device supports it;
5. background and foreground the application during an active permitted
   operation and verify visible/stoppable state;
6. revoke a relevant Android permission and confirm the application fails
   visibly rather than claiming success.

## RTP / DLNA / PXE

Use real independent clients:

- RTP: send to a real receiver/speaker and separately verify audio at the
  receiver. Packet transmission alone is not playback confirmation.
- DLNA: discover/control the renderer from at least one independent controller
  and test audio plus a supported video/image path as applicable.
- PXE: boot at least one real or independently virtualized PXE client from a
  configured profile. HTTP/TFTP request success alone is not boot success.

## Screen

Check each available platform separately:

- SimpleOffice WebRTC sender/receiver;
- Windows Miracast;
- Linux GNOME Network Displays/MiracleCast;
- Android system cast/MediaProjection.

Record unsupported platform features as not-applicable with the technical
reason; do not convert them into a pass.

## Accessibility and responsive UI

On desktop, tablet, phone and Android WebView:

- keyboard-only navigation and visible focus;
- zoom/reflow and narrow-screen navigation;
- touch target usability;
- contrast and status/error text;
- live status announcements where present;
- no controls hidden behind fixed navigation or viewport edges.

This is a practical review against the project's WCAG 2.2 intent, not an
automatic claim of formal WCAG conformance.

## Load and endurance

For every environment used for release acceptance:

1. capture idle and active CPU/RAM for the enabled services;
2. run repeated start/stop/restart cycles;
3. run representative DNS/TFTP/RTP/PXE traffic within an isolated test network;
4. observe thread/process/socket/queue counts before and after;
5. run a multi-hour endurance session for the services intended to stay active;
6. document throughput/latency together with the exact workload and hardware.

The repository loopback benchmark is the software baseline only. These figures
must not be compared across machines without recording the environment.

## Completion rule

Issue #330 can be closed only when every applicable section above has a dated,
sanitized evidence record, or a concrete documented technical exception. CI,
mocked OS calls and successful APK/Desktop builds remain supporting evidence,
not substitutes for these runs.

# Reaktionsparcours im lokalen Raum / Local reaction rooms (#1305)

**Deutsch:** Öffne **Kinder → Reaktionsparcours → Mit Handys oder PCs zusammenspielen**. Alle zwei bis drei Geräte öffnen dieselbe SimpleOffice-Installation im selben lokalen Netz unter einer privaten HTTPS-Adresse und melden sich an. Ein Gerät öffnet den Raum (Station 1); die anderen geben den achtstelligen Code ein und erhalten Station 2 bzw. 3. Der Code wird nur am Host angezeigt und muss nicht als Link geteilt werden.

**English:** Open **Kids → Reaction course → Play together on phones or computers**. From the same local network, sign each of two or three devices into the same SimpleOffice instance at a private HTTPS address. One device hosts (station 1); the others type the eight-character code and become stations 2 and 3. Only the host sees the code; it is not part of a link.

- Use the existing [SimpleOffice HTTPS LAN proxy](DESKTOP_LAN_PROXY_SCREEN_VIEWER.md) or a directly served private IP with a certificate already trusted by each device. Do not disable certificate validation. Public domains, insecure HTTP, and untrusted forwarding are intentionally unavailable for rooms; the single-device virtual game still works.
- Start with untimed practice. On the active device, tap the large **Touch me** button or focus the station area and press the number shown. Only that station can contribute a hit. Stations must stay on the visible page. Do not run or obstruct walkways.
- In a duel, one player presses the targets in the first turn; then another player uses the **same** devices and order for the second. Ties use hits, then missed targets, then cumulative time. The host can start another practice or duel with the same roster, or end the room for everyone. There is no hidden input capture.
- A browser estimates cue-to-input time locally with its monotonic clock. The measurement starts when the cue is enabled in a browser animation callback, not at independently measured display onset. Polling and server acknowledgements determine when a cue is offered, not the locally measured interval. Display latency and event delivery vary; results are for play, not calibration or synchronized cross-device measurement.
- Navigation away or another hidden tab releases the current station. During a round, a lost device aborts the room rather than scoring invisibly or silently replacing a station. A fresh join is required after a disconnect; nothing is recovered from disk. The join code expires after five minutes in the lobby, and every room expires after 30 minutes from creation. A missing heartbeat expires within six seconds. Invite, per-session station credential and results are in memory only and never placed in a URL. No camera, microphone, USB, local network discovery or background listener is used by this page.
- This initial room mode is for the standard single-process SimpleOffice server (multiple threads are supported). Independent WSGI worker processes do not share room state; do not route this feature across separate workers or hosts. Switching a request to a different worker can end the room safely instead of activating the wrong station.

## Reproducible checks

From the repository root, use the same supported Python environment as the application; the Node fixtures require no installed JavaScript packages:

```bash
python -m pip install -e '.[test,quality]'
python -m unittest -v tests.test_kids_reaction_rooms tests.test_kids_reaction_room_tls \
  tests.test_kids_reaction tests.test_template_accessibility \
  tests.test_frontend_route_contract tests.test_template_shell
node --test tests/kids_reaction.test.cjs tests/kids_reaction_rooms.test.cjs
ruff check app/kids_reaction*.py tests/test_kids_reaction_room*.py
```

The TLS integration starts SimpleOffice's existing HTTPS LAN proxy on loopback, verifies the locally issued certificate, signs in two independent HTTP clients and completes a real shared round and global stop. This is an HTTP/application integration test, not a rendered browser or physical phone/PC test. Node uses a deliberately synthetic DOM and network, including the actual presentation code and shared keyboard/clock helpers.

Before using this for play, check a physical phone and PC on the same network with HTTPS trust enabled: complete an untimed round and both duel turns, verify the handset's big cue appears without scrolling, compare both scoreboards, then hide the active station and verify that the game stops and neither device silently rejoins. QR invitations, network discovery and shared play across federated servers are out of scope.

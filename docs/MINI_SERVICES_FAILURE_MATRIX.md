# Mini Services failure and recovery matrix

This matrix is the repository-side negative-case contract for issue #330.
It separates deterministic CI evidence from scenarios that require real
operating systems, networks or hardware.

Legend:

- **CI**: deterministic regression coverage exists in the repository.
- **External**: must be executed with the external acceptance protocol.
- **Boundary**: intentionally unsupported or cannot be inferred safely.

| Service | Negative case | Expected behavior | Evidence |
|---|---|---|---|
| DHCP | second start | keep one owned listener; no duplicate bind | CI: `test_mini_lifecycle` |
| DHCP | address/port unavailable | fail with bounded redacted diagnostic | CI: `test_mini_lifecycle` |
| DHCP | foreign DHCP detected | do not start; report conflict | CI: `test_mini_lifecycle` |
| DHCP | safety probe unavailable | fail closed; do not start | CI: `test_mini_lifecycle` |
| DHCP | configured link disappears/returns | enter waiting; restart once after return; explicit stop wins | CI: `test_mini_network_recovery` |
| DHCP | real competing server/LAN change | no lease disruption outside configured test LAN | External |
| DNS | second start/stop/restart | idempotent lifecycle; no orphan listener | CI: `test_mini_lifecycle`, benchmark smoke |
| DNS | configured link disappears/returns | waiting/restart without restarting unrelated services | CI: `test_mini_network_recovery` |
| DNS | invalid persisted config | preserve last good running service | CI: `test_mini_lifecycle` |
| DNS | external upstream outage | local state remains explicit; no claim of Internet health | External for real upstream behavior |
| TFTP | second start/stop/restart | one listener; bounded transfer tasks; cleanup on stop | CI: lifecycle/boot tests |
| TFTP | missing/corrupt boot asset | waiting/degraded or explicit error; no fabricated success | CI: network-boot tests |
| TFTP | real PXE transfer interruption | client-visible failure/retry is recorded, not boot success | External |
| Gateway | repeated stop | idempotent when owned rules are absent | CI: `test_gateway_lifecycle` |
| Gateway | rule read/delete permission failure | fail; retain ownership for explicit retry | CI: `test_gateway_lifecycle` |
| Gateway | failed Windows apply | do not silently report successful NAT | CI: `test_gateway_lifecycle` |
| Gateway | Linux rules/forwarding missing | degraded/unknown, never healthy by process presence alone | CI: `test_gateway_lifecycle` |
| Gateway | real packet path/link change | verify actual forwarding/NAT and recovery | External |
| SIP | second start/stop/restart | one bound listener; bounded retry | CI: lifecycle/SIP tests |
| SIP | nonce/replay pressure | bounded challenge store and replay counters | CI: `test_mini_security`, SIP tests |
| SIP | real client registration/network loss | recover without false registered/healthy state | External |
| HTTP/PXE | disabled/corrupt settings | assets/scripts unavailable; fail closed | CI: `test_mini_security` |
| HTTP/PXE | missing boot files | explicit waiting/degraded status | CI: network-boot tests |
| HTTP/PXE | client actually boots | only call successful after independent client reaches expected stage | External |
| Audio output | discovery/backend unavailable | HTTP 503 with controlled message; error type only in audit | CI: audio-output tests |
| Audio output | invalid registration/group/queue input | HTTP 400 without raw exception text | CI + diagnostic audit |
| Audio output | player stop failure | retain unresolved state; do not lose cleanup ownership | CI: audio-output tests |
| Audio output | audible playback | packet/process success alone is insufficient | External |
| Audio sender | missing capture/backend | controlled unavailable/invalid response; no raw exception | CI: audio lifecycle/validation tests |
| Audio sender | restart/failed configuration save | do not destroy previous valid state before preflight succeeds | CI: audio lifecycle tests |
| Audio sender | microphone disappears during capture | visible recovery/failure on real device | External |
| Audio receiver | missing playback backend | controlled unavailable state | CI: audio receiver/lifecycle tests |
| Audio receiver | process exits/device disappears | status must follow process/PCM evidence, not object existence | CI where synthetic; External for real device |
| Screen | unsupported/missing platform launcher | controlled user message; only error type audited | CI: screen tests + diagnostic audit |
| Screen | session/signaling bounds exceeded | reject boundedly; no unbounded signaling queue | CI: screen tests |
| Screen | native Miracast/Cast/WebRTC runtime | verify actual sender/receiver behavior | External |
| All | stale heartbeat | never report stale worker as running | CI: `test_mini_lifecycle` |
| All | raw exception contains a secret | user/log sink must not receive raw exception text | CI: `tools/mini_services_log_audit.py` plus targeted tests |
| All | repeated recovery attempts | bounded backoff; explicit stop cancels automatic restart | CI: lifecycle/recovery tests |
| All | long-run thread/socket/queue leak | no leak in bounded loopback smoke; multi-hour behavior | CI smoke + External endurance |

## External-only completion

Cases marked **External** are executed using
`docs/MINI_SERVICES_EXTERNAL_ACCEPTANCE.md`. A CI pass may not be substituted
for those rows.

## Change rule

When a Mini Service adds a new state-changing path, hardware backend or network
listener, add its relevant failure cases here together with a regression test or
an explicit external/boundary classification. “Process exists” is never enough
to claim end-to-end health.

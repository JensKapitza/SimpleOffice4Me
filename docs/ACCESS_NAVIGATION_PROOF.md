# Bedienhilfe / navigation assistance (private proof, not a release feature)

Initial proof base: `fe22bd4b6aa6dff8e99ada464c158725343a2261` (main after #1307).
The proof is separate from the Kids mini-game. It uses that merged branch's
hand-result parser and camera lifecycle without editing those modules.
No new runtime dependency, configuration, asset serving route, deployment,
network request for inference, audio, data writing or permission change.

## Reproduce with synthetic input

Using a Python version supported by the checkout, from the project root:

```sh
python -m pip install -e '.[test]'
PYTHONPATH=.:tests python -m unittest -v test_access_navigation_proof test_kids_hand_game test_template_accessibility
node --test tests/access_navigation_proof.test.mjs tests/kids_hand_game.test.mjs
```

An ordinary sign-in at `/` leads to `/documents/dashboard`. **Only in this local
proof checkout**, open `/documents/bedienhilfe` to inspect the fallback.
The start button is unavailable; no browser code wires up a test adapter, the
actual page denies camera/microphone and its links use normal authorization.
Check the three real destinations with keyboard, touch or mouse. An unsigned
request redirects to login; this endpoint does not mutate user data.

The Node controller tests use a fake local video track, fresh presented-frame
events, a synthetic two-dimensional iris feature and controlled poses. The
decoder also exercises 478-index synthetic landmarks, missing/multiple faces,
occlusion and uncertain/multiple hands; #1307's tests verify known poses via
the real classifier on synthetic geometry. The five targets are not inferred:
first four fit a session-only map, fifth is an independent test. A failed fit
or fifth point requires another calibration. No valid face or stale samples
clear target and gesture state. In the positive harness sequence:

1. Start is explicit and asks for one video stream with audio off.
2. Measure each point. After valid holdout, open hand and show two fingers
   (**Fokus-Siegel**). Only the armed transition plays the brief text
   **Blick-Ninja: Hokuspokus Fokus!**, without motion or sound. Plain active
   status stays when the cue disappears.
3. Hold the intended link position; the test selects Overview and shows it as
   the current target. Highlighting, blinking or simply waiting never commits.
4. With that current stable target, distinctly release with an open hand, then
   hold a fist (**Klick-Siegel**). One same-origin known app location opens
   only after stream/model/frame cleanup. The harness exercises both one
   selection and the separate mandatory release at the reducer level; no
   generic DOM element click or arbitrary URL is dispatched.
5. Pause, Esc, loss of visibility, resize and leaving discard calibration,
   release and pending frames. A subsequent start remeasures. Permission
   denied is visible; a late grant is shut down without model sampling.

This prototype deliberately does **not** include a standalone shortcut mode.
The camera architecture would require an explicit separately scoped activation
mode for that; it should not piggyback on a half-completed gaze calibration.

## Runtime and measurement gate before enabling a real use path

- Google's [Face Landmarker documentation](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker)
  and linked [model card](https://huggingface.co/litert-community/MediaPipe-FaceLandmarker)
  describe 478 face and iris landmarks and Apache-2.0. Google's published
  [iris mapping](https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/modules/face_landmark/tensors_to_face_landmarks_with_attention.pbtxt)
  numbers the centers 468 and 473. In this proof tree no assets are shipped.
  The official versioned
  [FP16 /1/ bundle](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task)
  is 3,758,596 bytes and independently downloaded verification matched
  [publisher SHA-256](https://huggingface.co/litert-community/MediaPipe-FaceLandmarker/blob/main/face_landmarker.task)
  `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`.
  Neither the landmark bundle documentation nor these tests establish screen
  gaze accuracy.
- Before exposure, provision the exact assets manually with notices and
  integrity verification; reuse #1307's same-origin pinned CPU runtime,
  serve checked immutable bytes only on authenticated fixed paths, allow
  camera and WASM only on the trusted, unframeable app page, explicitly
  disable blendshapes and face transforms, and use two-face detection with a
  reject-if-not-exactly-one gate and confidence thresholds. Run hand and face
  inference on the same frozen transient frame, not two live video reads.
- Next environment test: standard supported Chromium with the pinned local
  SDK/model, first verify real detected landmarks with a permitted
  pre-recorded fixture (including occlusion/empty/multiple-face negatives),
  assert no request outside the app, and run the complete Compose/HTTP
  navigation against a fixture with known gaze points if one exists. A static
  portrait or a repeated fake feed cannot verify calibrated screen gaze.
  This sandbox has no video device; vanilla Chromium cannot start due to
  blocked local socket creation. No real inference ran for this proof.
- Before using this as an accessibility feature, measured holdout accuracy,
  target disambiguation, false-selection rate, runtime latency (two models),
  head movement, glasses, screen size/zoom, fallback instructions and secure
  stream lifecycle require explicit consent and real device evidence. Do not
  store an identity template, camera image, per-person landmark trace,
  calibration or telemetry. If calibration fails on real gaze, retain
  keyboard/touch and improve calibration or consider purpose-built assistive
  hardware; do not switch to dwell or blink activation.

## Separate staged hand-only shortcut

The later hand-only slice is separate from the unavailable gaze proof above.
It is **off by default**, including on the Dashboard. It can be staged on a
trusted private deployment via `SIMPLEOFFICE_HAND_NAV_STAGING=1` with the same
verified local Hand Landmarker files already used by Fingerzauber (#1307). The
ordinary authenticated **Dashboard → Navigation assistance (test)** link is
visible only when the flag is on and the pinned assets verify. Scoped camera
permission and the live controls require the same checks. No second device, camera on the Dashboard, persistent
preference, external browser fetch for inference, recording, microphone or
generic mouse click. On current Chromium, the experimental shortcut can use
the optional SIMD build from the **same** pinned MediaPipe archive. Add
`--with-simd` to the ordinary provisioning command below; a browser must
also pass [MediaPipe's local SIMD capability check](https://github.com/google-ai-edge/mediapipe/blob/v0.10.21/mediapipe/tasks/web/core/fileset_resolver.ts.template). Existing four-asset
setups and unsupported browsers keep the slower portable path. Fingerzauber
continues to use the original portable runtime. Setting the flag does **not** enable gaze, the
Fokus-Siegel, the Blickcursor or the gaze-related Klick-Siegel/banner.

On the staged page, choose **Overview / Übersicht** (the default) or
**Documents / Dokumente**, then explicitly start the camera and arm once.
First show a clear frame without a hand, then hold up only the index finger
(the exact gesture used by the existing genuine public Hand fixture). That
one sign confirms the already chosen fixed destination, not arbitrary
elements. No web or OS pointer is controlled. Stop, Esc, tab focus loss,
navigation, 12 seconds without completion or 30 seconds of idle camera end
the media stream. A continuously held pose does not complete the necessary
release; uncertain, multiple-hand or stale evidence requires fresh release.
The mode is restricted to this test page and cannot move from an arbitrary
page to the Dashboard. All manual links still work.

Reproduce the candidate's dedicated browser gate in a supported local/CI
environment with Chromium and the test extra. Download/provision the three
**public pinned** original inputs as described in
[KIDS_HAND_GAME.md](KIDS_HAND_GAME.md); this is an isolated test setup, not
an application download. Then:

```sh
python -m pip install -e '.[test,web-export]'
python -m playwright install --with-deps --no-shell chromium
python tools/provision_kids_hand_game.py --package "$fixture/package.tgz" \
  --model "$fixture/model.task" --destination "$fixture/assets" --with-simd
python -m tools.make_kids_hand_test_video "$fixture/pointing.jpg" "$fixture"
python -m tools.make_hand_nav_test_video "$fixture"
node --test tests/access_hand_staging.test.mjs tests/access_navigation_proof.test.mjs
SIMPLEOFFICE_HAND_NAV_FIXTURES="$fixture" \
SIMPLEOFFICE_KIDS_HAND_GAME_ASSET_DIR="$fixture/assets" \
SIMPLEOFFICE_HAND_NAV_BROWSER_REQUIRED=1 SIMPLEOFFICE_HAND_NAV_EXPECT_SIMD=1 PYTHONPATH=.:tests \
  python -m unittest -v test_access_navigation_proof test_access_hand_staging_browser
```

Browser acceptance requires actual Hand Landmarker WASM and model files and
no inference stub: the generated fake-device video loops three seconds of
blank pixels then three seconds of the official public pointing photograph.
It must navigate once to each selected nonmutating app destination in
separate runs, with video-only media and explicit track stop before
navigation, no cross-origin requests, and no app mutations. Two separate
fixture feeds (blank-only and the already-held official pose) test that
neither alone triggers and Esc releases the track. The public photograph
covers one gesture and does not establish a physical user's reliability or
gaze calibration. The browser suite has been prepared but was not executed in
the local restricted environment (Chromium cannot open its startup socket).
18 local Python and 16 Node tests pass; admission with the real asset bytes
and composition are separate from inference. Do not claim a positive
browser run for this candidate until its own gate has passed.


The first hosted run reached the page and fetched all four pinned assets
(HTTP 200), but no preview became live. Its previous generic status did not
identify the exact native step that failed. The follow-up explicitly mutes the
video element before delayed playback, tests that call order, separates model,
permission/preview and inference failure statuses, and records the browser
phase and track state on the unchanged genuine fixture gate. This is not a
claim of a successful browser run or a proven root cause for the first run.

The follow-up run did isolate the observed failure: native
`getUserMedia` returned `NotSupportedError` after a successful model load
and before it yielded any track. Playwright's default headless is the
separate shell. Its upstream
[view delegate](https://github.com/chromium/chromium/blob/main/headless/lib/browser/headless_web_contents_impl.cc)
inherits the base
[media-access method](https://github.com/chromium/chromium/blob/main/content/public/browser/web_contents_delegate.cc),
which returns `NOT_SUPPORTED`. The hosted test now uses the documented
[full Chromium in headless mode](https://playwright.dev/python/docs/browsers#chromium-new-headless-mode).
This is a test-launcher correction, not a change to camera permissions or the
model. A fresh hosted pass is required to establish genuine app acceptance.

Native preview then worked in full Chromium, but the original frame loop
still reset confirmation before the pointing sequence could navigate.
A bounded check of the public synthetic fixture found true gesture matches.
It showed that inference delay was counted both as part of the previous
frame gap and as result age, and that the paint-clock handoff added up to
17 ms before inference. The application now consumes each fresh native
video-frame callback and keeps capture cadence, result age, and stall timing
separate. Release, 190 ms result age, 260 ms gap, four-frame sustained
gesture, and overall arming limits stay in force.

The exact
[full Chromium fixture gate](https://github.com/JensKapitza/SimpleOffice4Me/actions/runs/45762814549)
passed both targets with one video-only track stopped before navigation,
plus the already-held and blank negative cases. This remains staged hand-only
operation with a pinned public test picture, not physical webcam or gaze
acceptance. Temporary synthetic test diagnostics are not part of the gate.

The [uninstrumented control](https://github.com/JensKapitza/SimpleOffice4Me/actions/runs/45763265872)
subsequently timed out on the Dashboard confirmation, so the staged mode
is not considered reliable. In the
[paired real-model fixture experiment](https://github.com/JensKapitza/SimpleOffice4Me/actions/runs/45765245521/job/139724486500),
the default gate passed, but the instrumented 640-by-480 repeat opened just
one of four intended destinations. In both Dashboard repeats, the third
pointing result was late (191 or 194 ms) and correctly required release
again. The same real camera frames scaled to 320 by 240 in the test opened
all four selected destinations; the blank and held-pose negatives passed at
both sizes. The smaller input still did not show a timing reserve: those
positive navigations each took about ten seconds after the model load
request, and multiple timed samples from the held-pose control still
exceeded 190 ms. Do not interpret the small experiment as a guarantee for
a real webcam or change production video dimensions on that basis. The
temporary experiment is removed; the normal gate and freshness limits
are unchanged.

### SIMD timing comparison (simulated camera, genuine model)

The [paired Chromium measurement](https://github.com/JensKapitza/SimpleOffice4Me/actions/runs/45778736963/job/139768705929)
kept the exact model, CPU delegate, two-hand detection, 640-by-480 camera
source, and gesture guards. The two repeated portable cases opened 1 of 4
selected destinations. The two SIMD cases opened 4 of 4, on the first
sequence present in the fixture. The maximum age of the 20 selected
pointing samples was 77 ms at the unchanged callback-age gate, and
70.8 ms from the browser's reported presentation time. Blank-only and
already-held pointing samples never navigated in either mode. These
measurements are browser timings, not physical sensor latency.

The same archive supplies both independently pinned SIMD assets. The
existing signed-in resource route verifies their size and SHA-256. A
missing, changed or partially installed pair leaves the portable runtime
in use. The browser does not download or enable any new external service.
The normal model, camera, control timing and CSP are unchanged. Test
acceptance cannot establish gesture accuracy across physical cameras or
general gaze readiness; the feature remains opt-in staging only.

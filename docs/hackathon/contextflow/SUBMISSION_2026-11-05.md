# ContextFlow / Buroengel — Meta Global AI Developer Hackathon 2026

**Submission candidate — 5 November 2026, 00:xx CET.** Track: Indie Developer (confirm in the submission portal). Theme: **Build for Your World**.

> **Submission deadline:** 4 November 2026, 9:00 PM Eastern Standard Time = **5 November 2026, 03:00 CET**. The portal, not this document or a Git commit, is the place to submit. Confirm any event-specific fields there.

## Project title
**Buroengel — ContextFlow: An evidence-first AI assistant that turns scattered office work into reviewable next steps**

## Short pitch (English; ready to paste)

Small businesses lose track of the work between a customer email and the final invoice: a site visit produces findings, a replacement part remains unresolved, nobody follows up, and the next person cannot reconstruct why. Buroengel connects those records inside SimpleOffice4Me. ContextFlow uses confirmed case links, actor-scoped read tools and a Meta Muse Spark agent to turn disconnected records into **source-backed recommendations**. It does not silently send messages, book appointments, create invoices or charge customers. A person reviews and confirms any action. The demo uses a fully synthetic customer, visit and invoice on localhost; the Meta Contributor API path is restricted to synthetic data and explicit consent.

## Problem and solution

- **Input:** a customer asks for help; an on-site visit records several open findings, one contradictory parts-status note, a draft invoice and a five-day-old completed visit.
- **What happens:** a confirmed case link associates mail, customer, visit and invoice. A bounded Meta agent calls read-only tools, checks evidence, and suggests what to clarify or follow up.
- **Output:** a proposed next step, with evidence identifiers and source deep-links; approval remains with the user. Separate opt-in workflows can create a task exactly once, open the invoice editor or prepare a mail draft without sending it.
- **Why it matters:** less administrative context switching and fewer forgotten follow-ups, without surrendering control of customer-facing actions.

## Meta AI: exact, verifiable use

- **Model:** `muse-spark-1.3-contributor` through the Meta Model API Responses endpoint.
- **Code:** `app/contextflow/meta_agent.py` (bounded agent/tool loop); `app/contextflow/vision.py` (Meta image analysis and local Meta-only Ollama constraints); `app/contextflow/agent_web.py` and `app/contextflow/web.py` (web flow and approval boundaries).
- **Tools:** `get_case_context`, `get_visit_findings`, `get_invoice_state`, `get_calendar_context`; read-only, actor-scoped, evidence validated. The agent requires at least two evidence-tool calls and has a five-step bound.
- **Privacy:** cloud requests only with explicit consent and only against a verified isolated synthetic demo root; the Contributor model may use request data for training. The normal/local route does not need Meta cloud.
- **Proof already documented:** `docs/hackathon/contextflow/LIVE_VERIFICATION_2026-10-30.md` records a real Meta API call and HTTP 200 from the application's consented synthetic agent route. `docs/hackathon/contextflow/README.md` describes a separate two-step/four-tool live verification. These are historical records, **not evidence that a new run succeeded today**.
- **Fresh proof:** `tools/t20_live_probe.py` writes a machine-readable evidence JSON after exactly one explicit-consent synthetic Meta run. Do not present cached/mocked output as today's inference.

## Judge quickstart (Windows, fresh checkout)

1. Install the app's Python environment as described in the repository root `README.md`; configure `MODEL_API_KEY` outside the repository (process environment or parent `.env`).
2. In `cmd.exe` from repo root, set the Meta provider and launch a fresh local-only synthetic demo:

```bat
set CONTEXTFLOW_VISION_PROVIDER=meta
start.bat demo --port 5088 --fresh
```

3. Open `http://127.0.0.1:5088/contextflow/`. Login: `demo` / `demo-contextflow-1`. This fixed credential is for the **local synthetic demo only**; the server binds to `127.0.0.1`.
4. Navigate to `/contextflow/agent`, choose the confirmed synthetic case, read and check the Meta Contributor consent, and trigger one live proposal. Check model ID, number of steps, read tools, evidence IDs and the explicit approval boundary.
5. Optional independent proof in a second terminal (the demo must still be running):

```bat
.venv\Scripts\python.exe tools\t20_live_probe.py --base http://127.0.0.1:5088
```

The probe **makes a separate paid Meta inference call**; do not run it merely to prepare the camera. Save the JSON outside Git and check `T20 PASS` before citing fresh evidence. If Meta is unavailable, the `mock` provider demonstrates the interface, **not AI inference**. Local Ollama also requires an installed allowlisted Meta model; text-only Llama 3.2 is not an image model.

## Three-minute demo video
See [DEMO_VIDEO_2026-11-05.md](DEMO_VIDEO_2026-11-05.md) for a timed, ready-to-record script. Record actual screens and label every inference as live, historical, or mock. Suggested video length **3:00** (a practical target, not a claim about the organizer's mandatory length).

## What is new versus the pre-hackathon product

The existing SimpleOffice4Me provides domain records and stores. The hackathon contribution is the **ContextFlow layer, evidence contract, confirmed cross-module case links, deterministic follow-up evaluator, bounded Meta tool loop, consent and approval gates, synthetic seeder and demo tooling**. Existing mail, contacts, visits, invoicing and calendar modules are reused rather than falsely claimed as all newly created. Baseline and component breakdown: `docs/hackathon/contextflow/STATUS.md`, `README.md` and the reference baseline commit `af5a600bae1f526e61198edbec4d9022e742f228`.

## Implementation and reliability

- Backend: Python/Flask; actor-scoped source reads; bounded step count; server-side schema validation for proposal/evidence; read-only inference tools.
- Frontend: ContextFlow dashboard, agent proposal/evidence view, human confirmation path.
- Reproducibility: `tools/contextflow_demo.py` generates a fresh synthetic case without production writes.
- Tests and documentation: `tests/test_contextflow_*.py`, `docs/hackathon/contextflow/README.md`, `STATUS.md`, `T20_LIVE_PROOF.md`.
- Real limitation: browser E2E, current-commit live inference, real-world deployment and complete multiuser production hardening are **not established by this submission write-up**. The demo is a localhost prototype; do not expose the fixed demo account or Contributor-mode connection to real users.

## Submission checklist (human actions)

- [ ] Record the real three-minute browser demo; verify audio, visible source evidence, model provenance and approval boundary.
- [ ] If showing a live Meta run, check a fresh successful response and remove any key, cookie or personal data from the recording.
- [ ] Upload the resulting MP4/video URL according to the **actual portal fields**.
- [ ] Enter title, summary, track/theme, code repository and (if accepted) any team details.
- [ ] Submit in the official portal **before 03:00 CET** and verify the confirmation/receipt. A GitHub commit alone does not submit the project.

Repository: https://github.com/JensKapitza/SimpleOffice4Me

Official event: https://dev.meta.ai/events/global-hackathon

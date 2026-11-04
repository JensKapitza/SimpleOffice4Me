# ContextFlow / Buroengel — 3-minute demo video (recording script)

**Purpose:** Record a real product demonstration for Meta Global AI Developer Hackathon 2026. This is a **shot list and spoken English script**, not a claim that an MP4 has already been captured. The existing local demo is runnable using `start.bat demo --port 5088 --fresh`. See [SUBMISSION_2026-11-05.md](SUBMISSION_2026-11-05.md).

## Record only verifiable behavior

- Run with **synthetic data only**. Browser address `http://127.0.0.1:5088/contextflow/`.
- Meta provider requires `MODEL_API_KEY`, `CONTEXTFLOW_VISION_PROVIDER=meta` and explicit consent. If the model call is not successful, **say so** and show the existing historical evidence separately; never imply mock data is live inference.
- Keep browser, startup terminal and product code visible where possible. Do not record API keys, passwords, local private paths, or any real customer data.
- Reserve one take with a successful read-only tool-loop response. It may run longer than the target; capture the actual start and result, then make a **clearly signaled time cut** in editing if necessary. Do not replace with a prior run presented as a fresh result.
- If limited to screen recording, OBS Studio or the Windows capture facility can record the app. Verify the exported MP4 locally before uploading.

## Scene-by-scene running order

| Time | Screen | Spoken script (English, ready to read) |
|---|---|---|
| 00:00–00:18 | Title / browser dashboard: synthetic case | “An email, a site visit, three findings, and an unfinished invoice. In a small business, the next action often gets lost between these records. Meet Buroengel, powered by ContextFlow.” |
| 00:18–00:42 | Open seeded case in `/contextflow/`; show mail, visit, findings and invoice links | “SimpleOffice4Me already stores the business records. ContextFlow is the new connective layer: a confirmed case links the customer, incoming request, completed visit, unresolved findings and draft invoice. The demo customer is entirely synthetic.” |
| 00:42–01:06 | Show five-day follow-up / evidence hint | “Five days after the visit, there are unresolved findings. ContextFlow flags the follow-up deterministically and links every suggestion back to a source. It does not invent a repair or mark work complete.” |
| 01:06–01:43 | `/contextflow/agent`: consent checkbox, run agent, model and tools/evidence result | “Here I explicitly authorize a Meta Muse Spark 1.3 Contributor run, using synthetic data only. The agent calls bounded read-only tools for the case, visit, invoice and calendar. Its result is validated against evidence actually read in this run. These are recommendations, not autonomous commands.” |
| 01:43–02:10 | Focus result details: tool count, evidence identifiers, next action and deep-links | “The result shows the model, tool calls and supporting records. The proposed next step is to clarify the unresolved part and contact the customer. We can inspect the underlying findings instead of trusting an unexplained answer.” |
| 02:10–02:35 | Show review boundary, optional task creation or unsent mail draft, only if working in your local run | “Nothing is emailed, billed or changed just because the model suggested it. A person decides whether to create a task, open the invoice editor or prepare a message. The human remains accountable.” |
| 02:35–02:52 | Code / repo quick map, then return to product | “The new hackathon work is the case-link and evidence layer, the bounded Meta tool loop, the consent gates and the reproducible synthetic demo. The existing office system remains the source of truth.” |
| 02:52–03:00 | Product end frame, optional URL | “Buroengel: from scattered office context to the next justified action. Evidence first. Human approval always.” |

**Keep speaking naturally; the timestamps are guides rather than a guarantee.** If the model needs more time, record a complete real response before editing. Do not promise functionality that failed on the machine used.

## Startup / live run (Windows CMD)

Use a local Python venv with the project's dependencies installed. Set the API key only in your environment or a parent-directory `.env` (not in this repository).

```bat
set CONTEXTFLOW_VISION_PROVIDER=meta
start.bat demo --port 5088 --fresh
```

A successful banner says `Vision: Meta Muse Spark 1.3 Contributor (Meta Model API)`. **That banner proves configuration, not a completed API call.** Browse to `http://127.0.0.1:5088/contextflow/`, sign in with the synthetic demo account and then open `/contextflow/agent`.

Optional separate machine-readable live proof, **after** the camera recording (this makes its own API call):

```bat
.venv\Scripts\python.exe tools\t20_live_probe.py --base http://127.0.0.1:5088
```

Proof format: `t20-evidence-*.json` in the system temp directory. It records model IDs, tool count, evidence IDs, usage and duration, without intentionally recording secrets. Only cite its metrics when `T20 PASS` has actually appeared.

## Suggested on-screen captions (optional)

- “All demo data is synthetic”
- “Meta Muse Spark 1.3 Contributor — live request” **only during an actual live successful run**
- “Read-only tools → evidence-backed proposal”
- “No automatic email, invoice or payment”
- “Human approval is required”

## If the live Meta API is unavailable

Use the UI to demonstrate confirmed links and deterministic follow-up, and show `LIVE_VERIFICATION_2026-10-30.md` as **dated historical evidence**. Clearly label this as a fallback walkthrough, **not** a live Meta inference. The mock cannot be presented as meeting the Meta-inference criterion.

## Final recording quality check

- [ ] First 10 seconds explain the problem without code jargon
- [ ] Browser clearly shows the case, links and the agent's actual result
- [ ] Model ID and read-only tool/evidence output are visible, without secret values
- [ ] Explicit consent and approval boundary are visible
- [ ] File opens, audio is audible, text is legible, duration fits portal limit
- [ ] Project title, repository and submission description agree
- [ ] Actual upload and submission receipt are confirmed before deadline

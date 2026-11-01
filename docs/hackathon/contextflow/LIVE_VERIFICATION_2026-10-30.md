# ContextFlow live verification — 2026-10-30

## Meta Model API

- One short request to `POST https://api.meta.ai/v1/responses` using
  `muse-spark-1.3-contributor` returned the requested `META_API_READY` response.
- `MODEL_API_KEY` was read from the user-managed `.env` file outside the Git
  repository. Its value was not printed, logged, or added to the repository.

## Application path

- Started `tools/contextflow_demo.py` on `127.0.0.1:5101` with
  `CONTEXTFLOW_VISION_PROVIDER=meta` and a new synthetic demo root under the
  system temporary directory.
- Authenticated through the demo login page using its CSRF token. No security
  check was bypassed.
- `GET /contextflow/ml-status` reported provider `meta`, model
  `muse-spark-1.3-contributor`, key present, server consent enabled, and the
  model listed by Meta.
- Submitted one explicit-consent request to `POST /contextflow/agent/run` for
  a synthetic demo case. The route returned HTTP 200 with a proposal and no
  analysis error. The agent route returns a proposal requiring user approval;
  it does not create or send an action during analysis.
- The application log confirmed the login redirect, dashboard, agent page,
  and successful `POST /contextflow/agent/run` response.

## Scope of this evidence

This verifies a real API call and the application's synthetic Meta agent route,
not the complete browser interaction or every ContextFlow workflow. The
browser-visible demo is available at `http://127.0.0.1:5101/contextflow/agent`
while its local server remains running. No production data was used.

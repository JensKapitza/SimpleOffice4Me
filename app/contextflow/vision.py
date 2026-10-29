"""Image-based finding suggestions for ContextFlow (vision slice).

A photo plus an explicitly selected visit/asset yields a *draft* finding
suggestion. Drafts are never stored and never create records by themselves;
only an explicit user confirmation creates a finding through the existing
``SiteVisitStore.add_finding`` API. Raw image bytes stay in memory and are
never written to disk, logged, or forwarded anywhere except the selected
inference backend — and the Meta cloud backend only with explicit
per-request consent, an explicit server flag, an API key, and a verified
synthetic demo root. Real office data never leaves the machine.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.error
import urllib.request
from io import BytesIO
from typing import Any, BinaryIO
from urllib.parse import urlsplit

from PIL import Image
from PIL.Image import DecompressionBombError

from ..inventory import normalize_isbn
from ..site_visit_store import EVIDENCE_CLASSES, SiteVisitStore
from .actions import ApprovalStale
from .adapters import _require_actor
from .contracts import SuggestionDraft
from .demo import is_verified_demo_root

MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
TITLE_LIMIT = 240
DESCRIPTION_LIMIT = 12000
LOCALHOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
META_MODEL_API_ENDPOINT = "https://api.meta.ai/v1/responses"
META_MODEL_API_MODELS_ENDPOINT = "https://api.meta.ai/v1/models"
META_MODEL_API_MODEL = "muse-spark-1.3-contributor"
META_MODELS_TIMEOUT = 10.0
# Names are deliberately exact Ollama IDs. Custom aliases could point to
# non-Meta weights while claiming a Meta name, so inference fails closed.
META_OLLAMA_TEXT_MODELS = frozenset(
    {"llama3.2:1b", "llama3.2:3b", "llama3.2:latest"}
)
META_OLLAMA_VISION_MODELS = frozenset(
    {
        "llama3.2-vision",
        "llama3.2-vision:11b",
        "llama3.2-vision:90b",
        "llama3.2-vision:latest",
        "muse-glimmer",
        "muse-glimmer:latest",
        "muse-glimmer:30b",
    }
)
META_OLLAMA_MODELS = META_OLLAMA_TEXT_MODELS | META_OLLAMA_VISION_MODELS


class VisionError(ValueError):
    """Invalid image, invalid suggestion output, or unusable backend."""


class InferenceUnavailable(VisionError):
    """The selected local backend cannot be reached right now."""


class MockBlockedError(InferenceUnavailable):
    """Mock answers are forbidden where real classification is required."""


class InferenceTimeoutError(InferenceUnavailable):
    """The local backend did not answer within its time budget."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward Meta Model API credentials through an HTTP redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _ = (req, fp, code, msg, headers, newurl)
        return None


def require_meta_ollama_model(model: object, *, vision: bool = False) -> str:
    """Accept only explicitly supported Meta model IDs for local inference."""
    configured = str(model or "").strip().casefold()
    if configured not in META_OLLAMA_MODELS:
        raise VisionError(
            f"Das lokale Modell '{configured or '(leer)'}' ist nicht freigegeben. "
            "ContextFlow erlaubt zur Laufzeit ausschließlich Meta-Modelle: "
            "Llama 3.2 über Ollama oder Muse Glimmer."
        )
    if vision and configured not in META_OLLAMA_VISION_MODELS:
        raise VisionError(
            f"Das Meta-Modell '{configured}' verarbeitet nur Text, keine Bilder. "
            "Für Fotos bitte llama3.2-vision:11b oder muse-glimmer verwenden."
        )
    return configured


def validate_image(filename: object, content_type: object, stream: BinaryIO) -> bytes:
    """Read and validate an uploaded image; return the raw bytes.

    Enforces a filename, an allowlisted MIME type and suffix, a size cap,
    and real decodable pixels via Pillow. Raises VisionError otherwise.
    """
    name = str(filename or "").strip()
    mime = str(content_type or "").strip().lower()
    if not name or "." not in name:
        raise VisionError("a photo file is required")
    suffix = "." + name.rsplit(".", 1)[-1].lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise VisionError("only JPEG, PNG or WebP photos are accepted")
    if mime not in ALLOWED_MIME:
        raise VisionError("only JPEG, PNG or WebP photos are accepted")
    data = stream.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise VisionError("the photo is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise VisionError("the photo exceeds the 5 MB limit")
    try:
        with Image.open(BytesIO(data)) as img:
            img.verify()
            encoding = ALLOWED_MIME[mime]
            if img.format != encoding:
                raise VisionError("the photo content does not match its type")
    except VisionError:
        raise
    except (OSError, ValueError, SyntaxError, DecompressionBombError) as exc:
        raise VisionError(f"the photo cannot be decoded ({exc})") from None
    return data


def validate_suggestion(payload: object) -> SuggestionDraft:
    """Validate and normalize one suggestion object (never trusts the model)."""
    if not isinstance(payload, dict):
        raise VisionError("suggestion output must be a JSON object")
    title = str(payload.get("title", "") or "").strip()
    if not title or len(title) > TITLE_LIMIT:
        raise VisionError("suggestion needs a title of 1-240 characters")
    description = str(payload.get("description", "") or "").strip()
    if len(description) > DESCRIPTION_LIMIT:
        raise VisionError("suggestion description is too long")
    evidence_class = str(payload.get("evidence_class", "") or "").strip() or "unknown"
    if evidence_class not in EVIDENCE_CLASSES:
        raise VisionError("suggestion has an unknown evidence class")
    confidence = payload.get("confidence", 0)
    if isinstance(confidence, bool) or not isinstance(confidence, int):
        raise VisionError("suggestion confidence must be an integer 0-100")
    if not 0 <= confidence <= 100:
        raise VisionError("suggestion confidence must be an integer 0-100")
    return {
        "title": title,
        "description": description,
        "evidence_class": evidence_class,
        "confidence": confidence,
    }


OBJECT_ITEM_TYPES = frozenset(
    {
        "object", "book", "tool", "electrical-device", "machine", "vehicle",
        "it-device", "measuring-device", "ladder", "key", "furniture",
        "document", "spare-part", "consumable",
    }
)
OBJECT_NAME_LIMIT = 120
OBJECT_NOTE_LIMIT = 500


def validate_object_suggestion(payload: object) -> dict[str, Any]:
    """Validate one inventory object suggestion (never trusts the model)."""
    if not isinstance(payload, dict):
        raise VisionError("suggestion output must be a JSON object")
    name = str(payload.get("object_name", "") or "").strip()
    if not name or len(name) > OBJECT_NAME_LIMIT:
        raise VisionError("suggestion needs an object name of 1-120 characters")
    item_type = str(payload.get("item_type", "") or "").strip().lower()
    if item_type not in OBJECT_ITEM_TYPES:
        item_type = "object"
    confidence = payload.get("confidence", 0)
    if isinstance(confidence, bool) or not isinstance(confidence, int):
        raise VisionError("suggestion confidence must be an integer 0-100")
    if not 0 <= confidence <= 100:
        raise VisionError("suggestion confidence must be an integer 0-100")
    note = str(payload.get("note", "") or "").strip()
    if len(note) > OBJECT_NOTE_LIMIT:
        raise VisionError("suggestion note is too long")
    raw_isbn = payload.get("isbn", "")
    try:
        isbn = normalize_isbn(raw_isbn) if str(raw_isbn or "").strip() else ""
    except ValueError:
        isbn = ""
    return {
        "object_name": name,
        "item_type": item_type,
        "confidence": confidence,
        "note": note,
        "isbn": isbn,
    }


def _object_instruction(hint: dict[str, str]) -> str:
    scanned = hint.get("isbn") or hint.get("barcode") or ""
    scanned_part = f" Gescannte Kennung (nur Hinweis, nie in den Namen übernehmen): {scanned}." if scanned else ""
    return (
        "Benenne den Hauptgegenstand auf dem Foto für ein Inventar. Antworte "
        "NUR mit einem JSON-Objekt mit den Schlüsseln object_name (deutscher "
        "Gegenstandsname, max 120 Zeichen, z. B. Hammer), item_type (genau "
        "einer: object, book, tool, electrical-device, machine, vehicle, "
        "it-device, measuring-device, ladder, key, furniture, document, "
        "spare-part, consumable), confidence (0-100), note (kurz, deutsch), "
        "isbn (nur exakt sichtbare ISBN-Zeichen aus dem Bild, sonst leerer "
        "String; eine ISBN niemals raten, ableiten oder ergänzen)."
        + scanned_part
    )


def suggest_inventory_object(
    image: bytes,
    mime: str,
    hint: dict[str, str],
    settings: dict[str, Any],
    consent_meta: bool = False,
) -> dict[str, Any]:
    """Suggest an inventory object name; writes nothing, stores nothing.

    The optional scan hint (barcode/isbn, short strings) only guides the
    model and is never persisted. Cloud backends stay gated by
    ``select_provider``; the inventory UI offers no consent element, so
    callers there pass ``consent_meta=False`` and cloud is refused.
    """
    clean_hint = {
        key: str(hint.get(key, "") or "").strip()[:120]
        for key in ("barcode", "isbn")
    }
    provider = select_provider(settings, consent_meta, None)
    if provider.name == "mock":
        raise MockBlockedError(
            "Für Inventar-Vorschläge ist kein Meta-Modell verfügbar "
            "(Platzhalter sind hier nicht erlaubt). Bitte Ollama mit einem "
            "freigegebenen Vision-Modell einrichten."
        )
    context = {
        "mode": "object",
        "instruction": _object_instruction(clean_hint),
        "barcode": clean_hint["barcode"],
        "isbn": clean_hint["isbn"],
    }
    raw = provider.suggest(image, mime, context)
    return {
        "suggestion": validate_object_suggestion(raw),
        "provider": provider.name,
        "provider_label": provider.label,
        "model": getattr(provider, "resolved_model", None) or provider.model,
    }


def draft_fingerprint(visit_id: str, asset_id: str, draft: SuggestionDraft) -> str:
    """Bind a confirmation to the exact reviewed draft (accidental-change guard)."""
    canonical = json.dumps(
        [visit_id, asset_id, draft["title"], draft["description"],
         draft["evidence_class"], draft["confidence"]],
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class VisionProvider:
    """One inference backend behind a validated draft contract."""

    name = "base"
    label = "Unbekannt"

    def suggest(self, image: bytes, mime: str, context: dict[str, str]) -> dict[str, Any]:
        raise NotImplementedError

    def complete_text(self, prompt: str) -> dict[str, Any]:
        """Run one text-only JSON completion (Meta cloud only)."""
        raise NotImplementedError


class MockVisionProvider(VisionProvider):
    """Deterministic canned draft for tests and the labeled demo fallback."""

    name = "mock"
    label = "Demo-Mock (keine echte Analyse)"

    def suggest(self, image: bytes, mime: str, context: dict[str, str]) -> dict[str, Any]:
        _ = (image, mime)
        # Deliberately no object-mode canned output: a mock must never
        # fabricate classifications. Inventory callers block mock outright.
        where = context.get("asset_name") or "ohne Gerätebezug"
        return {
            "title": "Demo-Vorschlag bitte prüfen (Mock, synthetisch)",
            "description": (
                f"Platzhalter-Befund für {context.get('visit_title', '')} · {where}. "
                "Keine echte Bildanalyse — bitte selbst prüfen, dann bestätigen."
            ),
            "evidence_class": "unknown",
            "confidence": 0,
        }


def _localhost_only(url: str) -> str:
    """Return the URL only for plain-HTTP loopback backends, else refuse."""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise VisionError(f"invalid backend URL ({exc})") from None
    if parts.scheme != "http" or (parts.hostname or "") not in LOCALHOSTS:
        raise VisionError("local inference is limited to loopback HTTP")
    return url.rstrip("/")


OLLAMA_NUM_PREDICT = 2048
OLLAMA_STREAM_MAX_BYTES = 256 * 1024
OLLAMA_CONTENT_MAX_CHARS = 32 * 1024
OLLAMA_PROBE_TIMEOUT = 10.0
OLLAMA_PROBE_MAX_BYTES = 1024 * 1024

_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def local_urlopen(request: urllib.request.Request, timeout: float):
    """Open one loopback inference request without consulting proxies.

    Proxy environment variables (or OS proxy settings) are ignored so
    local document bytes never leave the machine through a proxy; the
    connection always goes directly to loopback. An empty-proxies
    ProxyHandler registers no scheme methods, so environment proxies
    cannot apply. The caller must already have validated the URL via
    :func:`_localhost_only`.
    """
    return _DIRECT_OPENER.open(request, timeout=timeout)


def ollama_chat_body(
    *,
    model: str,
    messages: list[dict[str, Any]],
    num_predict: int = OLLAMA_NUM_PREDICT,
) -> bytes:
    """Build one local /api/chat request body (streaming, bounded).

    ``stream`` stays true: only the streamed NDJSON path answers
    reliably for supported vision models over HTTP. The ``think``
    field is omitted entirely so each Meta model can use its default
    behavior. ``options`` carries only the hard
    ``num_predict`` cap: 2048 tokens leave room for reasoning plus
    the concise JSON answer inside a 4096-token window, given the
    bounded prompts (text excerpt at most 4000 chars, short
    templates, short scan hints).
    """
    return json.dumps(
        {
            "model": model,
            "messages": messages,
            "stream": True,
            "format": "json",
            "options": {
                "num_predict": max(1, min(int(num_predict), OLLAMA_NUM_PREDICT))
            },
        }
    ).encode("utf-8")


def parse_chat_stream(
    response: Any,
    *,
    timeout_seconds: int,
    deadline: float,
    max_bytes: int = OLLAMA_STREAM_MAX_BYTES,
    max_chars: int = OLLAMA_CONTENT_MAX_CHARS,
) -> str:
    """Read one NDJSON chat stream; return concatenated message.content.

    ``message.thinking`` is deliberately ignored: reasoning models spend
    most of their token budget thinking, and only ``content`` carries the
    JSON answer. Blank lines are skipped; every other line must be one
    JSON object. A closing ``done: true`` chunk plus nonempty content is
    required. Raw stream bytes and accumulated content are both capped.

    Raises InferenceTimeoutError (read/deadline exceeded),
    InferenceUnavailable (EOF without done, caps exceeded), or
    VisionError (malformed chunk, empty answer) — all German.
    """
    too_slow = (
        "Das lokale Modell hat nicht rechtzeitig geantwortet "
        f"(Zeitlimit: {timeout_seconds} Sekunden). "
        "Bitte später erneut versuchen."
    )
    too_long = (
        "Die Antwort des lokalen Modells überschreitet das "
        f"Stream-Limit ({max_bytes} Bytes). Bitte später erneut versuchen."
    )
    total = 0
    parts: list[str] = []
    content_len = 0
    done = False
    while True:
        if time.monotonic() > deadline:
            raise InferenceTimeoutError(too_slow)
        remaining = max_bytes - total
        if remaining <= 0:
            raise InferenceUnavailable(too_long)
        try:
            line = response.readline(remaining + 1)
        except TimeoutError:
            raise InferenceTimeoutError(too_slow) from None
        if not line:
            break
        if len(line) > remaining and not line.endswith(b"\n"):
            raise InferenceUnavailable(too_long)
        total += len(line)
        if total > max_bytes:
            raise InferenceUnavailable(too_long)
        text = line.strip()
        if not text:
            continue
        try:
            chunk = json.loads(text.decode("utf-8"))
        except ValueError as exc:
            raise VisionError(
                "lokale Inferenz antwortete ungültig "
                f"(beschädigtes Stream-Fragment: {exc})"
            ) from None
        if not isinstance(chunk, dict):
            raise VisionError(
                "lokale Inferenz antwortete ungültig (unerwartetes Stream-Fragment)"
            )
        message = chunk.get("message")
        if isinstance(message, dict):
            piece = message.get("content") or ""
            if piece:
                piece = str(piece)
                parts.append(piece)
                content_len += len(piece)
                if content_len > max_chars:
                    raise InferenceUnavailable(
                        "Die Antwort des lokalen Modells überschreitet das "
                        f"Inhalts-Limit ({max_chars} Zeichen). "
                        "Bitte später erneut versuchen."
                    )
        if chunk.get("done") is True:
            done = True
            break
    if not done:
        raise InferenceUnavailable(
            "Die Antwort des lokalen Modells ist unvollständig "
            "(kein Abschluss-Signal). Bitte später erneut versuchen."
        )
    answer = "".join(parts)
    if not answer.strip():
        raise VisionError(
            "Das lokale Modell hat leer geantwortet. Bitte später erneut versuchen."
        )
    return answer


def resolve_local_model(base_url: str, model: str, timeout: float) -> str:
    """Return the configured Meta model after a bounded Ollama probe.

    Nothing is ever downloaded; probes send only the model name over
    direct loopback. Probe failures fail open to the configured model;
    inference itself stays bounded by its own timeout and caps.
    """
    configured = require_meta_ollama_model(model)
    limit = min(OLLAMA_PROBE_TIMEOUT, max(1.0, float(timeout or 0)))
    try:
        probe = urllib.request.Request(
            base_url + "/api/show",
            data=json.dumps({"model": configured}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with local_urlopen(probe, timeout=limit) as response:
            payload = json.loads(response.read(OLLAMA_PROBE_MAX_BYTES).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise InferenceUnavailable(
                f"Das Modell '{configured}' ist nicht installiert "
                "(Ollama meldet: nicht gefunden). Bitte `ollama pull "
                f"{configured}` ausführen und erneut versuchen. Es wird "
                "nichts automatisch heruntergeladen."
            ) from None
        return configured
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return configured
    _ = payload
    return configured


class LocalVisionProvider(VisionProvider):
    """On-machine Ollama vision model; default backend, never cloud."""

    name = "local"
    label = "Meta-Modell lokal via Ollama"

    def __init__(self, base_url: str, model: str, timeout: int = 60) -> None:
        self.base_url = _localhost_only(base_url)
        self.model = require_meta_ollama_model(model)
        self.timeout = max(1, min(int(timeout or 60), 600))
        self.resolved_model: str | None = None

    def suggest(self, image: bytes, mime: str, context: dict[str, str]) -> dict[str, Any]:
        _ = mime
        require_meta_ollama_model(self.model, vision=True)
        model = resolve_local_model(self.base_url, self.model, self.timeout)
        self.resolved_model = model
        prompt = context.get("instruction") or (
            "Analysiere das Foto als Büro-Service-Befund. Antworte NUR mit "
            "einem JSON-Objekt mit den Schlüsseln title (max 240 Zeichen), "
            "description, evidence_class (observed|measured|derived|risk|"
            "unknown), confidence (0-100). Deutsch. KeineIDs erfinden. "
            f"Kontext: {context.get('visit_title', '')} / "
            f"{context.get('asset_name', 'ohne Gerätebezug')}."
        )
        body = ollama_chat_body(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                    "images": [base64.b64encode(image).decode("ascii")],
                }
            ],
        )
        request = urllib.request.Request(
            self.base_url + "/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        deadline = time.monotonic() + self.timeout
        try:
            with local_urlopen(request, timeout=self.timeout) as response:
                answer = parse_chat_stream(
                    response, timeout_seconds=self.timeout, deadline=deadline
                )
        except VisionError:
            raise
        except TimeoutError:
            raise InferenceTimeoutError(
                "Das lokale Modell hat nicht rechtzeitig geantwortet "
                f"(Zeitlimit: {self.timeout} Sekunden). "
                "Bitte später erneut versuchen."
            ) from None
        except (urllib.error.URLError, OSError) as exc:
            raise InferenceUnavailable(
                f"lokale Inferenz nicht erreichbar ({exc})"
            ) from None
        try:
            return json.loads(answer)
        except ValueError as exc:
            raise VisionError(
                f"lokale Inferenz lieferte kein JSON ({exc})"
            ) from None


def _responses_text(payload: Any) -> str:
    """Extract the first output_text from a Responses API payload."""
    if not isinstance(payload, dict):
        raise VisionError("Meta inference returned no suggestion JSON")
    status = payload.get("status")
    if status is not None and status != "completed":
        raise VisionError(f"Meta inference did not complete ({status})")
    output = payload.get("output")
    if not isinstance(output, list):
        raise VisionError("Meta inference returned no suggestion JSON")
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "output_text"
                and str(block.get("text") or "").strip()
            ):
                return str(block["text"])
    raise VisionError("Meta inference returned no suggestion JSON")


def check_meta_model(api_key: str, timeout: float = META_MODELS_TIMEOUT) -> tuple[bool, str]:
    """Check via GET /v1/models that muse-spark-1.3-contributor is listed.

    Returns (listed, short error). The key travels only in the
    Authorization header and never appears in errors, logs, or files.
    """
    if not api_key:
        return (False, "kein API-Schlüssel")
    request = urllib.request.Request(
        META_MODEL_API_MODELS_ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except TimeoutError:
        return (False, "Zeitüberschreitung")
    except (urllib.error.URLError, OSError, ValueError):
        return (False, "Modelliste nicht erreichbar")
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return (False, "ungültige Modelliste")
    listed = any(
        isinstance(entry, dict) and entry.get("id") == META_MODEL_API_MODEL
        for entry in entries
    )
    return (listed, "" if listed else "Modell nicht gelistet")


class MetaVisionProvider(VisionProvider):
    """Meta cloud inference: synthetic demo roots only (Contributor mode).

    Speaks Meta's official Responses endpoint (``POST /v1/responses``)
    with the pinned ``muse-spark-1.3-contributor`` model. Contributor
    content may be used to train future Meta models, so real roots are
    never allowed here — not even for text-only calls. Text goes in as
    ``input_text`` blocks, photos as ``input_image`` data URLs; the
    suggestion is parsed from the first ``output_text`` block and
    schema-validated by the caller. Image input (``suggest``) always
    stays behind the synthetic demo-root barrier, and the text-only
    contact path (``complete_text``) requires a verified synthetic
    demo root as well.
    """

    name = "meta"
    label = "Meta Muse Spark 1.3 Contributor (Meta Model API)"

    def __init__(
        self, api_key: str, endpoint: str, model: str, text_only: bool = False
    ) -> None:
        if not api_key:
            raise PermissionError(
                "Meta inference needs a Model API key"
            )
        if str(endpoint or "").strip() != META_MODEL_API_ENDPOINT:
            raise VisionError("Meta Model API endpoint is not the official endpoint")
        if str(model or "").strip() != META_MODEL_API_MODEL:
            raise VisionError("ContextFlow only allows the Meta Muse Spark 1.3 Contributor model")
        self.api_key = api_key
        self.endpoint = META_MODEL_API_ENDPOINT
        self.model = META_MODEL_API_MODEL
        self.text_only = text_only is True

    def _post_response(self, content: list[dict[str, Any]]) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": self.model,
                "input": [{"role": "user", "content": content}],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(_NoRedirectHandler())
            with opener.open(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise VisionError(f"Meta inference failed ({exc})") from None
        try:
            parsed = json.loads(_responses_text(payload))
        except ValueError as exc:
            raise VisionError(
                f"Meta inference returned no suggestion JSON ({exc})"
            ) from None
        if not isinstance(parsed, dict):
            raise VisionError("Meta inference returned no suggestion JSON")
        return parsed

    def suggest(self, image: bytes, mime: str, context: dict[str, str]) -> dict[str, Any]:
        if self.text_only:
            raise PermissionError(
                "Meta image inference is limited to a synthetic demo root"
            )
        prompt = context.get("instruction") or (
            "Analysiere das Foto als Büro-Service-Befund. Antworte NUR mit "
            "einem JSON-Objekt mit den Schlüsseln title (max 240 Zeichen), "
            "description, evidence_class (observed|measured|derived|risk|"
            "unknown), confidence (0-100). Deutsch. "
            f"Kontext: {context.get('visit_title', '')} / "
            f"{context.get('asset_name', 'ohne Gerätebezug')}."
        )
        return self._post_response(
            [
                {"type": "input_text", "text": prompt},
                {
                    "type": "input_image",
                    "image_url": (
                        f"data:{mime};base64,"
                        + base64.b64encode(image).decode("ascii")
                    ),
                },
            ]
        )

    def complete_text(self, prompt: str) -> dict[str, Any]:
        """Run one text-only JSON completion via the Meta Model API.

        Sends only the given prompt text (callers pass OCR text plus
        pasted user text; photo bytes never reach this call). The
        answer is parsed from the first ``output_text`` block and must
        be a JSON object; schema validation stays with the caller.
        """
        return self._post_response([{"type": "input_text", "text": str(prompt)}])


def select_provider(
    settings: dict[str, Any],
    consent_meta: bool,
    root: object,
) -> VisionProvider:
    """Pick the configured backend; Meta only with consent + synthetic root.

    The Meta Contributor path never runs on real roots: Contributor
    content may train future Meta models, so even text-only contact
    calls require a verified synthetic demo root. Local and mock
    providers are unaffected by the root.
    """
    from pathlib import Path as _Path

    choice = str(settings.get("provider", "local") or "local").strip().lower()
    if choice == "mock":
        return MockVisionProvider()
    if choice == "meta":
        if consent_meta is not True or settings.get("meta_consent") is not True:
            raise PermissionError("Meta inference needs explicit consent")
        key = str(settings.get("meta_api_key", "") or "")
        endpoint = str(
            settings.get("meta_endpoint", META_MODEL_API_ENDPOINT)
            or META_MODEL_API_ENDPOINT
        ).strip()
        model = str(
            settings.get("meta_model", META_MODEL_API_MODEL)
            or META_MODEL_API_MODEL
        ).strip()
        if not key or not endpoint or not model:
            raise PermissionError("Meta inference is not configured")
        if not is_verified_demo_root(_Path(str(root))):
            raise PermissionError("Meta inference is limited to a synthetic demo root")
        return MetaVisionProvider(key, endpoint, model)
    if choice != "local":
        raise VisionError(f"unknown vision provider: {choice}")
    return LocalVisionProvider(
        str(settings.get("ollama_url", "http://127.0.0.1:11434") or ""),
        str(settings.get("ollama_model", "") or ""),
        int(settings.get("ollama_timeout", 60) or 60),
    )


def _ollama_probe(url: str, model: str, timeout: float = 3.0) -> dict[str, Any]:
    """Check one loopback Ollama server; never proxy, never leak details."""
    try:
        base = _localhost_only(url)
    except VisionError:
        return {"reachable": False, "model_seen": False, "error": "kein Loopback-Backend"}
    try:
        request = urllib.request.Request(base + "/api/tags", method="GET")
        with local_urlopen(request, timeout) as response:
            payload = json.loads(response.read(OLLAMA_PROBE_MAX_BYTES).decode("utf-8"))
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        reason = "Zeitüberschreitung" if isinstance(exc, TimeoutError) else "nicht erreichbar"
        return {"reachable": False, "model_seen": False, "error": reason}
    except ValueError:
        return {"reachable": False, "model_seen": False, "error": "ungültige Antwort"}
    names = set()
    models = payload.get("models") if isinstance(payload, dict) else None
    if isinstance(models, list):
        for entry in models:
            if isinstance(entry, dict) and entry.get("name"):
                names.add(str(entry["name"]).strip().casefold())
    wanted = model.strip().casefold()
    seen = bool(wanted) and (wanted in names or any(name.startswith(wanted + ":") for name in names))
    return {"reachable": True, "model_seen": seen, "error": ""}


def ml_status(settings: dict[str, Any]) -> dict[str, Any]:
    """Summarize ML backend state for the header badge (no secrets).

    Reports the configured provider, loopback Ollama reachability plus
    whether the configured Meta model is installed, and Meta cloud
    readiness as booleans only — never keys, URLs with credentials,
    or tracebacks. When the Meta provider is selected and a key
    is present, the pinned model is verified once via GET /v1/models.
    """
    choice = str(settings.get("provider", "local") or "local").strip().lower()
    model = str(settings.get("ollama_model", "") or "").strip()
    url = str(settings.get("ollama_url", "") or "").strip()
    ollama: dict[str, Any] = {
        "url": url,
        "model": model,
        "reachable": False,
        "model_seen": False,
        "error": "",
    }
    if choice == "local" and url and model:
        ollama.update(_ollama_probe(url, model))
    elif choice == "local" and not model:
        ollama["error"] = "kein Modell konfiguriert"
    key = str(settings.get("meta_api_key", "") or "")
    meta: dict[str, Any] = {
        "model": META_MODEL_API_MODEL,
        "label": MetaVisionProvider.label,
        "key_present": bool(key),
        "server_consent": settings.get("meta_consent") is True,
        "model_listed": None,
        "models_error": "",
    }
    if choice == "meta" and key:
        listed, models_error = check_meta_model(key)
        meta["model_listed"] = listed
        meta["models_error"] = models_error
    return {
        "provider": choice,
        "ollama": ollama,
        "meta": meta,
    }


def _visit_assets(root: object, actor: str, visit_id: str) -> tuple[dict[str, Any], str]:
    """Read one visit with fresh actor rights; raises when revoked/missing."""
    from pathlib import Path as _Path

    record = SiteVisitStore(_Path(str(root))).get(visit_id, actor)
    return record, str(record.get("title", ""))


def prepare_draft(
    root: object,
    actor: str,
    *,
    visit_id: str,
    asset_id: str = "",
    filename: object = "",
    content_type: object = "",
    stream: BinaryIO | None = None,
    consent_meta: bool = False,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a reviewable draft; writes nothing, stores no image bytes."""
    actor = _require_actor(actor)
    if not isinstance(visit_id, str) or not visit_id.strip():
        raise ValueError("visit reference is required")
    asset = str(asset_id or "").strip()
    record, visit_title = _visit_assets(root, actor, visit_id)
    known = {
        str(item.get("asset_id", ""))
        for item in record.get("assets", []) or []
        if isinstance(item, dict) and item.get("asset_id")
    }
    if asset and asset not in known:
        raise ValueError("asset does not belong to this visit")
    asset_name = ""
    asset_kind = ""
    for item in record.get("assets", []) or []:
        if isinstance(item, dict) and str(item.get("asset_id", "")) == asset:
            asset_name = str(item.get("name", ""))
            asset_kind = str(item.get("kind", ""))
    if stream is None:
        raise VisionError("a photo file is required")
    image = validate_image(filename, content_type, stream)
    mime = str(content_type or "").strip().lower()
    provider = select_provider(settings or {}, consent_meta, root)
    context = {"visit_title": visit_title, "asset_name": asset_name or asset_kind}
    try:
        raw = provider.suggest(image, mime, context)
    finally:
        del image
    draft = validate_suggestion(raw)
    return {
        "visit_id": visit_id,
        "visit_title": visit_title,
        "asset_id": asset,
        "asset_name": asset_name,
        "asset_kind": asset_kind,
        "draft": draft,
        "fingerprint": draft_fingerprint(visit_id, asset, draft),
        "provider": provider.name,
        "provider_label": (
            f"{provider.label} · "
            f"{getattr(provider, 'resolved_model', None) or provider.model}"
            if provider.name == "local"
            else provider.label
        ),
    }


def confirm_draft(
    root: object,
    actor: str,
    *,
    visit_id: str,
    asset_id: str = "",
    title: str = "",
    description: str = "",
    evidence_class: str = "",
    confidence: object = 0,
    expected_fingerprint: str = "",
) -> dict[str, Any]:
    """Create the reviewed finding exactly as confirmed; revalidates rights.

    Raises ApprovalStale when the submitted draft no longer matches the
    reviewed fingerprint, and propagates PermissionError/ValueError when
    visit or asset rights were revoked since the review.
    """
    from pathlib import Path as _Path

    actor = _require_actor(actor)
    if not isinstance(visit_id, str) or not visit_id.strip():
        raise ValueError("visit reference is required")
    asset = str(asset_id or "").strip()
    if not isinstance(expected_fingerprint, str) or not expected_fingerprint.strip():
        raise ValueError("draft fingerprint is required")
    draft = validate_suggestion(
        {
            "title": title,
            "description": description,
            "evidence_class": evidence_class,
            "confidence": confidence,
        }
    )
    if draft_fingerprint(visit_id, asset, draft) != expected_fingerprint:
        raise ApprovalStale("draft changed since review")
    record, _ = _visit_assets(root, actor, visit_id)
    known = {
        str(item.get("asset_id", ""))
        for item in record.get("assets", []) or []
        if isinstance(item, dict) and item.get("asset_id")
    }
    if asset and asset not in known:
        raise ValueError("asset does not belong to this visit")
    values: dict[str, Any] = {
        "title": draft["title"],
        "description": draft["description"],
        "status": "unreviewed",
        "evidence_class": draft["evidence_class"],
        "confidence": draft["confidence"],
    }
    if asset:
        values["asset_id"] = asset
    after = SiteVisitStore(_Path(str(root))).add_finding(visit_id, actor, values)
    created = after["findings"][-1]
    return {
        "finding_id": str(created.get("finding_id", "")),
        "visit_id": visit_id,
        "asset_id": asset,
        "title": draft["title"],
    }

"""Local-AI tag suggestions for imported documents (Bueroengel).

Reads existing metadata plus locally extracted text, asks the on-machine
Meta Ollama model "Welche Tags wuerdest du hinzufuegen?", and returns concrete
tag proposals with short justifications for explicit user confirmation.
Images get a short description plus object hints through a separate
local vision call, so text tags never wait for vision. Suggestion
calls store nothing; applying tags uses the normal DocumentStore path
after confirmation.
"""

from __future__ import annotations

import io
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..document_store import DocumentStore
from ..preview_service import TEXT_SUFFIXES
from ..safe_paths import resolve_file_under
from ..v2.contracts import LogicalObjectId
from ..v2.storage_runtime import storage_for
from . import vision
from .vision import (
    ALLOWED_SUFFIXES as IMAGE_SUFFIXES,
)
from .vision import (
    InferenceUnavailable,
    InferenceTimeoutError,
    MAX_IMAGE_BYTES,
    MockBlockedError,
    VisionError,
    select_provider,
    validate_image,
)

PROMPT_TEXT_CHARS = 4000
MAX_STORED_TEXT_CHARS = 8000
MAX_VERIFIED_READ_BYTES = 256 * 1024
MAX_SUGGESTIONS = 5
MAX_CONFIRM_TAGS = 20
TAG_LIMIT = 60
REASON_LIMIT = 240
EVIDENCE_LIMIT = 160
DESCRIPTION_LIMIT = 500
MAX_OBJECTS = 1
OBJECT_LIMIT = 40
TAG_RE = re.compile(r"^[\w][\w .\-/:]{0,59}$")
OBJECT_RE = re.compile(r"^[\w][\w .\-/()]{0,39}$")
IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def image_mime_type(filename: str) -> str:
    """Return the MIME type for a vision-supported suffix, else ""."""
    return IMAGE_MIME_TYPES.get(Path(str(filename)).suffix.casefold(), "")

IMAGE_INSTRUCTION = (
    "Du bist der Bueroengel. Beschreibe das Bild in einem kurzen deutschen "
    "Satz (max. 500 Zeichen). Nenne dazu hoechstens EIN klar erkennbares, "
    "ganzes Hauptobjekt als kurzes Label (z. B. Bank): nur ein "
    "eigenstaendiges Objekt, keine Teile (Sitzflaeche), keine Materialien "
    "(Holz), keine Masse, Farben oder Eigenschaften. Das Label muss das "
    "im Satz klar benannte Hauptobjekt woertlich wiederholen; nennt der "
    "Satz kein eindeutiges Objekt, bleibt die Liste leer. Bei Unsicherheit "
    "kein Label (leere Liste). Antworte NUR mit einem JSON-Objekt mit den "
    "Schluesseln description und objects (Liste, hoechstens 1 Eintrag)."
)


class DocumentNotFoundError(ValueError):
    """Unknown or invisible document reference."""


class DocTagError(ValueError):
    """Unreadable image bytes or unusable document input for suggestions."""


def stored_text(document: dict[str, Any]) -> tuple[str, str]:
    """Return capped stored extraction plus its source, or ("", "none")."""
    extracted = str(document.get("extracted_text", "") or "").strip()
    if extracted:
        return extracted[:MAX_STORED_TEXT_CHARS], "extracted"
    ocr = str(document.get("ocr_text", "") or "").strip()
    if ocr:
        return ocr[:MAX_STORED_TEXT_CHARS], "ocr"
    return "", "none"


def verified_file_text(*, root: object, actor: str, document: dict[str, Any]) -> str:
    """Read small verified UTF-8 text files; return "" when unusable."""
    if Path(str(document.get("last_path", ""))).suffix.casefold() not in TEXT_SUFFIXES:
        return ""
    try:
        storage = storage_for(str(root), str(actor))
        object_id = LogicalObjectId(str(document["document_id"]))
        description = storage.stat(object_id)
        if not description.ok:
            return ""
        if description.value.size > MAX_VERIFIED_READ_BYTES:
            return ""
        spool = io.BytesIO()
        copied = storage.copy_verified_range_to(
            object_id, spool, start=0, length=description.value.size
        )
        if (
            not copied.ok
            or copied.value.size != description.value.size
            or copied.value.version != description.value.version
        ):
            return ""
        content = spool.getvalue()
        if len(content) != description.value.size or any(
            (value < 32 and value not in {9, 10, 12, 13}) or value == 127
            for value in content
        ):
            return ""
        text = content.decode("utf-8-sig")
    except (OSError, RuntimeError, TypeError, ValueError, UnicodeDecodeError):
        return ""
    return text.strip()[:MAX_STORED_TEXT_CHARS]


def document_text(*, root: object, actor: str, document: dict[str, Any]) -> tuple[str, str]:
    """Return prompt-sized local text plus where it came from."""
    text, source = stored_text(document)
    if text:
        return text[:PROMPT_TEXT_CHARS], source
    text = verified_file_text(root=root, actor=actor, document=document)
    if text:
        return text[:PROMPT_TEXT_CHARS], "file"
    return "", "none"


def _is_image_document(document: dict[str, Any]) -> bool:
    """Return True when the stored file has a vision-supported suffix."""
    return Path(str(document.get("last_path", ""))).suffix.casefold() in IMAGE_SUFFIXES


def _require_local_provider(settings: dict[str, Any], *, purpose: str) -> Any:
    """Select the real local provider; refuse mock placeholders."""
    choice = str(settings.get("provider", "local") or "local").strip().lower()
    if choice == "local" and not str(settings.get("ollama_model", "") or "").strip():
        raise InferenceUnavailable(
            "Kein freigegebenes Meta-Modell konfiguriert. Bitte ein Ollama-Modell wählen."
        )
    provider = select_provider(settings, False, None)
    if provider.name == "mock":
        raise MockBlockedError(
            f"Für {purpose} ist kein Meta-Modell verfügbar "
            "(Platzhalter sind hier nicht erlaubt). Bitte Ollama mit "
            "einem freigegebenen Meta-Modell einrichten."
        )
    return provider


def _v2_image_bytes(*, root: object, actor: str, document: dict[str, Any]) -> bytes | None:
    """Return V2 image bytes, or None when V2 holds no usable object.

    A present V2 object stays authoritative (size and copy errors
    raise); only a missing or failing V2 object falls through to
    the real Smart Folder file below DOCUMENT_ROOT.
    """
    try:
        storage = storage_for(str(root), str(actor))
        object_id = LogicalObjectId(str(document["document_id"]))
        description = storage.stat(object_id)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    if not description.ok:
        return None
    if description.value.size > MAX_IMAGE_BYTES:
        raise DocTagError("Das Bild ist größer als 5 MiB und wird nicht analysiert.")
    spool = io.BytesIO()
    try:
        copied = storage.copy_verified_range_to(
            object_id, spool, start=0, length=description.value.size
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise DocTagError(f"Die Bilddatei kann nicht gelesen werden ({exc})") from None
    if not copied.ok:
        raise DocTagError("Die Bilddatei ist derzeit nicht verfügbar.")
    return spool.getvalue()


def _filesystem_image_bytes(*, root: object, name: str) -> bytes:
    """Read bounded image bytes below DOCUMENT_ROOT through the safe boundary.

    Traversal, symlink/path escapes and non-files are rejected; files
    over 5 MiB are refused before their bytes are buffered.
    """
    try:
        path = resolve_file_under(str(root), name)
    except ValueError as exc:
        raise DocTagError(f"Der Bildpfad ist ungültig ({exc})") from None
    except OSError:
        raise DocTagError("Die Bilddatei ist derzeit nicht verfügbar.") from None
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_IMAGE_BYTES + 1)
    except OSError:
        raise DocTagError("Die Bilddatei ist derzeit nicht verfügbar.") from None
    if len(data) > MAX_IMAGE_BYTES:
        raise DocTagError("Das Bild ist größer als 5 MiB und wird nicht analysiert.")
    return data


def document_image_bytes(*, root: object, actor: str, document: dict[str, Any]) -> bytes | None:
    """Return validated image bytes, None for non-images.

    V2 storage wins when it holds the object; otherwise the real Smart
    Folder file below DOCUMENT_ROOT is read through the safe-path
    boundary. Raises DocTagError with a German message when an actual
    image cannot be used (too large, unavailable, undecodable,
    unsafe path).
    """
    name = str(document.get("last_path", ""))
    if not _is_image_document(document):
        return None
    data = _v2_image_bytes(root=root, actor=actor, document=document)
    if data is None:
        data = _filesystem_image_bytes(root=root, name=name)
    try:
        return validate_image(name, image_mime_type(name), io.BytesIO(data))
    except VisionError as exc:
        raise DocTagError(f"Das Bild ist ungültig ({exc})") from None


def build_tag_prompt(*, filename: str, tags: list[str], folder_hint: str, text: str) -> str:
    """Build the local tag prompt from metadata plus extracted text."""
    existing = ", ".join(tags) if tags else "keine"
    excerpt = text.strip()[:PROMPT_TEXT_CHARS] if text.strip() else "(kein Textinhalt vorhanden)"
    return (
        f"Datei: {filename}\n"
        f"Ordner: {folder_hint or 'unbekannt'}\n"
        f"Vorhandene Tags: {existing}\n"
        f"Textauszug:\n{excerpt}\n\n"
        "Welche Tags würdest du hinzufügen? Antworte NUR mit einem "
        'JSON-Objekt {"tags": [{"tag": "...", "reason": "...", "evidence": "..."}]} '
        "mit höchstens 5 Vorschlägen. Jeder Tag ist deutsch, kurz (max. 60 "
        "Zeichen), hochwertig und eigenständig: keine Varianten, keine "
        "Plural- oder Synonym-Dubletten, keine Füllwörter (aus, und, mit), "
        "keine reinen Dateinamen-Floskeln (Notizen, Scan, Foto). "
        "Jeder Tag kommt wörtlich (bis auf "
        "Groß-/Kleinschreibung) in Dateiname oder Text vor und ist durch "
        "evidence belegt: ein kurzes wörtliches Zitat daraus (max. 160 "
        "Zeichen), das den Tag trägt; ohne Beleg kein Tag, nichts erfinden. "
        "Jede Begründung ist ein kurzer deutscher Satz (max. 240 Zeichen)."
    )


def complete_text(provider: Any, prompt: str) -> dict[str, Any]:
    """Run one short streaming text-only JSON completion (capped)."""
    model = vision.resolve_local_model(provider.base_url, provider.model, provider.timeout)
    provider.resolved_model = model
    body = vision.ollama_chat_body(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    request = urllib.request.Request(
        provider.base_url + "/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    deadline = time.monotonic() + provider.timeout
    try:
        with vision.local_urlopen(request, timeout=provider.timeout) as response:
            answer = vision.parse_chat_stream(
                response, timeout_seconds=provider.timeout, deadline=deadline
            )
    except VisionError:
        raise
    except TimeoutError:
        raise InferenceTimeoutError(
            "Das lokale Modell hat nicht rechtzeitig geantwortet "
            f"(Zeitlimit: {provider.timeout} Sekunden). "
            "Bitte später erneut versuchen."
        ) from None
    except (urllib.error.URLError, OSError) as exc:
        raise InferenceUnavailable(f"lokale Inferenz nicht erreichbar ({exc})") from None
    try:
        parsed = json.loads(answer)
    except ValueError as exc:
        raise VisionError(f"lokale Inferenz lieferte kein JSON ({exc})") from None
    if not isinstance(parsed, dict):
        raise VisionError("lokale Inferenz lieferte kein JSON-Objekt")
    return parsed


def _evidence_key(text: str) -> str:
    """Normalize a quote for substring matching (case/space/quote blind)."""
    folded = " ".join(str(text or "").split()).casefold()
    return folded.strip(" \t\"'„“”‚‘`´")


def _phrase_key(text: str) -> str:
    """Normalize a tag phrase (case/separator/punctuation blind)."""
    return "".join(char for char in str(text or "").casefold() if char.isalnum())


# Weak function words: closed-class German words plus unambiguous
# auxiliary/modal forms. Never useful as standalone tags. Deliberately
# excludes noun-colliding verbs (machen, gehen, stehen, zeigen, ...),
# all haben/sollen forms (Soll/Haben are accounting terms) and "muss"
# ("ein Muss"). Compared via _phrase_key(); sets dedupe spellings.
TAG_STOPWORDS_RAW = (
    "der", "die", "das", "den", "dem", "des",
    "ein", "eine", "einer", "einem", "einen", "eines",
    "kein", "keine", "keiner", "keinem", "keinen", "keines",
    "dieser", "diese", "dieses", "diesen", "diesem",
    "jener", "jene", "jenes", "jenen", "jenem",
    "jeder", "jede", "jedes", "jeden", "jedem",
    "alle", "alles", "allem", "allen", "aller", "all",
    "andere", "anderem", "anderen", "anderer", "anderes",
    "beide", "beiden", "beider", "beides",
    "manche", "mancher", "manches", "manchem", "manchen",
    "welche", "welcher", "welches", "welchem", "welchen",
    "solche", "solcher", "solches", "solchem", "solchen",
    "derselbe", "dieselbe", "dasselbe", "selbe", "selben",
    "derjenige", "diejenige", "dasjenige", "derer", "deren", "dessen",
    "folgende", "folgender", "folgendes", "folgendem", "folgenden",
    "obige", "obiger", "obiges", "obigem", "obigen",
    "genannte", "genannter", "genanntes", "genanntem", "genannten",
    "besagte", "besagter", "besagtes", "besagtem", "besagten",
    "jeweilige", "jeweiliger", "jeweiliges", "jeweiligem", "jeweiligen",
    "mehrere", "mehreren", "mehrerer", "mehrerem",
    "einzelne", "einzelner", "einzelnes", "einzelnem", "einzelnen",
    "einzeln", "gesamte", "gesamter", "gesamtes", "gesamtem", "gesamten",
    "sämtliche", "sämtlicher", "sämtliches", "sämtlichem", "sämtlichen",
    "jegliche", "jeglicher", "jegliches", "jeglichem", "jeglichen",
    "keinerlei", "dergleichen", "desgleichen",
    "vorige", "voriger", "voriges", "vorigem", "vorigen",
    "letzte", "letzter", "letztes", "letztem", "letzten",
    "nächste", "naechste", "nächster", "naechster",
    "nächstes", "naechstes", "nächstem", "naechstem",
    "nächsten", "naechsten",
    "weitere", "weiterer", "weiteres", "weiterem", "weiteren",
    "sonstige", "sonstiger", "sonstiges", "sonstigem", "sonstigen",
    "irgendwelche", "irgendwelcher", "irgendwelches",
    "irgendwelchem", "irgendwelchen",
    "irgendeine", "irgendeiner", "irgendeinem", "irgendeinen",
    "irgendetwas", "irgendjemand", "irgendwann", "irgendwo", "irgendwie",
    "ab", "an", "auf", "aus", "ausser", "außer", "bei", "beim",
    "durch", "für", "fuer", "gegen", "gegenüber", "gegenueber",
    "hinter", "im", "in", "mit", "nach", "neben", "ohne", "per",
    "pro", "seit", "statt", "trotz", "über", "ueber", "um",
    "unter", "vom", "von", "vor", "während", "waehrend", "wegen",
    "wider", "zufolge", "entsprechend", "gemäß", "gemaess", "laut",
    "minus", "plus", "versus", "vs", "kontra",
    "zwischen", "zu", "zum", "zur", "via", "samt", "entlang",
    "innerhalb", "ausserhalb", "oberhalb", "unterhalb",
    "am", "ans", "aufs", "fürs", "ins", "vors",
    "übers", "uebers", "unters",
    "aber", "als", "auch", "bevor", "bis", "da", "dann", "dass",
    "denn", "doch", "entweder", "falls", "indem", "jedoch",
    "kaum", "nachdem", "ob", "obgleich", "obwohl", "oder",
    "sobald", "sofern", "solange", "sondern", "sodass", "sowie",
    "sowohl", "und", "weder", "weil", "wenn", "wobei", "zwar",
    "schon", "noch", "nur", "sehr", "mehr", "weniger", "wieder",
    "mal", "wohl", "halt", "eben", "etwa", "quasi", "also",
    "ja", "nein", "bitte", "danke", "gern", "gerne", "vielleicht",
    "fast", "ganz", "gar", "genug", "bereits", "längst",
    "immerhin", "jedenfalls", "trotzdem", "dennoch", "allerdings",
    "sowieso", "beinahe", "nahezu", "durchaus", "lediglich",
    "bloss", "bloß", "allein", "sogar", "selbst", "selber",
    "zumindest", "wenigstens", "mindestens", "höchstens", "hoechstens",
    "beispielsweise", "beziehungsweise", "gegebenenfalls",
    "zusammen", "gemeinsam", "völlig", "voellig",
    "direkt", "sofort", "bald", "später", "spaeter",
    "früher", "frueher", "kürzlich", "kuerzlich", "neulich",
    "bisher", "bislang", "derzeit", "zurzeit", "momentan",
    "augenblicklich", "gegenwärtig", "gegenwaertig", "aktuell",
    "vorläufig", "vorlaeufig", "vorerst", "zunächst", "zunaechst",
    "anfangs", "schliesslich", "schließlich", "endlich",
    "zuletzt", "letztlich",
    "erstens", "zweitens", "drittens", "viertens", "fünftens",
    "fuenftens", "sechstens", "siebtens", "achtens", "neuntens",
    "zehntens",
    "ich", "du", "er", "sie", "es", "wir", "ihr", "man",
    "mich", "dich", "sich", "uns", "euch",
    "mir", "dir", "ihm", "ihnen", "einander",
    "mein", "meine", "meiner", "meinem", "meinen", "meines",
    "dein", "deine", "deiner", "deinem", "deinen", "deines",
    "sein", "seine", "seiner", "seinem", "seinen", "seines",
    "ihre", "ihren", "ihrer", "ihrem", "ihres",
    "unser", "unsere", "unserem", "unseren", "unserer", "unseres",
    "euer", "eure", "euere", "euerem", "eueren", "euerer", "eures",
    "was", "wer", "wen", "wem", "wessen",
    "etwas", "nichts", "niemand", "niemanden", "jemand",
    "jemanden", "jedermann", "einige", "einigen", "einiger", "einigem",
    "hier", "dort", "dahin", "daher", "darum", "dazu", "davon",
    "dafür", "dagegen", "damit", "danach", "daneben", "darüber",
    "darunter", "davor", "dazwischen", "dahinter", "dorthin",
    "hierhin", "heute", "gestern", "morgen", "jetzt", "nun",
    "damals", "vorher", "nachher", "gerade", "immer", "oft",
    "selten", "nie", "niemals", "stets", "jeweils", "sonst",
    "irgendwo", "überall", "nirgendwo",
    "dabei", "dadurch", "daran", "darauf", "daraus", "darin",
    "hierbei", "hiermit", "hiervon", "hierzu", "hierfür", "hierfuer",
    "hiergegen", "hierdurch", "hierüber", "hierueber", "hierunter",
    "hieran", "hierauf", "hieraus",
    "wo", "woher", "wohin", "womit", "wovon", "wozu", "worauf",
    "woraus", "wodurch", "wofür", "worüber", "worunter", "woran",
    "worin", "worum", "wovor", "wogegen", "wann", "warum",
    "wieso", "weshalb",
    "ist", "sind", "war", "waren", "wird", "werden", "wurde",
    "wurden", "worden", "wäre", "wären", "würde", "wuerde",
    "würden", "wuerden", "würdest", "wuerdest", "würdet", "wuerdet",
    "werde", "wirst", "sei", "seien", "seid", "bin", "bist",
    "gebe", "gibst", "gibt", "gib", "gebt", "geben", "gabt",
    "gab", "gegeben",
    "kann", "kannst", "können", "koennen", "könnt",
    "musst", "müssen", "muessen", "müsst",
    "darf", "darfst", "dürfen", "duerfen", "dürft",
    "will", "willst", "wollen", "wollt", "wollte", "wollten",
    "mag", "magst", "möchte", "moechte", "möchtest", "moechtest",
    "möchtet", "moechtet", "möchten", "moechten",
    "etc", "etcetera", "usw", "bzw", "dh", "zb", "ua", "ggf",
    "evtl", "ca", "inkl", "exkl", "zzgl", "sog", "vgl",
    "nr", "nummer",
)
TAG_STOPWORDS = frozenset(_phrase_key(word) for word in TAG_STOPWORDS_RAW)

# Generic filename stems: words that name the file, not its content.
# Dropped only when the body text itself does not carry them; a real
# body-text quote keeps even these. Trailing digits are stripped
# before comparison ("scan2" -> "scan"). Content-bearing words
# (Rechnung, Mahnung, bezahlt, unterschrieben, ...) stay out.
GENERIC_FILENAME_STEMS_RAW = (
    "notiz", "notizen", "dokument", "dokumente", "datei", "dateien",
    "dateiname", "pfad", "verzeichnis", "ordner", "ablage",
    "scan", "scans", "gescannt", "gescannte",
    "bild", "bilder", "foto", "fotos", "aufnahme", "aufnahmen",
    "screenshot", "screenshots", "bildschirmfoto", "bildschirmfotos",
    "seite", "seiten", "deckblatt", "titelblatt",
    "kopie", "kopien", "anhang", "anhänge", "anhaenge",
    "anlage", "anlagen", "beilage", "beilagen",
    "neu", "neue", "neuer", "neues", "neuen", "neuem",
    "neueste", "neuesten", "neuestem",
    "final", "finale", "endgültig", "endgueltig", "fertig",
    "entwurf", "entwürfe", "entwuerfe",
    "vorlage", "vorlagen", "muster",
    "test", "tests", "beispiel", "beispiele",
    "leer", "leere", "leerer", "leeres", "leerem", "leeren",
    "voll", "volle", "voller", "volles", "vollem", "vollen",
    "download", "downloads", "heruntergeladen",
    "import", "importe", "importiert",
    "export", "exporte", "exportiert",
    "backup", "backups", "sicherung", "sicherungen",
    "version", "versionen", "cache", "temp", "tmp", "bak", "old",
    "temporär", "temporaer",
    "unbenannt", "unbenannte", "namenlos",
    "untitled", "document", "documents", "file", "files",
    "image", "images", "photo", "photos", "picture", "pictures",
    "scanned", "copy", "copies", "new", "draft", "drafts",
    "template", "templates", "sample", "samples",
    "attachment", "attachments", "upload", "uploaded",
)
GENERIC_FILENAME_STEMS = frozenset(
    _phrase_key(word) for word in GENERIC_FILENAME_STEMS_RAW
)


# Component, material and background words: part names, substances and
# non-object labels that must never surface as the primary image
# object. Everyday object-nouns win over their material reading and
# stay out (Glas, Stein, Blech, Papier, Karton, Wolle, Decke), as do
# whole-item words (Tür, Fenster, Kabel, Schubladenschrank, ...) and
# document subjects (Rechnung, Beleg, Brief, Paket, ...).
IMAGE_PART_WORDS_RAW = (
    "sitz", "sitze", "sitzfläche", "sitzflaeche",
    "lehne", "lehnen", "rückenlehne", "rueckenlehne",
    "armlehne", "armlehnen",
    "kopfstütze", "kopfstuetze", "kopfstützen", "kopfstuetzen",
    "bein", "beine", "tischbein", "tischbeine",
    "stuhlbein", "stuhlbeine",
    "fuss", "fuß", "füsse", "füße", "fuesse",
    "stütze", "stützen", "stuetze", "stuetzen",
    "stützfuß", "stützfuss", "stuetzfuss",
    "stützfüße", "stützfüsse", "stuetzfuesse",
    "platte", "platten", "tischplatte", "tischplatten",
    "arbeitsplatte", "arbeitsplatten",
    "glasplatte", "glasplatten", "holzplatte", "holzplatten",
    "kante", "kanten", "ecke", "ecken",
    "griff", "griffe", "türgriff", "tuergriff", "tuergriffe",
    "fenstergriff", "schrankgriff", "schrankgriffe",
    "knauf", "knäufe", "knaeufe",
    "schraube", "schrauben", "niet", "nieten",
    "nagel", "nägel", "naegel",
    "dübel", "duebel", "duebeln",
    "unterlegscheibe", "unterlegscheiben", "splint", "splinte",
    "scharnier", "scharniere",
    "schublade", "schubladen", "fach", "fächer", "faecher",
    "regalbrett", "regalbretter",
    "seitenwand", "seitenwände", "seitenwaende",
    "rückwand", "rueckwand", "rückwände", "rueckwaende",
    "deckel", "rahmen", "gestell",
    "polster", "polsterung", "bezug", "bezüge", "bezuege",
    "naht", "nähte", "naehte", "sockel", "sockelleiste",
    "traverse", "traversen", "quertraverse",
    "längsstrebe", "laengsstrebe", "strebe", "streben",
    "sprosse", "sprossen", "sparren",
    "zylinder", "beschlag", "beschläge", "beschlaege",
    "dichtung", "dichtungen", "gleiter", "befestigung",
    "etikett", "etiketten",
    "kratzer", "fleck", "flecken", "riss", "risse",
    "loch", "löcher", "loecher", "delle", "dellen",
    "schaden", "schäden", "schaeden",
    "mangel", "mängel", "maengel",
    "rest", "reste", "abfall", "müll", "muell", "staub",
    "folie", "folien", "verpackung",
    "zubehör", "zubehoer",
    "holz", "metall", "kunststoff", "plastik",
    "stoff", "stoffe", "leder", "kunstleder", "textil", "gewebe",
    "pappe", "mdf", "spanplatte", "sperrholz", "furnier",
    "eiche", "buche", "kiefer", "ahorn", "nussbaum",
    "teak", "mahagoni", "bambus", "kork", "rattan",
    "marmor", "granit", "schiefer", "beton", "gips",
    "keramik", "porzellan", "steinzeug", "terrakotta",
    "stahl", "eisen", "edelstahl", "aluminium", "alu",
    "messing", "kupfer", "bronze", "chrom", "zink", "draht",
    "gummi", "silikon", "acryl", "plexiglas",
    "samt", "leinen", "baumwolle", "filz", "schaumstoff",
    "polyester", "nylon", "emaille", "lack", "lacke", "lasur",
    "oel", "oele", "öl", "öle", "wachs",
    "gold", "silber", "platin", "nickel", "titan", "zinn", "blei",
    "ocker", "anthrazit", "petrol",
    "boden", "böden", "boeden", "wand", "wände", "waende",
    "vordergrund", "hintergrund", "ansicht",
    "seite", "seiten", "teil", "teile", "detail", "details",
    "muster", "struktur", "oberfläche", "oberflaeche",
    "material", "materialien", "farbe", "farben",
    "maße", "masse", "größe", "groesse", "gewicht",
    "höhe", "hoehe", "länge", "laenge", "stärke", "staerke",
    "name", "namen", "zahl", "zahlen",
    "cm", "mm", "zoll", "inch", "meter", "zentimeter", "millimeter",
    "kilogramm", "gramm", "kilo", "liter",
    "stk", "stück", "stueck",
    "s", "m", "l", "xl", "xxl", "xxxl",
    "typ", "type", "hersteller", "artikel", "artikelnummer",
    "ean", "gtin", "isbn", "barcode", "strichcode",
    "preis", "preise", "kosten", "wert", "rabatt", "angebot",
)
IMAGE_PART_WORDS = (
    frozenset(_phrase_key(word) for word in IMAGE_PART_WORDS_RAW)
    | TAG_STOPWORDS
)

# Attribute roots for the adjective-ending-tolerant token check (raw
# tokens look up IMAGE_PART_WORDS; stripped tokens only these roots,
# so "Kanne" never dies via "kann" and "Matte" never via "matt").
IMAGE_ATTRIBUTE_ROOTS_RAW = (
    "rot", "blau", "grün", "gruen", "gelb", "schwarz",
    "weiß", "weiss", "grau", "braun", "beige", "orange",
    "rosa", "pink", "lila", "violett", "türkis", "tuerkis",
    "silbern", "golden", "bunt", "farbig", "farblos",
    "hell", "dunkel", "dunkl", "einzeln",
    "schnell", "langsam", "groß", "gross", "klein",
    "lang", "kurz", "breit", "schmal", "hoch", "hoh",
    "tief", "flach", "platt", "dick", "dünn", "duenn",
    "rund", "eckig", "kantig", "oval", "quadratisch", "rechteckig",
    "neu", "alt", "modern", "antik", "rustikal",
    "massiv", "stabil", "robust", "leer", "voll",
    "offen", "geschlossen", "kaputt", "defekt",
    "beschädigt", "beschaedigt", "sauber", "schmutzig",
    "glatt", "rau", "glänzend", "glaenzend",
    "lackiert", "furniert", "poliert", "eloxiert",
    "pulverbeschichtet", "verzinkt", "verchromt",
    "gebürstet", "geschliffen", "strukturiert", "gemasert",
    "gemustert", "gestreift", "kariert", "gepunktet",
    "einfarbig", "mehrfarbig", "hochglanz", "seidenmatt",
    "unbehandelt", "behandelt", "geölt", "geoelt", "gewachst",
    "lasiert", "gebeizt", "grundiert", "versiegelt",
    "beschichtet", "laminiert", "foliert", "nass",
    "metallen", "hölzern", "hoelzern", "gläsern", "glaesern",
    "eisern", "steinern", "roh", "weit",
)
IMAGE_ATTRIBUTE_ROOTS = frozenset(
    _phrase_key(word) for word in IMAGE_ATTRIBUTE_ROOTS_RAW
)

_ADJECTIVE_ENDINGS = ("em", "en", "er", "es", "e")


def _word_tokens(text: str) -> list[str]:
    """Split a label into casefolded alphanumeric tokens."""
    return re.findall(r"[0-9a-zäöüß]+", str(text or "").casefold())


def _strip_adjective_ending(token: str) -> str:
    """Strip one German adjective ending (longest match first)."""
    for ending in _ADJECTIVE_ENDINGS:
        if len(token) > len(ending) + 1 and token.endswith(ending):
            return token[: -len(ending)]
    return token


def _is_stopword_tag(tag: str, phrase: str) -> bool:
    """True when a tag holds only weak function words (never useful)."""
    if phrase in TAG_STOPWORDS:
        return True
    tokens = _word_tokens(tag)
    return bool(tokens) and all(
        _phrase_key(token) in TAG_STOPWORDS for token in tokens
    )


def _is_component_label(text: str) -> bool:
    """True for part/material/attribute labels (never primary objects).

    Whole-object labels survive: only labels whose every token is a
    part, material, attribute, background or function word drop out,
    so "runder Tisch" stays while "runde Platte" and "Sitzfläche" go.
    """
    if _phrase_key(text) in IMAGE_PART_WORDS:
        return True
    return all(
        _phrase_key(token) in IMAGE_PART_WORDS
        or _phrase_key(_strip_adjective_ending(token)) in IMAGE_ATTRIBUTE_ROOTS
        for token in _word_tokens(text)
    )


def validate_tag_suggestions(
    raw: object,
    existing: list[str],
    *,
    source_text: str = "",
    filename: str = "",
    body_text: str = "",
) -> list[dict[str, Any]]:
    """Validate model tag output; skip invalid entries, never invent any.

    Malformed payloads raise; valid JSON with zero surviving
    suggestions returns [] so callers answer 200 with an empty list
    ("no supported suggestions") instead of an error. Each entry
    needs a nonempty `evidence` quote found verbatim
    (case/space/quote blind) in the source (filename plus body
    text), AND the normalized tag phrase itself must occur verbatim
    in the source. Weak function words always drop; generic
    filename stems drop unless the body text itself carries them.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("tags"), list):
        raise VisionError("Tag-Vorschläge müssen ein JSON-Objekt mit 'tags'-Liste sein")
    split = bool(filename or body_text)
    if split:
        source_text = f"{filename}\n{body_text}"
    seen_existing = {str(tag).strip().casefold() for tag in existing if str(tag).strip()}
    seen_new: set[str] = set()
    suggestions: list[dict[str, Any]] = []
    source_key = _evidence_key(source_text)
    source_phrase = _phrase_key(source_text)
    body_phrase = _phrase_key(body_text) if split else None
    for entry in raw["tags"]:
        if len(suggestions) >= MAX_SUGGESTIONS:
            break
        if not isinstance(entry, dict):
            continue
        tag = str(entry.get("tag", "") or "").strip()
        if not tag or len(tag) > TAG_LIMIT or not TAG_RE.match(tag):
            continue
        folded = tag.casefold()
        if folded in seen_new:
            continue
        evidence = str(entry.get("evidence", "") or "").strip()
        if not evidence or len(evidence) > EVIDENCE_LIMIT:
            continue
        key = _evidence_key(evidence)
        if not key or key not in source_key:
            continue
        phrase = _phrase_key(tag)
        if not phrase or phrase not in source_phrase:
            continue
        if len(phrase) < 2 or _is_stopword_tag(tag, phrase):
            continue
        if (
            body_phrase is not None
            and phrase.rstrip("0123456789") in GENERIC_FILENAME_STEMS
            and phrase not in body_phrase
        ):
            continue
        seen_new.add(folded)
        reason = str(entry.get("reason", "") or "").strip()[:REASON_LIMIT]
        suggestions.append(
            {"tag": tag, "reason": reason, "already": folded in seen_existing,
             "evidence": evidence}
        )
    return suggestions


def validate_image_description(raw: object) -> dict[str, Any]:
    """Validate model image output; allow empty objects, never invent any.

    Component, material, attribute and background labels drop out, and
    a surviving label must also occur in the description sentence
    (case/separator-normalized) — a tag its own sentence never names
    is dropped, never inferred. MAX_OBJECTS 1 can therefore only
    surface a whole-object label its description confirms (or none
    when the model is uncertain or names parts only). The description
    is always kept.
    """
    if not isinstance(raw, dict):
        raise VisionError("Bildvorschlag muss ein JSON-Objekt sein")
    description = str(raw.get("description", "") or "").strip()[:DESCRIPTION_LIMIT]
    if not description:
        raise VisionError("Bildvorschlag ohne Beschreibung erhalten")
    objects: list[str] = []
    candidates = raw.get("objects", [])
    if not isinstance(candidates, list):
        raise VisionError("Bildvorschlag braucht eine 'objects'-Liste")
    desc_phrase = _phrase_key(description)
    for candidate in candidates:
        if len(objects) >= MAX_OBJECTS:
            break
        text = str(candidate or "").strip()
        if not text or len(text) > OBJECT_LIMIT or not OBJECT_RE.match(text):
            continue
        if _is_component_label(text):
            continue
        label = _phrase_key(text)
        if not label or label not in desc_phrase:
            continue
        if text.casefold() not in {known.casefold() for known in objects}:
            objects.append(text)
    return {"description": description, "objects": objects}


def suggest_tags_for_document(
    *,
    document: dict[str, Any],
    settings: dict[str, Any],
    root: object,
    actor: str,
    folder_hint: str = "",
) -> dict[str, Any]:
    """Suggest text tags; stores nothing. Never touches image bytes."""
    provider = _require_local_provider(settings, purpose="Tag-Vorschläge")
    text, text_source = document_text(root=root, actor=actor, document=document)
    filename = Path(str(document.get("last_path", ""))).name or str(document.get("document_id", ""))
    existing = [str(tag).strip() for tag in document.get("tags", []) if str(tag).strip()]
    prompt = build_tag_prompt(filename=filename, tags=existing, folder_hint=folder_hint, text=text)
    suggestions = validate_tag_suggestions(
        complete_text(provider, prompt), existing,
        filename=filename, body_text=text,
    )
    return {
        "suggestions": suggestions,
        "provider_label": provider.label,
        "text_source": text_source,
        "model": getattr(provider, "resolved_model", None) or provider.model,
    }


def suggest_image_for_document(
    *,
    document: dict[str, Any],
    settings: dict[str, Any],
    root: object,
    actor: str,
) -> dict[str, Any] | None:
    """Describe one image document; stores nothing.

    Separate call so text tags never wait for vision. Returns None
    for non-image documents (no error); raises DocTagError (German)
    for unusable images and InferenceUnavailable when the local
    model is down or too slow.
    """
    if not _is_image_document(document):
        return None
    provider = _require_local_provider(settings, purpose="die Bildanalyse")
    image = document_image_bytes(root=root, actor=actor, document=document)
    if image is None:  # suffix was checked above; kept for safety
        return None
    try:
        raw = provider.suggest(
            image,
            image_mime_type(str(document.get("last_path", ""))),
            {"instruction": IMAGE_INSTRUCTION, "mode": "describe"},
        )
    except InferenceUnavailable:
        raise
    except VisionError as exc:
        raise DocTagError(f"Die Bildanalyse ist fehlgeschlagen ({exc})") from None
    return {
        "image": validate_image_description(raw),
        "provider_label": provider.label,
        "model": getattr(provider, "resolved_model", None) or provider.model,
    }


def apply_tag_confirmation(
    *, root: object, actor: str, document_id: str, tags: object
) -> dict[str, Any]:
    """Apply explicitly confirmed tags through the normal tag path."""
    clean_actor = str(actor or "").strip()
    if not clean_actor:
        raise ValueError("actor is required")
    store = DocumentStore(root)
    try:
        document = store.get_document(str(document_id))
    except ValueError:
        raise DocumentNotFoundError("Unbekanntes Dokument") from None
    if not isinstance(tags, list):
        raise ValueError("Tags müssen als Liste übergeben werden")
    if len(tags) > MAX_CONFIRM_TAGS:
        raise ValueError(f"Es können höchstens {MAX_CONFIRM_TAGS} Tags auf einmal bestätigt werden")
    proposed: list[str] = []
    for tag in tags:
        clean = str(tag or "").strip()
        if not clean or len(clean) > TAG_LIMIT or not TAG_RE.match(clean):
            raise ValueError(f"Ungültiger Tag: {str(tag or '')[:60]}")
        if clean.casefold() not in {known.casefold() for known in proposed}:
            proposed.append(clean)
    existing = [str(tag).strip() for tag in document.get("tags", []) if str(tag).strip()]
    seen = {tag.casefold() for tag in existing}
    additions = [tag for tag in proposed if tag.casefold() not in seen]
    skipped = [tag for tag in proposed if tag.casefold() in seen]
    if additions:
        metadata = store.set_tags(str(document_id), [*existing, *additions], clean_actor)
    else:
        metadata = document
    return {"added": additions, "skipped": skipped, "tags": list(metadata.get("tags", []))}

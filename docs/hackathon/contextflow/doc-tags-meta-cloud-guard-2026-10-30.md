# Doc-Tags Meta-Cloud-Sperre — 30.10.2026 (isolierter Testauftrag)

Kein Produktionscode geändert. Keine WORKLOG-/STATUS-Änderung, kein
Commit/Push/Rebase. Läufe: Muse Spark 1.3 Contributor (Meta); keine
fremden Modelle, keine externen Aufrufe.

## Befund: Sperre hält im Code

- `suggest_tags_for_document` und `suggest_image_for_document`
  (`app/contextflow/doc_tags.py:814/845`) nehmen kein Consent-Argument
  und wählen ihr Backend zuerst über `_require_local_provider`
  (Consent fest `False`, `select_provider(settings, False, None)`).
- Bei `provider=meta` wirft `select_provider` immer
  `PermissionError`, bevor Schlüssel/Endpoint/Modell/Root geprüft
  werden — auch mit Server-Consent, Key, Endpoint und Modell in den
  Settings. Darum kein Minimal-Fix nötig.
- Maßstab: `AGENTS.md` §23 + `docs/hackathon/contextflow/README.md`
  (Meta-only-Laufzeitregel, synthetische Demo-Schranke).

## Neue Tests (`tests/test_contextflow_doc_tags.py`)

- Klasse `DocTagMetaCloudRefusal`: Text- und Bildpfad je mit
  Voll-Consent-Meta-Settings (`meta_consent`, Key, echter Endpoint,
  `muse-spark-1.3-contributor`) — erwarten `PermissionError` und
  belegen per Mock, dass weder `vision.local_urlopen` noch
  `MetaVisionProvider._post_response` aufgerufen wird.
- Bestehender Routen-Test `test_meta_provider_is_refused_without_consent`
  deckt nur fehlenden Consent ab; die neuen Tests decken volle
  Konfiguration ab.

## Ausführung (Eigentümer, Haupt-Checkout)

`python -m unittest tests.test_contextflow_doc_tags -v`
Erwartet: 47 Tests (45 Bestand + 2 neu), alle OK, offline.

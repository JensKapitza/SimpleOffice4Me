# AI Assist – Hinweis-Pakete Batch 4

Diese Erweiterungen sind lokal, schreibgeschützt und führen keine Änderungen ohne explizite Nutzeraktion aus. Ergänzung zu [AI_ASSIST_2026-11-04.md](AI_ASSIST_2026-11-04.md).

## Audio: offline Ausgaenge und Worker-Probleme vorschlagen

- `app/audio_output_hints.py::suggest_output_review` markiert
  offline gemeldete Ausgaenge sowie einen ungesunden Worker bei
  vorhandenen Ausgaengen (nur Namen, Knoten, Status, keine
  Audioinhalte).
- Eingebettet in `audio_output_admin.py::index` als
  `output_hints`, angezeigt in
  `templates/admin/audio_output.html`.
- Es wird nichts geaendert oder gestartet; Umsetzung nur ueber die
  bestehenden Audio-Routen nach Bestaetigung.
- Tests: `tests/test_audio_output_hints.py` (6 Faelle).

## Streamer: abgestuerzte oder wartende Dienste vorschlagen

- `app/streamer_care_hints.py::suggest_streamer_review` markiert
  abgestuerzte Sender/Empfaenger, Sender ohne Ziele sowie wartende
  Empfaenger (nur Zustaende und Zaehler, keine Audioinhalte).
- Eingebettet in `audio_streamer_admin.py::page` als
  `streamer_hints`, angezeigt in
  `templates/admin/audio_streamer.html`.
- Es wird nichts gestartet oder gestoppt; Umsetzung nur ueber die
  bestehenden Streamer-Routen nach Bestaetigung.
- Tests: `tests/test_streamer_care_hints.py` (6 Faelle).

## Schnellsuche: Eingrenzung bei vielen oder keinen Treffern vorschlagen

- `app/search_narrow_hints.py::suggest_search_narrowing` markiert
  gedeckelte Trefferlisten (50+), Treffer ueber viele Bereiche sowie
  leere Suchen (nur Zaehler und Bereiche, keine Inhalte).
- Eingebettet in `documents_routes_content.py::quick_search` als
  `narrow_hints`, angezeigt in
  `templates/documents/quick_search.html`.
- Es wird nichts veraendert; Umsetzung nur ueber die bestehenden
  Suchrouten nach Bestaetigung.
- Tests: `tests/test_search_narrow_hints.py` (6 Faelle).

## Drucker: fehlende Geraete und Freigabe-Luecken vorschlagen

- `app/printer_setup_hints.py::suggest_printer_review` markiert
  leeres Inventar, fehlenden Standarddrucker sowie aktive
  Federation-Freigabe ohne geteilten Drucker (nur Namen, Flags,
  Zaehler).
- Eingebettet in `admin.py::printer_settings` als
  `setup_hints`, angezeigt in
  `templates/admin/printers.html`.
- Es wird nichts hinzugefuegt oder geteilt; Umsetzung nur ueber die
  bestehenden Admin-Routen nach Bestaetigung.
- Tests: `tests/test_printer_setup_hints.py` (6 Faelle).

## iTIP: offene Terminnachrichten vorschlagen

- `app/itip_wait_hints.py::suggest_itip_review` markiert
  ausstehende Nachrichten an den aktuellen Benutzer sowie
  wartende Nachrichten bei anderen Empfaengern (nur Status,
  Zaehler, Absender).
- Eingebettet in `documents_routes_workflows.py::calendar` als
  `itip_hints`, angezeigt in
  `templates/documents/calendar.html`.
- Es wird nichts angewendet oder abgelehnt; Umsetzung nur ueber
  die bestehenden Kalender-Routen nach Bestaetigung.
- Tests: `tests/test_itip_wait_hints.py` (6 Faelle).

## Teamkalender: fehlende Vertretung und Ueberschneidungen vorschlagen

- `app/team_absence_hints.py::suggest_absence_review` markiert
  bestaetigte Abwesenheiten ohne Vertretung sowie zeitgleiche
  Abwesenheiten verschiedener Personen (nur Namen und Daten).
- Eingebettet in `personnel.py::team_calendar` als
  `absence_hints`, angezeigt in
  `templates/personnel/team_calendar.html`.
- Es wird nichts geaendert; Umsetzung nur ueber die bestehenden
  Personal-Routen nach Bestaetigung.
- Tests: `tests/test_team_absence_hints.py` (6 Faelle).

## Konten: Kollisionen, Dubletten und ablaufende Einladungen vorschlagen

- `app/invite_care_hints.py::suggest_invite_review` markiert
  Antraege mit bereits vergebenem Namen, doppelte Antraege sowie
  abgelaufene oder bald ablaufende Einladungen (nur Namen, Status,
  keine Passwoerter).
- Eingebettet in `account_requests.py::admin` als
  `invite_hints`, angezeigt in
  `templates/admin/account_requests.html`.
- Es wird nichts freigegeben oder widerrufen; Umsetzung nur ueber
  die bestehenden Konto-Routen nach Bestaetigung.
- Tests: `tests/test_invite_care_hints.py` (6 Faelle).

## S3: ablaufende, ungenutzte und ueberfluessige Zugaenge vorschlagen

- `app/s3_credential_hints.py::suggest_credential_review` markiert
  abgelaufene, bald ablaufende und seit 90 Tagen ungenutzte
  Zugaenge sowie aktive Zugaenge bei deaktiviertem Overlay (nur
  Namen und Daten, nie Keys oder Secrets).
- Eingebettet in `s3_overlay/routes.py::manage` als
  `credential_hints`, angezeigt in
  `templates/admin/s3_overlay.html`.
- Es wird nichts erstellt oder widerrufen; Umsetzung nur ueber die
  bestehenden S3-Routen nach Bestaetigung.
- Tests: `tests/test_s3_credential_hints.py` (6 Faelle).

## HR: fehlende Wochen-Sollzeiten vorschlagen

- `app/staff_plan_hints.py::suggest_plan_gaps` markiert aktive
  Mitarbeiter ohne Wochen-Soll (nur Namen, keine Vertragsdetails).
- Eingebettet in `personnel.py::hr` als `plan_hints`,
  angezeigt in `templates/personnel/hr.html`.
- Es wird nichts geaendert; Umsetzung nur ueber die bestehenden
  Personal-Routen nach Bestaetigung.
- Tests: `tests/test_staff_plan_hints.py` (6 Faelle).

## Bildschirm: fehlende native Sendewerkzeuge vorschlagen

- `app/screen_ready_hints.py::suggest_screen_review` markiert
  fehlende Drahtlose Anzeige (Windows) sowie fehlende native
  Linux-Sendeprogramme (nur Plattform und Verfuegbarkeiten, keine
  Pfade oder Binaries).
- Eingebettet in `screen_share.py::index` als
  `screen_hints`, angezeigt in
  `templates/screen/index.html`.
- Es wird nichts installiert oder gestartet; Umsetzung nur ueber
  die bestehenden Bildschirm-Routen nach Bestaetigung.
- Tests: `tests/test_screen_ready_hints.py` (6 Faelle).

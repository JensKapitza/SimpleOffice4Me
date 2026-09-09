# Data-Quality-Gamification

## Ziel

Die Gamification ist eine generische Engine zur spielerischen Verbesserung der Datenqualitaet fuer Bilder, explizit freigegebene Dateien/Dokumente und freigegebene Kontaktdaten.

Die Engine ist kein alternativer Synchronisationskanal und darf Zugriffsrechte der Anwendung oder der Federation niemals erweitern.

Grundablauf:

`Objekt -> Challenge -> Antwort -> Vorschlag -> Abstimmung -> Konsens -> Freigabe -> optionale Uebernahme -> optionale Belohnung`

## Sicherheitsinvarianten

Diese Regeln sind verbindlich und haben Vorrang vor Spielregeln, Punkten und Federation-Funktionen.

1. **Default deny.** Kein Datensatz nimmt automatisch am Spiel teil.
2. **Kein Rechtegewinn durch Gamification.** Ein Teilnehmer darf nur Daten sehen, die er ohne das Spiel bereits lesen darf und die zusaetzlich fuer die konkrete Spielrunde freigegeben wurden.
3. **Explizite Ausschlussklassen.** Rechnungsstellung, Buchhaltung/Belege, Zahlungsdaten, CRM-interne Daten, Zugangsdaten, Security-Daten, private Notizen und sonstige sensible Daten sind nicht spielbar und nicht ueber Gamification transferierbar.
4. **Kein Federation-Bypass.** Challenge, Vorschau, Antwort, Abstimmung oder Konsensmeldung duerfen keine Federation-Policy umgehen.
5. **Minimale Offenlegung.** Es wird nur der fuer die Challenge benoetigte Ausschnitt ausgeliefert. Bilder verwenden Cache-Thumbnails statt Originale. Kontakte legen keine vorhandenen Telefonnummern, E-Mails oder Notizen offen, wenn nur ein fehlendes Feld gesucht wird.
6. **Originaldaten bleiben autoritativ.** Antworten und Mehrheitsentscheidungen sind Vorschlaege. Verifizierte oder anderweitig autoritative Metadaten werden nicht stillschweigend ersetzt.
7. **Auditierbarkeit.** Erzeugung, Freigabe, Antwort, Abstimmung, Uebernahme und Belohnung sind nachvollziehbar.
8. **Widerruf wirkt weiter.** Feature-Sperren, Objektklassifikation und Organisationsmitgliedschaft werden beim Spielen erneut geprueft. Federation revalidiert den lokalen Ursprung bei Abruf, Preview und Antwort.
9. **Keine Punkte fuer Raten.** `weiss_nicht`/Ueberspringen ist gueltig und erzeugt keine Punkte.
10. **Unabhaengige Stimmen.** Pro Identitaet zaehlt fuer denselben Vorschlag hoechstens eine Stimme. AI-/System-Stimmen sind getrennt und zaehlen nicht zum normalen menschlichen Konsens.
11. **Acceptance ist kein Schreibrecht.** Vor realen Aenderungen gelten weiterhin die normalen Schreib-ACLs des Providers.

## Provider

### Bilder

Aktive Challenges:

- Jahr/Zeitraum
- Ort/Region
- Objekt-/Inhaltstags

Reale Bild-Challenges werden derzeit nur aus expliziten `mobile-web-bulk`-Foto-Uploads erzeugt. Voraussetzung ist ein bereits erzeugtes sicheres `.webcache`-Thumbnail. Originalbild, lokaler Dateipfad, Dokument-ID und EXIF-Daten gelangen nicht in den Browser-Payload.

Sensible Marker wie `privat`, `vertraulich`, Rechnungs-/CRM-/Buchhaltungsbezug schliessen das Bild aus.

### Allgemeine Dateien/Dokumente

Aktive Challenges:

- Dokumenttyp
- Jahr
- allgemeine Tags

Dateien sind positive Opt-in-Ressourcen. Erlaubte Freigabe-Tags sind derzeit `gamification`, `gamification-freigegeben`, `daten-roulette` und `spiel-freigabe`. Selbst mit Opt-in bleiben sensible Klassen hart gesperrt.

Die Challenge zeigt nur den Basis-Dateinamen. Pfad, Inhalt, OCR-Text, Notizen, Attribute und vorhandene Tags werden nicht mitgegeben. Bilddateien laufen ausschliesslich durch den strengeren Bildprovider.

### Kontakte

Aktive Felder:

- Strasse und Hausnummer
- PLZ und Ort
- Land
- Telefonnummer/Mobilnummer
- E-Mail-Adresse
- Firma

Der erste Live-Modus fragt nur nach fehlenden Werten. Kontakte mit CRM-, Rechnungs-, Buchhaltungs-, Bank- oder Zahlungsmerkmalen sind ausgeschlossen.

Eine manuelle Uebernahme ist nur fuer den Rundenbesitzer moeglich und verwendet `ContactStore.patch_fields`. Owner-/Manager-Schreibrecht und der aktuelle Datenstand werden unmittelbar vor dem Schreiben erneut geprueft. Bereits spaeter befuellte Felder werden nicht ueberschrieben.

## Spielmodi

### Lokal

Nur Daten der eigenen Instanz. Normale Feature-/Objektberechtigung und die Gamification-Policy gelten gleichzeitig.

### Familie/Freunde ueber Federation

Eine Federation-Runde fuegt einen bekannten Peer explizit als Session-Teilnehmer hinzu. Der Peer muss in seiner Policy den Gamification-Empfang sowie den jeweiligen Provider erlauben.

HTTP-Aufrufe verwenden eine peer-spezifische HMAC-Signatur (`SOFP-GAME-V1`) ueber Peer-ID, Zeitstempel, Nonce, HTTP-Methode, Pfad, Body-SHA-256 und Aktion. Nonces werden gegen Replay gespeichert. Mehrfach verwendete Peer-Tokens werden als mehrdeutige Identitaet abgelehnt.

Federation-Endpunkte:

- `GET /federation/v1/gamification/sessions/<session_id>/next`
- `GET /federation/v1/gamification/challenges/<challenge_id>/preview`
- `POST /federation/v1/gamification/challenges/<challenge_id>/answer`

Die Gegenrichtung besitzt einen No-Redirect-Client mit signierten Requests, begrenzten Antwortgroessen und lokal konstruierten Preview-Pfaden. Vom Peer gelieferte URLs werden nicht verfolgt.

### Firmenintern

Organisationen referenzieren reale lokale `user.id`-Konten. Rollen sind `member`, `manager`, `owner`. Nur Administratoren koennen Organisationen anlegen; Manager koennen keine Owner ernennen oder bestehende Owner herabstufen.

Eine Firmenrunde erfordert:

- gemeinsame explizite Organisationsmitgliedschaft,
- explizite Session-Teilnahme,
- normales Feature-/Objekt-Leserecht jedes eingeladenen Teilnehmers,
- zusaetzliche Gamification-Freigabe des Objekts,
- weiterhin wirksame sensible Ausschlussklassen.

Beim Erzeugen der Firmenrunde wird die Schnittmenge der fuer **alle** eingeladenen Teilnehmer normal sichtbaren Kandidaten gebildet. Spaetere Rechte- oder Mitgliedschaftsaenderungen werden beim Zugriff erneut geprueft.

## Policy-Modell

Eine Spielrunde enthaelt mindestens:

- `scope`: `local`, `federation` oder `organization`
- erlaubte Provider/Ressourcentypen
- erlaubte Sammlungen/Objekte
- erlaubte Felder/Challenge-Typen
- Teilnehmer/Peers/Organisation
- `preview_allowed`
- `original_allowed` (fuer Federation standardmaessig `false`)
- `submit_proposals`
- `view_other_proposals`
- `auto_accept_consensus`
- Konsensschwelle

Effektive Berechtigung:

`normaler Zugriff AND lokale Gamification-Policy AND ggf. Peer-/Organisationspolicy AND Session-Teilnahme`

Fehlt eine Freigabe, lautet das Ergebnis `deny`.

## Vorschlaege, Stimmen und Konsens

Konsens ist ein Qualitaetssignal und kein Wahrheitsbeweis.

Standard:

- mindestens 3 unabhaengige **menschliche** Stimmen,
- mindestens 75 % Zustimmung.

`annotation_vote.source` unterscheidet `human`, `ai` und `system`. Bestehende Datenbanken werden additiv migriert; alte Stimmen gelten als `human`. Der normale Konsens wertet ausschliesslich menschliche Stimmen aus.

Nur der Rundenbesitzer darf einen erreichten Konsens endgueltig bestaetigen. Bei Kontakten folgt danach erneut die normale Schreibrechtepruefung. Bei Bild-/Datei-Metadaten wird derzeit nur die bestaetigte Annotation gespeichert; die Originaldatei wird nicht automatisch veraendert.

## Datenmodell

Implementiert sind:

- `game_session`
- `game_participant`
- `game_item`
- `game_challenge`
- `annotation_proposal`
- `annotation_vote`
- `annotation_acceptance`
- `game_reward`
- `game_audit`
- `game_organization`
- `game_organization_member`

## Belohnungen

Belohnungen sind vollstaendig von Wahrheit, Konsens und Autorisierung getrennt.

- XP nur fuer **akzeptierte menschliche** Vorschlaege,
- derzeit 10 XP pro akzeptierter Verbesserung,
- keine XP fuer Antworten allein,
- keine XP fuer `weiss_nicht`/Skip,
- keine XP fuer AI-/System-Proposals,
- eine Belohnung pro Proposal maximal einmal.

Profilwerte:

- XP
- Anzahl bestaetigter Verbesserungen
- Tagesserie
- Achievements fuer erste, 10 und 50 Verbesserungen sowie 3-/7-Tage-Serien

Die Rangliste ist privacy-scoped: sichtbar sind nur lokale Personen, mit denen der aktuelle Nutzer eine aktive Gamification-Runde teilt. `peer:*`-Identitaeten werden nicht als Personen angezeigt. Ohne explizite Sichtbarkeitsmenge liefert die Reward-Schicht keine globale Rangliste.

## Federation-Capability

Beispiel:

```json
{
  "gamification": {
    "receive_challenges": true,
    "submit_answers": true,
    "preview_media": true,
    "original_media": false,
    "providers": ["images", "documents", "contacts"]
  }
}
```

Gamification-Capabilities ersetzen niemals bestehende SOFP-Ressourcenrechte. `original_media` wird vom aktuellen Gamification-Transport nicht zur Auslieferung von Originalen verwendet.

## Nicht-Ziele

- kein Exportmechanismus fuer CRM oder Rechnungen
- kein Ersatz fuer ACL/RBAC
- kein automatisches Zusammenfuehren oder Loeschen von Kontakten
- keine automatische Aenderung von Originaldateien aufgrund von Spielantworten
- kein offenes Internet-Spiel mit privaten Daten
- keine Weitergabe kompletter Sammlungen zur Challenge-Erzeugung
- keine Punkte als Wahrheits- oder Berechtigungssignal

## Implementierungsstand

1. Policy-/Exclusion-Layer und Auditlog: **implementiert**
2. generisches Datenmodell und Provider-API: **implementiert**
3. lokales Roulette fuer Bilder, Dateien und Kontakte: **implementiert**
4. Vorschlaege/Voting/menschlicher Konsens: **implementiert**
5. kontrollierte Kontakt-Uebernahme mit erneuter Schreibrechtepruefung: **implementiert**
6. signierte Federation-Capability, minimierte Payloads und sicherer Client: **implementiert**
7. Organisations-/Firmenmodus mit realen Nutzern und Schnittmenge der Zugriffsrechte: **implementiert**
8. XP, Serien, Achievements und privacy-scoped Rangliste: **implementiert**

Sicherheit und Datenqualitaet bleiben fachlich vor Belohnungsmechanismen.

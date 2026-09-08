# Data-Quality-Gamification

## Ziel

Die Gamification ist eine generische Engine zur spielerischen Verbesserung der Datenqualitaet. Sie darf fuer Bilder, allgemeine Dateien/Dokumente und freigegebene Kontaktdaten verwendet werden und spaeter durch weitere Provider erweitert werden.

Die Engine ist kein alternativer Synchronisationskanal und darf Zugriffsrechte der Anwendung oder der Federation niemals erweitern.

Grundablauf:

`Objekt -> Challenge -> Antwort -> Vorschlag -> Abstimmung -> Konsens -> Freigabe -> optionale Uebernahme`

## Sicherheitsinvarianten

Diese Regeln sind verbindlich und haben Vorrang vor Spielregeln, Punkten und Federation-Funktionen.

1. **Default deny.** Kein Datensatz nimmt automatisch am Spiel teil.
2. **Kein Rechtegewinn durch Gamification.** Ein Teilnehmer darf im Spiel nur Daten sehen, die er ohne das Spiel bereits lesen darf und die zusaetzlich fuer die konkrete Spielrunde freigegeben wurden.
3. **Explizite Ausschlussklassen.** Rechnungsstellung, Buchhaltung/Belege, Zahlungsdaten, CRM-interne Daten, Zugangsdaten, Security-Daten, private Notizen und sonstige als sensibel markierte Daten sind standardmaessig nicht spielbar und nicht ueber Gamification transferierbar.
4. **Kein Federation-Bypass.** Eine Challenge, Vorschau, Antwort, Abstimmung oder Konsensmeldung darf keine Federation-Send-/Receive-/Collection-Policy umgehen.
5. **Minimale Offenlegung.** Es wird nur der fuer die Challenge benoetigte Ausschnitt ausgeliefert. Bei Bildern soll eine begrenzte Vorschau statt des Originals moeglich sein. Bei Kontakten werden nur explizit spielbare Felder gezeigt.
6. **Originaldaten bleiben autoritativ.** Antworten und Mehrheitsentscheidungen sind zunaechst Vorschlaege. Verifizierte oder anderweitig autoritative Metadaten werden nicht durch Abstimmungen ueberschrieben.
7. **Auditierbarkeit.** Erzeugung, Freigabe, Antwort, Abstimmung, Konsens und Uebernahme muessen mit Akteur, Zeitpunkt und Objektbezug nachvollziehbar sein.
8. **Widerruf.** Spiel- und Federation-Freigaben muessen widerrufbar sein. Ein Widerruf verhindert neue Auslieferungen und neue Bearbeitungen.
9. **Keine Punkte fuer Raten.** `weiss_nicht`/Ueberspringen ist eine gueltige Antwort. Punkte duerfen Nutzer nicht dazu motivieren, unbekannte Daten zu erfinden.
10. **Unabhaengige Stimmen.** Pro Nutzer/Identitaet zaehlt fuer denselben Vorschlag hoechstens eine Stimme. Automatische/AI-Aussagen werden getrennt von menschlichen Stimmen behandelt.

## Provider

### Bilder

Moegliche Challenges:

- Jahr/Zeitraum
- Ort/Region
- Objekt-/Inhaltstags
- Ereignis/Album
- Personen nur bei ausdruecklicher Freigabe
- vorhandene Tags bestaetigen oder ablehnen

Federierte Spiele sollen nach Moeglichkeit eine Vorschau und eine opaque Objekt-ID verwenden. Das Originalbild muss nicht uebertragen werden.

### Allgemeine Dateien/Dokumente

Moegliche Challenges:

- Dokumenttyp
- Datum
- allgemeine Tags
- Projekt/Kategorie, soweit der Teilnehmer diese bereits lesen darf
- Duplikat-Vorschlag
- Archivierungsvorschlag

Ausgeschlossen bleiben insbesondere Rechnungsstellung, Buchhaltung/Belege, Zahlungsinformationen und CRM-interne Dokumente, sofern sie nicht spaeter durch eine explizite, strengere Unternehmenspolicy als eigener Modus implementiert werden. Der normale Spielmodus darf diese Klassen nicht freischalten.

### Kontakte

Ziel ist die Verbesserung gemeinsamer Kontaktdaten, z. B.:

- Strasse und Hausnummer
- PLZ und Ort
- Telefonnummern
- E-Mail-Adressen
- Firma/Organisation
- Duplikat-/Identitaetsvorschlaege
- Aktualitaetsbestaetigung

Feldwerte werden als Vorschlaege gespeichert. Eine Telefonnummer darf z. B. nicht entfernt werden, nur weil ein anderer Teilnehmer eine zweite Nummer nennt.

## Spielmodi

### Lokal

Nur Daten der eigenen Instanz. Die normale Leseberechtigung und die zusaetzliche Gamification-Policy gelten gleichzeitig.

### Familie/Freunde ueber Federation

Nur explizit fuer den Peer/die Gruppe und die Spielrunde freigegebene Objekte/Felder. Freigabe fuer das Spiel bedeutet insbesondere **nicht** Freigabe des Originals oder der restlichen Sammlung.

### Firmenintern

Ein Unternehmensspiel ist erlaubt, wenn alle folgenden Bedingungen erfuellt sind:

- Teilnehmer gehoeren zum freigegebenen Unternehmenskontext bzw. zur zugelassenen Gruppe,
- der einzelne Teilnehmer besitzt bereits Leserecht auf dem betroffenen Datensatz/Feld,
- die Ressource ist explizit fuer die Spielrunde freigegeben,
- sensible Ausschlussklassen bleiben gesperrt,
- Schreib-/Uebernahmerechte werden separat geprueft.

Mitarbeiter duerfen dadurch z. B. gemeinsam freigegebene Firmenkontakte vervollstaendigen. Die Mitgliedschaft in derselben Firma allein reicht nicht als Leseberechtigung.

## Policy-Modell

Eine Spielrunde benoetigt mindestens:

- `scope`: local, federation oder organization
- erlaubte Provider/Ressourcentypen
- erlaubte Sammlungen/Objekte
- erlaubte Felder/Challenge-Typen
- Teilnehmer/Gruppe/Peers
- `preview_allowed`
- `original_allowed` (standardmaessig false fuer Federation-Spiele)
- `submit_proposals`
- `view_other_proposals`
- `auto_accept_consensus`
- Konsensschwelle
- Ablaufzeit/Widerruf

Effektive Berechtigung ist immer die Schnittmenge aus:

`normaler Lesezugriff AND lokale Gamification-Policy AND ggf. Federation-Peer-Policy AND Spielrunden-Policy`

Fehlt eine Freigabe, lautet das Ergebnis `deny`.

## Konsens

Konsens ist ein Qualitaetssignal und kein Wahrheitsbeweis.

Empfohlener Startwert fuer nicht-sensible Tags:

- mindestens 3 unabhaengige menschliche Stimmen,
- mindestens 75 % Zustimmung.

Fuer Kontaktdaten soll Konsens standardmaessig nur einen hervorgehobenen Vorschlag erzeugen. Automatische Uebernahme muss pro Feldklasse explizit aktiviert werden.

Werte mit autoritativer Quelle, z. B. verifiziertes EXIF-Aufnahmedatum oder manuell verifizierte Stammdaten, duerfen durch Spielkonsens nicht stillschweigend ersetzt werden.

## Datenmodell

Die Engine soll generisch aufgebaut werden:

- `game_session`: Runde, Scope, Policy, Status
- `game_item`: opaque Referenz auf Provider + Objekt
- `game_challenge`: Frage/Aufgabentyp und erlaubte Antwortform
- `annotation_proposal`: vorgeschlagener Wert mit Quelle
- `annotation_vote`: unabhaengige Stimme
- `annotation_consensus`: berechnetes Ergebnis
- `annotation_acceptance`: explizite oder policybasierte Uebernahme
- `game_reward`: Punkte/Achievements getrennt von der fachlichen Bewertung
- `game_audit`: sicherheitsrelevante Ereignisse

Provider muessen mindestens `can_expose`, `build_challenge`, `validate_answer`, `propose_change` und `apply_accepted_change` kapseln. `apply_accepted_change` prueft die normalen Schreibrechte erneut; eine vorherige Spielberechtigung ist kein Schreibrecht.

## Federation

Gamification wird als eigener Capability-/Ressourcentyp behandelt und nicht als Alias fuer `documents` oder `contacts`.

Eine Federation-Nachricht soll nur opaque Objektbezug, Challenge-Daten und die explizit erlaubte Vorschau/Feldmenge enthalten. Antworten/Votes koennen zum Ursprung zurueckgesendet werden. Der Ursprung bleibt fuer die Uebernahme autoritativ.

Empfohlene Capability:

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

Gamification-Capabilities ersetzen niemals die vorhandenen SOFP-Ressourcenrechte.

## Nicht-Ziele

- kein Exportmechanismus fuer CRM oder Rechnungen
- kein Ersatz fuer ACL/RBAC
- kein automatisches Zusammenfuehren von Kontakten ohne normale Merge-Pruefung
- kein automatisches Loeschen aufgrund von Spielantworten
- kein offenes Internet-Spiel mit privaten Daten
- keine Weitergabe kompletter Sammlungen nur zur Erzeugung von Challenges

## Implementierungsreihenfolge

1. Policy-/Exclusion-Layer und Auditlog
2. generisches Datenmodell und Provider-API
3. lokales Roulette fuer Bilder, Dokumente und Kontakte
4. Vorschlaege/Voting/Konsens
5. kontrollierte Uebernahme mit erneuter Schreibrechtepruefung
6. Federation-Capability und minimierte Challenge-Payloads
7. Organisations-/Firmenmodus mit Gruppen- und ACL-Pruefung
8. XP, Serien, Achievements und Ranglisten

Sicherheit und Datenqualitaet werden vor Belohnungsmechanismen implementiert.

# Rekursives und wiederherstellbares WebDAV-Ordnerlöschen nach RFC 4918

## Zweck und Nutzen

Desktop-Dateimanager und Synchronisationswerkzeuge löschen nicht nur einzelne
Dateien. Wird in Nautilus, Finder, Windows Explorer oder einem von
FreeFileSync verwendeten WebDAV-Mount ein Ordner entfernt, erwartet der Client,
dass dessen kompletter sichtbarer Teilbaum verschwindet. SimpleOffice führt
diese Operation nun für berechtigte Schreibzugänge aus, ohne Dateien physisch
zu vernichten oder bestehende Aufbewahrungsregeln zu umgehen.

Die Umsetzung verbindet fünf Ziele:

1. RFC-konformes `DELETE` auf nicht leeren Collections;
2. vollständige Vorprüfung von Rechten, Sperren und Dateitypen;
3. eine atomare Änderung des sichtbaren Namensraums;
4. Wiederanlauf nach einem Prozessabbruch und Rollback bei normalen Fehlern;
5. einzeln bestätigte Wiederherstellung jeder enthaltenen Datei.

## Primärstandard und normative Anforderungen

Maßgeblich ist
[RFC 4918 – Web Distributed Authoring and Versioning](https://www.rfc-editor.org/rfc/rfc4918.html).
Die folgende Tabelle paraphrasiert die für diese Funktion relevanten
Anforderungen. Die verlinkten Abschnitte bleiben die normative Quelle.

| Normative Aussage | Quelle | Abgeleitete Entscheidung |
|---|---|---|
| Ein erfolgreiches `DELETE` **MUST** alle Locks entfernen, deren Lock-Root auf der gelöschten Ressource liegt. Die frühere URL **MUST** danach bei `GET`, `HEAD` und `PROPFIND` wie eine nicht gefundene Ressource reagieren. | [RFC 4918 §9.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.6) | Nach dem atomaren Entfernen des sichtbaren Ordners werden alle expliziten Locks auf ihm oder seinen Mitgliedern gelöscht und auditiert. Der private Recovery-Baum ist kein WebDAV-Namensraum. |
| `DELETE` auf einer Collection **MUST** wie `Depth: infinity` wirken; ein Client **MUST NOT** einen anderen `Depth`-Wert senden. | [RFC 4918 §9.6.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.6.1) | Ein fehlender Header wird als `infinity` behandelt. Jeder andere Wert wird mit `400 Bad Request` ohne Änderung abgewiesen. |
| Der Server **MUST** die Collection und alle Mitglieder löschen, auf die er angewendet werden kann. Bedingungen und andere relevante Header gelten für jedes Mitglied. | [RFC 4918 §9.6.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.6.1) | Vor dem ersten sichtbaren Wechsel werden alle Mitglieder, Rechte, Retention-Zustände, Dateitypen und Lock-Token geprüft. Ein gesperrtes oder unsicheres Mitglied verhindert die gesamte Operation. |
| Kann ein Mitglied nicht gelöscht werden, **MUST** auch sein Vorfahr im konsistenten Namensraum erhalten bleiben. Ein `207 Multi-Status` **MAY** einzelne Fehler beschreiben. | [RFC 4918 §9.6.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.6.1) | SimpleOffice entscheidet sich für die stärkere, all-or-nothing Vorprüfung. Statt eines partiell gelöschten Baums und `207` bleibt bei einem Fehler der ganze Baum sichtbar; die Antwort ist ein eindeutiger `4xx`- oder `507`-Status. |
| Eine schreibende Anfrage auf eine gesperrte Ressource **MUST** den passenden Lock-Token übermitteln. | [RFC 4918 §7.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.4), [§7.5](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.5) | Der WebDAV-`If`-Header wird ressourcengenau ausgewertet. Für jeden expliziten Lock im Teilbaum muss der authentifizierte Benutzer genau dessen Token liefern. |
| Ein Server **SHOULD** vorsorglich vor Operationen warnen beziehungsweise sie ablehnen, die seine Ressourcen ausschöpfen würden. | [RFC 4918 §20.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-20.2) | Pro Operation gelten feste Grenzen von 2.000 Mitgliedern und 64 Ebenen. Überschreitungen ergeben `507`, bevor der sichtbare Baum verändert wird. |
| HTTP-Vorbedingungen werden vor der Methode ausgewertet; eine nicht erfüllte Bedingung verhindert die Zieloperation. | [RFC 9110 §13](https://www.rfc-editor.org/rfc/rfc9110.html#section-13) | Ressourcenbezogene WebDAV-`If`-Bedingungen und Lock-Token laufen vor der Collection-Operation. Datei-ETags bleiben für einzelne Dateiänderungen maßgeblich. |

## Implementiertes Transaktionsmodell

### 1. Vollständige Vorprüfung

SimpleOffice erstellt unter einer exklusiven Inhaltssperre ein begrenztes
Manifest des Ordners. Dabei werden keine Symlinks verfolgt. Erfasst werden
relative Unterordner, Dokument-ID, Ursprungspfad, SHA-256 und Dateigröße aller
regulären Dateien. Abgewiesen werden:

- interne oder reservierte Pfade;
- Symlinks, Spezialdateien und ungültige Ordnerpolitiken;
- mehr als 2.000 Mitglieder oder mehr als 64 Ebenen;
- fehlende Metadaten oder ein nicht regulärer Payload;
- Dateien mit aktiver Arbeitssperre oder Zustand `cleanup_state=staged`;
- fehlende oder fremde Lock-Token;
- WebDAV-Wurzel und Grenze eines ordnergebundenen Gerätezugangs.

Diese Prüfung verändert weder Datei noch Metadaten. Dadurch gibt es keinen
halb gelöschten Teilbaum, wenn ein tiefes Mitglied geschützt ist.

### 2. Atomarer Namensraumwechsel

Nach der Vorprüfung entsteht ein nicht über WebDAV erreichbarer Vorgang unter
`.simpleoffice-meta/webdav-collection-trash/<UUID>/`. Das Manifest wird zuerst
im Zustand `prepared` gespeichert. Anschließend wird der komplette Ordner mit
einer Dateisystem-Umbenennung als `tree` in den Vorgang verschoben und das
Manifest auf `staged` gesetzt.

Damit verschwindet der sichtbare Baum in genau einer Dateisystemoperation.
`GET`, `HEAD` und `PROPFIND` liefern danach `404`; ein paralleler Client kann
keinen Zustand sehen, in dem nur ein Teil der Kinder entfernt ist.

### 3. Integrität, Index und Audit

Jeder verschobene Payload wird nochmals gegen den zuvor ermittelten SHA-256
geprüft. Erst danach werden Dokumentmetadaten auf `webdav_deleted` gesetzt,
aktive Scan- und Fingerprintpfade entfernt und der Recovery-Pfad gespeichert.
Für jede Datei entsteht ein vollständiger `document_soft_deleted`-Datensatz.
Zusätzlich protokolliert `webdav_collection_soft_deleted` Anzahl der Ordner,
Dokumente, Bytes, Akteur, Zeitpunkt und Vorgangs-ID.

Nach erfolgreichem Abschluss wird das Manifest `committed`. Enthaltene Locks
werden entfernt und mit `webdav_lock_destroyed_by_delete` dokumentiert. Das
RFC-6578-Sync-Journal meldet den entfernten Elternordner als `404`-Tombstone;
Clients dürfen daraus die Entfernung aller Nachfahren ableiten.

### 4. Rollback und Wiederanlauf

Ein normaler Speicher-, Hash- oder Metadatenfehler verschiebt den Baum sofort
an seinen Ursprung zurück, stellt die vorherigen Dokumentmetadaten und
Suchindexeinträge wieder her und schreibt
`webdav_collection_delete_rolled_back`. Die WebDAV-Antwort meldet den Fehler;
im sichtbaren Namensraum bleibt der komplette Ausgangsbaum erhalten.

Ein harter Prozessabbruch kann zwischen atomarer Verschiebung und Commit
liegen. Deshalb enthält das staged Manifest die zuvor gesicherten Metadaten.
Beim nächsten `DocumentStore.initialize()` wird jeder unvollständige Vorgang
validiert und automatisch zurückgerollt. Die erfolgreiche Reparatur erzeugt
`webdav_collection_delete_recovered`. Kann sie nicht eindeutig und sicher
ausgeführt werden, bleibt der Vorgang als `recovery_blocked` erhalten; es wird
nichts geraten oder überschrieben.

## Bedienung und Client-Kompatibilität

Es gibt keine zusätzliche Schaltfläche oder Konfiguration. Nach Einrichtung
des schreibenden Gerätezugangs aus
[WEBDAV_DATEIVERWALTUNG.md](WEBDAV_DATEIVERWALTUNG.md) kann ein Ordner wie
gewohnt gelöscht werden:

- **LibreOffice:** temporäre Sicherungsordner und Dokumentordner können über
  den eingebundenen WebDAV-Speicher verwaltet werden; offene Dokument-Locks
  müssen vom Client mitgesendet werden.
- **Nautilus / GNOME Files:** Ordner markieren und **In den Papierkorb
  verschieben** oder **Löschen** wählen. Der Server stellt selbst keinen
  sichtbaren DAV-Papierkorb bereit.
- **Windows-Datei-Explorer:** ein verbundener WebDAV-Netzwerkort kann nicht
  leere Ordner mit dem normalen Löschbefehl entfernen.
- **macOS Finder:** das Entfernen eines Ordners am verbundenen Server führt zu
  einem rekursiven `DELETE`.
- **FreeFileSync:** Löschpropagation in einer Vorschau prüfen. Der Server
  schützt Retention und Locks, kann aber eine fachlich falsche, vom Benutzer
  bestätigte Synchronisationsrichtung nicht erkennen.

Erfolgreiche Clients erhalten `204 No Content`. `Depth: 0` oder `Depth: 1`
erhalten `400`; ein Lock- oder Retention-Konflikt `423`; unsichere Mitglieder
oder ungültige Pfade `409`; Größen-, Tiefen- oder Speichergrenzen `507`.

## Wiederherstellung

Jede Datei aus einem gelöschten Ordner erscheint für den löschenden Benutzer
unter **Wiederherstellen**. Sie kann einzeln in einen bereits vorhandenen
Ordner zurückgeholt werden. Dabei gelten unverändert:

- ausdrückliche Bestätigung `WIEDERHERSTELLEN`;
- erwarteter und tatsächlicher SHA-256 müssen übereinstimmen;
- das Ziel darf nicht existieren;
- Zielpfad, Symlink-Grenzen und Benutzeridentität werden erneut geprüft;
- die ursprüngliche Dokument-ID und Herkunft des Collection-Vorgangs bleiben
  in `recovery_history` nachvollziehbar.

Die Wiederherstellung einer Datei löscht die übrigen Payloads desselben
Vorgangs nicht. Es gibt bewusst keinen automatisch wiederhergestellten
Komplettbaum und keine unbemerkte Überschreibung eines inzwischen neu
angelegten Ordners.

## Rechte, Sicherheit und Datenschutz

- Nur ein aktiver Gerätezugang mit Schreibumfang darf `DELETE` ausführen.
- Ordnergebundene Zugangsdaten dürfen ihre eigene Freigabegrenze nicht
  entfernen, weil dafür Zugriff auf deren Elternordner nötig wäre.
- Die bestehenden Retention- und Bearbeitungssperren werden für jedes Mitglied
  geprüft; diese Änderung lockert keine Berechtigung.
- Recovery-Dateien und Manifeste liegen im internen Steuerverzeichnis, werden
  weder von WebDAV gelistet noch direkt heruntergeladen.
- Inhalte werden nicht an externe Dienste übertragen. Auditdaten enthalten
  Pfade, IDs, Größen und Hashwerte, aber keine Dateiinhalte oder Geheimnisse.
- HTTPS und getrennte App-Passwörter bleiben für Desktopzugriffe zwingende
  betriebliche Voraussetzungen.

## Migration und Rückwärtskompatibilität

Es gibt keine Datenbankmigration und keine Änderung bestehender Dateien,
Freigaben, Zugänge oder Aufbewahrungsfristen. Einzeldatei-Soft-Delete unter
`.simpleoffice-meta/webdav-trash/` bleibt unverändert kompatibel. Der neue
Collection-Papierkorb wird erst bei einer erfolgreichen neuen Ordnerlöschung
angelegt.

Ältere Clients, die leere Ordner löschen, erhalten weiterhin `204`. Clients,
die keinen `Depth`-Header senden, sind RFC-konform kompatibel, weil
`infinity` die vorgeschriebene Semantik ist. Die Beschränkungen für `COPY`,
`MOVE`, `PUT` und Datei-`DELETE` ändern sich nicht.

## Tests

Automatisierte Positiv-, Negativ-, Rechte-, Lock-, Konflikt-, Wiederanlauf- und
Interoperabilitätstests prüfen insbesondere:

- rekursives Löschen eines verschachtelten Baums mit zwei Dokumenten;
- `404` im sichtbaren Baum, Recovery-Payloads und RFC-6578-Tombstone;
- Ablehnung eines anderen `Depth`-Werts ohne Teiländerung;
- Vorprüfung einer Retention-/Cleanup-Sperre in einem tiefen Mitglied;
- fehlenden und korrekt getaggten Lock-Token sowie anschließende Lock-Löschung;
- Symlink-Mitglied ohne Veränderung des sicheren Nachbarn;
- simulierten Metadatenfehler mit vollständigem Rollback;
- simulierten Prozessabbruch und automatische Reparatur beim Initialisieren;
- einzelne, bestätigte und hashgeprüfte Wiederherstellung aus dem gelöschten
  Collection-Baum.

Die vollständige Suite läuft zusätzlich unter allen in GitHub Actions
konfigurierten Python-Versionen und mit Dependency Audit.

## Bewusst nicht implementiert und Grenzen

- Kein partielles Löschen mit `207 Multi-Status`: all-or-nothing ist für die
  vorhandene Dateiverwaltung sicherer und erfüllt die Konsistenzanforderung.
- Keine Wiederherstellung eines leeren Ordners über die Oberfläche; Dateien
  können einzeln zurückgeholt werden, leere Strukturinformationen bleiben im
  internen committed Manifest nachvollziehbar.
- Kein sichtbarer WebDAV-Papierkorb und kein Zugriff auf interne Manifeste.
- Keine automatische endgültige Löschung, Quota-Bereinigung oder neue
  Aufbewahrungsregel. Der Recovery-Bestand muss im Backup berücksichtigt
  werden.
- Die Grenzen 2.000 Mitglieder und 64 Ebenen sind derzeit feste
  Schutzkonstanten. Größere Bäume müssen in kleineren Einheiten bearbeitet
  werden.
- Ein Betriebsausfall nach erfolgreichem Commit, aber vor Sync- oder
  Lock-Nachbearbeitung wird durch Audit und nächste Client-Erkundung sichtbar;
  ein vollständiges verteiltes Transaktionsprotokoll über Dateisystem, Lock-
  und Sync-Datei ist nicht implementiert.

## Deaktivierung und Rückkehr

WebDAV kann wie bisher global über `SIMPLEOFFICE_WEBDAV_ENABLED=0` deaktiviert
oder ein einzelner Gerätezugang widerrufen werden. Eine Rückkehr auf eine
frühere Anwendungsversion benötigt keine Migration. Bereits committed
Collection-Recovery-Payloads dürfen dabei nicht entfernt werden; für ihre
komfortable Wiederherstellung sollte zunächst jede benötigte Datei in der
aktuellen Oberfläche zurückgeholt werden.

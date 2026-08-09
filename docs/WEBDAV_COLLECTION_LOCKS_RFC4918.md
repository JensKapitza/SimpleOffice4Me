# Rekursive WebDAV-Ordnersperren nach RFC 4918

## Zweck und Nutzen

Office-Programme und Dateimanager können nicht nur eine einzelne Datei,
sondern auch einen ganzen Arbeitsordner gegen konkurrierende Änderungen
sperren. SimpleOffice unterstützt dafür exklusive Schreibsperren auf
Collections mit `Depth: infinity`. Die Sperre gilt für vorhandene und später
angelegte Mitglieder. LibreOffice, Nautilus, Finder, Windows Explorer und
Synchronisationswerkzeuge erhalten dadurch denselben Konfliktschutz, unabhängig
davon, ob sie eine Datei über ihre stabile Dokument-URL oder den hierarchischen
Dateibaum ansprechen.

Eine Ordnersperre ersetzt weder Zugriffsrechte noch ETag-Prüfungen. Sie erlaubt
nur dem bereits berechtigten Besitzer des passenden Lock-Tokens die Mutation.
App-Passwörter, HTTPS-Pflicht, Aufbewahrung, Versionierung und Audit bleiben
unverändert wirksam.

## Ausgewertete Anforderungen des Primärstandards

| Normative Anforderung | Abgeleitete Umsetzung |
| --- | --- |
| Eine Write-Lock-Sperre schützt die URL-Zuordnung und alle schreibenden Methoden; der Client **MUST** das Token bei einer betroffenen Mutation übermitteln. | `PUT`, `DELETE`, `MKCOL`, `PROPPATCH`, `COPY` und `MOVE` prüfen die für Quelle und Ziel geltenden Sperren. Ein fehlendes oder fremdes Token liefert `423 Locked`, ohne Datei, Index oder Journal zu verändern. [RFC 4918 §6.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-6.1), [§7.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.4) |
| Ein exklusiver Lock kollidiert mit jedem weiteren Lock auf derselben Ressource; ein Server **MUST NOT** widersprüchliche aktive Locks erzeugen. | Neue Sperren werden unter derselben globalen Mutationssperre wie Dateioperationen gegen exakte, geerbte und künftig überlappende Sperren geprüft. Eltern- und Kind-Collection können daher nicht widersprüchlich rekursiv gesperrt werden. [RFC 4918 §6.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-6.1), [§7.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.3) |
| Ein `Depth: infinity`-Lock auf einer Collection gilt für alle Mitglieder in beliebiger Tiefe, einschließlich später hinzugefügter Mitglieder. Der Lock Root bleibt die Request-URI. | Die gespeicherte relative Lock-Wurzel wird für jeden Zielpfad sicher auf Vererbung geprüft. Neue Dateien und Unterordner sind sofort geschützt; `lockroot` verweist auch bei der Discovery eines Kindes auf den gesperrten Ordner. [RFC 4918 §7.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.4), [§9.10.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.10.3) |
| LOCK akzeptiert nur Tiefe `0` oder `infinity`; fehlt `Depth`, ist `infinity` der Standard. Ein Server mit Lock-Unterstützung **MUST** unendliche Tiefe unterstützen. | Collections akzeptieren `0` und `infinity`; ohne Header wird rekursiv gesperrt. Dateien werden immer effektiv mit Tiefe 0 gespeichert. Andere Werte liefern `400`. [RFC 4918 §9.10.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.10.3), [§14.17](https://www.rfc-editor.org/rfc/rfc4918.html#section-14.17) |
| `DAV:lockdiscovery` listet die auf einer Ressource wirkenden aktiven Locks und **SHOULD** auch geerbte Collection-Locks sichtbar machen. | `PROPFIND` auf einem Kind zeigt den geerbten Lock mit Umfang, Typ, Tiefe, Restlaufzeit und ursprünglicher Lock-Wurzel. Nicht betroffene Nachbarordner erhalten keine Sperrinformation. [RFC 4918 §15.8](https://www.rfc-editor.org/rfc/rfc4918.html#section-15.8) |
| Ein LOCK ohne Body aktualisiert einen vorhandenen Lock; UNLOCK wird auf die Lock-Wurzel angewendet. | Refresh und UNLOCK müssen exakt die ursprüngliche Lock-URL und das richtige Token verwenden. Ein Versuch auf einem Kind liefert `412` beziehungsweise `409` und verändert die Laufzeit nicht. [RFC 4918 §7.7](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.7), [§9.10.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.10.2), [§9.11](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.11) |
| Bei COPY und MOVE müssen Locks auf den jeweils betroffenen Quell- und Zielressourcen beachtet werden. | Getaggte `If`-Bedingungen können Quell- und Ziel-Token getrennt zuordnen. Eine rekursiv gesperrte Quelle oder ein gesperrtes Ziel wird ohne das jeweils passende Token abgewiesen. [RFC 4918 §7.5](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.5), [§7.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.6) |
| Server **MAY** Lock-Laufzeiten begrenzen und abgelaufene Locks entfernen, um Ressourcenmissbrauch zu vermeiden. | Angeforderte Timeouts werden auf höchstens eine Stunde begrenzt. Abgelaufene Ordnersperren werden atomar bereinigt und blockieren keine Nachfolger. [RFC 4918 §6.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-6.6), [§20.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-20.2) |

## Bedienung und Konfiguration

Es ist keine neue Serveroption nötig. Der Benutzer richtet den bestehenden
WebDAV-Gerätezugang aus **Dokumente auf dem Desktop bearbeiten** in seinem
Client ein. Clients, die einen Arbeitsordner sperren, senden beispielsweise:

```http
LOCK /webdav/files/alex/Projekte HTTP/1.1
Depth: infinity
Timeout: Second-3600
Content-Type: application/xml

<d:lockinfo xmlns:d="DAV:">
  <d:lockscope><d:exclusive/></d:lockscope>
  <d:locktype><d:write/></d:locktype>
</d:lockinfo>
```

Die Antwort enthält das Token ausschließlich im `Lock-Token`-Header und in
`lockdiscovery`. Bei Änderungen an Mitgliedern sendet der Client es im
ressourcengenauen `If`-Header. LibreOffice verwaltet seine Tokens automatisch.
Nautilus, Finder und Explorer benötigen keine zusätzliche Option. FreeFileSync
kann weiterhin ETags verwenden; eine bestehende fremde Sperre wird als
Konflikt gemeldet und nicht still überschrieben.

## Rechte, Sicherheit und Datenschutz

- Nur ein aktiver Gerätezugang mit Schreibrecht kann LOCK oder eine Mutation
  ausführen. Lesezugänge dürfen Sperren sehen, aber nicht anlegen oder lösen.
- Ein ordnergebundenes App-Passwort kann nur innerhalb seines freigegebenen
  Teilbaums sperren. Fremde Benutzerpfade bleiben `404`.
- Pfade werden als normalisierte relative Pfade verglichen; `..`, Symlinks,
  Spezialdateien und interne Steuerpfade bleiben ausgeschlossen.
- Lock-Anlage, Refresh und Freigabe erzeugen Git- und Ereignis-Auditdaten mit
  Wurzel, Tiefe, Benutzer und Ablauf. Token sowie optionaler Owner-Inhalt
  werden nicht protokolliert.
- Lock-Prüfung und Mutation teilen sich eine Prozesssperre. Dadurch kann kein
  paralleler stabiler Dokument-Link die hierarchische Ordnersperre umgehen.
- Ein Lock erteilt keine Freigabe und überträgt keine Daten an externe Dienste.

## Fehler- und Ausfallverhalten

- `400 Bad Request`: ungültige Tiefe, Lock-Art oder XML.
- `409 Conflict`: falsche Lock-Wurzel bei UNLOCK oder fehlender Elternordner.
- `412 Precondition Failed`: falsches Token bei Refresh beziehungsweise eine
  nicht erfüllte `If`-Bedingung.
- `423 Locked`: aktive exklusive Sperre auf Ressource, Vorfahr oder
  überschneidender Nachfahr.
- Ein abgewiesener Vorgang verändert weder Nutzdaten noch Lock-Laufzeit,
  Metadaten, Versionsarchiv, Audit oder Sync-Journal.
- Beim erfolgreichen Löschen einer leeren Lock-Wurzel wird deren Lock entfernt.
  Eindeutig validierte verwaiste Metadaten-Sidecars werden mit dem Ordner
  bereinigt; kanonische Metadaten, Soft-Delete-Nutzdaten, Versionen und Audit
  bleiben in der privaten Systemablage erhalten. Unbekannte Inhalte oder
  portable Exporte verhindern die Ordnerlöschung weiterhin.
- Nach einem Prozessneustart bleiben noch nicht abgelaufene Locks erhalten.
  Abgelaufene Datensätze werden bei der nächsten Lock-Prüfung entfernt.

## Migration und Rückwärtskompatibilität

Es gibt keine Datenmigration. Neue Lock-Datensätze enthalten zusätzlich den
relativen Ressourcenpfad. Alte Datensätze ohne dieses Feld werden weiterhin
als exakte Dateisperren gelesen und beim nächsten Refresh ergänzt. Bestehende
Dateien, App-Passwörter, ETags, Freigaben und Aufbewahrungsregeln ändern sich
nicht.

Clients, die bisher `Depth: infinity` erhielten und wegen `501 Not Implemented`
auf Einzelsperren auswichen, können nun den Standardablauf verwenden.
`Depth: 0` auf Collections bleibt absichtlich verfügbar und schützt nur den
Ordner selbst, nicht seine Mitglieder.

## Automatisierte Tests

Abgedeckt sind:

- rekursiver Schutz bestehender und später angelegter Dateien und Ordner;
- Erfolg mit geerbtem Token sowie Ablehnung ohne oder mit fremdem Token;
- Lock-Discovery auf Kindern und Abschirmung nicht betroffener Nachbarn;
- Kollisionen zwischen Eltern- und Nachfahr-Locks in beiden Richtungen;
- nicht vererbte `Depth: 0`-Collection-Locks;
- COPY mit getrennten Quell- und Zielbedingungen;
- Refresh und UNLOCK ausschließlich an der Lock-Wurzel;
- identischer Schutz über hierarchische und stabile LibreOffice-URLs sowie
  Freigabe der Sperre beim bestätigten Löschen ihrer leeren Wurzel;
- Ablauf und Bereinigung rekursiver Sperren;
- ordnergebundene Gerätezugänge und tokenfreies Audit;
- die vollständige WebDAV-Suite sowie die Gesamt-Suite auf allen in GitHub
  Actions konfigurierten Python-Versionen.

## Bekannte Grenzen und Rückkehr

SimpleOffice unterstützt weiterhin nur exklusive Write-Locks, keine Shared
Locks. Rekursive serverseitige COPY- und MOVE-Operationen für ganze Ordner
sind unabhängig von der Lock-Vererbung nicht implementiert. In einem Cluster
müssen Lock-Speicher und Mutationssperre gemeinsam koordiniert werden; die
eingebaute Prozesssperre ist für eine einzelne Anwendungsinstanz ausgelegt.

Die Funktion wird nicht separat deaktiviert, weil der Server DAV-Klasse 2
ankündigt und rekursive Collection-Locks dafür interoperabel unterstützen muss.
Ein Rollback auf eine ältere Version benötigt keine Konvertierung: Neue
Lock-Datensätze werden dort als exakte Locks behandelt und laufen spätestens
nach einer Stunde ab. Nutzdaten bleiben unverändert.

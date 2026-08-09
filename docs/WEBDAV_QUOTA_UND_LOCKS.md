# WebDAV-Speichergrenzen und robuste Office-Sperren

## Zweck und Nutzen

LibreOffice, Nautilus, Finder, Windows Explorer und Synchronisationswerkzeuge
müssen vor einem Schreibvorgang erkennen können, ob ausreichend Speicher
verfügbar ist. Gleichzeitig müssen länger geöffnete Office-Dokumente ihre
WebDAV-Sperre ausdrücklich verlängern können. Dieser Ausbau verbindet deshalb
die Quota-Eigenschaften aus RFC 4331 mit dem vollständigen Lebenszyklus eines
exklusiven Schreib-Locks aus RFC 4918.

Die Begrenzung ist optional und standardmäßig deaktiviert. Wird sie aktiviert,
werden neue Dateien, wachsende Aktualisierungen und Kopien vor der ersten
Mutation geprüft. Ein abgewiesener Vorgang verändert weder Datei noch Index,
Sync-Journal oder vorhandene Version. Gleich große oder kleinere
Aktualisierungen sowie MOVE/Umbenennen bleiben auch bei ausgeschöpftem Kontingent
möglich.

## Ausgewertete Primärstandards

| Anforderung | Umsetzung in SimpleOffice |
| --- | --- |
| Ein Quota-fähiger Server implementiert `DAV:quota-available-bytes` und `DAV:quota-used-bytes` mindestens auf Collections. | Beide berechneten Live Properties stehen auf authentifizierten Dateibaum-Collections und auf der alten Dokument-Collection zur Verfügung. [RFC 4331 §2](https://www.rfc-editor.org/rfc/rfc4331.html#section-2), [§3](https://www.rfc-editor.org/rfc/rfc4331.html#section-3), [§4](https://www.rfc-editor.org/rfc/rfc4331.html#section-4) |
| `allprop` **SHOULD NOT** die Erweiterungseigenschaften liefern; `propname` **MUST** sie bei endlichem Limit nennen. | `allprop` lässt beide Werte aus. Eine ausdrückliche `prop`-Abfrage liefert Werte, `propname` liefert die Namen. Bei deaktiviertem, unendlichem Limit werden sie mit 404 behandelt. [RFC 4331 §2](https://www.rfc-editor.org/rfc/rfc4331.html#section-2) |
| `quota-used-bytes` **MUST** gegebenenfalls Unterressourcen einschließen und die Berechnung **SHOULD** wiederholbar sein. | Gezählt werden alle regulären, sichtbaren Dateien des verwalteten Dokumentbaums einschließlich Unterordnern. Steuerpfade, Git-Audit, Quarantäne, Papierkorb, Inhaltsarchive, Symlinks und Spezialdateien zählen nicht. [RFC 4331 §4](https://www.rfc-editor.org/rfc/rfc4331.html#section-4) |
| Die Quota-Eigenschaften sind berechnet und geschützt; Schreibversuche sollen 403 mit `cannot-modify-protected-property` liefern. | `PROPPATCH` kann die beiden Werte nicht überschreiben. Ein gemischter Auftrag wird atomar mit 403/424 zurückgerollt. [RFC 4331 §3](https://www.rfc-editor.org/rfc/rfc4331.html#section-3), [§4](https://www.rfc-editor.org/rfc/rfc4331.html#section-4) |
| Überschreitet PUT, COPY oder eine andere Allokation das Kontingent, **SHOULD** 507 mit `quota-not-exceeded` verwendet werden. Physischer Platzmangel verwendet `sufficient-disk-space`. | Positive Größenänderungen werden unter derselben WebDAV-Mutationssperre wie der anschließende Schreibvorgang geprüft. Die XML-Fehlerbedingung unterscheidet Kontingent und freien Datenträger. [RFC 4331 §6](https://www.rfc-editor.org/rfc/rfc4331.html#section-6) |
| Ein neuer LOCK **MUST** ein `lockinfo`-XML enthalten und die Antwort **MUST** `lockdiscovery` sowie bei neuen Locks `Lock-Token` enthalten. | Nur `exclusive`/`write` wird akzeptiert. Eigentümer, Lock-Wurzel, Tiefe, Restlaufzeit und Token erscheinen in LOCK-Antwort und PROPFIND. [RFC 4918 §9.10.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.10.1), [§14.11](https://www.rfc-editor.org/rfc/rfc4918.html#section-14.11) |
| Ein LOCK ohne Body **MUST NOT** einen neuen Lock erzeugen; er aktualisiert genau den mit einem einzelnen Token im `If`-Header bezeichneten Lock. Der `Depth`-Header wird bei Refresh ignoriert und die Antwort enthält keinen neuen `Lock-Token`-Header. | Fehlender oder fremder Token liefert 412. Ein gültiger Refresh startet die begrenzte Laufzeit neu, bewahrt Eigentümer und Erstellungszeit und liefert aktualisiertes `lockdiscovery`. [RFC 4918 §7.7](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.7), [§9.10.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.10.2) |
| Ein erfolgreicher LOCK auf einer noch nicht belegten URL **MUST** eine gesperrte leere reguläre Ressource anlegen. | Die leere Datei wird atomar als normales Dokument mit ID, Audit und Sync-Eintrag angelegt. Sie ist sofort per GET und PROPFIND sichtbar. Ein späteres PUT mit Lock-Token aktualisiert sie; UNLOCK vor PUT lässt die leere Datei bestehen. [RFC 4918 §7.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.3), [§9.10.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.10.4) |
| LOCK-Tiefe darf nur `0` oder `infinity` sein. | Dateisperren werden effektiv mit Tiefe 0 gespeichert. Collection-Depth-0 ist möglich; rekursive Collection-Sperren werden mit 501 abgewiesen, weil deren sichere Vererbung noch nicht implementiert ist. [RFC 4918 §9.10.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.10.3) |

## Konfiguration und Bedienung

Ein globales Kontingent für den sichtbaren verwalteten Dokumentbaum wird in
MiB gesetzt:

```bash
SIMPLEOFFICE_WEBDAV_QUOTA_MIB=10240 ./start.sh
```

Das Beispiel begrenzt den Dateibaum auf 10 GiB. Unter Windows wird vor
`start.bat` entsprechend gesetzt:

```bat
set SIMPLEOFFICE_WEBDAV_QUOTA_MIB=10240
start.bat
```

Zulässig sind positive Ganzzahlen bis 1 TiB. `0`, ein leerer, negativer oder
ungültiger Wert deaktiviert die Begrenzung. Die Seite **Dokumente auf dem
Desktop bearbeiten** zeigt Belegung und Obergrenze. Standardkonforme Clients
können auf einer Collection direkt abfragen:

```xml
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:quota-used-bytes/>
    <d:quota-available-bytes/>
  </d:prop>
</d:propfind>
```

LibreOffice erzeugt einen exklusiven Schreib-Lock beim Öffnen und kann ihn mit
einem leeren LOCK sowie seinem Token verlängern. Benutzer müssen dafür nichts
zusätzlich konfigurieren. FreeFileSync und Dateimanager können ohne LOCK mit
ETag-Vorbedingungen arbeiten; vorhandene Dateien lassen sich weiterhin nicht
blind überschreiben.

## Rechte, Sicherheit und Datenschutz

- Quota-Werte sind erst nach erfolgreicher App-Passwort-Authentifizierung
  sichtbar. Fremde Benutzerpfade bleiben 404.
- Nur ein Schreibzugang darf LOCK, PUT oder COPY ausführen. Lesezugänge sehen
  die Kapazität, können sie aber nicht verändern.
- Eine Quota-Ablehnung wird als `webdav_quota_rejected` mit Benutzer,
  Ressourcenpfad, Vorgang, Größenwachstum, Nutzung und Limit im Git-Audit
  festgehalten. Dateiinhalte und Zugangsdaten werden nicht protokolliert.
- Lock-Anlage, ausdrücklicher Refresh und Freigabe werden getrennt im
  Git-Audit festgehalten. Lock-Token und Owner-Inhalt werden nicht gespeichert;
  das Audit enthält nur Pfad, Tiefe, Ablauf und ob ein Owner angegeben war.
- Die verfügbare Zahl ist das Minimum aus Kontingentrest und aktuell freiem
  Datenträger. Dadurch verspricht der Server nicht mehr Platz, als physisch
  verfügbar ist.
- Prüfung und WebDAV-Mutation laufen unter derselben Prozesssperre. Ein
  Cluster benötigt weiterhin einen gemeinsam koordinierten Schreibpfad und
  eine verteilte Sperre.
- LOCK-XML unterliegt denselben Größen-, Knoten-, DTD- und Entity-Grenzen wie
  PROPFIND und PROPPATCH. Nur exklusive Schreibsperren sind erlaubt.

## Formate, Kompatibilität sowie Fehler- und Ausfallverhalten

- Werte sind Dezimalzahlen in Bytes; MiB werden nur in der Weboberfläche zur
  leichteren Bedienung angezeigt.
- `507 DAV:quota-not-exceeded` bedeutet, dass der konfigurierte Anteil nicht
  reicht. `507 DAV:sufficient-disk-space` bezeichnet den physischen
  Datenträger.
- Ein fehlgeschlagener Quota-Check legt keine Temporärdatei an. Scheitert der
  spätere atomare Schreibvorgang, bleibt die vorherige Datei erhalten.
- `400` meldet ungültige Lock-Tiefe, Lock-Typ oder Lock-XML; `412` einen
  fehlenden/falschen Refresh-Token; `423` einen kollidierenden Lock; `501` eine
  nicht unterstützte rekursive Collection-Sperre.
- Alte Lock-Dateien ohne `href` oder `depth` bleiben lesbar und erhalten diese
  Felder beim nächsten erfolgreichen Refresh.

## Migration und Rückwärtskompatibilität

Es gibt keine Datenbank- oder Dateimigration. Ohne die neue Umgebungsvariable
bleibt das bisherige unbegrenzte Verhalten erhalten und Quota-Eigenschaften
antworten als nicht vorhanden. Vorhandene Dateien werden nie gelöscht oder
automatisch verkleinert, wenn ein Limit nachträglich unter die aktuelle
Belegung gesetzt wird. Der Server verweigert dann nur weiteres Wachstum;
kleinere Aktualisierungen, MOVE, Download und kontrolliertes Löschen bleiben
möglich.

Die frühere Lock-null-Implementierung reservierte nur den Namen. Neue Locks
legen stattdessen die von RFC 4918 geforderte leere Ressource an. Das entspricht
dem Verhalten aktueller WebDAV-Clients; ein anschließendes PUT antwortet deshalb
mit 204 statt 201.

## Automatisierte Tests

Abgedeckt sind:

- explizite Quota-PROPFIND-Antworten, `propname`, Ausschluss aus `allprop` und
  404 bei deaktiviertem Limit;
- Schutz gegen PROPPATCH und sichtbare Anzeige in der Weboberfläche;
- erlaubte Neuanlage bis zur Grenze sowie atomare 507-Ablehnung für PUT,
  wachsende Aktualisierung und COPY;
- kleinere Aktualisierung und MOVE bei ausgeschöpftem Kontingent;
- Audit-Einträge ohne Dateiinhalte;
- neuer Lock, Lock-Konflikt, expliziter Refresh, bewahrter Eigentümer,
  aktualisierte Laufzeit und vollständiges `lockdiscovery`;
- leere Ressource nach LOCK auf unbelegter URL, Sichtbarkeit in GET/PROPFIND,
  anschließendes PUT und UNLOCK;
- ungültige Tiefe, Shared-Lock, rekursive Collection-Sperre, falscher und
  fehlender Refresh-Token sowie Rechte- und XML-Grenzen.

## Bekannte Grenzen und Deaktivierung

Das Kontingent gilt für den gemeinsam verwalteten sichtbaren Dokumentbaum und
nicht für einzelne Unterordner oder Benutzer. Private Wiederherstellungs- und
Versionsdateien werden bewusst nicht eingerechnet, da sie über bestehende
Aufbewahrungs- und Backup-Regeln verwaltet werden. Serverinterne Web-Importe
werden bei der nächsten Abfrage mitgezählt, aber die neue Schranke greift nur
bei WebDAV-PUT und -COPY. Eine künftige benutzerspezifische Abrechnung benötigt
eine verbindliche Dateieigentümerschaft und darf nicht aus URL-Namen abgeleitet
werden.

Rekursive Collection-Locks, Shared Locks und WebDAV ACL sind nicht
implementiert. Zur Rückkehr zum vorherigen Verhalten wird
`SIMPLEOFFICE_WEBDAV_QUOTA_MIB=0` gesetzt und der Dienst neu gestartet. LOCK
kann nicht separat deaktiviert werden, ohne die angekündigte DAV-Klasse 2 und
Desktop-Kompatibilität anzupassen; bestehende Locks laufen spätestens nach der
serverseitig begrenzten Stunde ab oder werden regulär per UNLOCK entfernt.

# WebDAV-Principal- und Rechteerkennung nach RFC 3744 und RFC 5397

## Zweck und Nutzen

SimpleOffice stellt dem angemeldeten WebDAV-Client jetzt eine stabile
Principal-URL und den für die angefragte Ressource wirksamen Rechteumfang des
verwendeten Gerätezugangs bereit. ACL-fähige Dateimanager und Office-Clients
können damit schreibgeschützte Elemente erkennen und Schreibaktionen in ihrer
Oberfläche deaktivieren, bevor eine Datei unnötig übertragen wird.

Die Erkennung ist ausschließlich lesend. Ein Desktop-Client kann weder
Benutzer suchen noch ACLs, Freigaben, Ordnergrenzen oder Aufbewahrungsregeln
ändern. SimpleOffice kündigt deshalb **nicht** den DAV-Headerwert
`access-control` und keine vollständige RFC-3744-Konformität an.

## Maßgebliche Originalstandards

### RFC 3744 – WebDAV Access Control Protocol

| Stufe | Originalanforderung | Implementierte Entscheidung |
|---|---|---|
| MUST für das Privilegienmodell | Die Fähigkeit, eine Methode auszuführen, wird durch Privilegien kontrolliert; bei verborgenen Ressourcen darf `404` statt `403` verwendet werden. | Authentifizierung, Benutzer, Geräteordner und `read`/`write` werden weiterhin vor der Operation geprüft. Außerhalb der eigenen Grenze bleibt die Ressource unsichtbar. Siehe [RFC 3744 §3](https://www.rfc-editor.org/rfc/rfc3744.html#section-3). |
| Zweckvorgabe | `DAV:read-current-user-privilege-set` schützt das Lesen des aktuellen Privilegiensatzes. Dieser ist ausdrücklich dafür vorgesehen, nicht erlaubte UI-Aktionen zu deaktivieren. | Jeder gültige Gerätezugang darf seinen eigenen, ressourcengenauen Privilegiensatz lesen. Andere Benutzer oder Credentials werden nicht aufgelistet. Siehe [§3.7](https://www.rfc-editor.org/rfc/rfc3744.html#section-3.7). |
| Protected Property | `DAV:principal-URL` identifiziert einen Principal eindeutig; `DAV:owner` verweist auf einen Principal. | `/webdav/principals/<Benutzer>/self` ist der stabile, geschützte Benutzer-Principal. Dateien und Ordner liefern ihn auf ausdrückliche Anfrage als `owner`. Siehe [§4.2](https://www.rfc-editor.org/rfc/rfc3744.html#section-4.2) und [§5.1](https://www.rfc-editor.org/rfc/rfc3744.html#section-5.1). |
| Protected Property | `DAV:current-user-privilege-set` enthält die dem aktuellen Benutzer gewährten Privilegien. Access-Control-Properties sollten wegen Aufwand und Sensibilität nicht ungefragt in `allprop` erscheinen. | Der Wert wird pro Anfrage aus Geräteumfang und Ressourcentyp berechnet, nie als Dead Property gespeichert und nur per explizitem `prop`, `propname` oder `allprop/include` ausgegeben. Siehe [§5.4](https://www.rfc-editor.org/rfc/rfc3744.html#section-5.4) und [§5](https://www.rfc-editor.org/rfc/rfc3744.html#section-5). |
| MUST für ACL-Server | `DAV:principal-collection-set` benennt Sammlungen, in denen Principals liegen. | Die Eigenschaft verweist ausschließlich auf die private Sammlung des angemeldeten Benutzers. `Depth: 1` enthält genau dessen `self`-Principal. Siehe [§5.8](https://www.rfc-editor.org/rfc/rfc3744.html#section-5.8). |
| MUST für ACL-Fehler | Ein privilegienbedingtes `403` enthält `DAV:need-privileges` mit Ressource und fehlendem Privileg. | Schreibversuche eines Lesezugangs liefern XML mit dem angefragten Pfad und konservativ `write`, bei PROPPATCH `write-properties`, bei UNLOCK `unlock`. Es wird kein fremder Elternpfad offengelegt. Siehe [§7.1.1](https://www.rfc-editor.org/rfc/rfc3744.html#section-7.1.1). |
| MUST bei Compliance-Werbung | `DAV: access-control` darf nur gemeldet werden, wenn alle MUST-/REQUIRED-Teile einschließlich ACL-Methode und Principal-Suche unterstützt sind. | Der Token wird bewusst nicht beworben. ACL-Methode, `DAV:acl`, Gruppen- und Principal-Suche fehlen absichtlich. Siehe [§7.2](https://www.rfc-editor.org/rfc/rfc3744.html#section-7.2) und [§9.4](https://www.rfc-editor.org/rfc/rfc3744.html#section-9.4). |
| Sicherheitsanforderung | ACL- und Privileginformationen können sensible Benutzer- und Strukturangaben offenlegen. Basic Authentication ist nur über sicheren Transport zulässig. | Nur der authentifizierte Benutzer sieht seinen eigenen Principal; fremde Namen liefern `404`. App-Passwörter bleiben HTTPS-pflichtig und Antworten `private, no-store`. Siehe [§12.2](https://www.rfc-editor.org/rfc/rfc3744.html#section-12.2) und [§13](https://www.rfc-editor.org/rfc/rfc3744.html#section-13). |

### RFC 5397 – Current Principal Extension

`DAV:current-user-principal` ist eine geschützte, pro Anfrage berechnete
Eigenschaft und enthält für einen authentifizierten Benutzer genau einen
`DAV:href`. Die URL muss konsistent auf dessen Principal-Ressource verweisen
und wird bei COPY/MOVE nie als gespeicherte Property übernommen. SimpleOffice
setzt diese Vorgaben direkt um; siehe
[RFC 5397 §3](https://www.rfc-editor.org/rfc/rfc5397.html#section-3).

## Datenmodell und URLs

Es wird keine neue Datenbank- oder Metadatendatei angelegt. Der Principal wird
aus dem bereits authentifizierten SimpleOffice-Benutzer abgeleitet:

```text
/webdav/principals/BENUTZER/
/webdav/principals/BENUTZER/self
```

Die erste URL ist eine private Principal-Sammlung; die zweite repräsentiert
den Benutzer. Verschiedene App-Passwörter desselben Kontos verwenden dieselbe
Principal-URL, melden aber ihren jeweils kleineren effektiven Geräteumfang.
Der Principal liefert auf ausdrückliche PROPFIND-Anfrage:

- `DAV:displayname` und `DAV:resourcetype` mit `DAV:principal`;
- `DAV:principal-URL`;
- eine leere `DAV:alternate-URI-set`;
- eine leere `DAV:group-membership`, weil keine WebDAV-Gruppen veröffentlicht
  werden;
- den aktuellen Principal und den aktuellen Privilegiensatz.

Die Sammlung akzeptiert ausschließlich `OPTIONS` und `PROPFIND` mit
`Depth: 0` oder `1`. Unbekannte Principal-IDs, andere Benutzernamen und
Zugriffe außerhalb der Authentifizierung antworten ohne Namensauskunft.

## Abbildung der wirksamen Rechte

| Gerätezugang/Ressource | Gemeldete Privilegien |
|---|---|
| Nur lesen, Datei oder Ordner | `read`, `read-current-user-privilege-set` |
| Lesen und schreiben, Datei | zusätzlich `write`, `write-properties`, `write-content`, `unlock` |
| Lesen und schreiben, Ordner | zusätzlich zu Datei-Rechten `bind` und `unbind` für Mitglieder |

`write-acl` wird nie gemeldet. Die Rechteanzeige beschreibt den grundsätzlichen
Geräteumfang an der sichtbaren Ressource. Eine konkrete Mutation kann trotzdem
an ETag, `If`-Header, Lock, Aufbewahrung, Quarantäne, Namensregeln, Quota oder
Virenprüfung scheitern. Diese Bedingungen werden nicht fälschlich als
dauerhafte ACL-Rechte modelliert.

## Bedienung und Client-Kompatibilität

Es ist keine neue Konfiguration erforderlich. LibreOffice, FreeFileSync,
Nautilus/GNOME Files, Windows Explorer und Finder verwenden weiterhin die
vorhandene HTTPS-WebDAV-Adresse mit Benutzername und App-Passwort.

- Clients können `current-user-privilege-set` explizit abfragen und bei einem
  Lesezugang Speichern, Umbenennen oder Löschen deaktivieren.
- Ein Client kann mit `current-user-principal` die eigene Principal-Ressource
  finden und deren Anzeigenamen lesen.
- Unbekannte Properties dürfen Clients gemäß WebDAV ignorieren; bestehende
  Verbindungen verhalten sich deshalb unverändert.
- FreeFileSync muss seine Konfliktvorschau weiterhin auswerten. Ein gemeldetes
  Schreibrecht ersetzt weder ETag noch Lock-Token.

## Rechte, Sicherheit, Datenschutz und Audit

- Die Principal-Ressource ist an die erfolgreiche Basic-Authentifizierung
  gebunden. Sie ermöglicht keine zweite Anmeldung und enthält kein Passwort,
  Credential-ID, Hash, Ablaufdatum oder Ordnernamen.
- Ein ordnergebundener Zugang kann durch Principal-URLs keine Eltern- oder
  Geschwisterressourcen ermitteln.
- `owner`, `principal-URL`, `principal-collection-set`,
  `current-user-principal`, `current-user-privilege-set` und Principal-
  Stammdaten sind geschützte Live Properties. PROPPATCH liefert `403` und
  ändert keine Metadaten.
- Rechteabfragen sind reine Leseoperationen und erzeugen keine künstlichen
  Auditereignisse. Abgewiesene oder erfolgreiche Dateiänderungen behalten die
  bestehende vollständige Ereignis- und Versionshistorie.
- Antworten sind mit `Cache-Control: private, no-store` und
  `Vary: Authorization, Depth` gegen gemeinsame Caches abgegrenzt.
- Es erfolgt keine externe Datenübertragung und keine automatische Freigabe.

## Fehler- und Ausfallverhalten

- `401`: Zugangsdaten fehlen, sind ungültig, abgelaufen oder widerrufen.
- `404`: Benutzername oder Principal-ID stimmt nicht mit dem angemeldeten
  Konto überein. Der Server unterscheidet absichtlich nicht zwischen fehlend
  und verborgen.
- `403` mit `DAV:need-privileges`: ein Lesezugang versucht zu schreiben.
- `403` ohne Teilergebnis: Principal-PROPFIND verlangt eine unzulässige Tiefe.
- `400`/`413`: Property-XML ist ungültig oder überschreitet die bestehenden
  XML-Schutzgrenzen.
- `405`: Principal-Ressourcen erlauben keine PUT-, PROPPATCH-, DELETE-, ACL-
  oder Suchmethode.

Alle Antworten werden vollständig im Speicher aufgebaut. Es gibt weder
Teilmutation noch Principal-Persistenz, die nach einem Prozessabbruch
wiederhergestellt werden müsste.

## Migration, Rückwärtskompatibilität und Rückkehr

Keine Datei, ACL, App-Passwortdatei, Ordnerpolitik oder Aufbewahrungsregel wird
migriert. Die neuen Werte sind ausschließlich berechnete HTTP-Eigenschaften.
Clients ohne Principal-Unterstützung sehen keine Änderung, weil die
Erweiterungsproperties nicht in einer normalen `allprop`-Antwort erscheinen.

Ein Gerätezugang lässt sich wie bisher einzeln widerrufen. **Alle Zugänge
widerrufen** deaktiviert WebDAV einschließlich Principal-Erkennung. Das
Entfernen der Principal-Route und der berechneten Live Properties stellt das
frühere Verhalten ohne Datenrückmigration wieder her.

## Automatisierte Tests und bekannte Grenzen

Die Tests prüfen:

- stabile und zueinander passende `owner`-, Current-Principal- und Principal-
  Collection-URLs;
- unterschiedliche Privilegiensätze für Lesezugang, Schreibdatei und
  Schreibordner;
- Principal-PROPFIND mit Depth 0/1 und allen erforderlichen Basisproperties;
- `404` für fremde Benutzer und unbekannte Principal-IDs sowie `401` ohne
  Zugangsdaten;
- Ausschluss aus `allprop`, ausdrückliches `allprop/include` und PROPPATCH-
  Schutz;
- strukturiertes `DAV:need-privileges` bei verweigerten Schreibmethoden;
- den virtuellen WebDAV-Wurzelendpunkt ohne persistente Dateisystemzeit.

Bewusste Grenzen: Es gibt keine `DAV:acl`, ACL-Methode, Gruppenverwaltung,
veränderbare ACEs, Principal-Suche oder `access-control`-Compliance-Werbung.
Eine vollständige RFC-3744-Implementierung würde ein fachliches Gruppen- und
Freigabemodell einschließlich sicherer ACL-Migration benötigen und darf nicht
nebenbei Rechte öffnen.

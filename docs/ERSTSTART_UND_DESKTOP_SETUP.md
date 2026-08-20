# Erststart und Desktop-Einrichtung

## Ziel

Der Assistent unter **Einrichten** führt einen Benutzer wie die Oberfläche
eines Routers oder NAS durch die persönliche Anbindung. Er verändert keine
Freigaben automatisch. WebDAV/SFTP, CalDAV und CardDAV erhalten getrennte
App-Passwörter; diese werden nur unmittelbar nach dem Erzeugen angezeigt.
Die herunterladbare Geräteanleitung enthält absichtlich keine Geheimnisse.

## Sicherer Erststart

1. SimpleOffice zunächst nur lokal starten und das erste Benutzerkonto
   anlegen. In einer Einbenutzerinstallation ist dieses Konto
   Bootstrap-Administrator. Bei mehreren Benutzern werden Administratoren mit
   `SIMPLEOFFICE_DOCUMENT_ADMINS` ausdrücklich benannt.
2. Vor Zugriff aus dem Netz HTTPS gemäß [Proxy-Anleitung](PROXY_HTTPS.md)
   aktivieren. Der Assistent sperrt das Erzeugen von App-Passwörtern bei einer
   unverschlüsselten, nicht lokalen HTTP-Verbindung.
3. **Einrichten → Neue getrennte App-Passwörter erzeugen** wählen und alle drei
   Werte sofort in den jeweiligen Passwortspeicher übernehmen.
4. Unter **WebDAV und Ordnerrechte** je Ordner `Lesen`, `Schreiben` oder
   `Verwalten` vergeben. Unterordner erben Rechte nur entsprechend der
   sichtbaren Einstellung. Zugangsdaten können diese ACL nie erweitern.
5. Geräte verbinden und den Assistenten als abgeschlossen markieren. Dieser
   Zustand ist je Benutzer revisionssicher protokolliert und kann den
   Assistenten nicht ausblenden oder sperren.

## Windows

### Dateien mit Explorer und LibreOffice

Im Explorer **Dieser PC → Netzwerkadresse hinzufügen** wählen und die im
Assistenten angezeigte `https://…/webdav/files/BENUTZER/`-Adresse eintragen.
LibreOffice kann dieselbe Adresse über **Datei → Öffnen → Entfernte Dateien**
verwenden. Windows-WebDAV über unverschlüsseltes HTTP wird nicht unterstützt;
unsichere Registry-Lockerungen sind weder nötig noch empfohlen.

### Kalender und Kontakte

Thunderbird erkennt den persönlichen CalDAV-Kalender über
`https://SERVER/caldav/calendars/BENUTZER/`. CardBook oder ein anderer
CardDAV-Client erhält
`https://SERVER/carddav/addressbooks/BENUTZER/contacts/`. Für jeden Dienst ist
das gleichnamige, getrennte App-Passwort einzutragen.

### SFTP-Laufwerk

Windows stellt SFTP nicht nativ als Laufwerk bereit. Optional können
[WinFsp](https://winfsp.dev/) und
[SSHFS-Win](https://github.com/winfsp/sshfs-win) installiert werden. Der
Assistent zeigt den passenden UNC-Pfad. Der SimpleOffice-Dienst bietet nur
SFTP, keine Shell und keine Befehlsausführung.

## Linux

Nautilus akzeptiert unter **Andere Orte → Mit Server verbinden** sowohl
`davs://SERVER/webdav/files/BENUTZER/` als auch
`sftp://BENUTZER@SERVER:2222/`. Für ein dauerhaftes virtuelles Verzeichnis:

```bash
mkdir -p ~/SimpleOffice
sshfs -p 2222 BENUTZER@SERVER:/ ~/SimpleOffice
# später aushängen
fusermount3 -u ~/SimpleOffice
```

Der SFTP-Dienst wird getrennt vom Webprozess gestartet. Installation und
Host-Schlüssel beschreibt [Virtuelles Dateisystem und SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md).
Der Assistent meldet, ob `SIMPLEOFFICE_SFTP_HOST_KEY` auf eine vorhandene Datei
zeigt. Firewall und Reverse Proxy werden nicht automatisch geöffnet.

## Protokolle und Standards

- WebDAV folgt [RFC 4918](https://www.rfc-editor.org/rfc/rfc4918), insbesondere
  Authentisierung und Transport aus Abschnitt 20 sowie die Methoden aus
  Abschnitten 8–9. Konfliktschutz und Locks bleiben Teil des bestehenden
  WebDAV-Endpunkts.
- CalDAV basiert auf [RFC 4791](https://www.rfc-editor.org/rfc/rfc4791),
  insbesondere Kalenderzugriff aus Abschnitt 5.
- CardDAV basiert auf [RFC 6352](https://www.rfc-editor.org/rfc/rfc6352),
  insbesondere Adressbuchsammlungen aus Abschnitt 5.
- Service Discovery folgt den Empfehlungen aus
  [RFC 6764](https://www.rfc-editor.org/rfc/rfc6764), Abschnitte 6–7.
- SSH-Transport und Authentisierung beruhen auf
  [RFC 4253](https://www.rfc-editor.org/rfc/rfc4253) und
  [RFC 4252](https://www.rfc-editor.org/rfc/rfc4252). SimpleOffice bietet
  bewusst nur das SFTP-Subsystem.

## Sicherheit, Fehler und Rückkehr

Passwörter werden als gesalzene Scrypt-Prüfwerte gespeichert, nie im Export
oder Audit. Das WebDAV/SFTP-Passwort ist pro Gerät widerrufbar und zeitlich
begrenzt; CalDAV und CardDAV können durch erneutes Aktivieren rotiert werden.
Fällt ein Dienst aus, bleiben lokale Dateien, Kalender und Kontakte erhalten.
Das Abschalten des SFTP-Prozesses beeinflusst WebDAV nicht. Eine Rückkehr zum
reinen Browserbetrieb erfordert nur das Widerrufen der App-Zugänge; Daten,
Versionen, Aufbewahrung und Ordnerrechte werden dabei nicht verändert.

Bekannte Grenze: Der Assistent installiert keine Betriebssystempakete, erzeugt
keine Zertifikate und verändert keine Firewall. Dadurch erfolgen keine
unerwarteten Rechte- oder Netzfreigaben. macOS Finder kann WebDAV mit derselben
URL verwenden; die geführte Oberfläche konzentriert sich zunächst auf Windows
und Linux.

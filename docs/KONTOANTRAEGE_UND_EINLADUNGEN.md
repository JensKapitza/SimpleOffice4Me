# Konten, Freigaben und Einladungen

Die vorhandene Administration (`/admin/users`) bietet **+ Benutzer** und
**Kontoanträge**. Das gilt auch für die Android-Weboberfläche. Die bestehenden
Konten, Administratorrollen und acht Funktionsberechtigungen bleiben maßgeblich;
es entsteht kein zweites Benutzer- oder Rechtesystem.

## Konto beantragen

Nach Einrichtung des ersten Administrators führt **Konto beantragen** auf der
Loginseite zum Antragsformular. Benutzername: 3–64 ASCII-Zeichen aus Buchstaben,
Ziffern, Punkt, Unterstrich und Bindestrich; gültige E-Mail bis 254 Zeichen;
Passwort: 12–128 Zeichen. Ein Antrag erzeugt noch kein anmeldbares Konto.
Gleiche Benutzernamen einschließlich Groß-/Kleinschreibvarianten erhalten dieselbe
öffentliche Bestätigung. Anträge und Passwörter werden nicht an andere Benutzer
angezeigt. Passwörter werden ausschließlich gehasht gespeichert.

Administratoren sehen offene Anträge in der Navigation und der Benutzerverwaltung.
Sie können einen Antrag freigeben oder ablehnen. Administratorrechte benötigen
eine ausdrückliche Auswahl. Normale neue Konten haben zunächst alle bestehenden
Funktionsrechte deaktiviert; diese werden in der Benutzerverwaltung gezielt
freigegeben. Nach Ablehnung bleibt die Anmeldung unmöglich. Nach Entscheidung
wird der nicht mehr benötigte Passwort-Hash aus dem Antrag entfernt.

Die optionale Zuordnung **Übergeordneter Benutzer** verweist auf ein aktives
bestehendes Konto und erscheint in der Benutzerverwaltung. Sie ist eine
organisatorische Zuordnung: keine automatische Rechtevererbung und keine
Verwaltungsrechte für den übergeordneten Benutzer. Nur Administratoren legen sie
bei Freigabe oder Einladung fest.

## Sicher einladen

**+ Benutzer** erfasst Benutzername, E-Mail, Rolle und optionale Zuordnung.
Das angelegte Konto bleibt bis zum Setzen eines Passworts gesperrt.
Der erzeugte Link enthält einen zufälligen Einmal-Token im URL-Fragment:
`/auth/account-requests/invite#token=…` (Notation, kein tatsächliches Geheimnis).
Der Browser übernimmt ihn in das geschützte POST-Formular und entfernt das
Fragment aus der Adressleiste. Der Token gelangt dadurch nicht in den HTTP-Pfad
oder Referer. Auf dem Server liegt nur sein SHA-256-Hash. Antwortseiten tragen
`Cache-Control: no-store` und `Referrer-Policy: no-referrer`.

Den Link sicher übermitteln. Wenn ein eigenes E-Mail-Konto konfiguriert ist,
kann der Administrator den Versand ausdrücklich auswählen. Das Absenderkonto
muss zuvor in den bestehenden Mail-Einstellungen zum Schreiben freigegeben sein;
neue Mailkonten sind standardmäßig schreibgeschützt. Dafür wird der
vorhandene SMTP-Service mit den bestehenden TLS- und Eigentümerprüfungen benutzt.
Es wird niemals ein Passwort per E-Mail versandt. Bei nicht bestätigtem Versand
bleibt die Einladung nutzbar; Zustellung prüfen oder den Link sicher übermitteln.
Bei unklarem SMTP-Ergebnis wird nicht automatisch erneut gesendet. Das vorhandene
private Mailarchiv enthält die versandte Einladung; es unterliegt dem üblichen
Mailzugriff und darf nicht öffentlich veröffentlicht werden.

Offene und abgelaufene Einladungen können in **Kontoanträge** neu ausgestellt
oder widerrufen werden. Eine Neuausstellung macht den bisherigen Link ungültig;
Widerruf hält das Konto gesperrt. Bereits aktivierte Konten können über diesen
Ablauf nicht zurückgesetzt werden. Sperren und Rechteänderungen erfolgen weiterhin
in der bestehenden Benutzerverwaltung.

## Einstellungen und API-Verträge

Administratoren können Kontoanträge deaktivieren, die Einladungsdauer
(1–10080 Minuten, Standard 1440) und die Antragsquote
(1–100 pro Stunde und Clientadresse, Standard 5) setzen.
Ungültige Einstellungen liefern HTTP 400 und verändern die gespeicherten Werte
nicht. Deaktivierte Anträge liefern 403; administrative Einladungen bleiben möglich.
Bestehende Einladungen behalten ihre ursprüngliche Ablaufzeit. Neue und neu
ausgestellte Einladungen verwenden die aktuelle Einstellung.

| API | Vertrag |
| --- | --- |
| GET/POST `/auth/account-requests/new` | Öffentliches CSRF-geschütztes Formular; ungültig 400, deaktiviert 403, Quote überschritten 429, gültig 302 zur Loginseite |
| GET `/auth/account-requests/admin` | Nur Administratoren; Anträge, Einstellungen, offene Einladungen |
| POST `/auth/account-requests/admin/<request_id>` | `decision=approve` oder `reject`, optional `is_admin=1` und `parent_user_id`; bereits entschieden/unbekannt 404, Namenskonflikt 409 |
| POST `/auth/account-requests/admin/registration` | `enabled=1`, `invitation_minutes`, `requests_per_hour`; 302 bei Erfolg |
| GET/POST `/auth/account-requests/admin/invite` | Admin-Formular; Namenskonflikt 409, ungültige Eingabe 400, unbekannte Zuordnung 404; Erfolg 200 mit einmal angezeigtem Link |
| GET `/auth/account-requests/invite` | Öffentliches Passwortformular; Gültigkeit erst beim POST geprüft |
| POST `/auth/account-requests/invite` | `invitation_token`, `password`; unbekannt/abgelaufen/widerrufen/verwendet 404, Passwort ungültig 400; Erfolg 302 zur Loginseite |
| POST `/auth/account-requests/admin/invitations/<user_id>` | `action=reissue` oder `revoke`; kein offenes gesperrtes Zielkonto 404 |

Alle Änderungen benötigen CSRF. Unbefugte administrative Zugriffe liefern 403.
Die Einlösung erlaubt höchstens 30 Versuche je Clientadresse und Stunde;
Antragsquoten zählen auch fehlerhafte Eingaben. Quoten verwenden HMAC-Schlüssel
anstelle gespeicherter Klartext-IP-Adressen. Die Clientadresse entspricht der
bestehenden Reverse-Proxy-Konfiguration; kein Vertrauen in frei gesetzte Header.
Gleichzeitige Freigaben/Einlösungen werden transaktional serialisiert.
Audit-Ereignisse ordnen Aktionen dem Administrator und dem Antrag bzw. Konto zu;
Passwörter und Einladungs-Token werden nicht protokolliert.

Die Migration erweitert die vorhandene Authentifizierungsdatenbank additiv.
Bestehende Benutzer, Passwörter, Rollen und Rechte werden erhalten. Bibliothek,
Inventar und Einkauf verwenden das bestehende Dokumentrecht; Mailreader und
Mailindex verwenden das bestehende Mailrecht. Google-OAuth bleibt der bestehende,
separat konfigurierte Identitätsanbieter: automatische Anlage weiterer Identitäten
ist nur nach der bisherigen expliziten `GOOGLE_OAUTH_AUTO_PROVISION`-Freigabe
zulässig und wird durch diesen lokalen Antragsablauf nicht neu aktiviert.

Der Legacy-Schalter `SIMPLEOFFICE_ALLOW_PUBLIC_REGISTRATION` erlaubt keine
direkte öffentliche Kontenaktivierung mehr. Kontoanträge werden über die
bestehende administrative Einstellung gesteuert, initial aktiviert und auf
fünf Versuche je Clientadresse und Stunde begrenzt.

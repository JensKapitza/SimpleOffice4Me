# Login-Adressen als QR-Code

Die Loginseite zeigt die aktuelle Anmeldeadresse als QR-Code und kopierbaren
Link. Ein QR-Code ist ausschließlich ein URL-Kürzel: Er überträgt keine
Passwörter, Sitzungen oder Freigaben. Nach dem Öffnen ist die normale Anmeldung
und gegebenenfalls die bestehende Adminfreigabe weiterhin erforderlich.
Die Bildgenerierung verwendet den bestehenden ReportLab-Renderer, der bereits
für Federation und Bildschirmfreigabe genutzt wird. Es ist keine zusätzliche
QR-Bibliothek erforderlich.

Bei direkt verbundenen lokalen Clients erscheinen außerdem erkannte private
IPv4-Adresskandidaten. Sowohl Clientadresse als auch aufgerufener Host müssen
lokal sein; als Host zählt auch `localhost`. Bei öffentlichen Hosts oder
Forwarding-Headern (`Forwarded`, `X-Forwarded-For`, `X-Real-IP`) bleibt nur die
aktuelle Adresse sichtbar. Das gilt auch bei einem Reverse-Proxy auf Loopback.
Die bestehende Proxy-Konfiguration bleibt maßgeblich; unbestätigte Header
schalten die LAN-Auflistung niemals frei. Loginseiten sind nicht cachebar.

Mit **Vorherige Adresse**, **Nächste Adresse** oder einem horizontalen Wisch
wechseln Benutzer zwischen Kandidaten. Es ist jeweils nur ein Code sichtbar.
Die Ausgabe ist auf 20 eindeutige Adressen begrenzt, bevor Bilder erzeugt werden.
Server-Bindung, Routing und Firewall bestimmen, ob ein Kandidat tatsächlich
von einem anderen Gerät erreichbar ist. Die Anzeige führt keine Netzwerk-
oder Erreichbarkeitsprüfung durch. DNS-Erkennung liefert private IPv4-Adressen
des Serverhostnamens; weitere Interfaces können administrativ ergänzt werden.

## Instanzkonfiguration

| Variable | Wirkung |
| --- | --- |
| `SIMPLEOFFICE_LOGIN_QR` | Standard `1`; `0` blendet die ganze QR-Auswahl aus und lässt die Anmeldung bestehen |
| `SIMPLEOFFICE_LOGIN_QR_LAN` | Standard `1`; `0` beschränkt die Auswahl auf die aktuelle Adresse |
| `SIMPLEOFFICE_LOGIN_QR_PORTS` | Kommagetrennte zusätzliche Ports von 1 bis 65535 für erkannte LAN-Adressen |
| `SIMPLEOFFICE_LOGIN_QR_URLS` | Kommagetrennte HTTP(S)-Serverbasis- oder Login-URLs; nur für direkte lokale Clients |

Die beiden Schalter akzeptieren `1`, `true`, `yes`, `on`; andere Werte deaktivieren
die jeweilige Ausgabe. Unbekannte Ports werden ausgelassen, doppelte Ziele
zusammengefasst. Die aktuelle Adresse steht zuerst, dann erkannte LAN-Adressen
und schließlich administrativ ergänzte Adressen. Es werden höchstens 20 Ports
berücksichtigt. Der Server verwendet dieselbe Schematik (HTTP/HTTPS) wie die
aktuelle Anfrage; zusätzliche HTTPS-Endpunkte können als vollständige URL
konfiguriert werden.

Beispiel ohne Zugangsdaten:

```sh
SIMPLEOFFICE_LOGIN_QR_PORTS=8080,8443
SIMPLEOFFICE_LOGIN_QR_URLS=https://office.example/tenant/,https://[fd00::1]:8443/
```

Basis-URLs mit einem Pfad erhalten `/auth/login`; bereits angegebene Loginpfade
bleiben erhalten. Die aktuelle URL berücksichtigt den bestehenden WSGI-Mount-
Pfad, beispielsweise `/office/auth/login`. IPv6-URLs benötigen Klammern.
Ungültige URLs, Userinfo, Queryparameter, Fragmente und Wildcard-Hosts werden
nicht als QR-Ziel übernommen. Ziele sind auf 512 UTF-8-Bytes begrenzt. Niemals
Passwörter oder Token in diese Konfiguration eintragen.

Die Auswahl ist eine öffentliche Instanzfunktion vor der Anmeldung und benötigt
keine neuen Benutzerrollen, Tabellen oder Migrationen. Bereits vorhandene
Kontoanträge, CSRF, Rollen und Berechtigungen bleiben unverändert wirksam.

# Lizenzierung und Nutzungserfassung

Die lokale Nutzungserfassung zählt erfolgreiche HTTP-Anfragen (Status 200 bis
399) in den bestehenden Funktionsgruppen. Abgelehnte und fehlgeschlagene
Anfragen erhöhen die Zähler nicht. Aktive Benutzer werden pro Monat über ihre
lokale Benutzer-ID zusammengeführt.

WebDAV-, CalDAV- und CardDAV-Clients benötigen keine Browser-Sitzung. Nach
Prüfung der App-Zugangsdaten wird das authentifizierte DAV-Konto dem lokalen
Benutzer zugeordnet. WebDAV zählt zur Gruppe `webdav`, CalDAV zu `calendar` und
CardDAV zu `contacts`. Ein vorhandener Browser-Cookie mit einem anderen Konto
ändert diese Zuordnung nicht; jede erfolgreiche Anfrage wird einmal gezählt.
Die WebDAV-Dateisystem-Adresse `/webdav/files/<benutzer>` funktioniert sowohl
mit als auch ohne abschließenden Schrägstrich, einschließlich der im
Einrichtungsassistenten angezeigten Adresse.

Ein DAV-Konto ohne passenden lokalen Benutzer wird nicht einem Browser-Konto
zugerechnet. Die Erfassung verändert weder Rollen noch Zugriffsrechte.

Fehler der Nutzungserfassung werden protokolliert und verhindern die lokale
DAV-Nutzung nicht. Die Korrektur gilt für neue Anfragen. Zuvor nicht erfasste
Requests werden nicht nachträglich rekonstruiert; bestehende Monatsdaten und
Abrechnungen bleiben erhalten.

Diese HTTP-Erfassung umfasst keinen externen OpenSSH-Systemdienst und keine
SFTP-Protokollpakete des integrierten Paramiko-Dienstes.

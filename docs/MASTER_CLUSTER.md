# Master-Cluster-Betrieb

Master-Pakete prüfen alle 15 Sekunden die fest eingebaute Master-Adresse und den DNS-TXT-Eintrag. Die öffentliche Federation-Identität liefert den Knoten-Fingerprint. Ein Round-Robin-Treffer mit einem anderen Fingerprint stuft den lokalen Knoten nicht herab.

## DNS-Autorität

Für `https://simpleoffice4me.back2heaven.de` lautet der Standardname:

`_simpleoffice-master.simpleoffice4me.back2heaven.de TXT`

Der TXT-Wert hat dieses Format:

`simpleoffice-master-id=<64-stelliger SHA-256-Fingerprint>`

Der Fingerprint wird auf der jeweiligen Instanz als öffentliche Federation-Identität angezeigt. Es muss genau eine gültige Autoritäts-ID vorhanden sein. Der TXT-Eintrag bestimmt den autoritativen Knoten; er erteilt keinem unbekannten Peer automatisch Vertrauen.

## Betriebsmodi

- **Active-Active:** Jeder konfigurierte Master bleibt aktiv. Fällt die Adresse aus, wird der Zustand als `active_partitioned` angezeigt.
- **Backup-Active:** Nur der Knoten, dessen Fingerprint im TXT-Eintrag steht, wird bei erreichbarer Master-Adresse als aktiv markiert. Andere Knoten bleiben im Standby. Bei fehlendem TXT-Eintrag oder Verbindungsverlust wird kein Backup automatisch zum autoritativen Master erklärt.
- **Dauerhaft aktiver Master:** Der lokale Knoten bleibt aktiv, unabhängig von TXT-Eintrag und Erreichbarkeit.

Die Konfiguration befindet sich unter **Lizenzierung → Master-Cluster** und wird lokal mit Audit-Historie gespeichert. Der Status zeigt den Betriebsmodus, die zuletzt erreichte Knoten-ID und die DNS-Autorität.

Die Betriebsmoduswahl regelt die Aktivitätsrolle. Sie schaltet keine automatische Replikation der Fachdatenspeicher frei und ersetzt nicht die gerichtete Peer-Autorisierung der Federation.

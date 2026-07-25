# Dashboard, Bilder und Adressen

Nach der Anmeldung ist unter **Übersicht** eine kleine Betriebsansicht verfügbar. Sie zeigt die lokale Zeit, freien Speicher am Dokumentenbestand, erkannte eingebundene Speicher, die Inbox und persönliche To-dos. Die Anzeige ist lesend; sie ändert keine Mounts oder Berechtigungen.

Die **Bilder**-Ansicht verwendet bereits importierte Bilddateien. Bilder bleiben normale Dokumente: Sie können getaggt, über die Dokumentseite mit einem Passwort-Link geteilt und in einer Diashow angezeigt werden. Die Diashow lässt sich nach Tag, aktuellem Zeitraum (Woche, Monat, Jahr) oder vollständig eingrenzen.

Bei Kontakten können mehrere Adressen gespeichert werden. Bereits gespeicherte Werte werden lokal als Vorschläge angeboten; gleiche normalisierte Adressen werden mit der Anzahl der betroffenen Kontakte markiert. Das funktioniert ohne Weitergabe von Kontaktadressen an Dritte.

## OSM-Geocoding bewusst betreiben

Die öffentliche Nominatim-Instanz von OpenStreetMap ist **nicht** als Autovervollständigung oder zum Vorladen von Adressen vorgesehen. Ihr Betrieb verlangt unter anderem höchstens eine Anfrage pro Sekunde, Caching und eine eindeutige Kennung; clientseitiges Autocomplete sowie systematische/bulk Abfragen sind ausgeschlossen. Außerdem sind Kontaktadressen personenbezogene Daten.

Für Suche während der Eingabe oder einen lokalen Adressbestand wird deshalb eine selbst betriebene Nominatim-Instanz oder ein vertraglich passender Geocoder empfohlen. Erst dann sollte eine Instanz-URL explizit konfiguriert werden. Die aktuelle Oberfläche beschränkt sich absichtlich auf den lokalen Vorschlagsspeicher und löst keine Anfrage an öffentliche OSM-Dienste aus.

Quelle: [Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/).

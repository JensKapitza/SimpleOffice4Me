# Modellanbieter in SimpleOffice4Me

Die Einrichtung erfolgt innerhalb der Anwendung unter **Administration → Mini Services → Vision / ML** (`/admin/mini-services/vision`). Es wird kein zweites Dashboard und kein zusätzlicher Dienst benötigt. Die vorhandene Datei `vision-service.json` bleibt die einzige persistierte Providerkonfiguration; API-Schlüssel bleiben verschlüsselt.

## Lokales Ollama

`local` verwendet eine frei eingebbare, case-sensitive Modell-ID, z. B. `mimo-v2.6` oder eine lokal vorhandene Modellkennung. Der Name wird nicht gegen eine Herstellerliste geprüft. Das bedeutet nicht, dass das Modell installiert ist oder jede Fähigkeit besitzt.

Der Status prüft `/api/tags` und unterscheidet Erreichbarkeit und Modellbestand. Bei Bildaufgaben wird vor dem Bildversand `/api/show` auf die gemeldete `vision`-Fähigkeit geprüft. Ein reines Textmodell ist für Text verwendbar; ein Herstellername allein begründet keine Bildfähigkeit. Fehlende/ungeprüfte Bildfähigkeit führt zu einer konkreten Fehlermeldung. Ein fehlendes Modell wird nicht automatisch heruntergeladen. Die lokale HTTP-Verbindung bleibt auf Loopback beziehungsweise das bestehende explizite Docker-Host-Gateway begrenzt. Zugangsdaten, freie Pfade, Query-Parameter, Fragmente und Redirects sind dort nicht erlaubt.

## OpenAI und kompatible APIs

`openai` verwendet weiterhin den offiziellen OpenAI-Endpunkt. `compatible` verwendet denselben Chat-Completions-Adapter mit konfigurierbarer Basis-URL und Modell-ID. Die bereits vorhandenen API-Felder und der verschlüsselte API-Key werden wiederverwendet; es gibt keinen zweiten Schlüsselstore.

Beispielkonfigurationen (benötigen einen tatsächlich laufenden kompatiblen Dienst):

| Dienst | Anbieterwahl | Basis-URL | Modell-ID |
|---|---|---|---|
| LM Studio lokal | compatible | `http://127.0.0.1:1234/v1` | Exakt die vom Dienst angebotene Kennung |
| vLLM lokal | compatible | `http://127.0.0.1:8000/v1` | Konfigurierter served-model-name |
| LiteLLM lokal | compatible | `http://127.0.0.1:4000/v1` | Alias aus der vorhandenen Gatewaykonfiguration |
| Externer kompatibler Dienst | compatible | Dokumentierte HTTPS-Basis-URL des Anbieters | Freie vom Anbieter unterstützte Kennung |

Diese Beispiele bestätigen keine Produkt-/Modellverfügbarkeit. Der Adapter erwartet Chat Completions und ein JSON-Objekt als inhaltliche Antwort; Bildaufgaben benötigen zusätzlich passende Multimodal-Unterstützung des Dienstes und Modells. In compatible-Modus wird kein optionales `response_format` erzwungen. Native Anthropic- oder Gemini-Protokolle sind damit nicht automatisch implementiert; sie können über ein entsprechend eingerichtetes kompatibles Gateway genutzt werden.

Für lokale kompatible Loopback-Dienste sind keine Cloud-Einwilligung und kein Cloud-Key erforderlich. Ein erforderlicher lokaler Dienst-Key kann trotzdem verschlüsselt hinterlegt werden. Der lokale Status prüft die exakte Modell-ID über `/models`, ohne eine Inferenz auszulösen. Ein gelistetes Modell oder erfolgreicher HTTP-Test ist kein Nachweis einer echten Modellantwort.

## Externe Datenverarbeitung

Externe Anbieter benötigen weiterhin einen konfigurierten Schlüssel, die administrative Server-Einwilligung und eine ausdrückliche Zustimmung für die jeweilige Anfrage. Die Anfrageformulare zeigen Anbieter, Zielhost, Modell und Datenarten an. Beim Kontaktimport bleiben Fotodateien lokal; erkannter Text kann mit Zustimmung an den ausgewählten Dienst gehen. Bildanalyse-Workflows können mit Zustimmung die gewählten Bilddateien übertragen. Keine automatische Cloud-Ausweichroute.

OpenAI und kompatible Dienste sind bei dieser ausdrücklichen Freigabe auch auf normalen Datenwurzeln nutzbar. **Meta Contributor behält seine spezielle Beschränkung auf verifizierte synthetische Demodaten**, da dieser Dienst einen anderen Inhaltsnutzungsvertrag besitzt. Der separate mehrstufige Meta-Responses-Demoagent unter `/contextflow/agent` bleibt ein Meta-spezifischer Ablauf; seine Tool-Loop-Semantik wird nicht als kompatible Chat-Completions-Unterstützung ausgegeben.

Dokument-Tag-/Dokument-Bildvorschläge aus Smart Views, Dokumentdetail und Mailanhängen enthalten derzeit keine Einwilligung für externe Verarbeitung. Sie bleiben deshalb lokal, einschließlich kompatibler Loopback-Dienste. Die Oberfläche nennt diese Grenze ausdrücklich. Sie wird nicht durch das Speichern eines Cloud-Keys aufgehoben.

## Transport und Schlüssel

Kompatible externe Endpunkte benötigen HTTPS. Der Transport verwendet die vorhandenen LiteLLM-Helfer zur DNS-Prüfung, Adressbindung pro Verbindung und TLS-Hostnameprüfung. Private/Loopback-Ziele hinter externen DNS-Namen werden nicht implizit freigegeben. HTTP ist nur auf explizitem Loopback zulässig. Userinfo, Query-Parameter, Fragmente und relative Pfadsegmente sind verboten. Redirects und automatische Wiederholungen von Inferenz-POSTs sind ausgeschlossen; Antwortgröße und Laufzeit sind begrenzt.

Bei Wechsel des API-Zielhosts wird ein gespeicherter Key nicht still an den neuen Host weiterverwendet. Er muss erneut eingegeben oder über **Gespeicherten API-Key entfernen** ausdrücklich entfernt werden. Öffentliche Statusdaten enthalten nur Kennungen und boolesche Schlüsselzustände; Fehler enthalten keine Tokens, Antwortbodies oder internen Transportexceptions.

## Prüfung

Automatisierte Tests verwenden synthetische Daten und lokale HTTP-Testserver beziehungsweise transportgenaue Fakes. Sie prüfen freie Modellnamen, tatsächliche lokale Chat-Completions-Anfragen, Bildpayloads, Modelllisten, Consent/Key-Grenzen, URL-Validierung, DNS-Fehlerredaktion, HTTP 302/401/403/429/500, begrenzte Antworten und Konfigurationspersistenz. Kein externer API-Key und kein echter Anbieter-/Modellbetrieb wurden für diese Änderung eingerichtet oder getestet. Vor Nutzung muss die konkret konfigurierte Installation eine echte Minimalantwort liefern; erst diese belegt ihre Betriebsfähigkeit.

# Federation: verschlüsselter P2P-Speicher

## Ziel

Federation-Peers können als Speicherziele verwendet werden, ohne automatisch Entschlüsselungsrechte zu erhalten. Interne Peers werden bevorzugt; öffentliche Peers sind ein expliziter, standardmäßig deaktivierter Fallback.

## Sicherheitsgrenzen

Ein öffentlicher Peer gilt als nicht vertrauenswürdig. Er erhält nur zufällige Storage-IDs, Ciphertext, Ciphertext-Hash und Größe. Nicht übertragen werden Dateiname, Objekt-ID, Klartext-Hash, Passwort, Content Encryption Key (CEK), Key-Envelope oder das vertrauenswürdige Manifest.

Kollusion öffentlicher Peers kann technisch nicht verhindert werden. Die Architektur begrenzt deshalb die Informationen pro Peer und muss Vertraulichkeit auch dann erhalten, wenn Peers ihre gespeicherten Ciphertexte zusammenführen.

## Identität und Verschlüsselung

Die kanonische Inhaltsidentität bleibt `SHA-256(plaintext)`. Sie ändert sich weder durch Peer-Platzierung noch durch Passwortwechsel. Die Nutzdaten werden mit einem zufälligen CEK verschlüsselt. Passwörter oder Gruppenwissen schützen ausschließlich einen Key-Envelope für diesen CEK; sie werden nie gespeichert oder an Storage-Peers übertragen.

Ein Passwortwechsel packt den bestehenden CEK neu ein und erfordert keine Neuübertragung der Datenblöcke.

Für öffentliche Storage-Peers darf der Klartext-Hash nicht offengelegt werden, damit bekannte Dateien nicht über Hashlisten korreliert werden können. Jeder verschlüsselte Chunk besitzt zusätzlich einen Ciphertext-Hash zur Transport- und Speicherprüfung.

## Chunking

Der erste produktive Modus verwendet 4 MiB Chunks. Das vertrauenswürdige Manifest enthält Reihenfolge, Offset, Klartextlänge, Klartext-Hash, Ciphertext-Hash, zufällige Storage-ID und die für die Entschlüsselung benötigten nicht geheimen Parameter.

Öffentliche Peers sehen nur:

```json
{
  "storage_id": "opaque-random-id",
  "size": 4194320,
  "cipher_hash": "..."
}
```

Damit bleiben Resume, parallele Übertragung, Integritätsprüfung und Reparatur möglich, ohne das Dokument-Metadatenmodell offenzulegen.

## Placement

Vertrauensstufen in bevorzugter Reihenfolge:

1. local
2. trusted-lan
3. trusted-vpn
4. trusted-internet
5. private
6. public

Eine Policy definiert Mindest-/Zielkopien, ob öffentliche Peers verwendet werden dürfen, wie viele Shards maximal bei einem öffentlichen Peer liegen und wie viele verschiedene öffentliche Peers mindestens verfügbar sein müssen. Kann die geforderte öffentliche Diversität nicht erreicht werden, wird der öffentliche Fallback nicht benutzt.

Öffentliche Nutzung ist Default-Deny.

## Wissen und Berechtigung

Storage-Berechtigung und Entschlüsselungsberechtigung sind getrennt. Ein CEK kann unabhängig für bekannte Peer-Identitäten und für Wissensgruppen eingepackt werden. Eine Wissensgruppe speichert nur Gruppen-ID, Salt/KDF-Parameter und den verschlüsselten CEK. Das gemeinsame Wissen selbst wird nicht serialisiert oder synchronisiert.

Für passwortbasiertes Wissen ist Argon2id als KDF vorgesehen. Die konkrete Crypto-Implementierung muss vor Aktivierung des produktiven Datenpfads festgelegt, versionsgebunden und mit Testvektoren abgesichert werden.

## Erasure Coding

Die aktuelle Grundlage modelliert Shard-Platzierung, implementiert aber bewusst noch keinen eigenen Erasure-Codec. Ein späterer Codec (beispielsweise k Daten-Shards + m Recovery-Shards) muss nach Rekonstruktion zwingend gegen den kanonischen Klartext-SHA-256 verifiziert werden. Lossy-Transformationen dürfen niemals den kanonischen Blob ersetzen.

## Integration in vorhandene Federation

Die bestehenden Federation-Peers, HTTP/HTTPS-Transport-, Transfer-, Retry- und Resume-Primitiven bleiben maßgeblich. Diese Erweiterung liefert die Storage-/Disclosure-/Placement-Schicht und ersetzt keine fachlichen Synchronisationsregeln von Dokumenten, Personal/Stempeluhr, Kalender, Kontakten, Mail oder anderen Modulen.

Nächste Integrationsschritte:

- FederationStore um persistente Storage-Capabilities, Quotas und Placement-Policies erweitern.
- Crypto-Adapter für zufällige CEKs, AEAD pro Chunk und versionierte Key-Envelopes ergänzen.
- HTTP-API für opaque Chunk PUT/GET/HEAD/DELETE mit Range/Resume und Quota ergänzen.
- Placement-Worker an bestehende Federation-Transferqueue anbinden.
- UI für Peer-Vertrauen, bevorzugte Ziele, öffentlichen Fallback, Kopien/Shards und Diagnose ergänzen.
- Ende-zu-Ende-Tests mit mindestens drei Prozessen/Peers, Abbruch/Resume, Hashfehlern, Quota, Peer-Ausfall, Passwortwechsel und simuliertem Zusammenführen öffentlicher Peer-Daten ergänzen.

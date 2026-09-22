# Connectivity Relay: STUN/TURN und HTTPS-CONNECT

Der Connectivity Relay ist ein optionaler Mini Service für Verbindungen, die an
NAT oder restriktiven Firewalls scheitern. Er ist standardmäßig deaktiviert und
öffnet keine Router- oder Host-Firewallregel automatisch.

## STUN/TURN

SimpleOffice startet keinen selbst entwickelten TURN-Server. Wenn lokal
`turnserver` aus dem Paket **coturn** vorhanden ist, kann der Mini-Services-
Worker genau diesen Prozess besitzen und überwachen.

Administration → Mini Services → **Connectivity Relay** konfiguriert:

- öffentlich erreichbaren Hostnamen oder IP,
- Listening-/Relay-IP,
- optionale externe NAT-IP,
- TURN-Port (Standard 3478),
- begrenzten UDP-Relay-Portbereich,
- optional TURN über TLS/TCP (Standard 5349),
- Realm und kurze Gültigkeit der Client-Credentials.

TURN ist nur mit `use-auth-secret` aktiv. SimpleOffice speichert den Shared
Secret in einer privaten Runtime-Datei und gibt ihn weder in Status noch Audit
aus. Angemeldete Clients erhalten zeitlich begrenzte TURN-REST-Credentials.

Für den Browser steht `GET /api/connectivity/ice` bereit. Ist TURN deaktiviert
oder nicht verfügbar, liefert der Endpunkt eine leere ICE-Liste; WebRTC versucht
dann weiterhin eine direkte Verbindung. Die Bildschirmfreigabe nutzt diesen
Endpunkt automatisch.

### Firewall

Damit ein TURN-Knoten aus dem Internet erreichbar ist, müssen am tatsächlich
betriebenen Relay-Host die **bewusst konfigurierten** Ports freigegeben werden.
Typisch sind 3478 UDP/TCP, bei TLS 5349 TCP und der konfigurierte Relay-
Portbereich. SimpleOffice nimmt diese Freigaben absichtlich nicht selbst vor.

Steht TURN selbst hinter NAT, muss die externe IP explizit angegeben und die
Portweiterleitung außerhalb von SimpleOffice administriert werden.

## HTTPS-CONNECT für SSH

Zusätzlich kann SimpleOffice einen **vorhandenen authentifizierten HTTPS-
CONNECT-Proxy** als Transport für ausgewählte Administrationsziele verwenden.

Wichtig:

- nur `https://` als Proxy-Schema,
- Proxy-Benutzer und Passwort erforderlich,
- Passwort liegt nur in der privaten Secret-Datei,
- Ziele werden einzeln als `Host:Port` freigegeben,
- keine Wildcards,
- kein systemweiter Proxy,
- kein anonymer/open proxy,
- keine automatische Umleitung beliebiger Programme.

Für jedes freigegebene Ziel zeigt die Oberfläche einen OpenSSH-
`ProxyCommand`. Beispielprinzip:

```sshconfig
Host interner-server
    HostName server.example.net
    User admin
    ProxyCommand /opt/simpleoffice4me/.venv/bin/python -m simpleoffice_https_connect_tunnel --config /var/lib/simpleoffice4me/instance/mini-services.json --target server.example.net:22
```

Der Helper baut TLS zum konfigurierten Proxy auf, authentifiziert sich dort und
fordert ausschließlich `CONNECT` zum exakt erlaubten Ziel an. Optional kann
eine eigene CA-Datei für den Proxy angegeben werden.

## Grenzen

Der HTTPS-CONNECT-Pfad ist kein VPN und ersetzt TURN nicht. UDP/WebRTC-Medien
gehören über STUN/TURN; TCP-Dienste wie SSH können gezielt über CONNECT geführt
werden. Ein "alles durch den Tunnel"-Modus ist bewusst nicht vorhanden, weil das
einen offenen bzw. systemweiten Proxy erzeugen würde.

Die Funktion ist für eigene oder administrierte SimpleOffice-Knoten gedacht.
Netzwerk- oder Sicherheitsrichtlinien fremder Systeme werden nicht automatisch
umgangen.

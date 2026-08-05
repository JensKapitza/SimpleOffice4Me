# HTTPS und Reverse Proxy

Für öffentlich erreichbare Freigabelinks sowie CardDAV muss ein TLS-Terminator
(zum Beispiel Caddy, nginx oder Traefik) HTTPS vor der Anwendung bereitstellen.
Der Waitress-Server selbst sollte nur lokal oder im internen Container-Netz
lauschen.

Wenn genau ein vertrauenswürdiger Reverse Proxy davorsteht, setze:

```bash
SIMPLEOFFICE_HTTPS=true
SIMPLEOFFICE_TRUSTED_PROXY_HOPS=1
```

Mit dem Starter entspricht das beispielsweise:

```bash
./start.sh --host 127.0.0.1 --trusted-proxy-hops 1
```

Dann werden Session-Cookies nur über HTTPS gesetzt und die Anwendung erzeugt
korrekte externe HTTPS-URLs für CardDAV und Freigaben. Bei mehreren hintereinander
geschalteten Proxies ist die tatsächliche Zahl einzutragen. Ohne diese explizite
Angabe werden `X-Forwarded-*`-Header absichtlich ignoriert, da Clients sie
fälschen können.

Bei aktivierter Proxy-Vertrauensstellung muss der interne Waitress-Port für
Clients gesperrt sein; nur der angegebene Proxy darf ihn erreichen. Ohne diese
Absicherung könnten Clients die weitergereichten Header direkt einspeisen.

Der Proxy muss die Header `X-Forwarded-Proto`, `X-Forwarded-Host` und bei einem
Unterpfad `X-Forwarded-Prefix` selbst setzen und eingehende Werte überschreiben.

Für die automatische Einrichtung in Thunderbird muss der Proxy zusätzlich
`/.well-known/carddav` unverändert an SimpleOffice weiterleiten. Details zu
Redirect, Principal- und Adressbuch-Erkennung stehen in
[CARDDAV_DISCOVERY.md](CARDDAV_DISCOVERY.md).

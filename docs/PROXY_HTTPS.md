# HTTPS und Reverse Proxy

Für öffentlich erreichbare Freigabelinks sowie CardDAV muss ein TLS-Terminator
(zum Beispiel Caddy, nginx oder Traefik) HTTPS vor der Anwendung bereitstellen.
Der Flask-Server selbst sollte nur lokal oder im internen Container-Netz
lauschen.

Wenn genau ein vertrauenswürdiger Reverse Proxy davorsteht, setze:

```bash
SIMPLEOFFICE_HTTPS=true
SIMPLEOFFICE_TRUSTED_PROXY_HOPS=1
```

Dann werden Session-Cookies nur über HTTPS gesetzt und die Anwendung erzeugt
korrekte externe HTTPS-URLs für CardDAV und Freigaben. Bei mehreren hintereinander
geschalteten Proxies ist die tatsächliche Zahl einzutragen. Ohne diese explizite
Angabe werden `X-Forwarded-*`-Header absichtlich ignoriert, da Clients sie
fälschen können.

Der Proxy muss die Header `X-Forwarded-Proto`, `X-Forwarded-Host` und bei einem
Unterpfad `X-Forwarded-Prefix` selbst setzen und eingehende Werte überschreiben.

# Kamera-Gerätematrix (#736): offene Abnahme, keine Behauptungen

Stand: 30.10.2026, Branch `hackathon/contextflow-mvp`, uncommitted.
Jede Zelle ist Stand heute **offen**, sofern nicht anders genannt: Es wurden
in diesem Arbeitsschritt keine Browser-, PWA- oder Geräte-Tests durchgeführt,
nur Code-Audit, automatisierte Offline-Tests und ein Node-Stub-Harness für den
Ortstermin-Scanner (kein echter Browser, keine echte Kamera).

## Scopes (Code-Stand, per Test belegt)

- `camera=(self)` (Endpunkt-Allowlist `CAMERA_SCANNER_ENDPOINTS` in
  `app/__init__.py`): `inventory.index`, `library.index`,
  `federation_peer_admin.dashboard`, `shopping.index`, `site_visits.detail`
  (neu in diesem Schritt; der `data-barcode-scan`-Button existiert im
  Ortstermin-Template, die Vorgängerbehauptung „kein Scan-Button" war falsch).
- `camera=()`: alle übrigen Routen, darunter `inventory.*` außer `index`
  (Detail, Lookup, Fotoeingang, Aktionen), `library.*` außer `index`,
  `shopping.*` außer `index`, `site_visits.*` außer `detail`,
  `documents.contacts` (nur Datei+Decoder, keine Live-Kamera).
- Gemeinsamer Stream-Helfer `camera_capture.js` (facingMode-Fallback,
  Neustart-Wache): Inventar, Bibliothek, Federation-QR, Shopping, Ortstermin
  (neu; dort zusätzlich Overlay mit Foto-Fallback, jsQR-QR-Fallback,
  pagehide-Cleanup und spezifischen Fehlertexten statt `alert()`).
- Lokaler jsQR-Fallback (nur QR, kein EAN/UPC): Inventar, Bibliothek,
  Federation, Ortstermin (neu), Kontakt-QR-aus-Bild (neu).
  Shopping-EAN fällt auf Foto-Decode (nur mit `BarcodeDetector`) und manuelle
  Eingabe zurück (Foto-Decode neu).

## Erforderliche Abnahme je Plattform

Legende: L = Live-Scan, F = Datei-/Foto-Fallback, M = manuelle Eingabe,
P = Berechtigungs-Dialog, S = Stream-Stopp beim Verlassen.

| Plattform | Inventar | Bibliothek | Federation-QR | Shopping | Kontakt-QR | Ortstermin |
|---|---|---|---|---|---|---|
| Desktop Chrome/Edge (HTTPS) | L/F/M/P/S offen | L/F/M/P/S offen | L/F/P/S offen | L/Foto/M/P/S offen | F/M offen | L/F/M/P/S offen |
| Desktop Firefox | L-QR/F/M/P/S offen (jsQR statt BarcodeDetector) | L-QR/F/M/P/S offen | F offen (jsQR) | M offen (kein EAN-Decoder) | F-QR/M offen (jsQR) | L-QR/F/M/P/S offen (jsQR) |
| Android Chrome aktuell | L/F/M/P/S offen | L/F/M/P/S offen | L/F/P/S offen | L/Foto/M/P/S offen | F/M offen | L/F/M/P/S offen |
| Android WebView (Wrapper) | nativ ML-Kit/Chooser offen | L/Chooser offen | L/Chooser offen | nativ ML-Kit-Brücke offen | Chooser offen | L/Chooser offen |
| iPhone Safari + PWA | L-QR/F/M/P/S offen (jsQR; EAN nur manuell) | L-QR/F/M/P/S offen | L-QR/F/P/S offen | M/Foto-ohne-Decoder→M/P/S offen | F-QR/M offen (jsQR) | L-QR/F/M/P/S offen (jsQR) |
| iOS-Wrapper (WKWebView) | offen, s. ios/README | offen | offen | offen | offen | offen |

## Hinweise für die Abnahme

- Android-Wrapper: `MainActivity` fängt `#shopping-barcode-scan` immer und
  `#start-barcode` (Inventar) nur ohne `BarcodeDetector` per nativem
  ML-Kit-Scan ab; Ergebnis/Status werden in die Seite zurückgespielt.
  Zu prüfen: Abbruch, Fehler, Doppel-Start, Rotation.
- iOS: `ios/README.md` verlangt Geräte-Tests für Kamera, Picker, Downloads.
- Ortstermin-Scan läuft über `camera_capture.js` + jsQR-QR-Fallback mit
  Overlay (Foto wählen, Schließen, manuelle Seriennummer); EAN/UPC ohne
  `BarcodeDetector` bleibt manuell.
- Kontakt-QR nutzt erst `BarcodeDetector`, dann jsQR, dann manuelles Einfügen
  (alles lokal, kein externer Scanner).
- HTTPS ist für `getUserMedia` Pflicht; `http://localhost` gilt als sicher.
- Nach jeder Abnahme: Zelle mit Datum, Gerät, Browser-Version und Befund füllen.

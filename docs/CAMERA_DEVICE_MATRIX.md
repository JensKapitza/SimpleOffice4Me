# Kamera-Gerätematrix (#736): offene Abnahme, keine Behauptungen

Stand: 30.10.2026, Branch `hackathon/contextflow-mvp`, uncommitted.
Jede Zelle ist Stand heute **offen**, sofern nicht anders genannt: Es wurden
in diesem Arbeitsschritt keine Browser-, PWA- oder Geräte-Tests durchgeführt,
nur Code-Audit plus automatisierte Offline-Tests.

## Scopes (Code-Stand, per Test belegt)

- `camera=(self)`: `inventory.*`, `library.*`, `federation_peer_admin.dashboard`,
  `shopping.index` (neu in diesem Schritt).
- `camera=()`: alle übrigen Routen, darunter `shopping.*` außer `index`,
  `documents.contacts` (nur Datei+Decoder, keine Live-Kamera),
  `site_visits.detail` (Scan-Code vorhanden, aber kein Scan-Button im Template).
- Gemeinsamer Stream-Helfer `camera_capture.js` (facingMode-Fallback,
  Neustart-Wache): Inventar, Bibliothek, Federation-QR, Shopping (neu).
- Lokaler jsQR-Fallback (nur QR, kein EAN/UPC): Inventar, Bibliothek, Federation.
  Shopping/Kontaktimport fallen auf manuelle Eingabe zurück.

## Erforderliche Abnahme je Plattform

Legende: L = Live-Scan, F = Datei-/Foto-Fallback, M = manuelle Eingabe,
P = Berechtigungs-Dialog, S = Stream-Stopp beim Verlassen.

| Plattform | Inventar | Bibliothek | Federation-QR | Shopping | Kontakt-QR | Ortstermin |
|---|---|---|---|---|---|---|
| Desktop Chrome/Edge (HTTPS) | L/F/M/P/S offen | L/F/M/P/S offen | L/F/P/S offen | L/M/P/S offen | F/M offen | nur Upload, offen |
| Desktop Firefox | F/M offen (kein BarcodeDetector) | F/M offen | F offen (jsQR) | M offen (kein Decoder) | M offen | nur Upload, offen |
| Android Chrome aktuell | L/F/M/P/S offen | L/F/M/P/S offen | L/F/P/S offen | L/M/P/S offen | F/M offen | nur Upload, offen |
| Android WebView (Wrapper) | nativ ML-Kit/Chooser offen | L/Chooser offen | L/Chooser offen | nativ ML-Kit-Brücke offen | Chooser offen | Chooser offen |
| iPhone Safari + PWA | L/F/M/P/S offen | L/F/M/P/S offen | L/F/P/S offen | L/M/P/S offen | F/M offen | nur Upload, offen |
| iOS-Wrapper (WKWebView) | offen, s. ios/README | offen | offen | offen | offen | offen |

## Hinweise für die Abnahme

- Android-Wrapper: `MainActivity` fängt den Shopping-Scan-Button per nativem
  ML-Kit-Scan ab, wenn `BarcodeDetector` fehlt; Ergebnis/Status werden in die
  Seite zurückgespielt. Zu prüfen: Abbruch, Fehler, Doppel-Start, Rotation.
- iOS: `ios/README.md` verlangt Geräte-Tests für Kamera, Picker, Downloads.
- Ortstermin-Scan (`site_visits.js`) ist ohne Template-Button wirkungslos;
  vor einer Abnahme entscheiden, ob die Schaltfläche je gebaut werden soll.
- Kontakt-QR hat bewusst keinen jsQR-Fallback (Datei + manuelles Einfügen).
- HTTPS ist für `getUserMedia` Pflicht; `http://localhost` gilt als sicher.
- Nach jeder Abnahme: Zelle mit Datum, Gerät, Browser-Version und Befund füllen.

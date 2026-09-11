# 100 wichtige Quick Wins – September 2026

Diese Liste dokumentiert genau die 100 bewusst kleinen, breit wirksamen Verbesserungen dieses PRs. Zusätzliche Detailverbesserungen im Code sind nicht separat gezählt.

## Globale Bedienung und Formulare
1. JavaScript-fähige Oberfläche wird mit einer `js`-Klasse markiert.
2. Der Hauptinhalt erhält zuverlässig die ID `main-content`.
3. Der Hauptinhalt wird programmatisch fokussierbar.
4. Der Hauptinhalt erhält eine explizite `main`-Rolle.
5. Neue Tabs erhalten automatisch `rel=noopener`.
6. Neue Tabs erhalten automatisch `rel=noreferrer`.
7. Externe neue Tabs senden keinen Referrer.
8. Suchfelder `q`, `search` und `query` werden einheitlich als Suche erkannt.
9. Suchformulare erhalten automatisch `role=search`.
10. Suchfelder erhalten `enterkeyhint=search` für Mobil-Tastaturen.
11. Suchfelder deaktivieren automatische Großschreibung.
12. Suchfelder deaktivieren die Rechtschreibprüfung.
13. `/` fokussiert die erste sichtbare Suche.
14. `Ctrl+K` bzw. `Cmd+K` fokussiert die erste sichtbare Suche.
15. `Escape` verlässt ein fokussiertes Suchfeld.
16. Pflichtfelder erhalten `aria-required=true`.
17. Ungültige Felder erhalten `aria-invalid=true`.
18. Korrigierte Felder verlieren `aria-invalid` automatisch.
19. `invalid-feedback` wird per `aria-describedby` mit dem Feld verbunden.
20. Beim Validierungsfehler wird das erste ungültige Feld fokussiert.
21. Doppelte Formular-Submits werden abgefangen.
22. Laufende Formulare erhalten `aria-busy=true`.
23. Submit-Controls werden während der Übertragung deaktiviert.
24. Nach Browser-Back/Forward-Cache werden Submit-Controls korrekt wiederhergestellt.
25. Alerts erhalten automatisch passende Live-Region-Rollen.
26. Bilder werden standardmäßig lazy geladen.
27. Bilder werden standardmäßig asynchron dekodiert.
28. Iframes werden lazy geladen und mit Referrer-Policy versehen.
29. Videos erhalten mobile Inline-Wiedergabe und Metadata-Preload.
30. Tabellen werden automatisch in einen responsiven Scroll-Container gesetzt.

## Browser-Sicherheit
31. CSRF-Tokens werden nur an Same-Origin-Formulare angehängt.
32. Externe Formularziele erhalten niemals automatisch ein CSRF-Token.
33. Der aktuelle CSRF-Token wird pro Request neu aus dem Meta-Tag gelesen.
34. `formmethod` und `formaction` des tatsächlichen Submitters werden berücksichtigt.
35. Dynamisch eingefügte Formulare werden automatisch CSRF-geschützt.
36. Same-Origin-`fetch`-Mutationen erhalten `X-CSRF-Token`.
37. Cross-Origin-`fetch`-Requests erhalten keinen CSRF-Header.
38. Same-Origin-XMLHttpRequest-Mutationen erhalten ebenfalls CSRF-Schutz.
39. Vorhandene Fetch-Header bleiben beim CSRF-Hardening erhalten.
40. Sichere HTTP-Methoden bleiben vom CSRF-Header-Hook unangetastet.

## Mobile, Touch und Darstellung
41. Horizontales Seiten-Overflow wird global verhindert.
42. Der untere Safe-Area-Inset wird berücksichtigt.
43. Hauptcontainer respektieren linke und rechte Safe Areas.
44. Interaktive Controls nutzen `touch-action: manipulation`.
45. Auf Touch-Geräten erhalten zentrale Controls mindestens 44 px Höhe.
46. Der Tap-Highlight ist dezent und zum App-Design passend.
47. Gedrückte Buttons und Navigationselemente geben sichtbares Feedback.
48. Deaktivierte Controls zeigen Zustand und Cursor eindeutig.
49. Busy-Formulare zeigen einen Progress-Cursor.
50. Ungültige Felder erhalten klaren Fehlerrahmen und Fokuszustand.
51. Platzhaltertexte bleiben auch im Dark Mode besser lesbar.
52. Textareas sind vertikal vergrößerbar und starten mit sinnvoller Mindesthöhe.
53. Karten verhindern Flex/Grid-Overflow durch `min-width: 0`.
54. Direkte Flex- und Grid-Kinder dürfen auf kleinen Screens korrekt schrumpfen.
55. Lange Tabelleninhalte dürfen umbrechen.
56. Responsive Tabellen erhalten mobiles Momentum-Scrolling und Overscroll-Begrenzung.
57. Dropdowns bleiben innerhalb des sichtbaren Viewports scrollbar.
58. Modals berücksichtigen dynamische Mobile-Viewport-Höhen.
59. Offcanvas- und Toast-Bereiche berücksichtigen Safe Areas.
60. Druckansichten blenden Navigation und Bediencontrols sauber aus.

## PWA und Offline-Verhalten
61. Der App-Shell-Cache wird auf eine neue klar versionierte Generation angehoben.
62. Kritische CSS- und JS-Dateien werden im App-Shell-Cache vorgehalten.
63. Ein einzelner fehlender Precache-Eintrag bricht die komplette Installation nicht mehr ab.
64. Ein neuer Worker kann nach erfolgreicher Installation direkt übernehmen.
65. Alte SimpleOffice-App-Shell-Caches werden beim Aktivieren gezielt entfernt.
66. Navigation Preload wird genutzt, wenn der Browser es unterstützt.
67. Seiten-Navigation arbeitet network-first.
68. Bei Netzausfall wird zuverlässig die Offline-Seite ausgeliefert.
69. Statische Assets nutzen stale-while-revalidate.
70. Nur Same-Origin-Static-Assets werden gecacht.
71. Nur erfolgreiche Basic-Responses landen im Cache.
72. Antworten mit `Cache-Control: no-store` werden nicht persistiert.
73. Range-Requests werden vom Service Worker nicht gecacht.
74. Der Worker versteht eine explizite `SKIP_WAITING`-Nachricht.
75. Service-Worker-Updates umgehen den HTTP-Cache mit `updateViaCache=none`.
76. Die Oberfläche kann ein wartendes Update explizit aktivieren.
77. Neue Service-Worker-Versionen werden über `updatefound` erkannt.
78. Ein verfügbares Update löst ein eigenes App-Event aus.
79. Nach Wiederherstellung der Netzwerkverbindung wird sofort auf Updates geprüft.
80. Bei erneut sichtbarer App wird throttled auf Updates geprüft.

## Android-App
81. Safe Browsing wird nur auf Android-Versionen aktiviert, die die API unterstützen.
82. WebView-Debugging ist ausschließlich in Debug-Builds aktiv.
83. Fehlermeldungen im Startbildschirm können markiert und kopiert werden.
84. Der Start-Spinner erhält eine Accessibility-Beschreibung.
85. File-URL-Zugriff aus Web-Inhalten wird explizit deaktiviert.
86. Universal Access aus File-URLs wird explizit deaktiviert.
87. Die WebView kennzeichnet sich mit SimpleOffice-Version im User-Agent.
88. Medienwiedergabe benötigt weiterhin bewusst eine Benutzeraktion.
89. `mailto:`-Links werden an eine passende Android-App übergeben.
90. `tel:`-Links werden an eine passende Android-App übergeben.
91. Externe HTTP/HTTPS-Links öffnen außerhalb der eingebetteten App.
92. Nicht unterstützte externe URI-Schemata werden sichtbar blockiert.
93. Beim Laden lokaler Seiten wird ein klarer Ladezustand angezeigt.
94. Main-Frame-Netzwerkfehler werden mit echter WebView-Fehlermeldung angezeigt.
95. Main-Frame-HTTP-5xx-Fehler werden sichtbar gemeldet.
96. Backend-Healthchecks umgehen Android/HTTP-Caches.
97. Backend-Healthchecks schließen Verbindungen explizit nach jedem Versuch.
98. Backend-Timeouts enthalten die letzte konkrete Fehlerursache.
99. WebView-Zustand wird bei Activity-Neuerstellung gespeichert und wiederhergestellt.
100. Gebündelte Runtime-Assets werden pro APK-Version nur einmal neu synchronisiert.

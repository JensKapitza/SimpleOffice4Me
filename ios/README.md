# SimpleOffice4Me iOS (initial remote client)

This is an initial SwiftUI/WKWebView iPhone client for an existing HTTPS SimpleOffice4Me server. It does **not** bundle the Python backend or work offline.

## Build on a Mac

Install Xcode and XcodeGen (brew install xcodegen).

```sh
cd ios
xcodegen generate
open SimpleOffice4Me.xcodeproj
```

In Xcode, select the SimpleOffice4Me target, configure Signing & Capabilities with your Apple development team, and set a unique bundle identifier if required. Connect an iPhone and select Run.

For a signed IPA: Product > Archive, then Organizer > Distribute App. Distribution signing requires appropriate Apple credentials/profiles. Do not commit signing keys or provisioning secrets.

The GitHub workflow compiles an unsigned simulator app only. It does not claim to produce a distributable IPA.

## Limitations

The remote server must have a valid HTTPS certificate. Login and cookies are handled by WKWebView. Native file picking, camera permissions, downloads, notifications, deep links and background behavior need device testing and dedicated integration before production release.

## Signiertes IPA lokal erstellen

Xcode mit aktivierter Apple-Developer-Team-Signierung und XcodeGen installieren. Eine eindeutige, in deinem Team registrierte Bundle-ID verwenden.

```sh
export APPLE_TEAM_ID=YOUR_TEAM_ID
export IOS_BUNDLE_ID=de.example.simpleoffice4me
bash ios/build-ipa.sh
```

Das Skript archiviert für echte iOS-Geräte und exportiert mit `ExportOptions.example.plist` für App Store Connect. Für Ad-hoc-Verteilung muss die Exportmethode und die Provisionierung passend geändert werden. Ohne gültige Signierungsidentität kann der Export nicht erfolgreich sein. Die Datei `ios/build/export/*.ipa` entsteht erst nach erfolgreichem Export.

CI baut derzeit nur den Simulator ohne Signierung; geheime Apple-Zertifikate werden nicht im Repository gespeichert.

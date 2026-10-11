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

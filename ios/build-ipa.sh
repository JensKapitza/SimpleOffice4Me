#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
: "${APPLE_TEAM_ID:?Set APPLE_TEAM_ID to your Apple Developer Team ID}"
: "${IOS_BUNDLE_ID:?Set IOS_BUNDLE_ID to your registered iOS bundle identifier}"
command -v xcodegen >/dev/null || { echo "Install xcodegen (brew install xcodegen)" >&2; exit 1; }
command -v xcodebuild >/dev/null || { echo "Install Xcode" >&2; exit 1; }
xcodegen generate
mkdir -p build
xcodebuild -project SimpleOffice4Me.xcodeproj -scheme SimpleOffice4Me -configuration Release \
  -destination 'generic/platform=iOS' -archivePath "$PWD/build/SimpleOffice4Me.xcarchive" \
  DEVELOPMENT_TEAM="$APPLE_TEAM_ID" PRODUCT_BUNDLE_IDENTIFIER="$IOS_BUNDLE_ID" \
  CODE_SIGN_STYLE=Automatic archive
xcodebuild -exportArchive -archivePath "$PWD/build/SimpleOffice4Me.xcarchive" \
  -exportPath "$PWD/build/export" -exportOptionsPlist "$PWD/ExportOptions.example.plist"
find "$PWD/build/export" -name '*.ipa' -print

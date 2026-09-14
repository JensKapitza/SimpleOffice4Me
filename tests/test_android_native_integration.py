from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android" / "apk" / "app"
JAVA = ANDROID / "src" / "main" / "java" / "de" / "simpleoffice4me" / "android"


class AndroidNativeIntegrationTests(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_manifest_exposes_share_and_open_targets_without_legacy_storage_permission(self):
        manifest = self.read(ANDROID / "src" / "main" / "AndroidManifest.xml")
        self.assertIn('android.intent.action.SEND', manifest)
        self.assertIn('android.intent.action.SEND_MULTIPLE', manifest)
        self.assertIn('android:mimeType="*/*"', manifest)
        self.assertIn('android:scheme="content"', manifest)
        self.assertIn('android:launchMode="singleTop"', manifest)
        self.assertNotIn('WRITE_EXTERNAL_STORAGE', manifest)
        self.assertNotIn('READ_EXTERNAL_STORAGE', manifest)
        self.assertNotIn('GET_ACCOUNTS', manifest)

    def test_manifest_has_narrow_browsable_deep_link(self):
        manifest = self.read(ANDROID / "src" / "main" / "AndroidManifest.xml")
        self.assertIn('android.intent.action.VIEW', manifest)
        self.assertIn('android.intent.category.BROWSABLE', manifest)
        self.assertIn('android:scheme="simpleoffice4me"', manifest)
        self.assertIn('android:host="open"', manifest)

    def test_manifest_uses_native_bootstrap_before_navigation_activity(self):
        manifest = self.read(ANDROID / "src" / "main" / "AndroidManifest.xml")
        navigation = self.read(JAVA / "NavigationActivity.java")
        bootstrap = self.read(JAVA / "BootstrapActivity.java")
        self.assertIn('android:name=".BootstrapActivity"', manifest)
        self.assertIn('android:name=".NavigationActivity"', manifest)
        self.assertIn('android:exported="false"', manifest)
        self.assertIn('android.intent.category.LAUNCHER', manifest)
        self.assertIn('public final class BootstrapActivity extends Activity', bootstrap)
        self.assertIn('new Intent(this, NavigationActivity.class)', bootstrap)
        self.assertIn('public final class NavigationActivity extends MainActivity', navigation)
        self.assertIn('EXIT_CONFIRM_WINDOW_MS = 1800L', navigation)
        self.assertIn('webView.canGoBack()', navigation)
        self.assertIn('webView.goBack()', navigation)
        self.assertIn('finishAndRemoveTask()', navigation)
        self.assertIn('Noch einmal Zurück schließt SimpleOffice.', navigation)

    def test_native_bootstrap_uses_system_google_account_picker_without_account_permission(self):
        manifest = self.read(ANDROID / "src" / "main" / "AndroidManifest.xml")
        bootstrap = self.read(JAVA / "BootstrapActivity.java")
        self.assertNotIn('android.permission.GET_ACCOUNTS', manifest)
        self.assertIn('AccountManager.newChooseAccountIntent(', bootstrap)
        self.assertIn('new String[]{"com.google"}', bootstrap)
        self.assertIn('AccountManager.KEY_ACCOUNT_NAME', bootstrap)
        self.assertIn('IDENTITY_EMAIL', bootstrap)
        self.assertIn('rememberIdentity(email)', bootstrap)
        self.assertNotIn('GoogleSignIn', bootstrap)

    def test_native_bootstrap_establishes_session_before_webview_and_uses_native_password_dialog(self):
        bootstrap = self.read(JAVA / "BootstrapActivity.java")
        self.assertIn('"/auth/android/challenge"', bootstrap)
        self.assertIn('"/auth/android/bootstrap"', bootstrap)
        self.assertIn('"/auth/android/unlock"', bootstrap)
        self.assertIn('X-SimpleOffice-Android-Token', bootstrap)
        self.assertIn('CookieManager.getInstance()', bootstrap)
        self.assertIn('SimpleOffice4Me entsperren', bootstrap)
        self.assertIn('InputType.TYPE_TEXT_VARIATION_PASSWORD', bootstrap)
        self.assertIn('module.callAttr(', bootstrap)
        self.assertIn('BuildConfig.ERROR_REPORT_URL', bootstrap)
        self.assertIn('bootstrapToken', bootstrap)
        self.assertNotIn('new WebView(', bootstrap)

    def test_intent_router_limits_files_and_rejects_cross_app_file_paths(self):
        router = self.read(JAVA / "AndroidIntentRouter.java")
        self.assertIn('MAX_SHARED_FILES = 20', router)
        self.assertIn('"content".equalsIgnoreCase(uri.getScheme())', router)
        self.assertNotIn('"file".equalsIgnoreCase(uri.getScheme())', router)
        self.assertIn('Intent.ACTION_SEND_MULTIPLE', router)
        self.assertIn('intent.getClipData()', router)
        self.assertIn('Intent.ACTION_VIEW.equals(action)', router)
        self.assertIn('isContentUri(data)', router)
        self.assertIn('addIncomingContentUri(data)', router)

    def test_deep_link_router_is_an_allowlist_and_survives_login_redirect(self):
        router = self.read(JAVA / "AndroidIntentRouter.java")
        self.assertIn('"simpleoffice4me".equalsIgnoreCase(uri.getScheme())', router)
        self.assertIn('"open".equalsIgnoreCase(uri.getHost())', router)
        self.assertIn('DOCUMENT_ID.matcher(documentId).matches()', router)
        self.assertIn('private boolean navigationDispatched', router)
        self.assertIn('if (!navigationDispatched)', router)
        self.assertIn('navigationDispatched = true', router)
        self.assertIn('navigationDispatched = false', router)
        for target in (
            'return "/documents"',
            'return "/inventory"',
            'return "/documents/images"',
            'return "/documents/calendar"',
            'return "/documents/contacts"',
            'return "/settings/google-drive"',
        ):
            self.assertIn(target, router)
        self.assertNotIn('getQueryParameter("oauth")', router)
        self.assertNotIn('uri.toString()', router)

    def test_main_activity_uses_existing_document_upload_for_android_shares(self):
        activity = self.read(JAVA / "MainActivity.java")
        self.assertIn('intentRouter.accept(getIntent())', activity)
        self.assertIn('protected void onNewIntent(Intent intent)', activity)
        self.assertIn('intentRouter.consumeSharedFiles()', activity)
        self.assertIn('isDocumentsIndexUrl(view.getUrl())', activity)
        self.assertIn('input[type=file][name=files]', activity)
        self.assertIn('Vollständig importieren bestätigen', activity)
        self.assertIn('currentPath.startsWith("/auth/")', activity)

    def test_downloads_use_storage_access_framework_and_local_origin_only(self):
        handler = self.read(JAVA / "AndroidDownloadHandler.java")
        activity = self.read(JAVA / "MainActivity.java")
        self.assertIn('Intent.ACTION_CREATE_DOCUMENT', handler)
        self.assertIn('CookieManager.getInstance().getCookie(url)', handler)
        self.assertIn('connection.setInstanceFollowRedirects(false)', handler)
        self.assertIn('127.0.0.1', handler)
        self.assertIn('localhost', handler)
        self.assertIn('FLAG_GRANT_READ_URI_PERMISSION', handler)
        self.assertIn('getContentResolver().delete(destination', handler)
        self.assertIn('isAllowedExternalUrl(url)', handler)
        self.assertIn('"http".equals(normalized) || "https".equals(normalized)', handler)
        self.assertIn('nicht unterstütztem Link-Schema wurde blockiert', handler)
        self.assertIn('webView.setDownloadListener', activity)
        self.assertIn('downloadHandler.onActivityResult', activity)

    def test_native_google_drive_authorization_stays_out_of_javascript(self):
        activity = self.read(JAVA / "MainActivity.java")
        authorization = self.read(JAVA / "AndroidGoogleAuthorization.java")
        gradle = self.read(ANDROID / "build.gradle")
        self.assertIn("play-services-auth:21.6.0", gradle)
        self.assertIn('new AndroidGoogleAuthorization(this, this::handleGoogleAuthorizationResult)', activity)
        self.assertIn('authorizeGoogleDrive(String token, String action, String csrfToken)', activity)
        self.assertIn("googleAuthorization.onActivityResult", activity)
        self.assertIn("googleAuthorization.close()", activity)
        self.assertIn("AuthorizationRequest.builder()", authorization)
        self.assertIn("setRequestedScopes(Arrays.asList(new Scope(DRIVE_SCOPE)))", authorization)
        self.assertIn("Identity.getAuthorizationClient(activity)", authorization)
        self.assertIn("result.getAccessToken()", authorization)
        self.assertIn("/settings/google-drive/android-token", authorization)
        self.assertIn('setRequestProperty("X-CSRF-Token", csrf)', authorization)
        self.assertIn('setRequestProperty("Cookie", cookie)', authorization)
        self.assertNotIn("simpleoffice4me://", authorization)
        self.assertNotIn("accessToken", activity)

    def test_webview_follows_android_activity_lifecycle(self):
        activity = self.read(JAVA / "MainActivity.java")
        self.assertIn('webView.onPause()', activity)
        self.assertIn('webView.pauseTimers()', activity)
        self.assertIn('webView.resumeTimers()', activity)
        self.assertIn('webView.onResume()', activity)
        self.assertIn('downloadHandler.close()', activity)

    def test_android_7_and_both_abis_remain_supported(self):
        gradle = self.read(ANDROID / "build.gradle")
        self.assertIn('minSdk 24', gradle)
        self.assertIn("'arm64-v8a': '3.13'", gradle)
        self.assertIn("'armeabi-v7a': '3.11'", gradle)
        self.assertRegex(gradle, r"versionCode\s+8\b")
        self.assertIn("versionName '1.0.7'", gradle)

    def test_android_integration_is_documented(self):
        docs = self.read(ROOT / "docs" / "ANDROID_INTEGRATION.md")
        self.assertIn('ACTION_SEND', docs)
        self.assertIn('ACTION_CREATE_DOCUMENT', docs)
        self.assertIn('content://', docs)
        self.assertIn('Android 7', docs)
        self.assertIn('Deep Links', docs)
        self.assertIn('AuthorizationClient', docs)


if __name__ == "__main__":
    unittest.main()

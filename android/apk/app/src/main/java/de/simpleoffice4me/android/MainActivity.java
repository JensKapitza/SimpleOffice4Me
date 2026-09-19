package de.simpleoffice4me.android;

import android.Manifest;
import android.app.Activity;
import android.media.projection.MediaProjectionManager;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.AssetManager;
import android.net.Uri;
import android.nfc.NfcAdapter;
import android.nfc.Tag;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import com.google.mlkit.vision.codescanner.GmsBarcodeScanner;
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final String LOCAL_URL = "http://127.0.0.1:8765/";
    private static final String RUNTIME_PREFS = "simpleoffice-runtime";
    private static final String RUNTIME_VERSION = "bundle-version";
    private static final int CAMERA_PERMISSION_REQUEST = 701;
    private static final int FILE_CHOOSER_REQUEST = 702;
    private static final int AUDIO_PERMISSION_REQUEST = 703;
    private static final int SCREEN_CAPTURE_REQUEST = 704;

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final String nativeBridgeToken = UUID.randomUUID().toString();
    private final AndroidAudioStreamer audioStreamer = new AndroidAudioStreamer();
    private final AndroidIntentRouter intentRouter = new AndroidIntentRouter();
    private AndroidDownloadHandler downloadHandler;
    private AndroidGoogleAuthorization googleAuthorization;
    private WebView webView;
    private ProgressBar progress;
    private TextView status;
    private Bundle pendingWebState;
    private PermissionRequest pendingCameraPermission;
    private ValueCallback<Uri[]> fileChooserCallback;
    private NfcAdapter nfcAdapter;
    private boolean nfcScanRequested;
    private volatile boolean localPageVisible;
    private boolean mainFrameLoadFailed;
    private String pendingBarcodeResult;
    private String pendingBarcodeStatus;
    private String pendingAudioTargets;
    private int pendingAudioBitrate = 64;
    private boolean screenCapturePermissionPending;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        pendingWebState = savedInstanceState;
        nfcAdapter = NfcAdapter.getDefaultAdapter(this);
        intentRouter.accept(getIntent());
        downloadHandler = new AndroidDownloadHandler(this);
        googleAuthorization = new AndroidGoogleAuthorization(this, this::handleGoogleAuthorizationResult);
        ScreenCaptureService.setListener(this::dispatchNativeScreenEvent);
        buildUi();
        executor.execute(this::prepareAndStartBackend);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        intentRouter.accept(intent);
        if (webView != null && isLocalUrl(webView.getUrl())) {
            if (!navigateToPendingIntent(webView, webView.getUrl())) {
                injectPendingShareUi(webView, webView.getUrl());
            }
        }
    }

    private void buildUi() {
        FrameLayout root = new FrameLayout(this);
        webView = new WebView(this);
        progress = new ProgressBar(this);
        status = new TextView(this);
        status.setText("SimpleOffice4Me wird gestartet …");
        status.setTextSize(18f);
        status.setPadding(32, 32, 32, 32);
        status.setGravity(Gravity.CENTER_HORIZONTAL);
        status.setTextIsSelectable(true);
        progress.setContentDescription("SimpleOffice4Me wird gestartet");

        root.addView(webView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));
        root.addView(status, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT));
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(96, 96);
        progressParams.gravity = Gravity.CENTER;
        root.addView(progress, progressParams);
        setContentView(root);

        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setUserAgentString(settings.getUserAgentString()
                + " SimpleOffice4Me-Android/" + BuildConfig.VERSION_NAME);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) settings.setSafeBrowsingEnabled(true);

        webView.addJavascriptInterface(new NativeBridge(), "SimpleOfficeAndroid");
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                mainHandler.post(() -> handleWebPermissionRequest(request));
            }

            @Override
            public void onPermissionRequestCanceled(PermissionRequest request) {
                mainHandler.post(() -> {
                    if (pendingCameraPermission == request) pendingCameraPermission = null;
                });
            }

            @Override
            public boolean onShowFileChooser(
                    WebView view,
                    ValueCallback<Uri[]> callback,
                    FileChooserParams params) {
                if (!localPageVisible || !isLocalUrl(view.getUrl())) {
                    callback.onReceiveValue(null);
                    return true;
                }
                if (fileChooserCallback != null) {
                    fileChooserCallback.onReceiveValue(null);
                    fileChooserCallback = null;
                }
                if (intentRouter.hasPendingSharedFiles() && isDocumentsIndexUrl(view.getUrl())) {
                    Uri[] shared = intentRouter.consumeSharedFiles();
                    callback.onReceiveValue(shared);
                    showSharedFilesSelected(shared.length);
                    return true;
                }
                fileChooserCallback = callback;
                try {
                    startActivityForResult(params.createIntent(), FILE_CHOOSER_REQUEST);
                    return true;
                } catch (ActivityNotFoundException error) {
                    fileChooserCallback = null;
                    callback.onReceiveValue(null);
                    showStatus("Keine App zum Auswählen einer Datei gefunden.", false);
                    return true;
                }
            }
        });

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String host = uri.getHost();
                if ("http".equalsIgnoreCase(uri.getScheme())
                        && ("127.0.0.1".equals(host) || "localhost".equals(host))
                        && uri.getPort() == 8765) return false;
                String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
                if ("http".equals(scheme) || "https".equals(scheme)
                        || "mailto".equals(scheme) || "tel".equals(scheme)) {
                    openExternalUri(uri);
                } else {
                    showStatus("Externer Link mit nicht unterstütztem Schema blockiert: " + scheme, false);
                }
                return true;
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                super.onPageStarted(view, url, favicon);
                localPageVisible = isLocalUrl(url);
                if (!localPageVisible) {
                    stopNfcReader();
                    denyPendingCameraPermission();
                    return;
                }
                mainFrameLoadFailed = false;
                showStatus("SimpleOffice4Me wird geladen …", true);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                localPageVisible = isLocalUrl(url);
                if (localPageVisible) {
                    view.evaluateJavascript(nativeShim(nativeBridgeToken), null);
                    flushPendingBarcodeResult();
                    if (navigateToPendingIntent(view, url)) return;
                    injectPendingShareUi(view, url);
                    if (!mainFrameLoadFailed) {
                        progress.setVisibility(View.GONE);
                        status.setVisibility(View.GONE);
                    }
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                super.onReceivedError(view, request, error);
                if (request.isForMainFrame()) {
                    mainFrameLoadFailed = true;
                    localPageVisible = false;
                    stopNfcReader();
                    denyPendingCameraPermission();
                    showStatus("Seite konnte nicht geladen werden:\n" + error.getDescription(), false);
                }
            }

            @Override
            public void onReceivedHttpError(
                    WebView view,
                    WebResourceRequest request,
                    WebResourceResponse errorResponse) {
                super.onReceivedHttpError(view, request, errorResponse);
                if (request.isForMainFrame() && errorResponse.getStatusCode() >= 500) {
                    mainFrameLoadFailed = true;
                    showStatus("Lokaler Serverfehler: HTTP " + errorResponse.getStatusCode(), false);
                }
            }
        });

        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            if (downloadHandler != null && downloadHandler.begin(url, userAgent, contentDisposition, mimeType)) return;
            try {
                openExternalUri(Uri.parse(url));
            } catch (Exception error) {
                showStatus("Download-Link konnte nicht geöffnet werden.", false);
            }
        });
    }

    private void openExternalUri(Uri uri) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (ActivityNotFoundException error) {
            showStatus("Keine passende App für diesen Link gefunden.", false);
        }
    }

    private boolean navigateToPendingIntent(WebView view, String currentUrl) {
        if (!intentRouter.hasPendingNavigation() || !isLocalUrl(currentUrl)) return false;
        String currentPath = Uri.parse(currentUrl).getPath();
        if (currentPath != null && currentPath.startsWith("/auth/")) return false;
        String target = intentRouter.pendingNavigationPath();
        if (samePath(currentPath, target)) {
            intentRouter.clearPendingNavigation();
            return false;
        }
        intentRouter.clearPendingNavigation();
        view.loadUrl(localUrl(target));
        return true;
    }

    private static boolean samePath(String first, String second) {
        if (first == null || second == null) return false;
        return trimTrailingSlash(first).equals(trimTrailingSlash(second));
    }

    private static String trimTrailingSlash(String value) {
        return value.length() > 1 && value.endsWith("/")
                ? value.substring(0, value.length() - 1) : value;
    }

    private static String localUrl(String path) {
        if (path == null || path.isEmpty() || "/".equals(path)) return LOCAL_URL;
        return LOCAL_URL + (path.startsWith("/") ? path.substring(1) : path);
    }

    private static boolean isDocumentsIndexUrl(String value) {
        if (!isLocalUrl(value)) return false;
        String path = Uri.parse(value).getPath();
        return "/documents".equals(trimTrailingSlash(path == null ? "" : path));
    }

    private void injectPendingShareUi(WebView view, String url) {
        if (!intentRouter.hasPendingSharedFiles() || !isLocalUrl(url)) return;
        String path = Uri.parse(url).getPath();
        if (path != null && path.startsWith("/auth/")) return;
        int count = intentRouter.pendingSharedFileCount();
        String script = "(function(count){"
                + "let box=document.getElementById('android-share-import');if(box)box.remove();"
                + "const root=document.getElementById('main-content')||document.querySelector('main')||document.body;if(!root)return;"
                + "box=document.createElement('div');box.id='android-share-import';box.className='alert alert-primary d-flex flex-wrap align-items-center justify-content-between gap-2';"
                + "const text=document.createElement('span');text.textContent='Android hat '+count+' Datei(en) an SimpleOffice geteilt.';"
                + "const button=document.createElement('button');button.type='button';button.className='btn btn-sm btn-primary';"
                + "const input=document.querySelector('input[type=file][name=files]');"
                + "if(input){button.textContent='Dateien übernehmen';button.addEventListener('click',()=>input.click());}"
                + "else{button.textContent='Zu Dokumenten';button.addEventListener('click',()=>{window.location.href='/documents';});}"
                + "box.append(text,button);root.insertBefore(box,root.firstChild);"
                + "})(" + count + ");";
        view.evaluateJavascript(script, null);
    }

    private void showSharedFilesSelected(int count) {
        if (webView == null || !isLocalUrl(webView.getUrl())) return;
        String script = "(function(count){const box=document.getElementById('android-share-import');if(!box)return;"
                + "box.className='alert alert-success';box.textContent=count+' Android-Datei(en) sind ausgewählt. Mit Vollständig importieren bestätigen.';"
                + "})(" + count + ");";
        webView.evaluateJavascript(script, null);
    }

    private static String nativeShim(String token) {
        String quotedToken = JSONObject.quote(token);
        return "(function(){"
                + "if(!window.SimpleOfficeAndroid)return;"
                + "const bridgeToken=" + quotedToken + ";"
                + "window.SimpleOfficeNativeAudio={"
                + "status:()=>JSON.parse(String(window.SimpleOfficeAndroid.audioStatus(bridgeToken))),"
                + "startSender:(targets,bitrate)=>String(window.SimpleOfficeAndroid.startAudioSender(bridgeToken,JSON.stringify(targets||[]),Number(bitrate||64))),"
                + "stopSender:()=>String(window.SimpleOfficeAndroid.stopAudioSender(bridgeToken)),"
                + "startReceiver:(port)=>String(window.SimpleOfficeAndroid.startAudioReceiver(bridgeToken,Number(port||5004))),"
                + "stopReceiver:()=>String(window.SimpleOfficeAndroid.stopAudioReceiver(bridgeToken))};"
                + "window.SimpleOfficeNativeScreen=(function(){let pending=null,canvas=null,context=null,track=null;"
                + "const ensureCanvas=(width,height)=>{if(!canvas){canvas=document.createElement('canvas');context=canvas.getContext('2d',{alpha:false});}canvas.width=width;canvas.height=height;return canvas;};"
                + "window.addEventListener('simpleoffice:native-screen',event=>{const detail=event.detail||{};"
                + "if(detail.state==='active'&&pending){const target=ensureCanvas(Number(detail.width||1280),Number(detail.height||720));track=target.captureStream(12).getVideoTracks()[0];pending.resolve(new MediaStream([track]));pending=null;}"
                + "else if(detail.state==='frame'&&detail.data&&context){const image=new Image();image.onload=()=>context.drawImage(image,0,0,canvas.width,canvas.height);image.src='data:image/jpeg;base64,'+detail.data;}"
                + "else if((detail.state==='cancelled'||detail.state==='error')&&pending){pending.reject(new Error(detail.message||'Android-Bildschirmfreigabe abgebrochen.'));pending=null;}"
                + "else if(detail.state==='stopped'&&track){track.stop();track=null;}});"
                + "return {openCast:()=>String(window.SimpleOfficeAndroid.openCastSettings(bridgeToken)),"
                + "startShare:()=>new Promise((resolve,reject)=>{if(pending)return reject(new Error('Bildschirmauswahl läuft bereits.'));pending={resolve,reject};const state=String(window.SimpleOfficeAndroid.startScreenCapture(bridgeToken));if(state!=='permission'){pending=null;reject(new Error(state==='busy'?'Bildschirmauswahl läuft bereits.':'MediaProjection ist nicht verfügbar.'));}}),"
                + "stopShare:()=>{if(track){track.stop();track=null;}return String(window.SimpleOfficeAndroid.stopScreenCapture(bridgeToken));}};})();"
                + audioUiShim()
                + googleDriveUiShim()
                + "window.dispatchEvent(new Event('simpleoffice:native-audio-ready'));"
                + "if(!window.NDEFReader){class NativeNDEFReader extends EventTarget{async scan(){"
                + "const state=String(window.SimpleOfficeAndroid.startNfcScan(bridgeToken));"
                + "if(state!=='ok')throw new DOMException(state==='disabled'?'NFC ist deaktiviert.':'NFC ist nicht verfügbar.','NotSupportedError');"
                + "window.addEventListener('simpleoffice:nfc',(event)=>{const reading=new Event('reading');"
                + "Object.defineProperty(reading,'serialNumber',{value:String(event.detail||'')});"
                + "Object.defineProperty(reading,'message',{value:{records:[]}});this.dispatchEvent(reading);},{once:true});}}"
                + "window.NDEFReader=NativeNDEFReader;}"
                + "document.addEventListener('click',function(event){"
                + "const trigger=event.target&&event.target.closest?event.target.closest('#start-barcode'):null;"
                + "if(!trigger||('BarcodeDetector' in window))return;"
                + "event.preventDefault();event.stopImmediatePropagation();"
                + "const state=String(window.SimpleOfficeAndroid.startBarcodeScan(bridgeToken));"
                + "const status=document.getElementById('scan-status');"
                + "if(status){status.className='alert alert-'+(state==='ok'?'primary':'warning')+' py-2 small';"
                + "status.textContent=state==='ok'?'Android-Scanner wird geöffnet …':'Nativer Scanner ist nicht verfügbar. Kennung bitte manuell eingeben.';}"
                + "},true);"
                + "})();";
    }

    private static String googleDriveUiShim() {
        return "document.addEventListener('submit',function(event){"
                + "const form=event.target;if(!(form instanceof HTMLFormElement))return;let path='';"
                + "try{path=new URL(form.action,window.location.href).pathname;}catch(error){return;}"
                + "if(path!=='/settings/google-drive/connect'&&path!=='/settings/google-drive/sync')return;"
                + "event.preventDefault();event.stopImmediatePropagation();"
                + "const action=path.endsWith('/sync')?'sync':'connect';"
                + "const csrf=document.querySelector('meta[name=csrf-token]')?.content||'';"
                + "const root=document.getElementById('main-content')||document.querySelector('main')||document.body;"
                + "let notice=document.getElementById('android-google-drive-status');"
                + "if(!notice){notice=document.createElement('div');notice.id='android-google-drive-status';root.insertBefore(notice,root.firstChild);}"
                + "notice.className='alert alert-primary';notice.textContent='Google-Berechtigung wird auf Android geprüft …';"
                + "const result=String(window.SimpleOfficeAndroid.authorizeGoogleDrive(bridgeToken,action,csrf));"
                + "if(result!=='ok'){notice.className='alert alert-warning';notice.textContent=result==='busy'?'Eine Google-Autorisierung läuft bereits.':'Native Google-Autorisierung ist nicht verfügbar.';}"
                + "},true);";
    }

    private static String audioUiShim() {
        return "const audioRoot=document.getElementById('audio-streamer-app');"
                + "if(audioRoot){const nativeAudio=window.SimpleOfficeNativeAudio;const status=document.getElementById('stream-status');"
                + "const show=(text,kind)=>{if(status){status.className='alert alert-'+(kind||'secondary');status.textContent=text;}};"
                + "const render=()=>{const state=nativeAudio.status();const sender=state.sender&&state.sender.running?'Sender läuft':'Sender aus';"
                + "const receiver=state.receiver&&state.receiver.running?'Receiver läuft auf Port '+state.receiver.port:'Receiver aus';"
                + "if(state.last_error)show(state.last_error,'danger');else show(sender+' · '+receiver+' · Android nativ','secondary');};"
                + "const backend=document.getElementById('capture-backend');if(backend){let option=Array.from(backend.options).find(o=>o.value==='android');"
                + "if(!option){option=new Option('Android · automatisch','android',true,true);backend.prepend(option);}backend.value='android';backend.disabled=true;}"
                + "const source=document.getElementById('capture-source');if(source){source.value='Android Systemmikrofon';source.readOnly=true;}"
                + "const speaker=document.getElementById('speaker-device');if(speaker){speaker.value='Android Systemausgabe';speaker.readOnly=true;}"
                + "const virtualMic=document.getElementById('virtual-microphone');if(virtualMic){virtualMic.checked=false;virtualMic.disabled=true;}"
                + "const bitrate=document.getElementById('stream-bitrate');if(bitrate)bitrate.value='64';"
                + "const receiverPort=document.getElementById('receiver-port');if(receiverPort)receiverPort.value='5004';"
                + "const explain=(result)=>result==='permission'?['Mikrofonzugriff bitte einmal erlauben. Der Sender startet danach automatisch.','primary']:"
                + "result==='unsupported'?['Dieses Android-Gerät stellt keinen Opus-Encoder bereit.','warning']:"
                + "result==='invalid-target'?['Bitte ein Ziel wie 192.168.1.50:5004 angeben.','warning']:"
                + "result==='invalid-port'?['RTP-Port muss zwischen 1024 und 65535 liegen.','warning']:['Audio-Aktion fehlgeschlagen: '+result,'danger'];"
                + "document.addEventListener('click',function(event){const button=event.target&&event.target.closest?event.target.closest('#sender-start,#sender-stop,#receiver-start,#receiver-stop'):null;"
                + "if(!button)return;event.preventDefault();event.stopImmediatePropagation();try{let result='ok';"
                + "if(button.id==='sender-start'){const box=document.getElementById('stream-targets');const targets=String(box?box.value:'').split(/\\r?\\n/).map(line=>line.trim()).filter(Boolean).map(line=>{const match=line.match(/^(.+):(\\d+)$/);if(!match)throw new Error('Ungültiges Ziel: '+line);return {host:match[1],port:Number(match[2])};});"
                + "result=nativeAudio.startSender(targets,Number(bitrate&&bitrate.value||64));}"
                + "else if(button.id==='sender-stop')result=nativeAudio.stopSender();"
                + "else if(button.id==='receiver-start')result=nativeAudio.startReceiver(Number(receiverPort&&receiverPort.value||5004));"
                + "else if(button.id==='receiver-stop')result=nativeAudio.stopReceiver();"
                + "if(result==='ok')render();else{const info=explain(result);show(info[0],info[1]);}}catch(error){show(String(error&&error.message||'Android-Audio konnte nicht gestartet werden.'),'danger');}},true);"
                + "window.addEventListener('simpleoffice:native-audio-status',event=>{if(event.detail&&event.detail.message)show(String(event.detail.message),'danger');else render();});render();}";
    }

    private void handleWebPermissionRequest(PermissionRequest request) {
        if (!localPageVisible || !isTrustedLocalOrigin(request.getOrigin()) || !requestsOnlyVideo(request)) {
            request.deny();
            return;
        }
        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
            return;
        }
        denyPendingCameraPermission();
        pendingCameraPermission = request;
        requestPermissions(new String[]{Manifest.permission.CAMERA}, CAMERA_PERMISSION_REQUEST);
    }

    private static boolean requestsOnlyVideo(PermissionRequest request) {
        String[] resources = request.getResources();
        return resources.length == 1 && PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resources[0]);
    }

    private static boolean isTrustedLocalOrigin(Uri origin) {
        if (origin == null || !"http".equalsIgnoreCase(origin.getScheme())) return false;
        String host = origin.getHost();
        return ("127.0.0.1".equals(host) || "localhost".equals(host)) && origin.getPort() == 8765;
    }

    private void denyPendingCameraPermission() {
        PermissionRequest request = pendingCameraPermission;
        pendingCameraPermission = null;
        if (request != null) {
            try {
                request.deny();
            } catch (IllegalStateException ignored) {
            }
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == AUDIO_PERMISSION_REQUEST) {
            handleAudioPermissionResult(grantResults);
            return;
        }
        if (requestCode != CAMERA_PERMISSION_REQUEST) return;
        PermissionRequest request = pendingCameraPermission;
        pendingCameraPermission = null;
        if (request == null) return;
        boolean granted = localPageVisible && grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED
                && isTrustedLocalOrigin(request.getOrigin()) && requestsOnlyVideo(request);
        try {
            if (granted) request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
            else request.deny();
        } catch (IllegalStateException ignored) {
        }
    }

    private void handleAudioPermissionResult(int[] grantResults) {
        String targets = pendingAudioTargets;
        pendingAudioTargets = null;
        boolean granted = grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED;
        if (granted && targets != null && webView != null && isLocalUrl(webView.getUrl())) {
            String result = audioStreamer.startSender(targets, pendingAudioBitrate);
            dispatchNativeAudioStatus(
                    "ok".equals(result) ? "" : "Android-Audiosender konnte nicht gestartet werden.");
        } else {
            dispatchNativeAudioStatus("Mikrofonberechtigung wurde nicht erteilt.");
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (googleAuthorization != null && googleAuthorization.onActivityResult(requestCode, resultCode, data)) return;
        if (downloadHandler != null && downloadHandler.onActivityResult(requestCode, resultCode, data)) return;
        if (requestCode == SCREEN_CAPTURE_REQUEST) {
            screenCapturePermissionPending = false;
            if (resultCode != RESULT_OK || data == null) {
                dispatchNativeScreenEvent("cancelled", 0, 0, "", "Bildschirmfreigabe wurde nicht erlaubt.");
                return;
            }
            Intent service = ScreenCaptureService.startIntent(this, resultCode, data);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(service);
            else startService(service);
            return;
        }
        if (requestCode == FILE_CHOOSER_REQUEST) {
            ValueCallback<Uri[]> callback = fileChooserCallback;
            fileChooserCallback = null;
            if (callback != null) {
                Uri[] selected = WebChromeClient.FileChooserParams.parseResult(resultCode, data);
                boolean trustedPage = webView != null && isLocalUrl(webView.getUrl());
                callback.onReceiveValue(trustedPage ? selected : null);
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    private final class NativeBridge {
        @JavascriptInterface
        public String openCastSettings(String token) {
            if (!bridgeAllowed(token)) return "blocked";
            Intent intent = new Intent(Settings.ACTION_CAST_SETTINGS);
            if (intent.resolveActivity(getPackageManager()) == null) return "unavailable";
            mainHandler.post(() -> startActivity(intent));
            return "ok";
        }

        @JavascriptInterface
        public String startScreenCapture(String token) {
            if (!bridgeAllowed(token)) return "blocked";
            if (screenCapturePermissionPending || ScreenCaptureService.isRunning()) return "busy";
            MediaProjectionManager manager = getSystemService(MediaProjectionManager.class);
            if (manager == null) return "unavailable";
            screenCapturePermissionPending = true;
            mainHandler.post(() -> startActivityForResult(manager.createScreenCaptureIntent(), SCREEN_CAPTURE_REQUEST));
            return "permission";
        }

        @JavascriptInterface
        public String stopScreenCapture(String token) {
            if (!bridgeAllowed(token)) return "blocked";
            screenCapturePermissionPending = false;
            mainHandler.post(() -> startService(ScreenCaptureService.stopIntent(MainActivity.this)));
            return "ok";
        }

        @JavascriptInterface
        public String startNfcScan(String token) {
            if (!bridgeAllowed(token)) return "blocked";
            if (nfcAdapter == null) return "unavailable";
            if (!nfcAdapter.isEnabled()) return "disabled";
            mainHandler.post(MainActivity.this::beginNfcScan);
            return "ok";
        }

        @JavascriptInterface
        public String startBarcodeScan(String token) {
            if (!bridgeAllowed(token)) return "blocked";
            mainHandler.post(MainActivity.this::beginBarcodeScan);
            return "ok";
        }

        @JavascriptInterface
        public String authorizeGoogleDrive(String token, String action, String csrfToken) {
            if (!bridgeAllowed(token)) return "blocked";
            String normalizedAction = action == null ? "" : action.trim().toLowerCase(Locale.ROOT);
            String csrf = csrfToken == null ? "" : csrfToken.trim();
            if (!("connect".equals(normalizedAction) || "sync".equals(normalizedAction))) return "invalid";
            if (csrf.length() < 32 || csrf.length() > 256 || googleAuthorization == null) return "invalid";
            mainHandler.post(() -> googleAuthorization.authorize(normalizedAction, csrf));
            return "ok";
        }

        @JavascriptInterface
        public String audioStatus(String token) {
            return bridgeAllowed(token)
                    ? audioStreamer.statusJson()
                    : "{\"platform\":\"blocked\"}";
        }

        @JavascriptInterface
        public String startAudioSender(String token, String targetsJson, int bitrateKbps) {
            if (!bridgeAllowed(token)) return "blocked";
            try {
                if (!AndroidAudioStreamer.opusEncoderAvailable()) return "unsupported";
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                    pendingAudioTargets = targetsJson;
                    pendingAudioBitrate = Math.max(16, Math.min(bitrateKbps, 256));
                    mainHandler.post(() -> requestPermissions(
                            new String[]{Manifest.permission.RECORD_AUDIO}, AUDIO_PERMISSION_REQUEST));
                    return "permission";
                }
                String result = audioStreamer.startSender(targetsJson, bitrateKbps);
                dispatchNativeAudioStatus(
                        "ok".equals(result) ? "" : "Android-Audiosender konnte nicht gestartet werden.");
                return result;
            } catch (RuntimeException error) {
                dispatchNativeAudioStatus("Android-Audiosender konnte nicht gestartet werden.");
                return "runtime-error";
            }
        }

        @JavascriptInterface
        public String stopAudioSender(String token) {
            if (!bridgeAllowed(token)) return "blocked";
            pendingAudioTargets = null;
            audioStreamer.stopSender();
            dispatchNativeAudioStatus("");
            return "ok";
        }

        @JavascriptInterface
        public String startAudioReceiver(String token, int port) {
            if (!bridgeAllowed(token)) return "blocked";
            try {
                String result = audioStreamer.startReceiver(port);
                dispatchNativeAudioStatus(
                        "ok".equals(result) ? "" : "Android-Audioempfang konnte nicht gestartet werden.");
                return result;
            } catch (RuntimeException error) {
                dispatchNativeAudioStatus("Android-Audioempfang konnte nicht gestartet werden.");
                return "runtime-error";
            }
        }

        @JavascriptInterface
        public String stopAudioReceiver(String token) {
            if (!bridgeAllowed(token)) return "blocked";
            audioStreamer.stopReceiver();
            dispatchNativeAudioStatus("");
            return "ok";
        }
    }

    private boolean bridgeAllowed(String token) {
        return nativeBridgeToken.equals(token) && localPageVisible;
    }

    private void dispatchNativeScreenEvent(String state, int width, int height, String data, String message) {
        mainHandler.post(() -> {
            if (webView == null || !isLocalUrl(webView.getUrl())) return;
            webView.evaluateJavascript("window.dispatchEvent(new CustomEvent('simpleoffice:native-screen',{detail:{state:"
                    + JSONObject.quote(state) + ",width:" + width + ",height:" + height + ",data:"
                    + JSONObject.quote(data == null ? "" : data) + ",message:"
                    + JSONObject.quote(message == null ? "" : message) + "}}));", null);
        });
    }

    private void handleGoogleAuthorizationResult(String action, String result) {
        if (webView == null) return;
        String statusResult;
        if ("connected".equals(result) || "synced".equals(result)
                || "cancelled".equals(result) || "unavailable".equals(result)) {
            statusResult = result;
        } else {
            statusResult = "error";
        }
        webView.loadUrl(localUrl("/settings/google-drive?android=" + statusResult));
    }

    private void dispatchNativeAudioStatus(String message) {
        mainHandler.post(() -> {
            if (webView == null || !isLocalUrl(webView.getUrl())) return;
            String statusJson = JSONObject.quote(audioStreamer.statusJson());
            String messageJson = JSONObject.quote(message == null ? "" : message);
            webView.evaluateJavascript(
                    "window.dispatchEvent(new CustomEvent('simpleoffice:native-audio-status',{detail:{status:JSON.parse("
                            + statusJson + "),message:" + messageJson + "}}));",
                    null);
        });
    }

    private void beginBarcodeScan() {
        if (!localPageVisible || webView == null) return;
        GmsBarcodeScanner scanner = GmsBarcodeScanning.getClient(this);
        scanner.startScan()
                .addOnSuccessListener(barcode -> dispatchBarcodeResult(barcode.getRawValue()))
                .addOnCanceledListener(() -> dispatchBarcodeStatus("cancelled"))
                .addOnFailureListener(error -> dispatchBarcodeStatus("error"));
    }

    private void dispatchBarcodeResult(String value) {
        String clean = value == null ? "" : value.trim();
        if (clean.isEmpty()) {
            dispatchBarcodeStatus("empty");
            return;
        }
        mainHandler.post(() -> {
            if (webView == null || !localPageVisible || !isLocalUrl(webView.getUrl())) {
                pendingBarcodeResult = clean;
                pendingBarcodeStatus = null;
                return;
            }
            applyBarcodeToPage(clean);
        });
    }

    private void applyBarcodeToPage(String clean) {
        if (webView == null || !localPageVisible || !isLocalUrl(webView.getUrl())) return;
        String quoted = JSONObject.quote(clean);
        String script = "(function(value){"
                + "const barcode=document.getElementById('barcode');if(!barcode)return;"
                + "barcode.value=value;barcode.dispatchEvent(new Event('input',{bubbles:true}));barcode.dispatchEvent(new Event('change',{bubbles:true}));"
                + "const compact=String(value).replace(/[^0-9Xx]/g,'').toUpperCase();"
                + "const likely=compact.length===10||(compact.length===13&&/^97[89]/.test(compact));"
                + "const isbn=document.getElementById('isbn');"
                + "if(likely&&isbn){isbn.value=value;isbn.dispatchEvent(new Event('input',{bubbles:true}));const lookup=document.getElementById('lookup-book');if(lookup)lookup.click();}"
                + "else{barcode.dispatchEvent(new Event('blur'));const status=document.getElementById('scan-status');if(status){status.className='alert alert-success py-2 small';status.textContent='Barcode erkannt: '+value;}}"
                + "})(" + quoted + ");";
        webView.evaluateJavascript(script, null);
    }

    private void dispatchBarcodeStatus(String state) {
        mainHandler.post(() -> {
            if (webView == null || !localPageVisible || !isLocalUrl(webView.getUrl())) {
                pendingBarcodeStatus = state;
                return;
            }
            applyBarcodeStatusToPage(state);
        });
    }

    private void applyBarcodeStatusToPage(String state) {
        if (webView == null || !localPageVisible || !isLocalUrl(webView.getUrl())) return;
        String quoted = JSONObject.quote(state);
        String script = "(function(state){const status=document.getElementById('scan-status');if(!status)return;"
                + "if(state==='cancelled'){status.className='alert alert-secondary py-2 small';status.textContent='Barcode-Scan abgebrochen.';}"
                + "else{status.className='alert alert-warning py-2 small';status.textContent='Barcode konnte nicht gelesen werden. Kennung kann manuell eingetragen werden.';}"
                + "})(" + quoted + ");";
        webView.evaluateJavascript(script, null);
    }

    private void flushPendingBarcodeResult() {
        if (!localPageVisible || webView == null || !isLocalUrl(webView.getUrl())) return;
        String result = pendingBarcodeResult;
        String state = pendingBarcodeStatus;
        pendingBarcodeResult = null;
        pendingBarcodeStatus = null;
        if (result != null) applyBarcodeToPage(result);
        else if (state != null) applyBarcodeStatusToPage(state);
    }

    private void beginNfcScan() {
        if (!localPageVisible || nfcAdapter == null || !nfcAdapter.isEnabled()) return;
        int flags = NfcAdapter.FLAG_READER_NFC_A | NfcAdapter.FLAG_READER_NFC_B
                | NfcAdapter.FLAG_READER_NFC_F | NfcAdapter.FLAG_READER_NFC_V
                | NfcAdapter.FLAG_READER_NFC_BARCODE;
        nfcScanRequested = true;
        try {
            nfcAdapter.enableReaderMode(this, this::handleNfcTag, flags, null);
        } catch (IllegalStateException error) {
            nfcScanRequested = false;
        }
    }

    private void handleNfcTag(Tag tag) {
        String serial = toHex(tag == null ? null : tag.getId());
        mainHandler.post(() -> {
            stopNfcReader();
            if (webView == null || !localPageVisible || serial.isEmpty()) return;
            String quoted = JSONObject.quote(serial);
            webView.evaluateJavascript(
                    "window.dispatchEvent(new CustomEvent('simpleoffice:nfc',{detail:" + quoted + "}));",
                    null);
        });
    }

    private void stopNfcReader() {
        if (nfcAdapter != null && nfcScanRequested) {
            try {
                nfcAdapter.disableReaderMode(this);
            } catch (IllegalStateException ignored) {
            }
        }
        nfcScanRequested = false;
    }

    private static String toHex(byte[] bytes) {
        if (bytes == null || bytes.length == 0) return "";
        char[] alphabet = "0123456789ABCDEF".toCharArray();
        char[] result = new char[bytes.length * 2];
        for (int index = 0; index < bytes.length; index++) {
            int value = bytes[index] & 0xff;
            result[index * 2] = alphabet[value >>> 4];
            result[index * 2 + 1] = alphabet[value & 0x0f];
        }
        return new String(result);
    }

    private static boolean isLocalUrl(String value) {
        try {
            Uri uri = Uri.parse(value);
            String host = uri.getHost();
            return "http".equalsIgnoreCase(uri.getScheme())
                    && ("127.0.0.1".equals(host) || "localhost".equals(host))
                    && uri.getPort() == 8765;
        } catch (Exception error) {
            return false;
        }
    }

    private void showStatus(String message, boolean showProgress) {
        mainHandler.post(() -> {
            if (status != null) {
                status.setText(message);
                status.setVisibility(View.VISIBLE);
            }
            if (progress != null) progress.setVisibility(showProgress ? View.VISIBLE : View.GONE);
        });
    }

    private void syncRuntimeAssets(File runtimeRoot) throws IOException {
        SharedPreferences preferences = getSharedPreferences(RUNTIME_PREFS, MODE_PRIVATE);
        int installedVersion = preferences.getInt(RUNTIME_VERSION, -1);
        if (installedVersion == BuildConfig.VERSION_CODE && runtimeRoot.isDirectory()) return;
        showStatus("Lokale Programmdateien werden aktualisiert …", true);
        copyAssetTree(getAssets(), "simpleoffice", runtimeRoot);
        preferences.edit().putInt(RUNTIME_VERSION, BuildConfig.VERSION_CODE).apply();
    }

    private void prepareAndStartBackend() {
        try {
            File runtimeRoot = new File(getFilesDir(), "simpleoffice-runtime");
            syncRuntimeAssets(runtimeRoot);
            showStatus("Python-Laufzeit wird gestartet …", true);
            if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
            Python python = Python.getInstance();
            PyObject module = python.getModule("android_runtime");
            module.callAttr(
                    "start",
                    runtimeRoot.getAbsolutePath(),
                    BuildConfig.ERROR_REPORT_URL,
                    "",
                    "",
                    AndroidLanNetwork.localPrivateIpv4(this));
            showStatus("Lokales Backend wird geprüft …", true);
            waitForBackend();
            mainHandler.post(() -> {
                if (webView == null) return;
                Bundle state = pendingWebState;
                pendingWebState = null;
                if (!intentRouter.hasPendingNavigation()
                        && state != null && webView.restoreState(state) != null) return;
                webView.loadUrl(LOCAL_URL);
            });
        } catch (Exception error) {
            String detail = error.getMessage();
            if (detail == null || detail.trim().isEmpty()) detail = error.getClass().getSimpleName();
            showStatus("Start fehlgeschlagen:\n" + detail, false);
        }
    }

    protected final void syncLanAddressesToBackend() {
        if (!Python.isStarted() || executor.isShutdown()) return;
        try {
            executor.execute(() -> {
                try {
                    Python.getInstance()
                            .getModule("android_runtime")
                            .callAttr("set_lan_addresses", AndroidLanNetwork.localPrivateIpv4(this));
                } catch (RuntimeException ignored) {
                    // Discovery still has the normal socket-based fallback.
                }
            });
        } catch (RuntimeException ignored) {
            // Activity shutdown may race with a late Android network callback.
        }
    }

    private void waitForBackend() throws Exception {
        long deadline = System.currentTimeMillis() + 45000;
        Exception lastError = null;
        while (System.currentTimeMillis() < deadline) {
            if (Thread.currentThread().isInterrupted()) {
                throw new InterruptedException("Backend-Prüfung wurde abgebrochen");
            }
            HttpURLConnection connection = null;
            try {
                connection = (HttpURLConnection) new URL(LOCAL_URL).openConnection();
                connection.setConnectTimeout(800);
                connection.setReadTimeout(800);
                connection.setInstanceFollowRedirects(false);
                connection.setUseCaches(false);
                connection.setRequestProperty("Connection", "close");
                int statusCode = connection.getResponseCode();
                if (statusCode >= 200 && statusCode < 500) return;
                lastError = new IOException("HTTP " + statusCode);
            } catch (Exception error) {
                lastError = error;
            } finally {
                if (connection != null) connection.disconnect();
            }
            Thread.sleep(250);
        }
        String detail = lastError == null ? "" : ": " + lastError.getClass().getSimpleName()
                + (lastError.getMessage() == null ? "" : " - " + lastError.getMessage());
        throw new IllegalStateException("Lokales Python-Backend antwortet nicht" + detail, lastError);
    }

    private static void copyAssetTree(AssetManager assets, String assetPath, File target) throws IOException {
        String[] children = assets.list(assetPath);
        if (children != null && children.length > 0) {
            if (!target.exists() && !target.mkdirs() && !target.isDirectory()) {
                throw new IOException("Verzeichnis kann nicht erstellt werden: " + target);
            }
            for (String child : children) {
                copyAssetTree(assets, assetPath + "/" + child, new File(target, child));
            }
            return;
        }
        File parent = target.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs() && !parent.isDirectory()) {
            throw new IOException("Verzeichnis kann nicht erstellt werden: " + parent);
        }
        try (InputStream input = assets.open(assetPath);
             FileOutputStream output = new FileOutputStream(target, false)) {
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = input.read(buffer)) >= 0) output.write(buffer, 0, read);
        }
    }

    @Override
    protected void onPause() {
        localPageVisible = false;
        stopNfcReader();
        denyPendingCameraPermission();
        if (webView != null) {
            webView.onPause();
            webView.pauseTimers();
        }
        super.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) {
            webView.resumeTimers();
            webView.onResume();
            localPageVisible = isLocalUrl(webView.getUrl());
            if (localPageVisible && !navigateToPendingIntent(webView, webView.getUrl())) {
                flushPendingBarcodeResult();
                injectPendingShareUi(webView, webView.getUrl());
            }
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        if (webView != null) webView.saveState(outState);
        super.onSaveInstanceState(outState);
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        ScreenCaptureService.setListener(null);
        localPageVisible = false;
        stopNfcReader();
        denyPendingCameraPermission();
        pendingAudioTargets = null;
        audioStreamer.stopAll();
        pendingBarcodeResult = null;
        pendingBarcodeStatus = null;
        if (fileChooserCallback != null) {
            fileChooserCallback.onReceiveValue(null);
            fileChooserCallback = null;
        }
        if (googleAuthorization != null) {
            googleAuthorization.close();
            googleAuthorization = null;
        }
        if (downloadHandler != null) {
            downloadHandler.close();
            downloadHandler = null;
        }
        if (webView != null) {
            webView.removeJavascriptInterface("SimpleOfficeAndroid");
            webView.stopLoading();
            webView.removeAllViews();
            webView.destroy();
            webView = null;
        }
        executor.shutdownNow();
        super.onDestroy();
    }
}

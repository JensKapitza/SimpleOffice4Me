package de.simpleoffice4me.android;

import android.Manifest;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.AssetManager;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaCodec;
import android.media.MediaCodecList;
import android.media.MediaFormat;
import android.media.MediaRecorder;
import android.net.Uri;
import android.nfc.NfcAdapter;
import android.nfc.Tag;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
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

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

public class MainActivity extends Activity {
    private static final String LOCAL_URL = "http://127.0.0.1:8765/";
    private static final String RUNTIME_PREFS = "simpleoffice-runtime";
    private static final String RUNTIME_VERSION = "bundle-version";
    private static final int CAMERA_PERMISSION_REQUEST = 701;
    private static final int FILE_CHOOSER_REQUEST = 702;
    private static final int AUDIO_PERMISSION_REQUEST = 703;

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final String nativeBridgeToken = UUID.randomUUID().toString();
    private final AndroidAudioStreamer audioStreamer = new AndroidAudioStreamer();
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

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        pendingWebState = savedInstanceState;
        nfcAdapter = NfcAdapter.getDefaultAdapter(this);
        buildUi();
        executor.execute(this::prepareAndStartBackend);
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
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (!localPageVisible || !isLocalUrl(view.getUrl())) {
                    callback.onReceiveValue(null);
                    return true;
                }
                if (fileChooserCallback != null) fileChooserCallback.onReceiveValue(null);
                fileChooserCallback = callback;
                try {
                    startActivityForResult(params.createIntent(), FILE_CHOOSER_REQUEST);
                    return true;
                } catch (ActivityNotFoundException error) {
                    fileChooserCallback = null;
                    callback.onReceiveValue(null);
                    showStatus("Keine App zum Auswählen eines Fotos gefunden.", false);
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
                if ("http".equals(scheme) || "https".equals(scheme) || "mailto".equals(scheme) || "tel".equals(scheme)) {
                    try {
                        startActivity(new Intent(Intent.ACTION_VIEW, uri));
                    } catch (ActivityNotFoundException error) {
                        showStatus("Keine passende App für diesen Link gefunden.", false);
                    }
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
            public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse errorResponse) {
                super.onReceivedHttpError(view, request, errorResponse);
                if (request.isForMainFrame() && errorResponse.getStatusCode() >= 500) {
                    mainFrameLoadFailed = true;
                    showStatus("Lokaler Serverfehler: HTTP " + errorResponse.getStatusCode(), false);
                }
            }
        });
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
                + "const audioRoot=document.getElementById('audio-streamer-app');"
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
                + "window.addEventListener('simpleoffice:native-audio-status',event=>{if(event.detail&&event.detail.message)show(String(event.detail.message),'danger');else render();});render();}"
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
            String targets = pendingAudioTargets;
            pendingAudioTargets = null;
            boolean granted = grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED;
            if (granted && targets != null && webView != null && isLocalUrl(webView.getUrl())) {
                String result = audioStreamer.startSender(targets, pendingAudioBitrate);
                dispatchNativeAudioStatus("ok".equals(result) ? "" : "Android-Audiosender konnte nicht gestartet werden.");
            } else {
                dispatchNativeAudioStatus("Mikrofonberechtigung wurde nicht erteilt.");
            }
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

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
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
        public String audioStatus(String token) {
            return bridgeAllowed(token) ? audioStreamer.statusJson() : "{\"platform\":\"blocked\"}";
        }

        @JavascriptInterface
        public String startAudioSender(String token, String targetsJson, int bitrateKbps) {
            if (!bridgeAllowed(token)) return "blocked";
            if (!AndroidAudioStreamer.opusEncoderAvailable()) return "unsupported";
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                pendingAudioTargets = targetsJson;
                pendingAudioBitrate = Math.max(16, Math.min(bitrateKbps, 256));
                mainHandler.post(() -> requestPermissions(
                        new String[]{Manifest.permission.RECORD_AUDIO}, AUDIO_PERMISSION_REQUEST));
                return "permission";
            }
            String result = audioStreamer.startSender(targetsJson, bitrateKbps);
            dispatchNativeAudioStatus("ok".equals(result) ? "" : "Android-Audiosender konnte nicht gestartet werden.");
            return result;
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
            String result = audioStreamer.startReceiver(port);
            dispatchNativeAudioStatus("ok".equals(result) ? "" : "Android-Audioempfang konnte nicht gestartet werden.");
            return result;
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
        return nativeBridgeToken.equals(token) && localPageVisible && webView != null && isLocalUrl(webView.getUrl());
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
                    "window.dispatchEvent(new CustomEvent('simpleoffice:nfc',{detail:" + quoted + "}));", null);
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
                    && ("127.0.0.1".equals(host) || "localhost".equals(host)) && uri.getPort() == 8765;
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
            module.callAttr("start", runtimeRoot.getAbsolutePath(), BuildConfig.ERROR_REPORT_URL);
            showStatus("Lokales Backend wird geprüft …", true);
            waitForBackend();
            mainHandler.post(() -> {
                if (webView == null) return;
                Bundle state = pendingWebState;
                pendingWebState = null;
                if (state != null && webView.restoreState(state) != null) return;
                webView.loadUrl(LOCAL_URL);
            });
        } catch (Exception error) {
            String detail = error.getMessage();
            if (detail == null || detail.trim().isEmpty()) detail = error.getClass().getSimpleName();
            showStatus("Start fehlgeschlagen:\n" + detail, false);
        }
    }

    private void waitForBackend() throws Exception {
        long deadline = System.currentTimeMillis() + 45000;
        Exception lastError = null;
        while (System.currentTimeMillis() < deadline) {
            if (Thread.currentThread().isInterrupted()) throw new InterruptedException("Backend-Prüfung wurde abgebrochen");
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
            for (String child : children) copyAssetTree(assets, assetPath + "/" + child, new File(target, child));
            return;
        }
        File parent = target.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs() && !parent.isDirectory()) {
            throw new IOException("Verzeichnis kann nicht erstellt werden: " + parent);
        }
        try (InputStream input = assets.open(assetPath); FileOutputStream output = new FileOutputStream(target, false)) {
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
        super.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) {
            localPageVisible = isLocalUrl(webView.getUrl());
            flushPendingBarcodeResult();
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

    private static final class AndroidAudioStreamer {
        private static final int SAMPLE_RATE = 48000;
        private static final int CHANNELS = 2;
        private static final int RTP_PAYLOAD_TYPE = 111;
        private static final int MAX_TARGETS = 16;
        private static final int FRAME_SAMPLES = 960;
        private static final int MONO_FRAME_BYTES = FRAME_SAMPLES * 2;
        private static final int STEREO_FRAME_BYTES = FRAME_SAMPLES * CHANNELS * 2;

        private final Object lock = new Object();
        private final AtomicBoolean senderRunning = new AtomicBoolean(false);
        private final AtomicBoolean receiverRunning = new AtomicBoolean(false);
        private Thread senderThread;
        private Thread receiverThread;
        private DatagramSocket senderSocket;
        private DatagramSocket receiverSocket;
        private volatile String lastError = "";
        private volatile int receiverPort;

        static boolean opusEncoderAvailable() {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return false;
            try {
                MediaFormat format = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, SAMPLE_RATE, CHANNELS);
                format.setInteger(MediaFormat.KEY_BIT_RATE, 64000);
                return new MediaCodecList(MediaCodecList.ALL_CODECS).findEncoderForFormat(format) != null;
            } catch (RuntimeException error) {
                return false;
            }
        }

        String startSender(String destinationsJson, int bitrateKbps) {
            if (!opusEncoderAvailable()) return "unsupported";
            final List<AudioTarget> targets;
            try {
                targets = parseTargets(destinationsJson);
            } catch (Exception error) {
                return "invalid-target";
            }
            int bitrate = Math.max(16, Math.min(bitrateKbps, 256));
            stopSender();
            lastError = "";
            senderRunning.set(true);
            senderThread = new Thread(() -> runSender(targets, bitrate), "simpleoffice-android-audio-send");
            senderThread.start();
            return "ok";
        }

        String startReceiver(int port) {
            if (port < 1024 || port > 65535) return "invalid-port";
            stopReceiver();
            lastError = "";
            receiverPort = port;
            receiverRunning.set(true);
            receiverThread = new Thread(() -> runReceiver(port), "simpleoffice-android-audio-receive");
            receiverThread.start();
            return "ok";
        }

        void stopSender() {
            senderRunning.set(false);
            DatagramSocket socket;
            Thread thread;
            synchronized (lock) {
                socket = senderSocket;
                senderSocket = null;
                thread = senderThread;
                senderThread = null;
            }
            if (socket != null) socket.close();
            if (thread != null) thread.interrupt();
        }

        void stopReceiver() {
            receiverRunning.set(false);
            DatagramSocket socket;
            Thread thread;
            synchronized (lock) {
                socket = receiverSocket;
                receiverSocket = null;
                thread = receiverThread;
                receiverThread = null;
            }
            if (socket != null) socket.close();
            if (thread != null) thread.interrupt();
            receiverPort = 0;
        }

        void stopAll() {
            stopSender();
            stopReceiver();
        }

        String statusJson() {
            try {
                JSONObject result = new JSONObject();
                result.put("platform", "android");
                result.put("opus_encoder", opusEncoderAvailable());
                result.put("last_error", lastError);
                result.put("sender", senderRunning.get()
                        ? new JSONObject().put("running", true).put("source", "Android Systemmikrofon").put("backend", "android")
                        : JSONObject.NULL);
                result.put("receiver", receiverRunning.get()
                        ? new JSONObject().put("running", true).put("port", receiverPort)
                                .put("speaker_devices", new JSONArray().put("Android Systemausgabe"))
                                .put("virtual_microphone", "")
                        : JSONObject.NULL);
                return result.toString();
            } catch (Exception error) {
                return "{\"platform\":\"android\",\"last_error\":\"Status konnte nicht gelesen werden.\"}";
            }
        }

        private void runSender(List<AudioTarget> targets, int bitrateKbps) {
            AudioRecord recorder = null;
            MediaCodec encoder = null;
            DatagramSocket socket = null;
            try {
                int minBuffer = AudioRecord.getMinBufferSize(
                        SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
                if (minBuffer <= 0) throw new IllegalStateException("audio-record-buffer");
                recorder = new AudioRecord(
                        MediaRecorder.AudioSource.MIC,
                        SAMPLE_RATE,
                        AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT,
                        Math.max(minBuffer, MONO_FRAME_BYTES * 4));
                if (recorder.getState() != AudioRecord.STATE_INITIALIZED) throw new IllegalStateException("audio-record-init");

                MediaFormat format = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, SAMPLE_RATE, CHANNELS);
                format.setInteger(MediaFormat.KEY_BIT_RATE, bitrateKbps * 1000);
                format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, STEREO_FRAME_BYTES);
                encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS);
                encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
                encoder.start();

                socket = new DatagramSocket();
                synchronized (lock) { senderSocket = socket; }
                List<ResolvedAudioTarget> resolvedTargets = resolveTargets(targets);
                recorder.startRecording();

                byte[] mono = new byte[MONO_FRAME_BYTES];
                long samplesSubmitted = 0;
                SecureRandom random = new SecureRandom();
                int sequence = random.nextInt(0x10000);
                int ssrc = random.nextInt();
                boolean firstPacket = true;
                MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();

                while (senderRunning.get() && !Thread.currentThread().isInterrupted()) {
                    int read = recorder.read(mono, 0, mono.length, AudioRecord.READ_BLOCKING);
                    if (read <= 0) continue;
                    read -= read % 2;
                    int inputIndex = encoder.dequeueInputBuffer(10000);
                    if (inputIndex >= 0) {
                        ByteBuffer input = encoder.getInputBuffer(inputIndex);
                        if (input != null) {
                            input.clear();
                            for (int offset = 0; offset < read; offset += 2) {
                                input.put(mono[offset]).put(mono[offset + 1]);
                                input.put(mono[offset]).put(mono[offset + 1]);
                            }
                            long ptsUs = samplesSubmitted * 1_000_000L / SAMPLE_RATE;
                            int monoSamples = read / 2;
                            encoder.queueInputBuffer(inputIndex, 0, monoSamples * 4, ptsUs, 0);
                            samplesSubmitted += monoSamples;
                        }
                    }
                    int outputIndex;
                    while ((outputIndex = encoder.dequeueOutputBuffer(info, 0)) >= 0) {
                        if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0 && info.size > 0) {
                            ByteBuffer output = encoder.getOutputBuffer(outputIndex);
                            if (output != null) {
                                byte[] opus = new byte[info.size];
                                output.position(info.offset);
                                output.limit(info.offset + info.size);
                                output.get(opus);
                                long timestamp = (info.presentationTimeUs * SAMPLE_RATE / 1_000_000L) & 0xffffffffL;
                                byte[] rtp = rtpPacket(opus, sequence++, timestamp, ssrc, firstPacket);
                                firstPacket = false;
                                for (ResolvedAudioTarget target : resolvedTargets) {
                                    socket.send(new DatagramPacket(rtp, rtp.length, target.address, target.port));
                                }
                            }
                        }
                        encoder.releaseOutputBuffer(outputIndex, false);
                    }
                }
            } catch (SecurityException error) {
                lastError = "Mikrofonberechtigung fehlt.";
            } catch (Exception error) {
                lastError = "Android-Audiosender konnte nicht gestartet werden.";
            } finally {
                senderRunning.set(false);
                if (recorder != null) {
                    try { recorder.stop(); } catch (IllegalStateException ignored) {}
                    recorder.release();
                }
                stopCodec(encoder);
                if (socket != null) socket.close();
                synchronized (lock) { senderSocket = null; senderThread = null; }
            }
        }

        private void runReceiver(int port) {
            MediaCodec decoder = null;
            AudioTrack track = null;
            DatagramSocket socket = null;
            try {
                MediaFormat format = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, SAMPLE_RATE, CHANNELS);
                format.setByteBuffer("csd-0", opusHead());
                format.setByteBuffer("csd-1", nativeLong(0));
                format.setByteBuffer("csd-2", nativeLong(80_000_000L));
                decoder = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS);
                decoder.configure(format, null, null, 0);
                decoder.start();

                int minBuffer = AudioTrack.getMinBufferSize(
                        SAMPLE_RATE, AudioFormat.CHANNEL_OUT_STEREO, AudioFormat.ENCODING_PCM_16BIT);
                if (minBuffer <= 0) throw new IllegalStateException("audio-track-buffer");
                track = new AudioTrack(
                        AudioManager.STREAM_MUSIC,
                        SAMPLE_RATE,
                        AudioFormat.CHANNEL_OUT_STEREO,
                        AudioFormat.ENCODING_PCM_16BIT,
                        Math.max(minBuffer, STEREO_FRAME_BYTES * 4),
                        AudioTrack.MODE_STREAM);
                if (track.getState() != AudioTrack.STATE_INITIALIZED) throw new IllegalStateException("audio-track-init");
                track.play();

                socket = new DatagramSocket(port);
                socket.setSoTimeout(200);
                synchronized (lock) { receiverSocket = socket; }
                byte[] packetBuffer = new byte[4096];
                MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
                long firstTimestamp = -1;

                while (receiverRunning.get() && !Thread.currentThread().isInterrupted()) {
                    try {
                        DatagramPacket datagram = new DatagramPacket(packetBuffer, packetBuffer.length);
                        socket.receive(datagram);
                        RtpAudioPayload payload = parseRtp(datagram.getData(), datagram.getOffset(), datagram.getLength());
                        if (payload != null) {
                            if (firstTimestamp < 0) firstTimestamp = payload.timestamp;
                            long delta = (payload.timestamp - firstTimestamp) & 0xffffffffL;
                            queueDecoder(decoder, payload.data, delta * 1_000_000L / SAMPLE_RATE);
                        }
                    } catch (SocketTimeoutException ignored) {
                    }
                    drainDecoder(decoder, track, info);
                }
            } catch (Exception error) {
                if (receiverRunning.get()) lastError = "Android-Audioempfang konnte nicht gestartet werden.";
            } finally {
                receiverRunning.set(false);
                if (socket != null) socket.close();
                stopCodec(decoder);
                if (track != null) {
                    try { track.stop(); } catch (IllegalStateException ignored) {}
                    track.release();
                }
                synchronized (lock) { receiverSocket = null; receiverThread = null; }
            }
        }

        private static void queueDecoder(MediaCodec decoder, byte[] payload, long ptsUs) {
            int inputIndex = decoder.dequeueInputBuffer(10000);
            if (inputIndex < 0) return;
            ByteBuffer input = decoder.getInputBuffer(inputIndex);
            if (input == null || input.capacity() < payload.length) {
                decoder.queueInputBuffer(inputIndex, 0, 0, ptsUs, 0);
                return;
            }
            input.clear();
            input.put(payload);
            decoder.queueInputBuffer(inputIndex, 0, payload.length, ptsUs, 0);
        }

        private static void drainDecoder(MediaCodec decoder, AudioTrack track, MediaCodec.BufferInfo info) {
            int outputIndex;
            while ((outputIndex = decoder.dequeueOutputBuffer(info, 0)) >= 0) {
                ByteBuffer output = decoder.getOutputBuffer(outputIndex);
                if (output != null && info.size > 0) {
                    byte[] pcm = new byte[info.size];
                    output.position(info.offset);
                    output.limit(info.offset + info.size);
                    output.get(pcm);
                    track.write(pcm, 0, pcm.length, AudioTrack.WRITE_BLOCKING);
                }
                decoder.releaseOutputBuffer(outputIndex, false);
            }
        }

        private static List<AudioTarget> parseTargets(String json) throws Exception {
            JSONArray array = new JSONArray(json == null ? "[]" : json);
            if (array.length() < 1 || array.length() > MAX_TARGETS) throw new IllegalArgumentException("targets");
            List<AudioTarget> result = new ArrayList<>();
            for (int index = 0; index < array.length(); index++) {
                JSONObject item = array.getJSONObject(index);
                String host = item.optString("host", "").trim();
                int port = item.optInt("port", 5004);
                if (host.isEmpty() || host.length() > 253 || port < 1024 || port > 65535) {
                    throw new IllegalArgumentException("target");
                }
                result.add(new AudioTarget(host, port));
            }
            return result;
        }

        private static List<ResolvedAudioTarget> resolveTargets(List<AudioTarget> targets) throws Exception {
            List<ResolvedAudioTarget> result = new ArrayList<>();
            for (AudioTarget target : targets) {
                result.add(new ResolvedAudioTarget(InetAddress.getByName(target.host), target.port));
            }
            return result;
        }

        private static byte[] rtpPacket(byte[] payload, int sequence, long timestamp, int ssrc, boolean marker) {
            ByteBuffer packet = ByteBuffer.allocate(12 + payload.length).order(ByteOrder.BIG_ENDIAN);
            packet.put((byte) 0x80);
            packet.put((byte) ((marker ? 0x80 : 0) | RTP_PAYLOAD_TYPE));
            packet.putShort((short) sequence);
            packet.putInt((int) timestamp);
            packet.putInt(ssrc);
            packet.put(payload);
            return packet.array();
        }

        private static RtpAudioPayload parseRtp(byte[] packet, int offset, int length) {
            if (length < 12 || (packet[offset] & 0xc0) != 0x80) return null;
            if ((packet[offset + 1] & 0x7f) != RTP_PAYLOAD_TYPE) return null;
            int header = 12 + (packet[offset] & 0x0f) * 4;
            if (header > length) return null;
            if ((packet[offset] & 0x10) != 0) {
                if (header + 4 > length) return null;
                int words = ((packet[offset + header + 2] & 0xff) << 8) | (packet[offset + header + 3] & 0xff);
                header += 4 + words * 4;
                if (header > length) return null;
            }
            int payloadLength = length - header;
            if ((packet[offset] & 0x20) != 0) {
                int padding = packet[offset + length - 1] & 0xff;
                if (padding < 1 || padding > payloadLength) return null;
                payloadLength -= padding;
            }
            if (payloadLength <= 0) return null;
            long timestamp = ((long) (packet[offset + 4] & 0xff) << 24)
                    | ((long) (packet[offset + 5] & 0xff) << 16)
                    | ((long) (packet[offset + 6] & 0xff) << 8)
                    | (long) (packet[offset + 7] & 0xff);
            byte[] payload = new byte[payloadLength];
            System.arraycopy(packet, offset + header, payload, 0, payloadLength);
            return new RtpAudioPayload(payload, timestamp);
        }

        private static ByteBuffer opusHead() {
            ByteBuffer buffer = ByteBuffer.allocate(19).order(ByteOrder.LITTLE_ENDIAN);
            buffer.put("OpusHead".getBytes(StandardCharsets.US_ASCII));
            buffer.put((byte) 1).put((byte) CHANNELS).putShort((short) 0);
            buffer.putInt(SAMPLE_RATE).putShort((short) 0).put((byte) 0);
            buffer.flip();
            return buffer;
        }

        private static ByteBuffer nativeLong(long value) {
            ByteBuffer buffer = ByteBuffer.allocate(8).order(ByteOrder.nativeOrder());
            buffer.putLong(value);
            buffer.flip();
            return buffer;
        }

        private static void stopCodec(MediaCodec codec) {
            if (codec == null) return;
            try { codec.stop(); } catch (IllegalStateException ignored) {}
            codec.release();
        }

        private static final class AudioTarget {
            final String host;
            final int port;
            AudioTarget(String host, int port) { this.host = host; this.port = port; }
        }

        private static final class ResolvedAudioTarget {
            final InetAddress address;
            final int port;
            ResolvedAudioTarget(InetAddress address, int port) { this.address = address; this.port = port; }
        }

        private static final class RtpAudioPayload {
            final byte[] data;
            final long timestamp;
            RtpAudioPayload(byte[] data, long timestamp) { this.data = data; this.timestamp = timestamp; }
        }
    }
}

import SwiftUI
import WebKit
import AVFoundation

@main
struct SimpleOffice4MeApp: App {
    @AppStorage("serverURL") private var serverURL = ""
    var body: some Scene {
        WindowGroup {
            NavigationStack {
                if let url = URL(string: serverURL), url.scheme == "https", url.host != nil {
                    OfficeWebView(url: url)
                        .ignoresSafeArea(edges: .bottom)
                        .toolbar {
                            ToolbarItem(placement: .topBarTrailing) {
                                Button("Server") { serverURL = "" }
                            }
                        }
                } else {
                    ServerSetup(serverURL: $serverURL)
                }
            }
        }
    }
}

struct ServerSetup: View {
    @Binding var serverURL: String
    @State private var candidate = ""
    var body: some View {
        Form {
            Text("SimpleOffice4Me Server").font(.headline)
            TextField("https://office.example.com", text: $candidate)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
            Button("Verbinden") {
                guard let url = URL(string: candidate.trimmingCharacters(in: .whitespacesAndNewlines)),
                      url.scheme == "https", url.host != nil,
                      url.user == nil, url.password == nil else { return }
                serverURL = url.absoluteString
            }
            Text("Für den iPhone-Client ist ein über HTTPS erreichbarer SimpleOffice4Me-Server erforderlich.")
                .font(.footnote)
        }
    }
}

struct OfficeWebView: UIViewRepresentable {
    let url: URL
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> WKWebView {
        let view = WKWebView()
        view.allowsBackForwardNavigationGestures = true
        view.uiDelegate = context.coordinator
        view.load(URLRequest(url: url))
        return view
    }
    func updateUIView(_ view: WKWebView, context: Context) {}
    final class Coordinator: NSObject, WKUIDelegate {
        private var permissionAlert: UIAlertController?

        @available(iOS 15.0, *)
        func webView(_ webView: WKWebView,
                     requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                     initiatedByFrame frame: WKFrameInfo,
                     type: WKMediaCaptureType,
                     decisionHandler: @escaping (WKPermissionDecision) -> Void) {
            guard let url = webView.url,
                  url.scheme == "https",
                  origin.protocol == "https",
                  origin.host == url.host,
                  origin.port == (url.port ?? 443) else {
                decisionHandler(.deny)
                return
            }
            let status = AVCaptureDevice.authorizationStatus(for: .video)
            if status == .denied || status == .restricted {
                decisionHandler(.deny)
                DispatchQueue.main.async { [weak self] in
                    guard let self = self, self.permissionAlert == nil,
                          let presenter = webView.window?.rootViewController else { return }
                    let alert = UIAlertController(title: "Kamera nicht freigegeben",
                        message: "Aktiviere den Kamerazugriff für SimpleOffice4Me in den iPhone-Einstellungen, um Bücher und Dokumente zu scannen.",
                        preferredStyle: .alert)
                    alert.addAction(UIAlertAction(title: "Abbrechen", style: .cancel) { _ in self.permissionAlert = nil })
                    alert.addAction(UIAlertAction(title: "Einstellungen", style: .default) { _ in
                        self.permissionAlert = nil
                        if let settings = URL(string: UIApplication.openSettingsURLString) {
                            UIApplication.shared.open(settings)
                        }
                    })
                    self.permissionAlert = alert
                    presenter.present(alert, animated: true)
                }
                return
            }
            decisionHandler(.prompt)
        }
    }
}

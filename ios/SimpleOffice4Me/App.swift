import SwiftUI
import WebKit

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
            decisionHandler(.prompt)
        }
    }
}

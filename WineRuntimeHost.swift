import SwiftUI

private extension Notification.Name {
    static let mxxHubRuntimeRequestedDismiss =
        Notification.Name("MxxHubRuntimeRequestedDismiss")
}

struct WineRuntimeHost: UIViewControllerRepresentable {
    let executablePath: String

    func makeUIViewController(context: Context) -> MxxWineRuntimeViewController {
        MxxWineRuntimeViewController(executablePath: executablePath)
    }

    func updateUIViewController(_ uiViewController: MxxWineRuntimeViewController, context: Context) {}
}

struct WineRuntimeScreen: View {
    @Environment(\.dismiss) private var dismiss
    let executablePath: String

    var body: some View {
        // MXXHUB_WINDOWS_V37_STABLE_IMMERSIVE_SWIFT_HOST
        WineRuntimeHost(executablePath: executablePath)
            .ignoresSafeArea(.all)
            .background(Color.black)
            .persistentSystemOverlays(.hidden)
            .onReceive(
                NotificationCenter.default.publisher(
                    for: .mxxHubRuntimeRequestedDismiss
                )
            ) { _ in
                dismiss()
            }
    }
}

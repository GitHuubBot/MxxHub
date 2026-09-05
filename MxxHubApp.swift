import SwiftUI

@main
struct MxxHubApp: App {
    @StateObject private var store = GameStore()

    var body: some Scene {
        WindowGroup {
            LibraryView()
                .environmentObject(store)
                .preferredColorScheme(.dark)
        }
    }
}

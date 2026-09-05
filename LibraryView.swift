import SwiftUI

struct LibraryView: View {
    @EnvironmentObject private var store: GameStore

    @State private var showingAddGame = false
    @State private var runtimeExecutablePath: String?
    @State private var runtimeFolder: URL?
    @State private var launchError: String?
    @State private var launchingGameID: UUID?
    @State private var detailsGame: GameEntry?
    @State private var launchTransitionLocked = false

    private let columns = [
        GridItem(.adaptive(minimum: 155, maximum: 230), spacing: 18)
    ]

    var body: some View {
        TabView {
            NavigationStack {
                Group {
                    if store.games.isEmpty {
                        ContentUnavailableView {
                            Label("No Games Yet", systemImage: "gamecontroller")
                        } description: {
                            Text("Add a Windows game folder. MxxHub will find its .exe files and add the game to your library.")
                        } actions: {
                            Button("Add Game") { showingAddGame = true }
                                .buttonStyle(.borderedProminent)
                        }
                    } else {
                        ScrollView {
                            LazyVGrid(columns: columns, spacing: 24) {
                                ForEach(store.games) { game in
                                    VStack(alignment: .leading, spacing: 7) {
                                        Button {
                                            launchGame(game)
                                        } label: {
                                            ZStack {
                                                GameCard(game: game)

                                                if launchingGameID == game.id {
                                                    RoundedRectangle(cornerRadius: 18)
                                                        .fill(.black.opacity(0.45))

                                                    ProgressView()
                                                        .controlSize(.large)
                                                        .tint(.white)
                                                }
                                            }
                                        }
                                        .buttonStyle(.plain)
                                        .disabled(launchingGameID != nil)

                                        HStack(spacing: 8) {
                                            Label("Play", systemImage: "play.fill")
                                                .font(.caption.bold())
                                                .foregroundStyle(.green)

                                            Spacer()

                                            Button {
                                                detailsGame = game
                                            } label: {
                                                Image(systemName: "info.circle")
                                                    .font(.body.bold())
                                            }
                                            .buttonStyle(.borderless)
                                            .accessibilityLabel("Game details")
                                        }
                                        .padding(.horizontal, 2)
                                    }
                                }
                            }
                            .padding()
                        }
                    }
                }
                .navigationTitle("My Games")
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        Button { showingAddGame = true } label: {
                            Image(systemName: "plus")
                        }
                    }
                }
                .navigationDestination(item: $detailsGame) { game in
                    GameDetailView(game: game)
                }
                .sheet(isPresented: $showingAddGame) {
                    AddGameView().environmentObject(store)
                }
            }
            .tabItem { Label("Library", systemImage: "square.grid.2x2.fill") }

            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape.fill") }
        }
        .tint(.green)
        .fullScreenCover(isPresented: Binding(
            get: { runtimeExecutablePath != nil },
            set: { visible in
                if !visible { endRuntimeSession() }
            }
        )) {
            if let path = runtimeExecutablePath {
                WineRuntimeScreen(executablePath: path)
            }
        }
        .alert("Windows runtime", isPresented: Binding(
            get: { launchError != nil },
            set: { if !$0 { launchError = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(launchError ?? "")
        }
    }

    // MXXHUB_WINDOWS_V38_MANUAL_GAME_LAUNCH_ONLY
    // Saved games remain in the library, but app launch never starts one automatically.

    private func launchGame(_ game: GameEntry) {
        guard !launchTransitionLocked,
              launchingGameID == nil,
              runtimeExecutablePath == nil else { return }
        launchTransitionLocked = true
        launchingGameID = game.id

        do {
            let folder = try store.resolvedFolder(for: game)
            guard folder.startAccessingSecurityScopedResource() else {
                throw DirectLaunchError.permissionDenied
            }

            let executable = folder.appendingPathComponent(game.executableRelativePath)
            guard FileManager.default.fileExists(atPath: executable.path) else {
                folder.stopAccessingSecurityScopedResource()
                throw DirectLaunchError.executableMissing
            }

            _ = try PEInspector.inspect(url: executable)

            runtimeFolder = folder
            runtimeExecutablePath = executable.path
            launchingGameID = nil
            // Keep launchTransitionLocked set while the full-screen runtime is
            // being presented. It prevents a double tap
            // from starting a duplicate engine during the transition.
        } catch {
            launchingGameID = nil
            launchTransitionLocked = false
            launchError = error.localizedDescription
        }
    }

    private func endRuntimeSession() {
        runtimeFolder?.stopAccessingSecurityScopedResource()
        runtimeFolder = nil
        runtimeExecutablePath = nil
        launchTransitionLocked = false
    }

    enum DirectLaunchError: LocalizedError {
        case permissionDenied
        case executableMissing

        var errorDescription: String? {
            switch self {
            case .permissionDenied:
                return "MxxHub lost access to this game folder. Open the game details or add the folder again."
            case .executableMissing:
                return "The selected Windows executable is no longer in the game folder."
            }
        }
    }
}

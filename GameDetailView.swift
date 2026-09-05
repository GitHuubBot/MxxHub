import SwiftUI

struct GameDetailView: View {
    @EnvironmentObject private var store: GameStore
    @Environment(\.dismiss) private var dismiss

    @State var game: GameEntry
    @State private var launchError: String?
    @State private var launching = false
    @State private var peInfo: PEInfo?
    @State private var runtimeExecutablePath: String?
    @State private var runtimeFolder: URL?

    var body: some View {
        Form {
            Section {
                VStack(spacing: 12) {
                    Image(systemName: "gamecontroller.fill")
                        .font(.system(size: 54))
                    Text(game.name)
                        .font(.title2.bold())
                    Text(game.executableRelativePath)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)

                    Button {
                        launchWindowsRuntime()
                    } label: {
                        HStack {
                            if launching { ProgressView() }
                            Image(systemName: "play.fill")
                            Text(launching ? "Starting…" : "Play")
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(launching)
                }
                .frame(maxWidth: .infinity)
            }

            Section("MxxHub Windows Runtime") {
                LabeledContent("Backend", value: "WineGlass + Blink")
                LabeledContent("Version", value: "v0.3 experimental")
                LabeledContent("x86", value: "Enabled")
                LabeledContent("x86-64", value: "Enabled")
                LabeledContent("Win32", value: "Experimental")
                LabeledContent("Direct3D games", value: "Not ready yet")
            }

            Section("Windows executable") {
                LabeledContent("Architecture", value: peInfo?.architecture.rawValue ?? "Checking…")
                LabeledContent("Subsystem", value: peInfo?.subsystem.rawValue ?? "—")
                if let peInfo {
                    LabeledContent("Entry point", value: String(format: "RVA 0x%08X", peInfo.entryPointRVA))
                    LabeledContent("Sections", value: "\(peInfo.sections.count)")
                }
            }

            Section("Game") {
                TextField("Name", text: $game.name)
                LabeledContent("Executable", value: game.executableRelativePath)
            }

            Section("Compatibility") {
                Picker("Renderer", selection: $game.settings.renderer) {
                    ForEach(GameSettings.Renderer.allCases) { renderer in
                        Text(renderer.rawValue).tag(renderer)
                    }
                }
                TextField("Resolution", text: $game.settings.resolution)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Stepper("Memory target: \(game.settings.memoryLimitMB) MB", value: $game.settings.memoryLimitMB, in: 512...8192, step: 256)
            }

            Section("Input") {
                Toggle("Controller", isOn: $game.settings.useController)
                Toggle("Touch controls", isOn: $game.settings.showTouchControls)
            }

            Section("Launch") {
                TextField("Arguments", text: $game.settings.launchArguments, axis: .vertical)
                    .font(.body.monospaced())
            }

            Section {
                Button("Save Settings") { store.update(game) }
                Button("Remove from Library", role: .destructive) {
                    store.remove(game)
                    dismiss()
                }
            } footer: {
                Text("v0.3 replaces the tiny test interpreter with an experimental WineGlass/Blink x86 and x86-64 Windows runtime. Basic Win32 programs may execute and render. Games that require Direct3D, Unity graphics, Steam services or missing Windows APIs can still stop or show a blank runtime screen.")
            }
        }
        .navigationTitle(game.name)
        .navigationBarTitleDisplayMode(.inline)
        .task { inspectExecutable() }
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

    private func inspectExecutable() {
        do {
            let folder = try store.resolvedFolder(for: game)
            guard folder.startAccessingSecurityScopedResource() else { return }
            defer { folder.stopAccessingSecurityScopedResource() }
            let executable = folder.appendingPathComponent(game.executableRelativePath)
            peInfo = try PEInspector.inspect(url: executable)
        } catch {
            peInfo = nil
        }
    }

    private func launchWindowsRuntime() {
        launching = true
        defer { launching = false }

        do {
            let folder = try store.resolvedFolder(for: game)
            guard folder.startAccessingSecurityScopedResource() else {
                throw RuntimeLaunchError.permissionDenied
            }

            let executable = folder.appendingPathComponent(game.executableRelativePath)
            guard FileManager.default.fileExists(atPath: executable.path) else {
                folder.stopAccessingSecurityScopedResource()
                throw RuntimeLaunchError.executableMissing
            }

            _ = try PEInspector.inspect(url: executable)
            runtimeFolder = folder
            runtimeExecutablePath = executable.path
        } catch {
            launchError = error.localizedDescription
        }
    }

    private func endRuntimeSession() {
        runtimeFolder?.stopAccessingSecurityScopedResource()
        runtimeFolder = nil
        runtimeExecutablePath = nil
    }

    enum RuntimeLaunchError: LocalizedError {
        case permissionDenied
        case executableMissing

        var errorDescription: String? {
            switch self {
            case .permissionDenied:
                return "MxxHub lost access to this game folder. Remove the game from the library and add the folder again."
            case .executableMissing:
                return "The selected Windows executable is no longer in the game folder."
            }
        }
    }
}

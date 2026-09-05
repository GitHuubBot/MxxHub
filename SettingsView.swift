import SwiftUI
import UIKit

struct SettingsView: View {
    private let runtime = WindowsRuntime.status

    @AppStorage("MxxHubAlwaysShowLog") private var alwaysShowLog = false
    @AppStorage("MxxHubCopyLogOnCrash") private var copyLogOnCrash = true

    @State private var filesStatus = ""
    @State private var copyStatus = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("MxxHub") {
                    LabeledContent("Version", value: "0.4.9.57 Windows V48")
                    LabeledContent("Runtime", value: runtime.name)
                    LabeledContent("Runtime build", value: runtime.version)
                }

                Section {
                    Toggle("Always show log", isOn: $alwaysShowLog)
                    Toggle("Copy log on crash", isOn: $copyLogOnCrash)

                    Button {
                        prepareFilesLogFolder()
                    } label: {
                        Label("Make MxxHub visible in Files", systemImage: "folder.badge.plus")
                    }

                    Button {
                        copyLatestCrashLog()
                    } label: {
                        Label("Copy latest crash log", systemImage: "doc.on.doc")
                    }

                    if !filesStatus.isEmpty {
                        Text(filesStatus).font(.footnote)
                    }
                    if !copyStatus.isEmpty {
                        Text(copyStatus).font(.footnote)
                    }
                } header: {
                    Text("Diagnostics")
                } footer: {
                    Text("Logs are stored in Files → Browse → On My iPad/iPhone → MxxHub → MxxHubLogs. A hard iOS crash cannot copy anything after the process is already dead, so MxxHub keeps live files updated before a crash happens.")
                }

                Section("Crash files you can send") {
                    LabeledContent("Host trail", value: "host-latest.txt")
                    LabeledContent("Live runtime", value: "runtime-latest.log")
                    LabeledContent("Latest crash", value: "crash-latest.log")
                }

                Section("Windows compatibility") {
                    Label("PE32 / PE32+ loader", systemImage: "checkmark.circle.fill")
                    Label("x86 / x86-64 translation", systemImage: "checkmark.circle.fill")
                    Label("Win32 API layer", systemImage: "wrench.and.screwdriver.fill")
                    Label("Metal Win32 compositor", systemImage: "display")
                    Label("Direct3D game rendering: future milestone", systemImage: "exclamationmark.triangle")
                }

                Section("Current target") {
                    Text("First prove that normal 32-bit and 64-bit Windows programs execute from the MxxHub library. After that the graphics work moves to DirectX/Unity/Source-engine compatibility for games such as Hollow Knight, Portal and Half-Life 2.")
                }
            }
            .navigationTitle("Settings")
            .onAppear {
                prepareFilesLogFolder(silent: true)
            }
        }
    }

    private func logDirectory() throws -> URL {
        guard let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first else {
            throw CocoaError(.fileNoSuchFile)
        }
        let folder = documents.appendingPathComponent("MxxHubLogs", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder
    }

    private func prepareFilesLogFolder(silent: Bool = false) {
        do {
            let folder = try logDirectory()
            let documents = folder.deletingLastPathComponent()
            let readme =
                "MxxHub V21 diagnostic files\n\n" +
                "Open: Files → Browse → On My iPad/iPhone → MxxHub → MxxHubLogs\n\n" +
                "Send: host-latest.txt, runtime-latest.log, crash-latest.log and crash-*.log\n"

            try readme.write(
                to: folder.appendingPathComponent("README.txt"),
                atomically: true,
                encoding: .utf8
            )
            try readme.write(
                to: documents.appendingPathComponent("MxxHub-Logs-README.txt"),
                atomically: true,
                encoding: .utf8
            )

            if !silent {
                filesStatus = "Ready. Open Files → Browse → On My iPad/iPhone → MxxHub → MxxHubLogs."
            }
        } catch {
            if !silent {
                filesStatus = "Could not prepare Files folder: \(error.localizedDescription)"
            }
        }
    }

    private func copyLatestCrashLog() {
        do {
            let folder = try logDirectory()
            let candidates = [
                folder.appendingPathComponent("crash-latest.log"),
                folder.appendingPathComponent("runtime-latest.log"),
                folder.appendingPathComponent("host-latest.txt")
            ]

            guard let file = candidates.first(where: {
                FileManager.default.fileExists(atPath: $0.path)
            }) else {
                copyStatus = "No crash/runtime log exists yet. Start a game once first."
                return
            }

            UIPasteboard.general.string = try String(contentsOf: file, encoding: .utf8)
            copyStatus = "Copied \(file.lastPathComponent)."
        } catch {
            copyStatus = "Could not copy log: \(error.localizedDescription)"
        }
    }
}

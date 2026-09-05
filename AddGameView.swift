import SwiftUI

struct AddGameView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var store: GameStore

    @State private var showingFolderPicker = false
    @State private var folder: URL?
    @State private var candidates: [ExecutableCandidate] = []
    @State private var selectedCandidate: ExecutableCandidate?
    @State private var gameName = ""
    @State private var errorMessage: String?
    @State private var scanning = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Game folder") {
                    Button {
                        showingFolderPicker = true
                    } label: {
                        Label(folder?.lastPathComponent ?? "Choose folder in Files", systemImage: "folder.badge.plus")
                    }
                }

                if scanning {
                    Section {
                        HStack { ProgressView(); Text("Scanning Windows executables…") }
                    }
                }

                if let folder {
                    Section("Game") {
                        TextField("Name", text: $gameName)
                        LabeledContent("Folder", value: folder.lastPathComponent)
                    }

                    Section("Executable") {
                        if candidates.isEmpty && !scanning {
                            ContentUnavailableView("No .exe found", systemImage: "exclamationmark.triangle", description: Text("Select a Windows game folder containing at least one .exe file."))
                        }

                        ForEach(candidates) { candidate in
                            Button {
                                selectedCandidate = candidate
                            } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(candidate.displayName)
                                        Text(candidate.relativePath)
                                            .font(.caption2.monospaced())
                                            .foregroundStyle(.secondary)
                                        Text(candidate.peInfo?.summary ?? "Unknown / malformed PE")
                                            .font(.caption2)
                                            .foregroundStyle(candidate.peInfo == nil ? Color.orange : Color.green)
                                    }
                                    Spacer()
                                    if selectedCandidate?.id == candidate.id {
                                        Image(systemName: "checkmark.circle.fill")
                                    }
                                }
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .navigationTitle("Add Game")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") { addGame() }
                        .disabled(folder == nil || selectedCandidate == nil)
                }
            }
            .sheet(isPresented: $showingFolderPicker) {
                FolderPicker { url in
                    showingFolderPicker = false
                    scan(url)
                } onCancel: {
                    showingFolderPicker = false
                }
                .ignoresSafeArea()
            }
            .alert("Couldn't add game", isPresented: Binding(
                get: { errorMessage != nil },
                set: { if !$0 { errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(errorMessage ?? "Unknown error")
            }
        }
    }

    private func scan(_ url: URL) {
        folder = url
        gameName = prettyName(url.lastPathComponent)
        candidates = []
        selectedCandidate = nil
        scanning = true

        Task {
            do {
                let result = try FolderScanner.executables(in: url)
                await MainActor.run {
                    candidates = result
                    selectedCandidate = result.first
                    scanning = false
                }
            } catch {
                await MainActor.run {
                    errorMessage = error.localizedDescription
                    scanning = false
                }
            }
        }
    }

    private func addGame() {
        guard let folder, let selectedCandidate else { return }
        do {
            try store.addGame(name: gameName, folder: folder, executableRelativePath: selectedCandidate.relativePath)
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func prettyName(_ raw: String) -> String {
        raw.replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "-", with: " ")
    }
}

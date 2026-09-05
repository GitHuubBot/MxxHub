import Foundation
import Combine

@MainActor
final class GameStore: ObservableObject {
    @Published private(set) var games: [GameEntry] = []

    private let saveURL: URL

    init() {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let directory = base.appendingPathComponent("MxxHub", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        saveURL = directory.appendingPathComponent("library.json")

        // v0.1/v0.2 used the MexxBox name. Keeping the original bundle ID lets
        // MxxHub update the installed app in place; migrate its saved library
        // once so existing game tiles do not need to be added again.
        let legacyURL = base
            .appendingPathComponent("MexxBox", isDirectory: true)
            .appendingPathComponent("library.json")
        if !FileManager.default.fileExists(atPath: saveURL.path),
           FileManager.default.fileExists(atPath: legacyURL.path) {
            try? FileManager.default.copyItem(at: legacyURL, to: saveURL)
        }

        load()
    }

    func addGame(name: String, folder: URL, executableRelativePath: String) throws {
        guard folder.startAccessingSecurityScopedResource() else {
            throw StoreError.permissionDenied
        }
        defer { folder.stopAccessingSecurityScopedResource() }

        let bookmark = try folder.bookmarkData(
            options: .minimalBookmark,
            includingResourceValuesForKeys: nil,
            relativeTo: nil
        )

        let game = GameEntry(
            name: name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? folder.lastPathComponent : name,
            folderBookmark: bookmark,
            executableRelativePath: executableRelativePath
        )
        games.append(game)
        games.sort { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
        save()
    }

    func update(_ game: GameEntry) {
        guard let index = games.firstIndex(where: { $0.id == game.id }) else { return }
        games[index] = game
        save()
    }

    func remove(_ game: GameEntry) {
        games.removeAll { $0.id == game.id }
        save()
    }

    func resolvedFolder(for game: GameEntry) throws -> URL {
        var stale = false
        let url = try URL(
            resolvingBookmarkData: game.folderBookmark,
            options: [],
            relativeTo: nil,
            bookmarkDataIsStale: &stale
        )
        if stale { throw StoreError.staleBookmark }
        return url
    }

    func resolvedExecutable(for game: GameEntry) throws -> URL {
        try resolvedFolder(for: game).appendingPathComponent(game.executableRelativePath)
    }

    private func load() {
        guard let data = try? Data(contentsOf: saveURL),
              let decoded = try? JSONDecoder().decode([GameEntry].self, from: data) else { return }
        games = decoded
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(games) else { return }
        try? data.write(to: saveURL, options: [.atomic])
    }

    enum StoreError: LocalizedError {
        case permissionDenied
        case staleBookmark

        var errorDescription: String? {
            switch self {
            case .permissionDenied:
                return "MxxHub couldn't keep access to this folder."
            case .staleBookmark:
                return "Folder access expired or moved. Remove the game and add its folder again."
            }
        }
    }
}

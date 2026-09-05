import Foundation

struct ExecutableCandidate: Identifiable, Hashable {
    let id = UUID()
    let url: URL
    let relativePath: String
    let score: Int
    let peInfo: PEInfo?

    var displayName: String { url.lastPathComponent }
}

enum FolderScanner {
    static func executables(in folder: URL) throws -> [ExecutableCandidate] {
        guard folder.startAccessingSecurityScopedResource() else {
            throw ScanError.permissionDenied
        }
        defer { folder.stopAccessingSecurityScopedResource() }

        let keys: [URLResourceKey] = [.isRegularFileKey, .isDirectoryKey]
        guard let enumerator = FileManager.default.enumerator(
            at: folder,
            includingPropertiesForKeys: keys,
            options: [.skipsHiddenFiles, .skipsPackageDescendants]
        ) else {
            return []
        }

        var found: [ExecutableCandidate] = []
        for case let url as URL in enumerator {
            guard url.pathExtension.lowercased() == "exe" else { continue }
            let relative = relativePath(of: url, inside: folder)
            let info = try? PEInspector.inspect(url: url)
            found.append(.init(url: url, relativePath: relative, score: rank(url: url), peInfo: info))
        }

        return found.sorted {
            if $0.score == $1.score { return $0.relativePath < $1.relativePath }
            return $0.score > $1.score
        }
    }

    static func relativePath(of child: URL, inside parent: URL) -> String {
        let parentPath = parent.standardizedFileURL.path
        let childPath = child.standardizedFileURL.path
        guard childPath.hasPrefix(parentPath) else { return child.lastPathComponent }
        let start = childPath.index(childPath.startIndex, offsetBy: min(parentPath.count + 1, childPath.count))
        return String(childPath[start...])
    }

    private static func rank(url: URL) -> Int {
        let name = url.deletingPathExtension().lastPathComponent.lowercased()
        let badNames = ["unins", "uninstall", "setup", "installer", "crash", "report", "redist", "vc_redist", "dxsetup", "srcds"]
        if badNames.contains(where: { name.contains($0) }) { return -50 }

        var score = 0
        if ["portal", "hl2", "portal2", "game", "launcher", "mexxruntimetest", "mxxruntime64test"].contains(name) { score += 50 }
        if url.pathComponents.count < 8 { score += 5 }
        return score
    }

    enum ScanError: LocalizedError {
        case permissionDenied

        var errorDescription: String? {
            "MxxHub couldn't access that folder. Pick it again in Files and allow access."
        }
    }
}

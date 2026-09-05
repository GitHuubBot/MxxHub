import Foundation

struct GameEntry: Identifiable, Codable, Hashable {
    var id: UUID
    var name: String
    var folderBookmark: Data
    var executableRelativePath: String
    var dateAdded: Date
    var settings: GameSettings

    init(
        id: UUID = UUID(),
        name: String,
        folderBookmark: Data,
        executableRelativePath: String,
        dateAdded: Date = .now,
        settings: GameSettings = .default
    ) {
        self.id = id
        self.name = name
        self.folderBookmark = folderBookmark
        self.executableRelativePath = executableRelativePath
        self.dateAdded = dateAdded
        self.settings = settings
    }
}

struct GameSettings: Codable, Hashable {
    enum Renderer: String, Codable, CaseIterable, Identifiable {
        case automatic = "Automatic"
        case directX9 = "DirectX 9"
        case dxvk = "DXVK"
        case software = "Software"
        var id: String { rawValue }
    }

    var renderer: Renderer
    var resolution: String
    var launchArguments: String
    var useController: Bool
    var showTouchControls: Bool
    var memoryLimitMB: Int

    static let `default` = GameSettings(
        renderer: .automatic,
        resolution: "1280x720",
        launchArguments: "",
        useController: true,
        showTouchControls: false,
        memoryLimitMB: 2048
    )
}

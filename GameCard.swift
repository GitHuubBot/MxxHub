import SwiftUI

struct GameCard: View {
    let game: GameEntry

    private var initials: String {
        game.name
            .split(separator: " ")
            .prefix(2)
            .compactMap { $0.first }
            .map(String.init)
            .joined()
            .uppercased()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ZStack {
                RoundedRectangle(cornerRadius: 18)
                    .fill(.thinMaterial)
                    .aspectRatio(16/10, contentMode: .fit)
                VStack(spacing: 8) {
                    Image(systemName: "gamecontroller.fill")
                        .font(.system(size: 34, weight: .semibold))
                    Text(initials.isEmpty ? "GAME" : initials)
                        .font(.system(.title2, design: .rounded, weight: .black))
                }
                .foregroundStyle(.white)
            }

            Text(game.name)
                .font(.headline)
                .lineLimit(1)
            Text(game.executableRelativePath)
                .font(.caption2.monospaced())
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }
}

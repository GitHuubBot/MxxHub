import Foundation

struct RuntimeStatus {
    let name: String
    let version: String
    let installed: Bool
    let jitRequired: Bool
}

/// Runtime metadata used by the MxxHub UI. Actual execution is hosted by
/// MxxWineRuntimeViewController, which calls WineGlass' C engine directly.
enum WindowsRuntime {
    static var status: RuntimeStatus {
        RuntimeStatus(
            name: "WineGlass + Blink",
            version: "0.4.9.57 V48 texture/SRV + safe COM",
            installed: true,
            jitRequired: false
        )
    }
}

import Cocoa
import CoreGraphics
import Vision

func emit(_ value: Any) throws {
    let data = try JSONSerialization.data(withJSONObject: value, options: [])
    print(String(decoding: data, as: UTF8.self))
}

func describe(_ app: NSRunningApplication) -> [String: Any] {
    ["name": app.localizedName ?? "", "bundle": app.bundleIdentifier ?? "",
     "path": app.bundleURL?.path ?? "", "pid": Int(app.processIdentifier)]
}

func scan(_ path: String) throws {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["en-US"]
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(url: URL(fileURLWithPath: path)).perform([request])
    var lines: [[String: Any]] = []
    for item in request.results ?? [] {
        guard let best = item.topCandidates(1).first else { continue }
        func add(_ label: String, _ box: CGRect) {
            lines.append(["text": label, "x": box.midX, "y": 1-box.midY, "x0": box.minX, "x1": box.maxX])
        }
        add(best.string, item.boundingBox)
        // Vision sometimes groups adjacent button titles on a single baseline.
        let groupedButtons = [("Settings", "Anonymous"), ("Close", "Update"),
                              ("Later", "Install Update"), ("Later", "Install and Relaunch")]
        for (neighbor, label) in groupedButtons
        where best.string.localizedCaseInsensitiveContains(neighbor) {
            if let range = best.string.range(of: label, options: [.caseInsensitive, .backwards]),
               range.upperBound == best.string.endIndex,
               let rect = try best.boundingBox(for: range) {
                add(label, rect.boundingBox)
            }
        }
    }
    try emit(lines)
}

func click(_ x: Double, _ y: Double) throws {
    guard ProcessInfo.processInfo.environment["RUNNER_ENVIRONMENT"] == "github-hosted",
          (0...1).contains(x), (0...1).contains(y), CGPreflightPostEventAccess() else {
        throw NSError(domain: "UpgradeUI", code: 2, userInfo: [NSLocalizedDescriptionKey: "GUI event posting unavailable"])
    }
    let display = CGDisplayBounds(CGMainDisplayID())
    let point = CGPoint(x: display.minX+x*display.width, y: display.minY+y*display.height)
    for type in [CGEventType.mouseMoved, .leftMouseDown, .leftMouseUp] {
        guard let event = CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: .left) else {
            throw NSError(domain: "UpgradeUI", code: 3)
        }
        event.post(tap: .cghidEventTap)
        Thread.sleep(forTimeInterval: 0.08)
    }
}

do {
    let args = Array(CommandLine.arguments.dropFirst())
    switch args.first {
    case "scan" where args.count == 2: try scan(args[1])
    case "click" where args.count == 3:
        guard let x = Double(args[1]), let y = Double(args[2]) else { exit(2) }
        try click(x, y)
    case "settings":
        guard ProcessInfo.processInfo.environment["RUNNER_ENVIRONMENT"] == "github-hosted",
              CGPreflightPostEventAccess() else { exit(2) }
        for down in [true, false] {
            let key = CGEvent(keyboardEventSource: nil, virtualKey: 43, keyDown: down)!
            key.flags = .maskCommand
            key.post(tap: .cghidEventTap)
            Thread.sleep(forTimeInterval: 0.12)
        }
    case "scroll" where args.count == 3:
        guard let x = Double(args[1]), let y = Double(args[2]),
              ProcessInfo.processInfo.environment["RUNNER_ENVIRONMENT"] == "github-hosted",
              CGPreflightPostEventAccess(), (0...1).contains(x), (0...1).contains(y) else { exit(2) }
        let display = CGDisplayBounds(CGMainDisplayID())
        let point = CGPoint(x: display.minX+x*display.width, y: display.minY+y*display.height)
        CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
        Thread.sleep(forTimeInterval: 0.1)
        let wheel = CGEvent(scrollWheelEvent2Source: nil, units: .line, wheelCount: 1, wheel1: -8, wheel2: 0, wheel3: 0)!
        wheel.location = point
        wheel.post(tap: .cghidEventTap)
    case "apps":
        let ws = NSWorkspace.shared
        let defaultBrowser = ws.urlForApplication(toOpen: URL(string: "https://example.invalid/")!)?.path ?? ""
        try emit(["apps": ws.runningApplications.map(describe), "browser": defaultBrowser,
                  "front": ws.frontmostApplication.map(describe) ?? [:], "canPostEvents": CGPreflightPostEventAccess()])
    default: exit(2)
    }
} catch {
    FileHandle.standardError.write(Data("\(error)\n".utf8)); exit(1)
}

import AppKit
import XCTest
import Vision

private struct Hit { let click: () -> Void }

final class ReleasedUpgradeUITests: XCTestCase {
    func testSignedAppUpdate() throws {
        // TEST_RUNNER_ environment variables supplied to xcodebuild are forwarded
        // to the UI test runner with that prefix removed.
        let env = ProcessInfo.processInfo.environment
        guard env["HOSTED_UPGRADE_TEST"] == "1", let path = env["RELEASED_APP_PATH"] else {
            throw XCTSkip("Requires a disposable hosted runner and a staged app")
        }
        let url = URL(fileURLWithPath: path)
        func version() throws -> String {
            let data = try Data(contentsOf: url.appendingPathComponent("Contents/Info.plist"))
            let plist = try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any]
            return try XCTUnwrap(plist?["CFBundleShortVersionString"] as? String)
        }
        let workspace = NSWorkspace.shared
        let browserURL = try XCTUnwrap(workspace.urlForApplication(toOpen: URL(string: "https://example.invalid")!))
        let browserIDs: Set<String> = ["com.apple.safari", "com.google.chrome", "org.mozilla.firefox",
                                      "com.microsoft.edgemac", "com.brave.browser", "com.operasoftware.opera",
                                      "company.thebrowser.browser", "com.vivaldi.vivaldi"]
        func isBrowser(_ candidate: NSRunningApplication) -> Bool {
            candidate.bundleURL == browserURL || browserIDs.contains(candidate.bundleIdentifier?.lowercased() ?? "")
        }
        XCTAssertFalse(workspace.runningApplications.contains(where: isBrowser), "Browser already open at baseline")
        XCTAssertFalse(workspace.runningApplications.contains { $0.bundleURL?.path == url.path }, "Baseline app already open")
        let lock = NSLock()
        var seen: [(Notification.Name, pid_t, String, Bool)] = []
        let center = workspace.notificationCenter
        let tokens = [NSWorkspace.didLaunchApplicationNotification, NSWorkspace.didActivateApplicationNotification,
                      NSWorkspace.didTerminateApplicationNotification].map { name in
            center.addObserver(forName: name, object: nil, queue: nil) { note in
                guard let client = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication else { return }
                lock.lock()
                seen.append((name, client.processIdentifier, client.bundleURL?.path ?? "", isBrowser(client)))
                lock.unlock()
            }
        }
        defer { tokens.forEach(center.removeObserver) }
        let app = XCUIApplication(url: url)
        XCTAssertEqual(try version(), "0.15.0")
        func progress(_ message: String) {
            print("[upgrade-ui] \(Date()) \(message)")
            fflush(stdout)
        }
        progress("Starting signed 0.15.0 bundle at \(url.path)")
        app.launch()
        let old = try XCTUnwrap(workspace.runningApplications.first { $0.bundleURL?.path == url.path })
        func shot(_ name: String) {
            let proof = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
            proof.name = name
            proof.lifetime = .keepAlways
            add(proof)
        }
        func snapshot(_ name: String) {
            progress("\(name). Frontmost: \(workspace.frontmostApplication?.bundleIdentifier ?? "unknown"). App windows: \(app.windows.count)")
            let tree = app.debugDescription
            progress(String(tree.prefix(1400)))
            let proof = XCTAttachment(string: tree)
            proof.name = name + "-AX"
            proof.lifetime = .keepAlways
            add(proof)
            shot(name)
        }
        func fromVisibleWindow(_ candidates: [String]) -> Hit? {
            let names = Set(candidates.map { $0.lowercased() })
            for w in app.windows.allElementsBoundByIndex where w.exists && !w.frame.isEmpty {
                let request = VNRecognizeTextRequest()
                request.recognitionLanguages = ["en-US"]
                request.recognitionLevel = .accurate
                let handler = VNImageRequestHandler(data: w.screenshot().pngRepresentation)
                guard (try? handler.perform([request])) != nil else { continue }
                for row in request.results ?? [] {
                    guard let best = row.topCandidates(1).first,
                          names.contains(best.string.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()) else { continue }
                    let box = row.boundingBox
                    let point = w.coordinate(withNormalizedOffset: CGVector(dx: box.midX, dy: 1-box.midY))
                    return Hit { progress("Click visible text \(best.string) in window \(w.title)"); point.click() }
                }
            }
            return nil
        }
        func label(_ candidates: [String], wait: TimeInterval) -> Hit? {
            progress("Waiting up to \(Int(wait))s for \(candidates)")
            var nextOCR = Date().addingTimeInterval(4)
            let end = Date().addingTimeInterval(wait)
            let visible = app.descendants(matching: .any)
            repeat {
                for candidate in candidates {
                    let query = visible.matching(NSPredicate(format: "label ==[c] %@ OR title ==[c] %@ OR value ==[c] %@", candidate, candidate, candidate))
                    let found = query.allElementsBoundByIndex.filter { $0.exists && $0.isHittable }
                    if let control = found.last {
                        return Hit { progress("Click AX \(candidate) \(control.elementType)"); control.click() }
                    }
                }
                if Date() >= nextOCR {
                    if let hit = fromVisibleWindow(candidates) { return hit }
                    nextOCR = Date().addingTimeInterval(8)
                }
                Thread.sleep(forTimeInterval: 0.8)
            } while Date() < end
            snapshot("missing-" + candidates.joined(separator: "-"))
            return nil
        }
        snapshot("0.15.0-started")
        if let guest = label(["Anonymous"], wait: 45) {
            guest.click()
        }
        if let banner = label(["Update"], wait: 30) {
            banner.click()
        } else {
            progress("Opening Settings with Cmd+,")
            app.typeKey(",", modifierFlags: [.command])
            let check = try XCTUnwrap(label(["Check for updates"], wait: 15), "Settings did not display Check for updates.\n\(app.debugDescription)")
            check.click()
        }
        let dialog = try XCTUnwrap(label(["Update"], wait: 15), "No update dialog.\n\(app.debugDescription)")
        shot("0.15.0-app-update-dialog")
        dialog.click()
        let native = try XCTUnwrap(label(["Install Update", "Install"], wait: 45), "No Sparkle download consent.\n\(app.debugDescription)")
        shot("native-install")
        native.click()
        let restart = try XCTUnwrap(label(["Install and Relaunch", "Install & Relaunch"], wait: 240), "No Sparkle installation prompt.\n\(app.debugDescription)")
        shot("native-relaunch")
        restart.click()
        let deadline = Date().addingTimeInterval(90)
        while Date() < deadline {
            if (try? version()) == "0.15.1" && app.state == .runningForeground { break }
            Thread.sleep(forTimeInterval: 1)
        }
        XCTAssertEqual(try version(), "0.15.1")
        XCTAssertEqual(app.state, .runningForeground)
        let new = try XCTUnwrap(workspace.runningApplications.first { $0.bundleURL?.path == url.path && $0.processIdentifier != old.processIdentifier })
        RunLoop.current.run(until: Date().addingTimeInterval(15))
        XCTAssertFalse(new.isTerminated)
        XCTAssertTrue(old.isTerminated)
        XCTAssertFalse(workspace.runningApplications.contains(where: isBrowser))
        lock.lock()
        let recorded = seen
        lock.unlock()
        XCTAssertFalse(recorded.contains { $0.3 && $0.0 != NSWorkspace.didTerminateApplicationNotification })
        XCTAssertTrue(recorded.contains { $0.0 == NSWorkspace.didLaunchApplicationNotification && $0.1 == old.processIdentifier })
        XCTAssertTrue(recorded.contains { $0.0 == NSWorkspace.didTerminateApplicationNotification && $0.1 == old.processIdentifier })
        XCTAssertTrue(recorded.contains { $0.0 == NSWorkspace.didLaunchApplicationNotification && $0.1 == new.processIdentifier })
        let log = XCTAttachment(string: recorded.map { "\($0.0.rawValue) \($0.1) \($0.2) browser=\($0.3)" }.joined(separator: "\n"))
        log.name = "App and browser lifecycle"
        log.lifetime = .keepAlways
        add(log)
        progress("Confirmed 0.15.1 remained running with a new PID and no browser launch.")
        shot("0.15.1-running")
    }
}

#!/usr/bin/swift
// THROWAWAY PROTOTYPE ONLY.
// Question: can one reviewed helper bind to an exact copied ChatGPT PID and
// select one disposable Git fixture through that process's standard Open panel
// without gaining a system-wide target or mutating application state itself?

import AppKit
import ApplicationServices
import CryptoKit
import Darwin
import Foundation
import Security

enum ProbeError: Error, CustomStringConvertible {
    case usage(String)
    case validation(String)
    case unavailable(String)
    case permission(String)

    var description: String {
        switch self {
        case .usage(let message), .validation(let message), .unavailable(let message),
             .permission(let message): return message
        }
    }

    var exitCode: Int32 {
        switch self {
        case .usage: return 64
        case .validation: return 65
        case .unavailable: return 69
        case .permission: return 77
        }
    }
}

enum Phase: String, CaseIterable {
    case inspectProjectPicker = "inspect-project-picker"
    case selectProject = "select-project"
}

struct Options {
    let pid: pid_t
    let runRoot: String
    let expectedBundle: String
    let expectedExecutable: String
    let fixtureRoot: String
    let phase: Phase
    let eventLog: String
    let permitKeyFallback: Bool
    let validateInputsOnly: Bool
}

struct ValidatedPaths {
    let runRoot: String
    let bundle: String
    let executable: String
    let fixture: String
    let eventLog: String
}

struct ProcessIdentity: Equatable {
    let pid: pid_t
    let startSeconds: UInt64
    let startMicroseconds: UInt64
    let executable: String
}

struct ElementDescription: Equatable {
    let role: String
    let subrole: String?
    let identifier: String?
    let title: String?
    let help: String?
    let actions: Set<String>
    let children: [ElementDescription]

    var searchableText: String {
        [identifier, title, help].compactMap { $0?.lowercased() }.joined(separator: " ")
    }

    var descendants: [ElementDescription] {
        children + children.flatMap(\.descendants)
    }
}

struct SelectionPlan: Equatable {
    enum Navigation: Equatable { case direct, commandShiftG }
    let navigation: Navigation
    let chooserTitle: String
}

enum PathPolicy {
    static let installedApp = "/Applications/ChatGPT.app"
    static let runPrefix = "chatgpt-route-prototype-08."

    static func canonicalExisting(_ path: String) throws -> String {
        guard path.hasPrefix("/") else { throw ProbeError.validation("path must be absolute: \(path)") }
        var buffer = [CChar](repeating: 0, count: Int(PATH_MAX))
        guard realpath(path, &buffer) != nil else {
            throw ProbeError.validation("path does not resolve: \(path)")
        }
        return String(cString: buffer)
    }

    static func canonicalLog(_ path: String) throws -> String {
        guard path.hasPrefix("/") else { throw ProbeError.validation("event log must be absolute") }
        var info = stat()
        if lstat(path, &info) == 0 && (info.st_mode & S_IFMT) == S_IFLNK {
            throw ProbeError.validation("event log must not be a symlink")
        }
        let parent = try canonicalExisting((path as NSString).deletingLastPathComponent)
        return (parent as NSString).appendingPathComponent((path as NSString).lastPathComponent)
    }

    static func contains(_ root: String, _ child: String) -> Bool {
        child != root && child.hasPrefix(root + "/")
    }

    static func validate(
        runRoot: String,
        bundle: String,
        executable: String,
        fixture: String,
        eventLog: String
    ) throws -> ValidatedPaths {
        let root = try canonicalExisting(runRoot)
        guard (root as NSString).deletingLastPathComponent == "/private/tmp",
              (root as NSString).lastPathComponent.hasPrefix(runPrefix) else {
            throw ProbeError.validation("run root is not an owned ticket-08 disposable root")
        }
        let canonicalBundle = try canonicalExisting(bundle)
        let canonicalExecutable = try canonicalExisting(executable)
        let canonicalFixture = try canonicalExisting(fixture)
        let canonicalEventLog = try canonicalLog(eventLog)
        guard canonicalBundle != installedApp,
              !canonicalBundle.hasPrefix(installedApp + "/") else {
            throw ProbeError.validation("installed ChatGPT.app is forbidden")
        }
        guard contains(root, canonicalBundle), contains(root, canonicalExecutable),
              contains(root, canonicalFixture), contains(root, canonicalEventLog) else {
            throw ProbeError.validation("bundle, executable, fixture, and log must be beneath the run root")
        }
        let requiredExecutable = (canonicalBundle as NSString)
            .appendingPathComponent("Contents/MacOS/ChatGPT")
        guard canonicalExecutable == requiredExecutable else {
            throw ProbeError.validation("expected executable is not the copied bundle executable")
        }
        guard FileManager.default.isExecutableFile(atPath: canonicalExecutable) else {
            throw ProbeError.validation("expected executable is not executable")
        }
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: canonicalFixture, isDirectory: &isDirectory),
              isDirectory.boolValue else {
            throw ProbeError.validation("fixture root is not a directory")
        }
        guard FileManager.default.fileExists(atPath: (canonicalFixture as NSString).appendingPathComponent(".git")) else {
            throw ProbeError.validation("fixture root is not a Git worktree")
        }
        guard (canonicalEventLog as NSString).deletingLastPathComponent ==
                (root as NSString).appendingPathComponent("logs"),
              (canonicalEventLog as NSString).lastPathComponent == "native-gui-probe.jsonl" else {
            throw ProbeError.validation("event log must be logs/native-gui-probe.jsonl")
        }
        return ValidatedPaths(runRoot: root, bundle: canonicalBundle,
                              executable: canonicalExecutable, fixture: canonicalFixture,
                              eventLog: canonicalEventLog)
    }
}

enum OpenPanelPolicy {
    static let chooserTitles: Set<String> = ["open", "choose", "select"]

    static func uniqueElements(_ root: ElementDescription, where predicate: (ElementDescription) -> Bool)
        throws -> [ElementDescription] {
        let matches = ([root] + root.descendants).filter(predicate)
        return matches
    }

    static func plan(windows: [ElementDescription], permitKeyFallback: Bool) throws -> SelectionPlan {
        let candidates = windows.filter { window in
            guard window.role == kAXWindowRole || window.role == kAXSheetRole else { return false }
            let descendants = [window] + window.descendants
            let hasCancel = descendants.contains {
                $0.role == kAXButtonRole && $0.title?.lowercased() == "cancel" &&
                    $0.actions.contains(kAXPressAction)
            }
            let chooserCount = descendants.filter {
                $0.role == kAXButtonRole && chooserTitles.contains($0.title?.lowercased() ?? "") &&
                    $0.actions.contains(kAXPressAction)
            }.count
            return hasCancel && chooserCount == 1
        }
        guard candidates.count == 1, let panel = candidates.first else {
            throw ProbeError.validation("expected exactly one standard Open panel; found \(candidates.count)")
        }
        let all = [panel] + panel.descendants
        let choosers = all.filter {
            $0.role == kAXButtonRole && chooserTitles.contains($0.title?.lowercased() ?? "") &&
                $0.actions.contains(kAXPressAction)
        }
        guard choosers.count == 1, let chooser = choosers.first, let title = chooser.title else {
            throw ProbeError.validation("Open panel chooser is missing or ambiguous")
        }
        let directFields = all.filter {
            $0.role == kAXTextFieldRole &&
                ($0.searchableText.contains("path") || $0.searchableText.contains("location") ||
                 $0.searchableText.contains("folder"))
        }
        if directFields.count == 1 {
            return SelectionPlan(navigation: .direct, chooserTitle: title)
        }
        guard directFields.isEmpty else {
            throw ProbeError.validation("Open panel path field is ambiguous")
        }
        guard permitKeyFallback else {
            throw ProbeError.validation("direct AX navigation unavailable and key fallback was not authorized")
        }
        return SelectionPlan(navigation: .commandShiftG, chooserTitle: title)
    }
}

final class EventLog {
    private let descriptor: Int32

    init(path: String) throws {
        descriptor = open(path, O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else { throw ProbeError.validation("cannot open append-only event log") }
        guard fchmod(descriptor, S_IRUSR | S_IWUSR) == 0 else {
            close(descriptor)
            throw ProbeError.validation("cannot restrict event log permissions")
        }
    }

    deinit { close(descriptor) }

    func write(_ kind: String, _ fields: [String: Any] = [:]) throws {
        var record = fields
        record["kind"] = kind
        record["schema"] = 1
        let data = try JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) + Data([0x0a])
        let result = data.withUnsafeBytes { raw in
            Darwin.write(descriptor, raw.baseAddress, raw.count)
        }
        guard result == data.count else { throw ProbeError.unavailable("event log append failed") }
    }
}

func sha256(_ value: String) -> String {
    SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
}

func parseOptions(_ arguments: [String]) throws -> Options {
    var values: [String: String] = [:]
    var permitKeyFallback = false
    var validateInputsOnly = false
    var index = 0
    let valueOptions = Set(["--pid", "--run-root", "--expected-bundle", "--expected-executable",
                            "--fixture-root", "--phase", "--event-log"])
    while index < arguments.count {
        let argument = arguments[index]
        if argument == "--permit-key-fallback" {
            guard !permitKeyFallback else { throw ProbeError.usage("duplicate --permit-key-fallback") }
            permitKeyFallback = true
            index += 1
        } else if argument == "--validate-inputs-only" {
            guard !validateInputsOnly else { throw ProbeError.usage("duplicate --validate-inputs-only") }
            validateInputsOnly = true
            index += 1
        } else if valueOptions.contains(argument) {
            guard values[argument] == nil, index + 1 < arguments.count else {
                throw ProbeError.usage("missing or duplicate value for \(argument)")
            }
            values[argument] = arguments[index + 1]
            index += 2
        } else {
            throw ProbeError.usage("unknown argument: \(argument)")
        }
    }
    for option in valueOptions where values[option] == nil {
        throw ProbeError.usage("missing required option: \(option)")
    }
    guard let rawPID = values["--pid"], let numericPID = Int32(rawPID), numericPID > 1 else {
        throw ProbeError.usage("--pid must be an integer greater than one")
    }
    guard let rawPhase = values["--phase"], let phase = Phase(rawValue: rawPhase) else {
        throw ProbeError.usage("--phase must be inspect-project-picker or select-project")
    }
    return Options(pid: numericPID, runRoot: values["--run-root"]!,
                   expectedBundle: values["--expected-bundle"]!,
                   expectedExecutable: values["--expected-executable"]!,
                   fixtureRoot: values["--fixture-root"]!, phase: phase,
                   eventLog: values["--event-log"]!, permitKeyFallback: permitKeyFallback,
                   validateInputsOnly: validateInputsOnly)
}

func processIdentity(_ pid: pid_t) throws -> ProcessIdentity {
    var info = proc_bsdinfo()
    let size = proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, Int32(MemoryLayout.size(ofValue: info)))
    guard size == MemoryLayout.size(ofValue: info) else {
        throw ProbeError.unavailable("PID exited or process identity is unavailable")
    }
    var path = [CChar](repeating: 0, count: Int(MAXPATHLEN) * 4)
    guard proc_pidpath(pid, &path, UInt32(path.count)) > 0 else {
        throw ProbeError.unavailable("cannot resolve PID executable path")
    }
    return ProcessIdentity(pid: pid, startSeconds: UInt64(info.pbi_start_tvsec),
                           startMicroseconds: UInt64(info.pbi_start_tvusec),
                           executable: String(cString: path))
}

func signingInformation(_ code: SecStaticCode) throws -> [String: Any] {
    var result: CFDictionary?
    let status = SecCodeCopySigningInformation(code, SecCSFlags(rawValue: kSecCSSigningInformation), &result)
    guard status == errSecSuccess, let info = result as? [String: Any] else {
        throw ProbeError.validation("cannot read code signing information")
    }
    return info
}

func verifyProcess(options: Options, paths: ValidatedPaths) throws -> ProcessIdentity {
    let before = try processIdentity(options.pid)
    guard try PathPolicy.canonicalExisting(before.executable) == paths.executable else {
        throw ProbeError.validation("PID executable does not match expected copied executable")
    }
    guard let running = NSRunningApplication(processIdentifier: options.pid), !running.isTerminated,
          let bundleURL = running.bundleURL, let executableURL = running.executableURL,
          try PathPolicy.canonicalExisting(bundleURL.path) == paths.bundle,
          try PathPolicy.canonicalExisting(executableURL.path) == paths.executable else {
        throw ProbeError.validation("running application paths do not match the copied bundle")
    }

    var staticCode: SecStaticCode?
    guard SecStaticCodeCreateWithPath(URL(fileURLWithPath: paths.bundle) as CFURL, [], &staticCode) == errSecSuccess,
          let staticCode else { throw ProbeError.validation("cannot create static code identity") }
    guard SecStaticCodeCheckValidity(staticCode, SecCSFlags(rawValue: kSecCSCheckAllArchitectures), nil) == errSecSuccess else {
        throw ProbeError.validation("copied bundle signature is invalid")
    }
    let attributes = [kSecGuestAttributePid as String: NSNumber(value: options.pid)] as CFDictionary
    var dynamicCode: SecCode?
    guard SecCodeCopyGuestWithAttributes(nil, attributes, [], &dynamicCode) == errSecSuccess,
          let dynamicCode,
          SecCodeCheckValidity(dynamicCode, [], nil) == errSecSuccess else {
        throw ProbeError.validation("live process code identity is invalid")
    }
    var dynamicStaticCode: SecStaticCode?
    guard SecCodeCopyStaticCode(dynamicCode, [], &dynamicStaticCode) == errSecSuccess,
          let dynamicStaticCode else {
        throw ProbeError.validation("cannot resolve live process static code identity")
    }
    let staticInfo = try signingInformation(staticCode)
    let dynamicInfo = try signingInformation(dynamicStaticCode)
    let identifierKey = kSecCodeInfoIdentifier as String
    let uniqueKey = kSecCodeInfoUnique as String
    guard let staticIdentifier = staticInfo[identifierKey] as? String,
          let dynamicIdentifier = dynamicInfo[identifierKey] as? String,
          let staticUnique = staticInfo[uniqueKey] as? Data,
          let dynamicUnique = dynamicInfo[uniqueKey] as? Data,
          !staticIdentifier.isEmpty, !staticUnique.isEmpty,
          staticIdentifier == dynamicIdentifier, staticUnique == dynamicUnique else {
        throw ProbeError.validation("live process signature does not match copied bundle signature")
    }
    let after = try processIdentity(options.pid)
    guard before == after else { throw ProbeError.validation("PID identity changed during validation") }
    return after
}

func attribute(_ element: AXUIElement, _ name: CFString) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name, &value) == .success else { return nil }
    return value
}

func stringAttribute(_ element: AXUIElement, _ name: CFString) -> String? {
    attribute(element, name) as? String
}

func actions(_ element: AXUIElement) -> Set<String> {
    var names: CFArray?
    guard AXUIElementCopyActionNames(element, &names) == .success,
          let values = names as? [String] else { return [] }
    return Set(values)
}

func childElements(_ element: AXUIElement) -> [AXUIElement] {
    attribute(element, kAXChildrenAttribute as CFString) as? [AXUIElement] ?? []
}

func describe(_ element: AXUIElement, depth: Int = 0, budget: inout Int) throws -> ElementDescription {
    guard depth <= 16, budget > 0 else { throw ProbeError.validation("AX tree exceeded traversal limit") }
    budget -= 1
    let children = try childElements(element).map { try describe($0, depth: depth + 1, budget: &budget) }
    return ElementDescription(role: stringAttribute(element, kAXRoleAttribute as CFString) ?? "",
                              subrole: stringAttribute(element, kAXSubroleAttribute as CFString),
                              identifier: stringAttribute(element, kAXIdentifierAttribute as CFString),
                              title: stringAttribute(element, kAXTitleAttribute as CFString),
                              help: stringAttribute(element, kAXHelpAttribute as CFString),
                              actions: actions(element), children: children)
}

func liveWindows(_ application: AXUIElement) throws -> [(AXUIElement, ElementDescription)] {
    guard let windows = attribute(application, kAXWindowsAttribute as CFString) as? [AXUIElement] else {
        throw ProbeError.validation("application exposes no AX windows")
    }
    return try windows.map { window in
        var budget = 500
        return (window, try describe(window, budget: &budget))
    }
}

func descendants(_ element: AXUIElement, depth: Int = 0, budget: inout Int) throws -> [AXUIElement] {
    guard depth <= 16, budget > 0 else { throw ProbeError.validation("AX tree exceeded traversal limit") }
    budget -= 1
    let children = childElements(element)
    return children + (try children.flatMap { try descendants($0, depth: depth + 1, budget: &budget) })
}

func matchingLiveElements(_ root: AXUIElement, _ predicate: (AXUIElement) -> Bool) throws -> [AXUIElement] {
    var budget = 500
    return ([root] + (try descendants(root, budget: &budget))).filter(predicate)
}

func press(_ element: AXUIElement, purpose: String) throws {
    guard actions(element).contains(kAXPressAction) else {
        throw ProbeError.validation("\(purpose) does not advertise AXPress")
    }
    guard AXUIElementPerformAction(element, kAXPressAction as CFString) == .success else {
        throw ProbeError.unavailable("AXPress failed for \(purpose)")
    }
}

func postCommandShiftG(to pid: pid_t) throws {
    guard let source = CGEventSource(stateID: .privateState),
          let down = CGEvent(keyboardEventSource: source, virtualKey: 5, keyDown: true),
          let up = CGEvent(keyboardEventSource: source, virtualKey: 5, keyDown: false) else {
        throw ProbeError.unavailable("cannot create audited Command-Shift-G events")
    }
    down.flags = [.maskCommand, .maskShift]
    up.flags = [.maskCommand, .maskShift]
    down.postToPid(pid)
    up.postToPid(pid)
}

func waitForUniquePathEntry(application: AXUIElement) throws -> (AXUIElement, AXUIElement) {
    let deadline = Date().addingTimeInterval(3)
    while Date() < deadline {
        let windows = try liveWindows(application)
        let entries = try windows.compactMap { window, _ -> (AXUIElement, AXUIElement)? in
            let fields = try matchingLiveElements(window) {
                stringAttribute($0, kAXRoleAttribute as CFString) == kAXTextFieldRole
            }
            let confirms = try matchingLiveElements(window) {
                stringAttribute($0, kAXRoleAttribute as CFString) == kAXButtonRole &&
                    actions($0).contains(kAXPressAction) &&
                    ["go", "open"].contains(stringAttribute($0, kAXTitleAttribute as CFString)?.lowercased() ?? "")
            }
            return fields.count == 1 && confirms.count == 1 ? (fields[0], confirms[0]) : nil
        }
        if entries.count == 1 { return entries[0] }
        if entries.count > 1 { throw ProbeError.validation("path-entry sheet is ambiguous") }
        usleep(100_000)
    }
    throw ProbeError.unavailable("unique Command-Shift-G path-entry sheet did not appear")
}

func chooseButton(in panel: AXUIElement, title: String) throws -> AXUIElement {
    let matches = try matchingLiveElements(panel) {
        stringAttribute($0, kAXRoleAttribute as CFString) == kAXButtonRole &&
            stringAttribute($0, kAXTitleAttribute as CFString)?.lowercased() == title.lowercased()
    }
    guard matches.count == 1 else { throw ProbeError.validation("chooser button is missing or ambiguous") }
    return matches[0]
}

func execute(options: Options, paths: ValidatedPaths, log: EventLog) throws {
    let identity = try verifyProcess(options: options, paths: paths)
    try log.write("process-validated", ["pid": Int(identity.pid),
                                         "startSeconds": identity.startSeconds,
                                         "executableSha256": sha256(identity.executable)])
    guard AXIsProcessTrusted() else {
        try log.write("accessibility-not-trusted", ["pid": Int(identity.pid)])
        throw ProbeError.permission("Accessibility is not granted to this exact helper artifact")
    }
    let application = AXUIElementCreateApplication(options.pid)
    let windows = try liveWindows(application)
    let plan = try OpenPanelPolicy.plan(windows: windows.map(\.1),
                                        permitKeyFallback: options.permitKeyFallback)
    try log.write("open-panel-validated", ["windowCount": windows.count,
                                            "navigation": plan.navigation == .direct ? "direct" : "command-shift-g",
                                            "chooserTitle": plan.chooserTitle])
    if options.phase == .inspectProjectPicker { return }

    let selectedDescriptions = windows.filter { pair in
        (try? OpenPanelPolicy.plan(windows: [pair.1], permitKeyFallback: options.permitKeyFallback)) != nil
    }
    guard selectedDescriptions.count == 1, let panel = selectedDescriptions.first?.0 else {
        throw ProbeError.validation("live Open panel identity changed before action")
    }
    switch plan.navigation {
    case .direct:
        let fields = try matchingLiveElements(panel) {
            guard stringAttribute($0, kAXRoleAttribute as CFString) == kAXTextFieldRole else { return false }
            let text = [stringAttribute($0, kAXIdentifierAttribute as CFString),
                        stringAttribute($0, kAXTitleAttribute as CFString),
                        stringAttribute($0, kAXHelpAttribute as CFString)]
                .compactMap { $0?.lowercased() }.joined(separator: " ")
            return text.contains("path") || text.contains("location") || text.contains("folder")
        }
        guard fields.count == 1,
              AXUIElementSetAttributeValue(fields[0], kAXValueAttribute as CFString,
                                           paths.fixture as CFString) == .success else {
            throw ProbeError.validation("direct path field is missing, ambiguous, or not writable")
        }
    case .commandShiftG:
        try postCommandShiftG(to: options.pid)
        let (pathField, confirmButton) = try waitForUniquePathEntry(application: application)
        guard AXUIElementSetAttributeValue(pathField, kAXValueAttribute as CFString,
                                           paths.fixture as CFString) == .success else {
            throw ProbeError.validation("path-entry field rejected fixture root")
        }
        try press(confirmButton, purpose: "path-entry confirmation")
        usleep(250_000)
    }
    try press(try chooseButton(in: panel, title: plan.chooserTitle), purpose: "Open panel chooser")
    let finalIdentity = try processIdentity(options.pid)
    guard finalIdentity == identity else { throw ProbeError.validation("PID identity changed during AX action") }
    try log.write("project-selection-issued", ["pid": Int(identity.pid),
                                                "fixtureSha256": sha256(paths.fixture)])
}

func testElement(role: String, title: String? = nil, identifier: String? = nil,
                 actions: Set<String> = [], children: [ElementDescription] = []) -> ElementDescription {
    ElementDescription(role: role, subrole: nil, identifier: identifier, title: title,
                       help: nil, actions: actions, children: children)
}

func runSelfTests() throws {
    func require(_ condition: Bool, _ message: String) throws {
        if !condition { throw ProbeError.validation("self-test failed: \(message)") }
    }
    let cancel = testElement(role: kAXButtonRole, title: "Cancel", actions: [kAXPressAction])
    let choose = testElement(role: kAXButtonRole, title: "Open", actions: [kAXPressAction])
    let direct = testElement(role: kAXTextFieldRole, identifier: "path", actions: [])
    let panel = testElement(role: kAXWindowRole, children: [cancel, choose, direct])
    try require(try OpenPanelPolicy.plan(windows: [panel], permitKeyFallback: false) ==
                SelectionPlan(navigation: .direct, chooserTitle: "Open"), "direct plan")
    let fallbackPanel = testElement(role: kAXWindowRole, children: [cancel, choose])
    try require(try OpenPanelPolicy.plan(windows: [fallbackPanel], permitKeyFallback: true).navigation ==
                .commandShiftG, "explicit key fallback")
    var rejected = false
    do { _ = try OpenPanelPolicy.plan(windows: [fallbackPanel], permitKeyFallback: false) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "unauthorized fallback passed")
    rejected = false
    do { _ = try OpenPanelPolicy.plan(windows: [panel, panel], permitKeyFallback: false) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "duplicate panels passed")
    let duplicateChooser = testElement(role: kAXWindowRole, children: [cancel, choose, choose, direct])
    rejected = false
    do { _ = try OpenPanelPolicy.plan(windows: [duplicateChooser], permitKeyFallback: false) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "duplicate chooser passed")
    try require(!PathPolicy.contains("/private/tmp/root", "/private/tmp/root2/file"), "prefix collision")
    try require(PathPolicy.contains("/private/tmp/root", "/private/tmp/root/file"), "contained path")
    try require(Phase(rawValue: "select-project") == .selectProject, "phase parsing")
    print("native GUI probe self-test passed")
}

do {
    if CommandLine.arguments.dropFirst().elementsEqual(["--self-test"]) {
        try runSelfTests()
        exit(0)
    }
    let options = try parseOptions(Array(CommandLine.arguments.dropFirst()))
    let paths = try PathPolicy.validate(runRoot: options.runRoot, bundle: options.expectedBundle,
                                        executable: options.expectedExecutable,
                                        fixture: options.fixtureRoot, eventLog: options.eventLog)
    let log = try EventLog(path: paths.eventLog)
    try log.write("inputs-validated", ["phase": options.phase.rawValue,
                                        "runRootSha256": sha256(paths.runRoot),
                                        "bundleSha256": sha256(paths.bundle),
                                        "fixtureSha256": sha256(paths.fixture),
                                        "keyFallbackAuthorized": options.permitKeyFallback])
    if !options.validateInputsOnly {
        try execute(options: options, paths: paths, log: log)
    }
} catch let error as ProbeError {
    FileHandle.standardError.write(Data(("native-gui-probe: \(error)\n").utf8))
    exit(error.exitCode)
} catch {
    FileHandle.standardError.write(Data(("native-gui-probe: \(error)\n").utf8))
    exit(70)
}

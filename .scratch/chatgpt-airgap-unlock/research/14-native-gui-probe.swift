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
    case inspectOpenFolderMenu = "inspect-open-folder-menu"
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
    let pressOpenFolderMenuItem: Bool
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

enum AppKitRegistrationSample: Equatable {
    case unavailable
    case published(isTerminated: Bool, bundlePath: String?, executablePath: String?)
}

struct ProcessRegistrationReadiness: Equatable {
    let pollCount: Int
}

struct ProcessVerification: Equatable {
    let identity: ProcessIdentity
    let registrationPollCount: Int
}

enum AttributePublication<Value: Equatable>: Equatable {
    case missing
    case malformed
    case readFailure(Int32)
    case value(Value)
}

struct ElementDescription: Equatable {
    let role: String
    let subrole: String?
    let identifier: String?
    let title: String?
    let help: String?
    let enabled: Bool?
    let menuCommandCharacter: AttributePublication<String>
    let menuCommandVirtualKey: AttributePublication<Int>
    let menuCommandModifiers: AttributePublication<Int>
    let actions: Set<String>
    let children: [ElementDescription]

    var searchableText: String {
        [identifier, title, help].compactMap { $0?.lowercased() }.joined(separator: " ")
    }

    var descendants: [ElementDescription] {
        children + children.flatMap(\.descendants)
    }
}

struct OpenFolderMenuPlan: Equatable, Hashable {
    let menuBarItemIndex: Int
    let menuIndex: Int
    let menuItemIndex: Int
    let parentTitle: String
    let itemTitle: String
    let commandVirtualKey: Int?
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
        let gitMetadata = (canonicalFixture as NSString).appendingPathComponent(".git")
        var gitIsDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: gitMetadata, isDirectory: &gitIsDirectory),
              gitIsDirectory.boolValue,
              try canonicalExisting(gitMetadata) == gitMetadata else {
            throw ProbeError.validation("fixture root does not contain owned Git metadata")
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

    static func panelShapedIndices(windows: [ElementDescription]) -> [Int] {
        windows.indices.filter { index in
            let window = windows[index]
            guard (window.role == kAXWindowRole && window.subrole == kAXStandardWindowSubrole) ||
                    window.role == kAXSheetRole else { return false }
            let descendants = [window] + window.descendants
            let hasCancel = descendants.contains {
                $0.role == kAXButtonRole && $0.title?.lowercased() == "cancel"
            }
            let hasChooser = descendants.contains {
                $0.role == kAXButtonRole && chooserTitles.contains($0.title?.lowercased() ?? "")
            }
            let hasFileBrowser = descendants.contains {
                [kAXOutlineRole, kAXBrowserRole, kAXTableRole].contains($0.role)
            }
            return [hasCancel, hasChooser, hasFileBrowser].filter { $0 }.count >= 2
        }
    }

    static func uniqueElements(_ root: ElementDescription, where predicate: (ElementDescription) -> Bool)
        throws -> [ElementDescription] {
        let matches = ([root] + root.descendants).filter(predicate)
        return matches
    }

    static func plan(windows: [ElementDescription], permitKeyFallback: Bool) throws -> SelectionPlan {
        let shapedIndices = panelShapedIndices(windows: windows)
        guard shapedIndices.count == 1, let candidateIndex = shapedIndices.first else {
            throw ProbeError.validation(
                "expected exactly one panel-shaped Open candidate; found \(shapedIndices.count)")
        }
        let panel = windows[candidateIndex]
        let all = [panel] + panel.descendants
        let cancels = all.filter {
            $0.role == kAXButtonRole && $0.title?.lowercased() == "cancel" &&
                $0.actions.contains(kAXPressAction)
        }
        let choosers = all.filter {
            $0.role == kAXButtonRole && chooserTitles.contains($0.title?.lowercased() ?? "") &&
                $0.actions.contains(kAXPressAction)
        }
        let fileBrowsers = all.filter {
            [kAXOutlineRole, kAXBrowserRole, kAXTableRole].contains($0.role)
        }
        guard cancels.count == 1, choosers.count == 1, !fileBrowsers.isEmpty,
              let chooser = choosers.first, let title = chooser.title else {
            throw ProbeError.validation(
                "malformed Open panel controls: cancel=\(min(cancels.count, 2)) " +
                "chooser=\(min(choosers.count, 2)) browser=\(min(fileBrowsers.count, 2))")
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

enum OpenFolderMenuPolicy {
    enum PendingState: String, Equatable {
        case applicationMenuBar = "application menu bar is unpublished"
        case fileMenuItem = "File menu item is unpublished"
        case fileAXMenu = "File AX menu is unpublished"
        case openFolderMenuItem = "Open Folder menu item is unpublished"
        case commandCharacter = "command character is unpublished"
        case emptyCommandCharacter = "command character is empty"
        case commandModifiers = "command modifiers are unpublished"
    }

    enum Readiness: Equatable {
        case pending(PendingState)
        case ready(OpenFolderMenuPlan)
    }

    static let parentTitle = "File"
    static let itemTitle = "Open Folder…"
    static let commandVirtualKey = 31
    static let commandModifiers = 0 // Command is implicit when AXNoCommand is absent.

    static func plan(menuBar: ElementDescription) throws -> OpenFolderMenuPlan {
        guard let plan = try readinessPlan(menuBar: menuBar) else {
            throw ProbeError.validation("direct File to Open Folder menu path is absent")
        }
        return plan
    }

    static func readinessPlan(menuBar: ElementDescription) throws -> OpenFolderMenuPlan? {
        switch try readiness(menuBar: menuBar) {
        case .pending: return nil
        case .ready(let plan): return plan
        }
    }

    static func readiness(menuBar: ElementDescription) throws -> Readiness {
        guard menuBar.role == kAXMenuBarRole else {
            throw ProbeError.validation("application AX menu bar has the wrong role")
        }
        let parentIndices = menuBar.children.indices.filter {
            menuBar.children[$0].title == parentTitle
        }
        guard !parentIndices.isEmpty else { return .pending(.fileMenuItem) }
        guard parentIndices.count == 1, let parentIndex = parentIndices.first else {
            throw ProbeError.validation(
                "expected exactly one direct File menu path; found \(min(parentIndices.count, 2))")
        }
        let parent = menuBar.children[parentIndex]
        guard parent.role == kAXMenuBarItemRole else {
            throw ProbeError.validation("Open Folder parent has the wrong AX role")
        }
        guard !parent.children.isEmpty else { return .pending(.fileAXMenu) }
        let menuIndices = parent.children.indices.filter {
            parent.children[$0].role == kAXMenuRole
        }
        guard parent.children.count == 1, menuIndices.count == 1,
              let menuIndex = menuIndices.first else {
            throw ProbeError.validation("Open Folder parent does not expose exactly one direct AX menu")
        }
        let menu = parent.children[menuIndex]
        guard !menu.children.isEmpty else { return .pending(.openFolderMenuItem) }
        let itemIndices = menu.children.indices.filter {
            menu.children[$0].title == itemTitle
        }
        guard !itemIndices.isEmpty else { return .pending(.openFolderMenuItem) }
        guard itemIndices.count == 1, let itemIndex = itemIndices.first else {
            throw ProbeError.validation(
                "expected exactly one direct Open Folder menu item; found \(min(itemIndices.count, 2))")
        }
        let item = menu.children[itemIndex]
        guard item.role == kAXMenuItemRole else {
            throw ProbeError.validation("Open Folder has the wrong AX role")
        }
        guard item.enabled == true else {
            throw ProbeError.validation("Open Folder menu item is not enabled")
        }
        guard item.actions.contains(kAXPressAction) else {
            throw ProbeError.validation("Open Folder menu item does not advertise AXPress")
        }
        let commandCharacter: String
        switch item.menuCommandCharacter {
        case .missing: return .pending(.commandCharacter)
        case .malformed:
            throw ProbeError.validation(
                "Open Folder menu item has malformed command character type")
        case .readFailure(let status):
            throw ProbeError.unavailable(
                "Open Folder menu item command character read failed: \(status)")
        case .value(let value):
            guard !value.isEmpty else { return .pending(.emptyCommandCharacter) }
            commandCharacter = value
        }
        let menuCommandVirtualKey: Int?
        switch item.menuCommandVirtualKey {
        case .missing: menuCommandVirtualKey = nil
        case .malformed:
            throw ProbeError.validation(
                "Open Folder menu item has malformed command virtual key type")
        case .readFailure(let status):
            throw ProbeError.unavailable(
                "Open Folder menu item command virtual key read failed: \(status)")
        case .value(let value):
            guard value == commandVirtualKey else {
                throw ProbeError.validation(
                    "Open Folder menu item has unexpected command virtual key: \(value)")
            }
            menuCommandVirtualKey = value
        }
        let menuCommandModifiers: Int
        switch item.menuCommandModifiers {
        case .missing: return .pending(.commandModifiers)
        case .malformed:
            throw ProbeError.validation(
                "Open Folder menu item has malformed command modifiers type")
        case .readFailure(let status):
            throw ProbeError.unavailable(
                "Open Folder menu item command modifiers read failed: \(status)")
        case .value(let value): menuCommandModifiers = value
        }
        guard commandCharacter.lowercased() == "o" else {
            throw ProbeError.validation(
                "Open Folder menu item has unexpected command character: \(commandCharacter)")
        }
        guard menuCommandModifiers == commandModifiers else {
            throw ProbeError.validation(
                "Open Folder menu item has unexpected command modifiers: \(menuCommandModifiers)")
        }
        return .ready(OpenFolderMenuPlan(
            menuBarItemIndex: parentIndex, menuIndex: menuIndex,
            menuItemIndex: itemIndex, parentTitle: parent.title!, itemTitle: itemTitle,
            commandVirtualKey: menuCommandVirtualKey))
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
    var pressOpenFolderMenuItem = false
    var validateInputsOnly = false
    var index = 0
    let orderedValueOptions = ["--pid", "--run-root", "--expected-bundle", "--expected-executable",
                               "--fixture-root", "--phase", "--event-log"]
    let valueOptions = Set(orderedValueOptions)
    while index < arguments.count {
        let argument = arguments[index]
        if argument == "--permit-key-fallback" {
            guard !permitKeyFallback else { throw ProbeError.usage("duplicate --permit-key-fallback") }
            permitKeyFallback = true
            index += 1
        } else if argument == "--press-open-folder-menu-item" {
            guard !pressOpenFolderMenuItem else {
                throw ProbeError.usage("duplicate --press-open-folder-menu-item")
            }
            pressOpenFolderMenuItem = true
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
    for option in orderedValueOptions where values[option] == nil {
        throw ProbeError.usage("missing required option: \(option)")
    }
    guard let rawPID = values["--pid"], let numericPID = Int32(rawPID), numericPID > 1 else {
        throw ProbeError.usage("--pid must be an integer greater than one")
    }
    guard let rawPhase = values["--phase"], let phase = Phase(rawValue: rawPhase) else {
        throw ProbeError.usage(
            "--phase must be inspect-open-folder-menu, inspect-project-picker, or select-project")
    }
    if phase == .selectProject && !pressOpenFolderMenuItem {
        throw ProbeError.usage("select-project requires --press-open-folder-menu-item")
    }
    if phase != .selectProject && pressOpenFolderMenuItem {
        throw ProbeError.usage("--press-open-folder-menu-item is only valid for select-project")
    }
    return Options(pid: numericPID, runRoot: values["--run-root"]!,
                   expectedBundle: values["--expected-bundle"]!,
                   expectedExecutable: values["--expected-executable"]!,
                   fixtureRoot: values["--fixture-root"]!, phase: phase,
                   eventLog: values["--event-log"]!, permitKeyFallback: permitKeyFallback,
                   pressOpenFolderMenuItem: pressOpenFolderMenuItem,
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

func requireProcessIdentity(_ actual: ProcessIdentity, matches expected: ProcessIdentity) throws {
    guard actual == expected else {
        throw ProbeError.validation("PID, process start, or kernel executable changed")
    }
}

func registrationIsReady(
    _ sample: AppKitRegistrationSample,
    expectedBundle: String,
    expectedExecutable: String
) throws -> Bool {
    switch sample {
    case .unavailable:
        return false
    case .published(let isTerminated, let bundlePath, let executablePath):
        guard !isTerminated else {
            throw ProbeError.validation("AppKit published a terminated process")
        }
        if let bundlePath, bundlePath != expectedBundle {
            throw ProbeError.validation("AppKit published an unexpected bundle path")
        }
        if let executablePath, executablePath != expectedExecutable {
            throw ProbeError.validation("AppKit published an unexpected executable path")
        }
        return bundlePath != nil && executablePath != nil
    }
}

// BEGIN_PROCESS_REGISTRATION_VALIDATION
func verifyProcessRegistration(
    timeoutNanoseconds: UInt64,
    pollMicroseconds: useconds_t,
    expectedBundle: String,
    expectedExecutable: String,
    nowNanoseconds: () throws -> UInt64,
    validateIdentity: () throws -> Void,
    readSample: () throws -> AppKitRegistrationSample,
    validateFinal: () throws -> Void,
    pause: (useconds_t) -> Void
) throws -> ProcessRegistrationReadiness {
    precondition(timeoutNanoseconds > 0 && pollMicroseconds > 0)
    let started = try nowNanoseconds()
    let addition = started.addingReportingOverflow(timeoutNanoseconds)
    let deadline = addition.overflow ? UInt64.max : addition.partialValue
    var pollCount = 0
    while try nowNanoseconds() < deadline {
        try validateIdentity()
        let sample = try readSample()
        try validateIdentity()
        pollCount += 1
        if try registrationIsReady(sample, expectedBundle: expectedBundle,
                                   expectedExecutable: expectedExecutable) {
            try validateIdentity()
            try validateFinal()
            try validateIdentity()
            return ProcessRegistrationReadiness(pollCount: pollCount)
        }
        let current = try nowNanoseconds()
        guard current < deadline else { break }
        let remainingMicroseconds = (deadline - current + 999) / 1_000
        pause(useconds_t(min(UInt64(pollMicroseconds), remainingMicroseconds)))
    }
    throw ProbeError.unavailable(
        "AppKit process registration did not become ready after \(pollCount) polls")
}

func appKitRegistrationSample(pid: pid_t) throws -> AppKitRegistrationSample {
    guard let running = NSRunningApplication(processIdentifier: pid) else {
        return .unavailable
    }
    let bundlePath = try running.bundleURL.map {
        try PathPolicy.canonicalExisting($0.path)
    }
    let executablePath = try running.executableURL.map {
        try PathPolicy.canonicalExisting($0.path)
    }
    return .published(isTerminated: running.isTerminated,
                      bundlePath: bundlePath, executablePath: executablePath)
}

func validateCodeIdentity(pid: pid_t, paths: ValidatedPaths) throws {
    var staticCode: SecStaticCode?
    guard SecStaticCodeCreateWithPath(URL(fileURLWithPath: paths.bundle) as CFURL, [],
                                      &staticCode) == errSecSuccess,
          let staticCode else { throw ProbeError.validation("cannot create static code identity") }
    guard SecStaticCodeCheckValidity(
        staticCode, SecCSFlags(rawValue: kSecCSCheckAllArchitectures), nil) == errSecSuccess else {
        throw ProbeError.validation("copied bundle signature is invalid")
    }
    let attributes = [kSecGuestAttributePid as String: NSNumber(value: pid)] as CFDictionary
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
}

func verifyProcess(options: Options, paths: ValidatedPaths) throws -> ProcessVerification {
    let before = try processIdentity(options.pid)
    guard try PathPolicy.canonicalExisting(before.executable) == paths.executable else {
        throw ProbeError.validation("PID executable does not match expected copied executable")
    }
    let readiness = try verifyProcessRegistration(
        timeoutNanoseconds: 5_000_000_000,
        pollMicroseconds: 100_000,
        expectedBundle: paths.bundle,
        expectedExecutable: paths.executable,
        nowNanoseconds: monotonicNanoseconds,
        validateIdentity: {
            try requireProcessIdentity(try processIdentity(options.pid), matches: before)
        },
        readSample: { try appKitRegistrationSample(pid: options.pid) },
        validateFinal: { try validateCodeIdentity(pid: options.pid, paths: paths) },
        pause: { _ = usleep($0) })
    return ProcessVerification(identity: before, registrationPollCount: readiness.pollCount)
}
// END_PROCESS_REGISTRATION_VALIDATION

func attribute(_ element: AXUIElement, _ name: CFString) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name, &value) == .success else { return nil }
    return value
}

func stringAttribute(_ element: AXUIElement, _ name: CFString) -> String? {
    attribute(element, name) as? String
}

func boolAttribute(_ element: AXUIElement, _ name: CFString) -> Bool? {
    attribute(element, name) as? Bool
}

func intAttribute(_ element: AXUIElement, _ name: CFString) -> Int? {
    (attribute(element, name) as? NSNumber)?.intValue
}

func stringAttributePublication(
    _ element: AXUIElement,
    _ name: CFString
) -> AttributePublication<String> {
    var value: CFTypeRef?
    let status = AXUIElementCopyAttributeValue(element, name, &value)
    return attributePublication(status: status) { stringPublication(value) }
}

func attributePublication<Value: Equatable>(
    status: AXError,
    published: () -> AttributePublication<Value>
) -> AttributePublication<Value> {
    if status == .success { return published() }
    if status == .attributeUnsupported || status == .noValue { return .missing }
    return .readFailure(status.rawValue)
}

func stringPublication(_ value: CFTypeRef?) -> AttributePublication<String> {
    guard let value = value as? String else { return .malformed }
    return .value(value)
}

func integralIntPublication(_ value: CFTypeRef?) -> AttributePublication<Int> {
    guard let value, CFGetTypeID(value) == CFNumberGetTypeID() else {
        return .malformed
    }
    let number = unsafeBitCast(value, to: CFNumber.self)
    guard !CFNumberIsFloatType(number) else { return .malformed }
    var converted: Int64 = 0
    guard CFNumberGetValue(number, .sInt64Type, &converted) else { return .malformed }
    return .value(Int(converted))
}

func intAttributePublication(
    _ element: AXUIElement,
    _ name: CFString
) -> AttributePublication<Int> {
    var value: CFTypeRef?
    let status = AXUIElementCopyAttributeValue(element, name, &value)
    return attributePublication(status: status) { integralIntPublication(value) }
}

func fileURLPathPublication(_ value: CFTypeRef?) -> AttributePublication<String> {
    let url: URL?
    if let value, CFGetTypeID(value) == CFURLGetTypeID() {
        url = unsafeBitCast(value, to: CFURL.self) as URL
    } else if let publishedString = value as? String {
        url = URL(string: publishedString)
    } else {
        return .malformed
    }
    guard let url, url.isFileURL,
          let canonical = try? PathPolicy.canonicalExisting(
            url.standardizedFileURL.path) else { return .malformed }
    return .value(canonical)
}

func fileURLPathAttributePublication(
    _ element: AXUIElement,
    _ name: CFString
) -> AttributePublication<String> {
    var value: CFTypeRef?
    let status = AXUIElementCopyAttributeValue(element, name, &value)
    return attributePublication(status: status) { fileURLPathPublication(value) }
}

func publishedDestinationPath(
    _ publication: AttributePublication<String>,
    attributeName: String
) throws -> String? {
    switch publication {
    case .missing: return nil
    case .value(let path): return path
    case .malformed:
        throw ProbeError.validation(
            "Open panel \(attributeName) destination is malformed")
    case .readFailure(let status):
        throw ProbeError.unavailable(
            "Open panel \(attributeName) destination read failed: \(status)")
    }
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
    return describeShallow(element, children: children)
}

func describeShallow(
    _ element: AXUIElement,
    children: [ElementDescription] = []
) -> ElementDescription {
    ElementDescription(role: stringAttribute(element, kAXRoleAttribute as CFString) ?? "",
                       subrole: stringAttribute(element, kAXSubroleAttribute as CFString),
                       identifier: stringAttribute(element, kAXIdentifierAttribute as CFString),
                       title: stringAttribute(element, kAXTitleAttribute as CFString),
                       help: stringAttribute(element, kAXHelpAttribute as CFString),
                       enabled: boolAttribute(element, kAXEnabledAttribute as CFString),
                       menuCommandCharacter: stringAttributePublication(
                        element, kAXMenuItemCmdCharAttribute as CFString),
                       menuCommandVirtualKey: intAttributePublication(
                        element, kAXMenuItemCmdVirtualKeyAttribute as CFString),
                       menuCommandModifiers: intAttributePublication(
                        element, kAXMenuItemCmdModifiersAttribute as CFString),
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

func monotonicNanoseconds() throws -> UInt64 {
    var value = timespec()
    guard clock_gettime(CLOCK_MONOTONIC, &value) == 0 else {
        throw ProbeError.unavailable("monotonic clock unavailable")
    }
    return UInt64(value.tv_sec) * 1_000_000_000 + UInt64(value.tv_nsec)
}

struct OpenPanelReadiness<Element> {
    let panel: Element
    let plan: SelectionPlan
    let initialElements: [Element]
    let pollCount: Int
}

// BEGIN_READ_ONLY_OPEN_PANEL_WAIT
func waitForUniqueOpenPanel<Element>(
    timeoutNanoseconds: UInt64,
    pollMicroseconds: useconds_t,
    permitKeyFallback: Bool,
    nowNanoseconds: () throws -> UInt64,
    validateIdentity: () throws -> Void,
    readWindows: () throws -> [(Element, ElementDescription)],
    sameElement: (Element, Element) -> Bool,
    pause: (useconds_t) -> Void
) throws -> OpenPanelReadiness<Element> {
    precondition(timeoutNanoseconds > 0 && pollMicroseconds > 0)
    let started = try nowNanoseconds()
    let addition = started.addingReportingOverflow(timeoutNanoseconds)
    let deadline = addition.overflow ? UInt64.max : addition.partialValue
    var pollCount = 0
    while try nowNanoseconds() < deadline {
        try validateIdentity()
        let windows = try readWindows()
        try validateIdentity()
        pollCount += 1
        let descriptions = windows.map(\.1)
        let shapedIndices = OpenPanelPolicy.panelShapedIndices(windows: descriptions)
        guard shapedIndices.count <= 1 else {
            throw ProbeError.validation(
                "expected exactly one panel-shaped Open candidate; found \(shapedIndices.count)")
        }
        if let selectedIndex = shapedIndices.first {
            let plan = try OpenPanelPolicy.plan(
                windows: [descriptions[selectedIndex]], permitKeyFallback: permitKeyFallback)
            let selected = windows[selectedIndex].0
            guard windows.filter({ sameElement($0.0, selected) }).count == 1 else {
                throw ProbeError.validation("live Open panel identity is ambiguous")
            }
            return OpenPanelReadiness(panel: selected, plan: plan,
                                      initialElements: windows.map(\.0), pollCount: pollCount)
        }
        let current = try nowNanoseconds()
        guard current < deadline else { break }
        let remainingMicroseconds = (deadline - current + 999) / 1_000
        pause(useconds_t(min(UInt64(pollMicroseconds), remainingMicroseconds)))
    }
    throw ProbeError.unavailable("Open panel did not become ready after \(pollCount) polls")
}

func waitForValidatedOpenPanel(
    application: AXUIElement,
    identity: ProcessIdentity,
    permitKeyFallback: Bool
) throws -> OpenPanelReadiness<AXUIElement> {
    try waitForUniqueOpenPanel(
        timeoutNanoseconds: 5_000_000_000,
        pollMicroseconds: 100_000,
        permitKeyFallback: permitKeyFallback,
        nowNanoseconds: monotonicNanoseconds,
        validateIdentity: { try requireSameProcess(identity) },
        readWindows: { try liveWindows(application) },
        sameElement: sameAXElement,
        pause: { _ = usleep($0) })
}
// END_READ_ONLY_OPEN_PANEL_WAIT

func requireSameProcess(_ expected: ProcessIdentity) throws {
    guard try processIdentity(expected.pid) == expected else {
        throw ProbeError.validation("PID identity changed before AX mutation")
    }
}

func press(_ element: AXUIElement, purpose: String, process: ProcessIdentity) throws {
    try requireSameProcess(process)
    guard actions(element).contains(kAXPressAction) else {
        throw ProbeError.validation("\(purpose) does not advertise AXPress")
    }
    guard AXUIElementPerformAction(element, kAXPressAction as CFString) == .success else {
        throw ProbeError.unavailable("AXPress failed for \(purpose)")
    }
    try requireSameProcess(process)
}

// BEGIN_PID_PATH_ENTRY_SHORTCUT
struct KeyboardShortcut: Equatable {
    let virtualKey: CGKeyCode
    let flags: CGEventFlags

    static let pathEntry = KeyboardShortcut(virtualKey: 5, flags: [.maskCommand, .maskShift])
}

func postKeyboardShortcut(_ shortcut: KeyboardShortcut, to process: ProcessIdentity) throws {
    guard let source = CGEventSource(stateID: .privateState),
          let down = CGEvent(keyboardEventSource: source, virtualKey: shortcut.virtualKey,
                             keyDown: true),
          let up = CGEvent(keyboardEventSource: source, virtualKey: shortcut.virtualKey,
                           keyDown: false) else {
        throw ProbeError.unavailable("cannot create audited PID-targeted keyboard events")
    }
    down.flags = shortcut.flags
    up.flags = shortcut.flags
    try requireSameProcess(process)
    down.postToPid(process.pid)
    up.postToPid(process.pid)
    try requireSameProcess(process)
}

func postCommandShiftG(to process: ProcessIdentity) throws {
    try postKeyboardShortcut(.pathEntry, to: process)
}
// END_PID_PATH_ENTRY_SHORTCUT

func sameAXElement(_ left: AXUIElement, _ right: AXUIElement) -> Bool {
    CFEqual(left, right)
}

func axElementAttribute(_ element: AXUIElement, _ name: CFString) -> AXUIElement? {
    guard let value = attribute(element, name),
          CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
    return unsafeBitCast(value, to: AXUIElement.self)
}

// BEGIN_PID_OPEN_FOLDER_MENU_PRESS
struct OpenFolderMenuSnapshot<Element> {
    let description: ElementDescription
    let items: [OpenFolderMenuPlan: Element]
}

struct OpenFolderMenuReadiness<Element> {
    let plan: OpenFolderMenuPlan
    let item: Element
    let pollCount: Int
}

func openFolderMenuSnapshot(application: AXUIElement) throws
    -> OpenFolderMenuSnapshot<AXUIElement>? {
    guard let menuBar = axElementAttribute(application, kAXMenuBarAttribute as CFString) else {
        return nil
    }
    let menuBarItems = childElements(menuBar)
    guard menuBarItems.count <= 64 else {
        throw ProbeError.validation("application AX menu bar exceeds the 64-item bound")
    }
    var liveItems: [OpenFolderMenuPlan: AXUIElement] = [:]
    let parentDescriptions = try menuBarItems.enumerated().map { parentIndex, parent in
        let parentTitle = stringAttribute(parent, kAXTitleAttribute as CFString)
        guard parentTitle == OpenFolderMenuPolicy.parentTitle else {
            return describeShallow(parent)
        }
        let menus = childElements(parent)
        guard menus.count <= 4 else {
            throw ProbeError.validation("Open Folder parent exceeds the four-menu bound")
        }
        let menuDescriptions = try menus.enumerated().map { menuIndex, menu in
            let menuItems = childElements(menu)
            guard menuItems.count <= 128 else {
                throw ProbeError.validation("Open Folder menu exceeds the 128-item bound")
            }
            let itemDescriptions = menuItems.enumerated().map { itemIndex, item in
                let description = describeShallow(item)
                if description.title == OpenFolderMenuPolicy.itemTitle,
                   let parentTitle {
                    let commandVirtualKey: Int?
                    switch description.menuCommandVirtualKey {
                    case .value(let value): commandVirtualKey = value
                    case .missing, .malformed, .readFailure: commandVirtualKey = nil
                    }
                    let path = OpenFolderMenuPlan(
                        menuBarItemIndex: parentIndex, menuIndex: menuIndex,
                        menuItemIndex: itemIndex, parentTitle: parentTitle,
                        itemTitle: OpenFolderMenuPolicy.itemTitle,
                        commandVirtualKey: commandVirtualKey)
                    liveItems[path] = item
                }
                return description
            }
            return describeShallow(menu, children: itemDescriptions)
        }
        return describeShallow(parent, children: menuDescriptions)
    }
    return OpenFolderMenuSnapshot<AXUIElement>(
        description: describeShallow(menuBar, children: parentDescriptions),
        items: liveItems)
}

// BEGIN_READ_ONLY_OPEN_FOLDER_MENU_WAIT
func waitForReadyOpenFolderMenu<Element>(
    timeoutNanoseconds: UInt64,
    pollMicroseconds: useconds_t,
    nowNanoseconds: () throws -> UInt64,
    validateIdentity: () throws -> Void,
    readSnapshot: () throws -> OpenFolderMenuSnapshot<Element>?,
    pause: (useconds_t) -> Void
) throws -> OpenFolderMenuReadiness<Element> {
    precondition(timeoutNanoseconds > 0 && pollMicroseconds > 0)
    let started = try nowNanoseconds()
    let addition = started.addingReportingOverflow(timeoutNanoseconds)
    let deadline = addition.overflow ? UInt64.max : addition.partialValue
    var pollCount = 0
    var lastPendingState: OpenFolderMenuPolicy.PendingState?
    while try nowNanoseconds() < deadline {
        try validateIdentity()
        let snapshot = try readSnapshot()
        try validateIdentity()
        pollCount += 1
        guard let snapshot else {
            lastPendingState = .applicationMenuBar
            let current = try nowNanoseconds()
            guard current < deadline else { break }
            let remainingMicroseconds = (deadline - current + 999) / 1_000
            pause(useconds_t(min(UInt64(pollMicroseconds), remainingMicroseconds)))
            continue
        }
        switch try OpenFolderMenuPolicy.readiness(menuBar: snapshot.description) {
        case .pending(let state): lastPendingState = state
        case .ready(let plan):
            guard let item = snapshot.items[plan] else {
                throw ProbeError.validation(
                    "validated Open Folder path has no unique live AX identity")
            }
            return OpenFolderMenuReadiness(plan: plan, item: item, pollCount: pollCount)
        }
        let current = try nowNanoseconds()
        guard current < deadline else { break }
        let remainingMicroseconds = (deadline - current + 999) / 1_000
        pause(useconds_t(min(UInt64(pollMicroseconds), remainingMicroseconds)))
    }
    let pendingSuffix = lastPendingState.map {
        "; last pending state: \($0.rawValue)"
    } ?? ""
    throw ProbeError.unavailable(
        "Open Folder menu did not become ready after \(pollCount) polls\(pendingSuffix)")
}

func waitForValidatedOpenFolderMenu(
    application: AXUIElement,
    process: ProcessIdentity
) throws -> OpenFolderMenuReadiness<AXUIElement> {
    try waitForReadyOpenFolderMenu(
        timeoutNanoseconds: 5_000_000_000,
        pollMicroseconds: 100_000,
        nowNanoseconds: monotonicNanoseconds,
        validateIdentity: { try requireSameProcess(process) },
        readSnapshot: { try openFolderMenuSnapshot(application: application) },
        pause: { _ = usleep($0) })
}
// END_READ_ONLY_OPEN_FOLDER_MENU_WAIT

func pressOpenFolderMenuItem(
    application: AXUIElement,
    process: ProcessIdentity
) throws -> OpenFolderMenuReadiness<AXUIElement> {
    let readiness = try waitForValidatedOpenFolderMenu(
        application: application, process: process)
    guard let snapshot = try openFolderMenuSnapshot(application: application),
          let revalidatedPlan = try OpenFolderMenuPolicy.readinessPlan(
            menuBar: snapshot.description),
          let menuItem = snapshot.items[revalidatedPlan],
          revalidatedPlan == readiness.plan,
          sameAXElement(menuItem, readiness.item) else {
        throw ProbeError.validation("Open Folder menu topology changed before AXPress")
    }
    try requireSameProcess(process)
    let pressStatus = AXUIElementPerformAction(menuItem, kAXPressAction as CFString)
    guard pressStatus == .success else {
        throw ProbeError.unavailable("AXPress failed for Open Folder menu item")
    }
    try requireSameProcess(process)
    return readiness
}
// END_PID_OPEN_FOLDER_MENU_PRESS

struct PathEntrySnapshot<Element> {
    let panel: Element
    let cancel: Element
    let chooser: Element
    let chooserEnabled: Bool
    let additionalCancelCount: Int
    let browserAnchors: [Element]
    let textFields: [Element]
    let goButtons: [Element]
    let restoredPlan: SelectionPlan?
    let destinationPaths: [String]
}

struct PathEntryCandidate<Element> {
    let field: Element
    let goButton: Element
}

struct PathEntryToken<Element> {
    let panel: Element
    let field: Element
    let goButton: Element
    let pollCount: Int
}

struct RestoredOpenPanelToken<Element> {
    let panel: Element
    let chooser: Element
    let pollCount: Int
}

func identityDeduplicated<Element>(
    _ elements: [Element],
    sameElement: (Element, Element) -> Bool
) -> [Element] {
    var result: [Element] = []
    for element in elements where !result.contains(where: { sameElement($0, element) }) {
        result.append(element)
    }
    return result
}

func sameIdentitySet<Element>(
    _ left: [Element],
    _ right: [Element],
    sameElement: (Element, Element) -> Bool
) -> Bool {
    let uniqueLeft = identityDeduplicated(left, sameElement: sameElement)
    let uniqueRight = identityDeduplicated(right, sameElement: sameElement)
    return uniqueLeft.count == uniqueRight.count && uniqueLeft.allSatisfy { element in
        uniqueRight.contains(where: { sameElement(element, $0) })
    }
}

func exactAnchor<Element>(
    expected: Element?,
    candidates: [Element],
    allowAdditional: Bool,
    sameElement: (Element, Element) -> Bool
) throws -> Element {
    guard let expected else {
        guard candidates.count == 1, let candidate = candidates.first else {
            throw ProbeError.validation("Open-panel anchor is missing or ambiguous")
        }
        return candidate
    }
    let matches = candidates.filter { sameElement($0, expected) }
    guard matches.count == 1, allowAdditional || candidates.count == 1 else {
        throw ProbeError.validation("original Open-panel anchor identity changed")
    }
    return expected
}

enum PathEntryPolicy {
    static func navigation(searchableFieldText: [String]) throws
        -> SelectionPlan.Navigation {
        let directCount = searchableFieldText.filter { text in
            let lowered = text.lowercased()
            return lowered.contains("path") || lowered.contains("location") ||
                lowered.contains("folder")
        }.count
        if directCount == 1 { return .direct }
        guard directCount == 0 else {
            throw ProbeError.validation("Open panel path field is ambiguous")
        }
        return .commandShiftG
    }

    static func isGoControl(
        role: String?,
        title: String?,
        enabled: Bool?,
        actions: Set<String>
    ) throws -> Bool {
        guard title?.lowercased() == "go" else { return false }
        guard role == kAXButtonRole, enabled == true,
              actions.contains(kAXPressAction) else {
            throw ProbeError.validation(
                "path-entry Go control is not an enabled AXPress button")
        }
        return true
    }

    static func baseline<Element>(
        _ snapshot: PathEntrySnapshot<Element>,
        expectedPlan: SelectionPlan,
        sameElement: (Element, Element) -> Bool
    ) throws -> PathEntrySnapshot<Element> {
        guard !snapshot.browserAnchors.isEmpty else {
            throw ProbeError.validation("Open panel has no file-browser anchor")
        }
        guard snapshot.chooserEnabled else {
            throw ProbeError.validation("Open panel chooser is not enabled at baseline")
        }
        guard snapshot.goButtons.isEmpty else {
            throw ProbeError.validation("Open panel exposed Go before the path-entry shortcut")
        }
        guard snapshot.restoredPlan == expectedPlan else {
            throw ProbeError.validation("Open panel baseline plan changed before shortcut")
        }
        return snapshot
    }

    static func requireAnchors<Element>(
        baseline: PathEntrySnapshot<Element>,
        current: PathEntrySnapshot<Element>,
        sameElement: (Element, Element) -> Bool
    ) throws {
        guard sameElement(baseline.panel, current.panel),
              sameElement(baseline.cancel, current.cancel),
              sameElement(baseline.chooser, current.chooser),
              sameIdentitySet(baseline.browserAnchors, current.browserAnchors,
                              sameElement: sameElement) else {
            throw ProbeError.validation("original Open-panel identity or anchors changed")
        }
        let baselineFields = identityDeduplicated(
            baseline.textFields, sameElement: sameElement)
        let currentFields = identityDeduplicated(
            current.textFields, sameElement: sameElement)
        guard baselineFields.allSatisfy({ baselineField in
            currentFields.contains(where: { sameElement(baselineField, $0) })
        }) else {
            throw ProbeError.validation("Open-panel baseline field identity changed")
        }
    }

    static func candidate<Element>(
        baseline: PathEntrySnapshot<Element>,
        current: PathEntrySnapshot<Element>,
        sameElement: (Element, Element) -> Bool
    ) throws -> PathEntryCandidate<Element>? {
        try requireAnchors(baseline: baseline, current: current,
                           sameElement: sameElement)
        let fields = identityDeduplicated(current.textFields, sameElement: sameElement)
        let baselineFields = identityDeduplicated(
            baseline.textFields, sameElement: sameElement)
        let newFields = fields.filter { field in
            !baselineFields.contains(where: { sameElement(field, $0) })
        }
        let goButtons = identityDeduplicated(current.goButtons, sameElement: sameElement)
        guard newFields.count <= 1 else {
            throw ProbeError.validation("multiple new path-entry fields appeared")
        }
        guard goButtons.count <= 1 else {
            throw ProbeError.validation("multiple new path-entry Go buttons appeared")
        }
        guard let field = newFields.first, let goButton = goButtons.first else {
            return nil
        }
        return PathEntryCandidate(field: field, goButton: goButton)
    }

    static func restored<Element>(
        baseline: PathEntrySnapshot<Element>,
        current: PathEntrySnapshot<Element>,
        expectedDestination: String,
        sameElement: (Element, Element) -> Bool
    ) throws -> Bool {
        try requireAnchors(baseline: baseline, current: current,
                           sameElement: sameElement)
        let fieldsMatch = sameIdentitySet(
            baseline.textFields, current.textFields, sameElement: sameElement)
        let destinations = Array(Set(current.destinationPaths))
        guard destinations.count <= 1 else {
            throw ProbeError.validation("Open panel published ambiguous destination paths")
        }
        return fieldsMatch && current.goButtons.isEmpty && current.chooserEnabled &&
            current.additionalCancelCount == 0 &&
            current.restoredPlan == baseline.restoredPlan &&
            destinations == [expectedDestination]
    }
}

func uniqueOriginal<T>(
    original: T,
    candidates: [T],
    equals: (T, T) -> Bool
) throws -> T {
    guard candidates.count == 1, let candidate = candidates.first,
          equals(original, candidate) else {
        throw ProbeError.validation("original Open panel was replaced or became ambiguous")
    }
    return original
}

func uniqueNewRelated<T>(
    initial: [T],
    candidates: [T],
    equals: (T, T) -> Bool
) throws -> T? {
    var deduplicated: [T] = []
    for candidate in candidates where !deduplicated.contains(where: { equals($0, candidate) }) {
        deduplicated.append(candidate)
    }
    let newCandidates = deduplicated.filter { candidate in
        !initial.contains(where: { equals($0, candidate) })
    }
    guard newCandidates.count <= 1 else {
        throw ProbeError.validation("multiple new path-entry children appeared")
    }
    return newCandidates.first
}

// BEGIN_READ_ONLY_PATH_ENTRY_WAITS
func waitForUniquePathEntry<Element>(
    timeoutNanoseconds: UInt64,
    pollMicroseconds: useconds_t,
    baseline: PathEntrySnapshot<Element>,
    nowNanoseconds: () throws -> UInt64,
    validateIdentity: () throws -> Void,
    readSnapshot: () throws -> PathEntrySnapshot<Element>,
    sameElement: (Element, Element) -> Bool,
    pause: (useconds_t) -> Void
) throws -> PathEntryToken<Element> {
    precondition(timeoutNanoseconds > 0 && pollMicroseconds > 0)
    let started = try nowNanoseconds()
    let addition = started.addingReportingOverflow(timeoutNanoseconds)
    let deadline = addition.overflow ? UInt64.max : addition.partialValue
    var pollCount = 0
    var lastPendingState = "unpublished"
    while try nowNanoseconds() < deadline {
        try validateIdentity()
        let snapshot = try readSnapshot()
        try validateIdentity()
        pollCount += 1
        if let candidate = try PathEntryPolicy.candidate(
            baseline: baseline, current: snapshot, sameElement: sameElement) {
            return PathEntryToken(panel: baseline.panel, field: candidate.field,
                                  goButton: candidate.goButton, pollCount: pollCount)
        }
        let baselineFields = identityDeduplicated(
            baseline.textFields, sameElement: sameElement)
        let currentFields = identityDeduplicated(
            snapshot.textFields, sameElement: sameElement)
        let newFieldCount = currentFields.filter { field in
            !baselineFields.contains(where: { sameElement(field, $0) })
        }.count
        lastPendingState =
            "new-fields=\(min(newFieldCount, 2)) " +
            "go-buttons=\(min(snapshot.goButtons.count, 2)) " +
            "extra-cancels=\(min(snapshot.additionalCancelCount, 2)) " +
            "chooser-enabled=\(snapshot.chooserEnabled)"
        let current = try nowNanoseconds()
        guard current < deadline else { break }
        let remainingMicroseconds = (deadline - current + 999) / 1_000
        pause(useconds_t(min(UInt64(pollMicroseconds), remainingMicroseconds)))
    }
    throw ProbeError.unavailable(
        "path-entry controls did not become ready after \(pollCount) polls; " +
        "last pending state: \(lastPendingState)")
}

func waitForRestoredOpenPanel<Element>(
    timeoutNanoseconds: UInt64,
    pollMicroseconds: useconds_t,
    baseline: PathEntrySnapshot<Element>,
    expectedDestination: String,
    nowNanoseconds: () throws -> UInt64,
    validateIdentity: () throws -> Void,
    readSnapshot: () throws -> PathEntrySnapshot<Element>,
    sameElement: (Element, Element) -> Bool,
    pause: (useconds_t) -> Void
) throws -> RestoredOpenPanelToken<Element> {
    precondition(timeoutNanoseconds > 0 && pollMicroseconds > 0)
    let started = try nowNanoseconds()
    let addition = started.addingReportingOverflow(timeoutNanoseconds)
    let deadline = addition.overflow ? UInt64.max : addition.partialValue
    var pollCount = 0
    while try nowNanoseconds() < deadline {
        try validateIdentity()
        let snapshot = try readSnapshot()
        try validateIdentity()
        pollCount += 1
        if try PathEntryPolicy.restored(
            baseline: baseline, current: snapshot,
            expectedDestination: expectedDestination, sameElement: sameElement) {
            return RestoredOpenPanelToken(
                panel: baseline.panel, chooser: baseline.chooser, pollCount: pollCount)
        }
        let current = try nowNanoseconds()
        guard current < deadline else { break }
        let remainingMicroseconds = (deadline - current + 999) / 1_000
        pause(useconds_t(min(UInt64(pollMicroseconds), remainingMicroseconds)))
    }
    throw ProbeError.unavailable(
        "Open panel did not restore after path entry in \(pollCount) polls")
}
// END_READ_ONLY_PATH_ENTRY_WAITS

func livePathEntrySnapshot(
    application: AXUIElement,
    openPanel: AXUIElement,
    originalPlan: SelectionPlan,
    initialElements: [AXUIElement],
    anchorBaseline: PathEntrySnapshot<AXUIElement>? = nil,
    readDestination: Bool = false
) throws -> PathEntrySnapshot<AXUIElement> {
    guard let windows = attribute(
        application, kAXWindowsAttribute as CFString) as? [AXUIElement] else {
        throw ProbeError.validation("application exposes no AX windows")
    }
    let originalMatches = windows.filter { sameAXElement($0, openPanel) }
    guard originalMatches.count == 1 else {
        throw ProbeError.validation(
            "original Open panel was replaced, missing, or ambiguous")
    }
    let currentPanel = originalMatches[0]
    let newWindows = windows.filter { window in
        !initialElements.contains(where: { sameAXElement(window, $0) })
    }
    for window in newWindows {
        let parent = axElementAttribute(window, kAXParentAttribute as CFString)
        let topLevel = axElementAttribute(window, kAXTopLevelUIElementAttribute as CFString)
        guard (parent.map { sameAXElement($0, currentPanel) } ?? false) ||
                (topLevel.map { sameAXElement($0, currentPanel) } ?? false) else {
            throw ProbeError.validation("an unrelated AX window appeared during path entry")
        }
    }
    guard newWindows.count <= 1 else {
        throw ProbeError.validation("multiple new AX windows appeared during path entry")
    }
    let panelElements = try matchingLiveElements(currentPanel) { _ in true }
    let panelMetadata = panelElements.map { element in
        (element: element,
         role: stringAttribute(element, kAXRoleAttribute as CFString),
         title: stringAttribute(element, kAXTitleAttribute as CFString))
    }
    let cancels = panelMetadata.filter {
        $0.role == kAXButtonRole && $0.title?.lowercased() == "cancel" &&
            actions($0.element).contains(kAXPressAction)
    }.map(\.element)
    let choosers = panelMetadata.filter {
        $0.role == kAXButtonRole && $0.title?.lowercased() ==
                originalPlan.chooserTitle.lowercased() &&
            actions($0.element).contains(kAXPressAction)
    }.map(\.element)
    let browsers = panelMetadata.filter {
        guard let role = $0.role else { return false }
        return [kAXOutlineRole, kAXBrowserRole, kAXTableRole].contains(role)
    }.map(\.element)
    let cancel = try exactAnchor(
        expected: anchorBaseline?.cancel, candidates: cancels,
        allowAdditional: anchorBaseline != nil, sameElement: sameAXElement)
    let chooser = try exactAnchor(
        expected: anchorBaseline?.chooser, candidates: choosers,
        allowAdditional: false, sameElement: sameAXElement)
    guard !browsers.isEmpty else {
        throw ProbeError.validation("original Open panel has no file-browser anchor")
    }

    let panelSheets = panelMetadata.filter {
        $0.role == kAXSheetRole && !sameAXElement($0.element, currentPanel)
    }.map(\.element)
    let relatedWindows = windows.filter { candidate in
        guard !sameAXElement(candidate, currentPanel) else { return false }
        let parent = axElementAttribute(candidate, kAXParentAttribute as CFString)
        let topLevel = axElementAttribute(candidate, kAXTopLevelUIElementAttribute as CFString)
        return (parent.map { sameAXElement($0, currentPanel) } ?? false) ||
            (topLevel.map { sameAXElement($0, currentPanel) } ?? false)
    }
    let newChild = try uniqueNewRelated(
        initial: initialElements,
        candidates: panelSheets + relatedWindows,
        equals: sameAXElement)
    var candidateMetadata = panelMetadata
    if let newChild,
       !panelElements.contains(where: { sameAXElement($0, newChild) }) {
        for element in try matchingLiveElements(newChild, { _ in true })
            where !candidateMetadata.contains(where: {
                sameAXElement($0.element, element)
            }) {
            candidateMetadata.append(
                (element: element,
                 role: stringAttribute(element, kAXRoleAttribute as CFString),
                 title: stringAttribute(element, kAXTitleAttribute as CFString)))
        }
    }
    let fieldMetadata = candidateMetadata.filter { $0.role == kAXTextFieldRole }
    let fields = fieldMetadata.map(\.element)
    let searchableFieldText = fieldMetadata.map { metadata in
        [metadata.title,
         stringAttribute(metadata.element, kAXIdentifierAttribute as CFString),
         stringAttribute(metadata.element, kAXHelpAttribute as CFString)]
            .compactMap { $0?.lowercased() }.joined(separator: " ")
    }
    let semanticPlan = SelectionPlan(
        navigation: try PathEntryPolicy.navigation(
            searchableFieldText: searchableFieldText),
        chooserTitle: originalPlan.chooserTitle)
    var titledGoControls: [AXUIElement] = []
    for metadata in candidateMetadata {
        guard metadata.title?.lowercased() == "go" else { continue }
        if try PathEntryPolicy.isGoControl(
            role: metadata.role,
            title: metadata.title,
            enabled: boolAttribute(metadata.element, kAXEnabledAttribute as CFString),
            actions: actions(metadata.element)) {
            titledGoControls.append(metadata.element)
        }
    }
    let currentFields = identityDeduplicated(fields, sameElement: sameAXElement)
    let currentGoButtons = identityDeduplicated(
        titledGoControls, sameElement: sameAXElement)
    let currentBrowsers = identityDeduplicated(
        browsers, sameElement: sameAXElement)
    let additionalCancelCount = cancels.filter {
        !sameAXElement($0, cancel)
    }.count
    let topologyRestored: Bool
    if let anchorBaseline {
        topologyRestored = sameIdentitySet(
            anchorBaseline.textFields, currentFields, sameElement: sameAXElement) &&
            sameIdentitySet(
                anchorBaseline.browserAnchors, currentBrowsers,
                sameElement: sameAXElement) &&
            currentGoButtons.isEmpty && additionalCancelCount == 0
    } else {
        topologyRestored = true
    }
    let restoredPlan = topologyRestored ? semanticPlan : nil
    let destinationPaths: [String]
    if readDestination {
        destinationPaths = try [
            publishedDestinationPath(
                fileURLPathAttributePublication(
                    currentPanel, kAXDocumentAttribute as CFString),
                attributeName: "AXDocument"),
            publishedDestinationPath(
                fileURLPathAttributePublication(
                    currentPanel, kAXURLAttribute as CFString),
                attributeName: "AXURL"),
        ].compactMap { $0 }
    } else {
        destinationPaths = []
    }
    return PathEntrySnapshot(
        panel: currentPanel,
        cancel: cancel,
        chooser: chooser,
        chooserEnabled: boolAttribute(
            chooser, kAXEnabledAttribute as CFString) == true,
        additionalCancelCount: additionalCancelCount,
        browserAnchors: currentBrowsers,
        textFields: currentFields,
        goButtons: currentGoButtons,
        restoredPlan: restoredPlan,
        destinationPaths: destinationPaths)
}

func validatedPathEntryBaseline(
    application: AXUIElement,
    openPanel: AXUIElement,
    originalPlan: SelectionPlan,
    initialElements: [AXUIElement],
    process: ProcessIdentity
) throws -> PathEntrySnapshot<AXUIElement> {
    try requireSameProcess(process)
    let snapshot = try livePathEntrySnapshot(
        application: application, openPanel: openPanel,
        originalPlan: originalPlan, initialElements: initialElements)
    try requireSameProcess(process)
    let baseline = try PathEntryPolicy.baseline(
        snapshot, expectedPlan: originalPlan, sameElement: sameAXElement)
    return baseline
}

func waitForValidatedPathEntry(
    application: AXUIElement,
    baseline: PathEntrySnapshot<AXUIElement>,
    originalPlan: SelectionPlan,
    initialElements: [AXUIElement],
    process: ProcessIdentity
) throws -> PathEntryToken<AXUIElement> {
    try waitForUniquePathEntry(
        timeoutNanoseconds: 3_000_000_000,
        pollMicroseconds: 100_000,
        baseline: baseline,
        nowNanoseconds: monotonicNanoseconds,
        validateIdentity: { try requireSameProcess(process) },
        readSnapshot: {
            try livePathEntrySnapshot(
                application: application, openPanel: baseline.panel,
                originalPlan: originalPlan, initialElements: initialElements,
                anchorBaseline: baseline)
        },
        sameElement: sameAXElement,
        pause: { _ = usleep($0) })
}

func revalidatePathEntryToken(
    _ token: PathEntryToken<AXUIElement>,
    application: AXUIElement,
    baseline: PathEntrySnapshot<AXUIElement>,
    originalPlan: SelectionPlan,
    initialElements: [AXUIElement],
    process: ProcessIdentity
) throws -> PathEntryCandidate<AXUIElement> {
    try requireSameProcess(process)
    let snapshot = try livePathEntrySnapshot(
        application: application, openPanel: baseline.panel,
        originalPlan: originalPlan, initialElements: initialElements,
        anchorBaseline: baseline)
    try requireSameProcess(process)
    guard let candidate = try PathEntryPolicy.candidate(
        baseline: baseline, current: snapshot, sameElement: sameAXElement),
          sameAXElement(candidate.field, token.field),
          sameAXElement(candidate.goButton, token.goButton) else {
        throw ProbeError.validation("path-entry control identities changed before mutation")
    }
    return candidate
}

func waitForValidatedOpenPanelRestoration(
    application: AXUIElement,
    baseline: PathEntrySnapshot<AXUIElement>,
    originalPlan: SelectionPlan,
    initialElements: [AXUIElement],
    expectedDestination: String,
    process: ProcessIdentity
) throws -> RestoredOpenPanelToken<AXUIElement> {
    try waitForRestoredOpenPanel(
        timeoutNanoseconds: 3_000_000_000,
        pollMicroseconds: 100_000,
        baseline: baseline,
        expectedDestination: expectedDestination,
        nowNanoseconds: monotonicNanoseconds,
        validateIdentity: { try requireSameProcess(process) },
        readSnapshot: {
            try livePathEntrySnapshot(
                application: application, openPanel: baseline.panel,
                originalPlan: originalPlan, initialElements: initialElements,
                anchorBaseline: baseline, readDestination: true)
        },
        sameElement: sameAXElement,
        pause: { _ = usleep($0) })
}

func revalidateRestoredOpenPanelToken(
    _ token: RestoredOpenPanelToken<AXUIElement>,
    application: AXUIElement,
    baseline: PathEntrySnapshot<AXUIElement>,
    originalPlan: SelectionPlan,
    initialElements: [AXUIElement],
    expectedDestination: String,
    process: ProcessIdentity
) throws -> RestoredOpenPanelToken<AXUIElement> {
    try requireSameProcess(process)
    let snapshot = try livePathEntrySnapshot(
        application: application, openPanel: baseline.panel,
        originalPlan: originalPlan, initialElements: initialElements,
        anchorBaseline: baseline, readDestination: true)
    try requireSameProcess(process)
    guard try PathEntryPolicy.restored(
        baseline: baseline, current: snapshot,
        expectedDestination: expectedDestination, sameElement: sameAXElement),
          sameAXElement(snapshot.panel, token.panel),
          sameAXElement(snapshot.chooser, token.chooser) else {
        throw ProbeError.validation("restored Open-panel identities changed before chooser press")
    }
    return token
}

func chooseButton(in panel: AXUIElement, title: String) throws -> AXUIElement {
    let matches = try matchingLiveElements(panel) {
        stringAttribute($0, kAXRoleAttribute as CFString) == kAXButtonRole &&
            stringAttribute($0, kAXTitleAttribute as CFString)?.lowercased() == title.lowercased()
    }
    guard matches.count == 1 else { throw ProbeError.validation("chooser button is missing or ambiguous") }
    return matches[0]
}

func revalidateOriginalOpenPanel(
    application: AXUIElement,
    originalPanel: AXUIElement,
    originalPlan: SelectionPlan,
    permitKeyFallback: Bool
) throws {
    let windows = try liveWindows(application)
    _ = try OpenPanelPolicy.plan(windows: windows.map(\.1), permitKeyFallback: permitKeyFallback)
    let candidates = windows.filter { pair in
        (try? OpenPanelPolicy.plan(windows: [pair.1], permitKeyFallback: permitKeyFallback)) != nil
    }.map(\.0)
    _ = try uniqueOriginal(original: originalPanel, candidates: candidates, equals: sameAXElement)
    var budget = 500
    let currentDescription = try describe(originalPanel, budget: &budget)
    let currentPlan = try OpenPanelPolicy.plan(windows: [currentDescription],
                                               permitKeyFallback: permitKeyFallback)
    guard currentPlan == originalPlan else {
        throw ProbeError.validation("original Open panel controls changed before final action")
    }
}

func execute(options: Options, paths: ValidatedPaths, log: EventLog) throws {
    let verification = try verifyProcess(options: options, paths: paths)
    let identity = verification.identity
    try log.write("process-validated", ["pid": Int(identity.pid),
                                         "startSeconds": identity.startSeconds,
                                         "executableSha256": sha256(identity.executable),
                                         "appKitRegistrationPollCount":
                                            verification.registrationPollCount])
    guard AXIsProcessTrusted() else {
        try log.write("accessibility-not-trusted", ["pid": Int(identity.pid)])
        throw ProbeError.permission("Accessibility is not granted to this exact helper artifact")
    }
    let application = AXUIElementCreateApplication(options.pid)
    if options.phase == .inspectOpenFolderMenu {
        let menuReadiness = try waitForValidatedOpenFolderMenu(
            application: application, process: identity)
        let menuPlan = menuReadiness.plan
        var menuFields: [String: Any] = [
            "pid": Int(identity.pid),
            "parentTitle": menuPlan.parentTitle,
            "itemTitle": menuPlan.itemTitle,
            "commandCharacter": "O",
            "commandVirtualKeyPublished": menuPlan.commandVirtualKey != nil,
            "commandModifiers": OpenFolderMenuPolicy.commandModifiers,
            "enabled": true,
            "action": kAXPressAction,
            "actionCount": 0,
            "pollCount": menuReadiness.pollCount,
        ]
        if let commandVirtualKey = menuPlan.commandVirtualKey {
            menuFields["commandVirtualKey"] = commandVirtualKey
        }
        try log.write("open-folder-menu-validated", menuFields)
        return
    }
    // BEGIN_PID_OPEN_FOLDER_REQUEST
    if options.pressOpenFolderMenuItem {
        let menuReadiness = try pressOpenFolderMenuItem(
            application: application, process: identity)
        let menuPlan = menuReadiness.plan
        var menuFields: [String: Any] = [
            "pid": Int(identity.pid),
            "parentTitle": menuPlan.parentTitle,
            "itemTitle": menuPlan.itemTitle,
            "commandCharacter": "O",
            "commandVirtualKeyPublished": menuPlan.commandVirtualKey != nil,
            "commandModifiers": OpenFolderMenuPolicy.commandModifiers,
            "actionCount": 1,
            "pollCount": menuReadiness.pollCount,
        ]
        if let commandVirtualKey = menuPlan.commandVirtualKey {
            menuFields["commandVirtualKey"] = commandVirtualKey
        }
        try log.write("open-folder-menu-item-pressed", menuFields)
    }
    let readiness = try waitForValidatedOpenPanel(
        application: application, identity: identity,
        permitKeyFallback: options.permitKeyFallback)
    // END_PID_OPEN_FOLDER_REQUEST
    let panel = readiness.panel
    let plan = readiness.plan
    let initialWindows = readiness.initialElements
    try log.write("open-panel-validated", ["windowCount": initialWindows.count,
                                            "pollCount": readiness.pollCount,
                                            "navigation": plan.navigation == .direct ? "direct" : "command-shift-g",
                                            "chooserTitle": plan.chooserTitle])
    if options.phase == .inspectProjectPicker { return }
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
        try requireSameProcess(identity)
        guard fields.count == 1,
              AXUIElementSetAttributeValue(fields[0], kAXValueAttribute as CFString,
                                           paths.fixture as CFString) == .success else {
            throw ProbeError.validation("direct path field is missing, ambiguous, or not writable")
        }
    case .commandShiftG:
        let initialSheets = try matchingLiveElements(panel) {
            stringAttribute($0, kAXRoleAttribute as CFString) == kAXSheetRole
        }
        let initialElements = identityDeduplicated(
            initialWindows + initialSheets, sameElement: sameAXElement)
        let baseline = try validatedPathEntryBaseline(
            application: application, openPanel: panel,
            originalPlan: plan, initialElements: initialElements,
            process: identity)
        try postCommandShiftG(to: identity)
        let token = try waitForValidatedPathEntry(
            application: application, baseline: baseline,
            originalPlan: plan, initialElements: initialElements,
            process: identity)
        var candidate = try revalidatePathEntryToken(
            token, application: application, baseline: baseline,
            originalPlan: plan, initialElements: initialElements,
            process: identity)
        try requireSameProcess(identity)
        guard AXUIElementSetAttributeValue(candidate.field, kAXValueAttribute as CFString,
                                           paths.fixture as CFString) == .success else {
            throw ProbeError.validation("path-entry field rejected fixture root")
        }
        try requireSameProcess(identity)
        candidate = try revalidatePathEntryToken(
            token, application: application, baseline: baseline,
            originalPlan: plan, initialElements: initialElements,
            process: identity)
        try requireSameProcess(identity)
        let publishedPath = stringAttribute(candidate.field, kAXValueAttribute as CFString)
        try requireSameProcess(identity)
        guard publishedPath == paths.fixture else {
            throw ProbeError.validation("path-entry field did not retain the exact fixture root")
        }
        try press(candidate.goButton, purpose: "path-entry confirmation", process: identity)
        let restored = try waitForValidatedOpenPanelRestoration(
            application: application, baseline: baseline,
            originalPlan: plan, initialElements: initialElements,
            expectedDestination: paths.fixture,
            process: identity)
        let revalidatedRestored = try revalidateRestoredOpenPanelToken(
            restored, application: application, baseline: baseline,
            originalPlan: plan, initialElements: initialElements,
            expectedDestination: paths.fixture,
            process: identity)
        try press(revalidatedRestored.chooser, purpose: "Open panel chooser",
                  process: identity)
        let finalIdentity = try processIdentity(options.pid)
        guard finalIdentity == identity else {
            throw ProbeError.validation("PID identity changed during AX action")
        }
        try log.write("project-selection-requested", ["pid": Int(identity.pid),
                                                       "fixtureSha256": sha256(paths.fixture),
                                                       "pathEntryPollCount": token.pollCount,
                                                       "restorePollCount": restored.pollCount])
        return
    }
    try revalidateOriginalOpenPanel(application: application, originalPanel: panel,
                                    originalPlan: plan,
                                    permitKeyFallback: options.permitKeyFallback)
    try press(try chooseButton(in: panel, title: plan.chooserTitle),
              purpose: "Open panel chooser", process: identity)
    let finalIdentity = try processIdentity(options.pid)
    guard finalIdentity == identity else { throw ProbeError.validation("PID identity changed during AX action") }
    try log.write("project-selection-requested", ["pid": Int(identity.pid),
                                                   "fixtureSha256": sha256(paths.fixture)])
}

func testPublication<Value: Equatable>(
    _ value: Value?,
    malformed: Bool,
    readFailure: Int32? = nil
) -> AttributePublication<Value> {
    if let readFailure { return .readFailure(readFailure) }
    if malformed { return .malformed }
    return value.map(AttributePublication.value) ?? .missing
}

func testElement(role: String, title: String? = nil, identifier: String? = nil,
                 subrole: String? = nil, enabled: Bool? = nil,
                 menuCommandCharacter: String? = nil,
                 menuCommandCharacterMalformed: Bool = false,
                 menuCommandCharacterReadFailure: Int32? = nil,
                 menuCommandVirtualKey: Int? = nil,
                 menuCommandVirtualKeyMalformed: Bool = false,
                 menuCommandVirtualKeyReadFailure: Int32? = nil,
                 menuCommandModifiers: Int? = nil,
                 menuCommandModifiersMalformed: Bool = false,
                 menuCommandModifiersReadFailure: Int32? = nil,
                 actions: Set<String> = [],
                 children: [ElementDescription] = []) -> ElementDescription {
    ElementDescription(role: role, subrole: subrole, identifier: identifier, title: title,
                       help: nil, enabled: enabled,
                       menuCommandCharacter: testPublication(
                        menuCommandCharacter, malformed: menuCommandCharacterMalformed,
                        readFailure: menuCommandCharacterReadFailure),
                       menuCommandVirtualKey: testPublication(
                        menuCommandVirtualKey, malformed: menuCommandVirtualKeyMalformed,
                        readFailure: menuCommandVirtualKeyReadFailure),
                       menuCommandModifiers: testPublication(
                        menuCommandModifiers, malformed: menuCommandModifiersMalformed,
                        readFailure: menuCommandModifiersReadFailure),
                       actions: actions, children: children)
}

func runSelfTests() throws {
    func require(_ condition: Bool, _ message: String) throws {
        if !condition { throw ProbeError.validation("self-test failed: \(message)") }
    }
    func sameString(_ left: String, _ right: String) -> Bool { left == right }
    try require(integralIntPublication(NSNumber(value: 31)) == .value(31),
                "integral AX command value was not preserved")
    try require(integralIntPublication(NSNumber(value: 31.5)) == .malformed,
                "fractional AX command value was coerced to an integer")
    try require(integralIntPublication(kCFBooleanTrue) == .malformed,
                "boolean AX command value was coerced to an integer")
    try require(integralIntPublication("31" as CFString) == .malformed,
                "string AX command value was coerced to an integer")
    try require(stringPublication(NSNumber(value: 31)) == .malformed,
                "numeric AX command character was coerced to a string")
    try require(fileURLPathPublication(
        URL(fileURLWithPath: "/private/tmp") as CFURL) == .value("/private/tmp"),
        "CFURL destination was not canonicalized to a file path")
    try require(fileURLPathPublication(
        "file:///private/tmp/" as CFString) == .value("/private/tmp"),
        "string URL destination was not canonicalized to a file path")
    try require(fileURLPathPublication(
        "https://example.invalid/" as CFString) == .malformed,
        "non-file destination URL passed")
    let unsupportedPublication: AttributePublication<Int> = attributePublication(
        status: .attributeUnsupported) { .value(31) }
    let noValuePublication: AttributePublication<Int> = attributePublication(
        status: .noValue) { .value(31) }
    try require(unsupportedPublication == .missing && noValuePublication == .missing,
                "unsupported or valueless AX attribute was not classified as unpublished")
    let successfulPublication: AttributePublication<Int> = attributePublication(
        status: .success) { .value(31) }
    try require(successfulPublication == .value(31),
                "successful AX attribute read discarded its published value")
    let hardReadFailures: [AXError] = [
        .failure, .invalidUIElement, .cannotComplete, .notImplemented, .apiDisabled,
    ]
    for status in hardReadFailures {
        let publication: AttributePublication<Int> = attributePublication(status: status) {
            .value(31)
        }
        try require(publication == .readFailure(status.rawValue),
                    "hard AX read failure was classified as unpublished")
    }
    let expectedProcess = ProcessIdentity(pid: 42, startSeconds: 10,
                                          startMicroseconds: 20,
                                          executable: "/private/tmp/copied/ChatGPT")
    let exactRegistration = AppKitRegistrationSample.published(
        isTerminated: false, bundlePath: "/private/tmp/copied/ChatGPT.app",
        executablePath: expectedProcess.executable)
    var registrationSamples: [AppKitRegistrationSample] = [.unavailable, exactRegistration]
    var registrationNow: UInt64 = 0
    var registrationIdentityChecks = 0
    var registrationFinalChecks = 0
    let delayedRegistration = try verifyProcessRegistration(
        timeoutNanoseconds: 1_000_000_000, pollMicroseconds: 100,
        expectedBundle: "/private/tmp/copied/ChatGPT.app",
        expectedExecutable: expectedProcess.executable,
        nowNanoseconds: { registrationNow },
        validateIdentity: { registrationIdentityChecks += 1 },
        readSample: { registrationSamples.removeFirst() },
        validateFinal: { registrationFinalChecks += 1 },
        pause: { registrationNow += UInt64($0) * 1_000 })
    try require(delayedRegistration.pollCount == 2 &&
                registrationIdentityChecks == 6 && registrationFinalChecks == 1,
                "missing AppKit registration did not retry before final validation")

    registrationSamples = [
        .published(isTerminated: false, bundlePath: nil,
                   executablePath: expectedProcess.executable),
        .published(isTerminated: false, bundlePath: "/private/tmp/copied/ChatGPT.app",
                   executablePath: nil),
        exactRegistration,
    ]
    registrationNow = 0
    let nilURLRegistration = try verifyProcessRegistration(
        timeoutNanoseconds: 1_000_000_000, pollMicroseconds: 100,
        expectedBundle: "/private/tmp/copied/ChatGPT.app",
        expectedExecutable: expectedProcess.executable,
        nowNanoseconds: { registrationNow }, validateIdentity: {},
        readSample: { registrationSamples.removeFirst() }, validateFinal: {},
        pause: { registrationNow += UInt64($0) * 1_000 })
    try require(nilURLRegistration.pollCount == 3,
                "missing AppKit bundle or executable URL did not retry")

    func requireImmediateRegistrationRejection(
        _ sample: AppKitRegistrationSample,
        _ message: String
    ) throws {
        var reads = 0
        var pauses = 0
        var finals = 0
        var rejected = false
        do {
            _ = try verifyProcessRegistration(
                timeoutNanoseconds: 1_000_000_000, pollMicroseconds: 100,
                expectedBundle: "/private/tmp/copied/ChatGPT.app",
                expectedExecutable: expectedProcess.executable,
                nowNanoseconds: { 0 }, validateIdentity: {},
                readSample: { reads += 1; return sample },
                validateFinal: { finals += 1 }, pause: { _ in pauses += 1 })
        } catch ProbeError.validation { rejected = true }
        try require(rejected && reads == 1 && pauses == 0 && finals == 0, message)
    }
    try requireImmediateRegistrationRejection(
        .published(isTerminated: false, bundlePath: "/private/tmp/wrong.app",
                   executablePath: expectedProcess.executable),
        "published AppKit bundle mismatch did not fail immediately")
    try requireImmediateRegistrationRejection(
        .published(isTerminated: false, bundlePath: "/private/tmp/copied/ChatGPT.app",
                   executablePath: "/private/tmp/wrong/ChatGPT"),
        "published AppKit executable mismatch did not fail immediately")
    try requireImmediateRegistrationRejection(
        .published(isTerminated: true, bundlePath: nil, executablePath: nil),
        "terminated AppKit registration did not fail immediately")

    registrationNow = 0
    var registrationTimeoutReads = 0
    var rejected = false
    do {
        _ = try verifyProcessRegistration(
            timeoutNanoseconds: 300_000, pollMicroseconds: 100,
            expectedBundle: "/private/tmp/copied/ChatGPT.app",
            expectedExecutable: expectedProcess.executable,
            nowNanoseconds: { registrationNow }, validateIdentity: {},
            readSample: { registrationTimeoutReads += 1; return .unavailable },
            validateFinal: {},
            pause: { registrationNow += UInt64($0) * 1_000 })
    } catch ProbeError.unavailable(let message) {
        rejected = message == "AppKit process registration did not become ready after 3 polls"
    }
    try require(rejected && registrationTimeoutReads == 3,
                "AppKit registration timeout used the wrong poll bound")

    func requireRegistrationIdentityDrift(
        identities: [ProcessIdentity],
        samples: [AppKitRegistrationSample],
        expectedReads: Int,
        expectedFinals: Int,
        _ message: String
    ) throws {
        var remainingIdentities = identities
        var remainingSamples = samples
        var reads = 0
        var finals = 0
        var now: UInt64 = 0
        var driftRejected = false
        do {
            _ = try verifyProcessRegistration(
                timeoutNanoseconds: 1_000_000_000, pollMicroseconds: 100,
                expectedBundle: "/private/tmp/copied/ChatGPT.app",
                expectedExecutable: expectedProcess.executable,
                nowNanoseconds: { now },
                validateIdentity: {
                    let actual = remainingIdentities.removeFirst()
                    try requireProcessIdentity(actual, matches: expectedProcess)
                },
                readSample: { reads += 1; return remainingSamples.removeFirst() },
                validateFinal: { finals += 1 },
                pause: { now += UInt64($0) * 1_000 })
        } catch ProbeError.validation { driftRejected = true }
        try require(driftRejected && reads == expectedReads && finals == expectedFinals,
                    message)
    }
    let pidDrift = ProcessIdentity(pid: 43, startSeconds: 10, startMicroseconds: 20,
                                   executable: expectedProcess.executable)
    let startDrift = ProcessIdentity(pid: 42, startSeconds: 11, startMicroseconds: 20,
                                     executable: expectedProcess.executable)
    let pathDrift = ProcessIdentity(pid: 42, startSeconds: 10, startMicroseconds: 20,
                                    executable: "/private/tmp/wrong/ChatGPT")
    let identityDrifts = [
        ("PID", pidDrift),
        ("process start", startDrift),
        ("kernel executable", pathDrift),
    ]
    for (label, drift) in identityDrifts {
        try requireRegistrationIdentityDrift(
            identities: [drift], samples: [exactRegistration],
            expectedReads: 0, expectedFinals: 0,
            "\(label) drift before AppKit sampling was not rejected")
        try requireRegistrationIdentityDrift(
            identities: [expectedProcess, drift], samples: [exactRegistration],
            expectedReads: 1, expectedFinals: 0,
            "\(label) drift after AppKit sampling was not rejected")
        try requireRegistrationIdentityDrift(
            identities: [expectedProcess, expectedProcess, drift],
            samples: [.unavailable, exactRegistration], expectedReads: 1,
            expectedFinals: 0,
            "\(label) drift before the next AppKit sample was not rejected")
        try requireRegistrationIdentityDrift(
            identities: [expectedProcess, expectedProcess, drift],
            samples: [exactRegistration], expectedReads: 1, expectedFinals: 0,
            "\(label) drift before final validation was not rejected")
        try requireRegistrationIdentityDrift(
            identities: [expectedProcess, expectedProcess, expectedProcess, drift],
            samples: [exactRegistration], expectedReads: 1, expectedFinals: 1,
            "\(label) drift after final validation was not rejected")
    }

    func menuBar(menuBarRole: String = kAXMenuBarRole,
                 parentTitle: String? = "File", parentRole: String = kAXMenuBarItemRole,
                 menuRole: String = kAXMenuRole, itemTitle: String = "Open Folder…",
                 itemRole: String = kAXMenuItemRole, enabled: Bool? = true,
                 actions: Set<String> = [kAXPressAction], commandCharacter: String? = "O",
                 commandCharacterMalformed: Bool = false,
                 commandCharacterReadFailure: Int32? = nil,
                 commandVirtualKey: Int? = 31,
                 commandVirtualKeyMalformed: Bool = false,
                 commandVirtualKeyReadFailure: Int32? = nil,
                 commandModifiers: Int? = 0,
                 commandModifiersMalformed: Bool = false,
                 commandModifiersReadFailure: Int32? = nil)
        -> ElementDescription {
        let item = testElement(role: itemRole, title: itemTitle, enabled: enabled,
                               menuCommandCharacter: commandCharacter,
                               menuCommandCharacterMalformed: commandCharacterMalformed,
                               menuCommandCharacterReadFailure: commandCharacterReadFailure,
                               menuCommandVirtualKey: commandVirtualKey,
                               menuCommandVirtualKeyMalformed: commandVirtualKeyMalformed,
                               menuCommandVirtualKeyReadFailure: commandVirtualKeyReadFailure,
                               menuCommandModifiers: commandModifiers,
                               menuCommandModifiersMalformed: commandModifiersMalformed,
                               menuCommandModifiersReadFailure: commandModifiersReadFailure,
                               actions: actions)
        let menu = testElement(role: menuRole, children: [item])
        let parent = testElement(role: parentRole, title: parentTitle,
                                 children: [menu])
        return testElement(role: menuBarRole, children: [parent])
    }
    func requireRejectedMenu(_ candidate: ElementDescription, _ message: String) throws {
        var wasRejected = false
        do { _ = try OpenFolderMenuPolicy.plan(menuBar: candidate) }
        catch ProbeError.validation { wasRejected = true }
        try require(wasRejected, message)
    }
    let validMenuPlan = try OpenFolderMenuPolicy.plan(menuBar: menuBar())
    try require(validMenuPlan.parentTitle == "File" &&
                validMenuPlan.itemTitle == "Open Folder…" &&
                validMenuPlan.commandVirtualKey == 31, "valid Open Folder menu plan")
    let unpublishedVirtualKeyPlan = try OpenFolderMenuPolicy.plan(
        menuBar: menuBar(commandVirtualKey: nil))
    try require(unpublishedVirtualKeyPlan.menuBarItemIndex == validMenuPlan.menuBarItemIndex &&
                unpublishedVirtualKeyPlan.menuIndex == validMenuPlan.menuIndex &&
                unpublishedVirtualKeyPlan.menuItemIndex == validMenuPlan.menuItemIndex &&
                unpublishedVirtualKeyPlan.commandVirtualKey == nil,
                "unpublished optional virtual key did not preserve the exact menu path")
    let lowercaseCommandPlan = try OpenFolderMenuPolicy.plan(
        menuBar: menuBar(commandCharacter: "o"))
    try require(lowercaseCommandPlan == validMenuPlan,
                "lowercase AX command character did not normalize to Command-O")
    try requireRejectedMenu(menuBar(menuBarRole: kAXWindowRole),
                            "wrong menu-bar role passed")
    try requireRejectedMenu(menuBar(menuBarRole: ""),
                            "missing menu-bar role passed")
    try requireRejectedMenu(menuBar(parentTitle: "Workspace"),
                            "wrong File parent title passed")
    try requireRejectedMenu(menuBar(parentTitle: nil),
                            "missing File parent title passed")
    try requireRejectedMenu(menuBar(parentRole: kAXButtonRole),
                            "wrong File parent role passed")
    try requireRejectedMenu(menuBar(parentRole: ""),
                            "missing File parent role passed")
    try requireRejectedMenu(menuBar(menuRole: kAXGroupRole),
                            "wrong direct menu role passed")
    try requireRejectedMenu(menuBar(menuRole: ""),
                            "missing direct menu role passed")
    try requireRejectedMenu(menuBar(commandVirtualKey: 35),
                            "wrong Open Folder command virtual key passed")
    try requireRejectedMenu(menuBar(commandModifiers: 1),
                            "wrong Open Folder command modifiers passed")
    try requireRejectedMenu(menuBar(commandModifiers: nil),
                            "missing Open Folder command modifiers passed")
    try requireRejectedMenu(menuBar(commandCharacter: nil),
                            "missing Open Folder command character passed")
    try requireRejectedMenu(menuBar(enabled: nil),
                            "missing Open Folder enabled state passed")
    rejected = false
    let ambiguousMenuBar = testElement(
        role: kAXMenuBarRole,
        children: menuBar(parentTitle: "File").children +
            menuBar(parentTitle: "File").children)
    do { _ = try OpenFolderMenuPolicy.plan(menuBar: ambiguousMenuBar) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "duplicate File menu paths passed")
    rejected = false
    do { _ = try OpenFolderMenuPolicy.plan(menuBar: menuBar(enabled: false)) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "disabled Open Folder menu item passed")
    rejected = false
    do { _ = try OpenFolderMenuPolicy.plan(menuBar: menuBar(itemTitle: "Open Folder...")) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "wrong Open Folder title passed")
    rejected = false
    do { _ = try OpenFolderMenuPolicy.plan(menuBar: menuBar(actions: [])) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "Open Folder item without AXPress passed")
    rejected = false
    do { _ = try OpenFolderMenuPolicy.plan(menuBar: menuBar(itemRole: kAXButtonRole)) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "Open Folder item with wrong role passed")
    rejected = false
    do { _ = try OpenFolderMenuPolicy.plan(menuBar: menuBar(commandCharacter: "P")) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "Open Folder item with wrong command metadata passed")
    rejected = false
    let duplicateItems = testElement(
        role: kAXMenuBarRole,
        children: [testElement(
            role: kAXMenuBarItemRole, title: "File",
            children: [testElement(
                role: kAXMenuRole,
                children: menuBar().children[0].children[0].children +
                    menuBar().children[0].children[0].children)])])
    do { _ = try OpenFolderMenuPolicy.plan(menuBar: duplicateItems) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "duplicate Open Folder menu items passed")
    let validMenuSnapshot = OpenFolderMenuSnapshot<String>(
        description: menuBar(), items: [validMenuPlan: "open-folder-item"])
    let unpublishedCommandMetadataCases: [(String, ElementDescription)] = [
        ("missing character", menuBar(commandCharacter: nil)),
        ("empty character", menuBar(commandCharacter: "")),
        ("missing modifiers", menuBar(commandModifiers: nil)),
    ]
    for (label, description) in unpublishedCommandMetadataCases {
        var snapshots: [OpenFolderMenuSnapshot<String>?] = [
            OpenFolderMenuSnapshot<String>(description: description, items: [:]),
            validMenuSnapshot,
        ]
        var now: UInt64 = 0
        var reads = 0
        var pauses = 0
        let readiness = try waitForReadyOpenFolderMenu(
            timeoutNanoseconds: 1_000_000_000,
            pollMicroseconds: 100,
            nowNanoseconds: { now },
            validateIdentity: {},
            readSnapshot: { reads += 1; return snapshots.removeFirst() },
            pause: { pauses += 1; now += UInt64($0) * 1_000 })
        try require(readiness.item == "open-folder-item" &&
                        readiness.pollCount == 2 && reads == 2 && pauses == 1,
                    "\(label) did not retry until exact command metadata")
    }
    var missingMetadataNow: UInt64 = 0
    var missingMetadataReads = 0
    var missingMetadataPauses = 0
    rejected = false
    do {
        _ = try waitForReadyOpenFolderMenu(
            timeoutNanoseconds: 300_000,
            pollMicroseconds: 100,
            nowNanoseconds: { missingMetadataNow },
            validateIdentity: {},
            readSnapshot: {
                missingMetadataReads += 1
                return OpenFolderMenuSnapshot<String>(
                    description: menuBar(commandModifiers: nil), items: [:])
            },
            pause: {
                missingMetadataPauses += 1
                missingMetadataNow += UInt64($0) * 1_000
            })
    } catch ProbeError.unavailable(let message) {
        rejected = message == "Open Folder menu did not become ready after 3 polls; " +
            "last pending state: command modifiers are unpublished"
    }
    try require(rejected && missingMetadataReads == 3 && missingMetadataPauses == 3,
                "persistent missing command metadata did not use the exact timeout")
    func requireImmediateCommandMetadataRejection(
        _ description: ElementDescription,
        expectedMessage: String,
        _ message: String
    ) throws {
        var reads = 0
        var pauses = 0
        var exactError = false
        do {
            _ = try waitForReadyOpenFolderMenu(
                timeoutNanoseconds: 1_000_000_000,
                pollMicroseconds: 100,
                nowNanoseconds: { 0 },
                validateIdentity: {},
                readSnapshot: {
                    reads += 1
                    return OpenFolderMenuSnapshot<String>(description: description, items: [:])
                },
                pause: { _ in pauses += 1 })
        } catch ProbeError.validation(let actualMessage) {
            exactError = actualMessage == expectedMessage
        }
        try require(exactError && reads == 1 && pauses == 0, message)
    }
    try requireImmediateCommandMetadataRejection(
        menuBar(commandCharacter: "P"),
        expectedMessage: "Open Folder menu item has unexpected command character: P",
        "published wrong command character did not fail immediately")
    try requireImmediateCommandMetadataRejection(
        menuBar(commandVirtualKey: 35),
        expectedMessage: "Open Folder menu item has unexpected command virtual key: 35",
        "published wrong command virtual key did not fail immediately")
    try requireImmediateCommandMetadataRejection(
        menuBar(commandModifiers: 1),
        expectedMessage: "Open Folder menu item has unexpected command modifiers: 1",
        "published wrong command modifiers did not fail immediately")
    try requireImmediateCommandMetadataRejection(
        menuBar(commandCharacterMalformed: true),
        expectedMessage: "Open Folder menu item has malformed command character type",
        "published malformed command character did not fail immediately")
    try requireImmediateCommandMetadataRejection(
        menuBar(commandVirtualKeyMalformed: true),
        expectedMessage: "Open Folder menu item has malformed command virtual key type",
        "published malformed command virtual key did not fail immediately")
    try requireImmediateCommandMetadataRejection(
        menuBar(commandModifiersMalformed: true),
        expectedMessage: "Open Folder menu item has malformed command modifiers type",
        "published malformed command modifiers did not fail immediately")
    func requireImmediateCommandMetadataReadFailure(
        _ description: ElementDescription,
        expectedMessage: String,
        _ message: String
    ) throws {
        var reads = 0
        var pauses = 0
        var exactError = false
        do {
            _ = try waitForReadyOpenFolderMenu(
                timeoutNanoseconds: 1_000_000_000,
                pollMicroseconds: 100,
                nowNanoseconds: { 0 },
                validateIdentity: {},
                readSnapshot: {
                    reads += 1
                    return OpenFolderMenuSnapshot<String>(description: description, items: [:])
                },
                pause: { _ in pauses += 1 })
        } catch ProbeError.unavailable(let actualMessage) {
            exactError = actualMessage == expectedMessage
        }
        try require(exactError && reads == 1 && pauses == 0, message)
    }
    try requireImmediateCommandMetadataReadFailure(
        menuBar(commandCharacterReadFailure: -25204),
        expectedMessage: "Open Folder menu item command character read failed: -25204",
        "command character AX read failure did not fail immediately")
    try requireImmediateCommandMetadataReadFailure(
        menuBar(commandVirtualKeyReadFailure: -25204),
        expectedMessage: "Open Folder menu item command virtual key read failed: -25204",
        "optional virtual key AX read failure did not fail immediately")
    try requireImmediateCommandMetadataReadFailure(
        menuBar(commandModifiersReadFailure: -25204),
        expectedMessage: "Open Folder menu item command modifiers read failed: -25204",
        "command modifiers AX read failure did not fail immediately")
    let wrongFileTitleSnapshot = OpenFolderMenuSnapshot<String>(
        description: menuBar(parentTitle: "Workspace"), items: [:])
    let missingFileTitleSnapshot = OpenFolderMenuSnapshot<String>(
        description: menuBar(parentTitle: nil), items: [:])
    let missingDirectMenuSnapshot = OpenFolderMenuSnapshot<String>(
        description: testElement(
            role: kAXMenuBarRole,
            children: [testElement(role: kAXMenuBarItemRole, title: "File")]),
        items: [:])
    let emptyDirectMenuSnapshot = OpenFolderMenuSnapshot<String>(
        description: testElement(
            role: kAXMenuBarRole,
            children: [testElement(
                role: kAXMenuBarItemRole, title: "File",
                children: [testElement(role: kAXMenuRole)])]),
        items: [:])
    let otherMenuChildrenSnapshot = OpenFolderMenuSnapshot<String>(
        description: testElement(
            role: kAXMenuBarRole,
            children: [testElement(
                role: kAXMenuBarItemRole, title: "File",
                children: [testElement(
                    role: kAXMenuRole,
                    children: [testElement(
                        role: kAXMenuItemRole, title: "New Window",
                        actions: [kAXPressAction])])])]),
        items: [:])
    let pendingTimeoutCases: [(String, OpenFolderMenuSnapshot<String>?, String)] = [
        ("application menu bar", nil, "application menu bar is unpublished"),
        ("File menu item", wrongFileTitleSnapshot, "File menu item is unpublished"),
        ("File AX menu", missingDirectMenuSnapshot, "File AX menu is unpublished"),
        ("Open Folder item", otherMenuChildrenSnapshot,
         "Open Folder menu item is unpublished"),
        ("command character", OpenFolderMenuSnapshot<String>(
            description: menuBar(commandCharacter: nil), items: [:]),
         "command character is unpublished"),
        ("empty command character", OpenFolderMenuSnapshot<String>(
            description: menuBar(commandCharacter: ""), items: [:]),
         "command character is empty"),
        ("command modifiers", OpenFolderMenuSnapshot<String>(
            description: menuBar(commandModifiers: nil), items: [:]),
         "command modifiers are unpublished"),
    ]
    for (label, snapshot, pendingState) in pendingTimeoutCases {
        var now: UInt64 = 0
        var reads = 0
        var pauses = 0
        var exactTimeout = false
        do {
            _ = try waitForReadyOpenFolderMenu(
                timeoutNanoseconds: 300_000,
                pollMicroseconds: 100,
                nowNanoseconds: { now },
                validateIdentity: {},
                readSnapshot: { reads += 1; return snapshot },
                pause: { pauses += 1; now += UInt64($0) * 1_000 })
        } catch ProbeError.unavailable(let message) {
            exactTimeout = message ==
                "Open Folder menu did not become ready after 3 polls; " +
                "last pending state: \(pendingState)" && !message.contains("New Window")
        }
        try require(exactTimeout && reads == 3 && pauses == 3,
                    "\(label) timeout did not report the bounded final pending state")
    }
    var changingPendingSnapshots: [OpenFolderMenuSnapshot<String>?] = [
        nil,
        OpenFolderMenuSnapshot<String>(
            description: menuBar(commandCharacter: nil), items: [:]),
        OpenFolderMenuSnapshot<String>(
            description: menuBar(commandModifiers: nil), items: [:]),
    ]
    var changingPendingNow: UInt64 = 0
    rejected = false
    do {
        _ = try waitForReadyOpenFolderMenu(
            timeoutNanoseconds: 300_000,
            pollMicroseconds: 100,
            nowNanoseconds: { changingPendingNow },
            validateIdentity: {},
            readSnapshot: { changingPendingSnapshots.removeFirst() },
            pause: { changingPendingNow += UInt64($0) * 1_000 })
    } catch ProbeError.unavailable(let message) {
        rejected = message == "Open Folder menu did not become ready after 3 polls; " +
            "last pending state: command modifiers are unpublished"
    }
    try require(rejected, "menu timeout did not report the final observed pending state")
    rejected = false
    do { _ = try OpenFolderMenuPolicy.plan(menuBar: menuBar(commandCharacter: nil)) }
    catch ProbeError.validation(let message) {
        rejected = message == "direct File to Open Folder menu path is absent"
    }
    try require(rejected, "pending plan wrapper changed its validation contract")
    var menuSnapshots: [OpenFolderMenuSnapshot<String>?] = [
        nil, wrongFileTitleSnapshot, missingFileTitleSnapshot,
        missingDirectMenuSnapshot, emptyDirectMenuSnapshot,
        otherMenuChildrenSnapshot, validMenuSnapshot,
    ]
    var menuNow: UInt64 = 0
    var menuIdentityChecks = 0
    var menuPauses = 0
    let menuReadiness = try waitForReadyOpenFolderMenu(
        timeoutNanoseconds: 1_000_000_000,
        pollMicroseconds: 100,
        nowNanoseconds: { menuNow },
        validateIdentity: { menuIdentityChecks += 1 },
        readSnapshot: { menuSnapshots.removeFirst() },
        pause: {
            menuPauses += 1
            menuNow += UInt64($0) * 1_000
        })
    try require(menuReadiness.item == "open-folder-item" && menuReadiness.pollCount == 7,
                "bounded menu readiness did not select the delayed menu")
    try require(menuIdentityChecks == 14 && menuPauses == 6,
                "PID identity was not checked around every menu snapshot")
    var malformedMenuReads = 0
    var malformedMenuPauses = 0
    rejected = false
    do {
        _ = try waitForReadyOpenFolderMenu(
            timeoutNanoseconds: 1_000_000_000,
            pollMicroseconds: 100,
            nowNanoseconds: { 0 },
            validateIdentity: {},
            readSnapshot: {
                malformedMenuReads += 1
                return OpenFolderMenuSnapshot<String>(
                    description: menuBar(enabled: false), items: [:])
            },
            pause: { _ in malformedMenuPauses += 1 })
    } catch ProbeError.validation { rejected = true }
    try require(rejected && malformedMenuReads == 1 && malformedMenuPauses == 0,
                "malformed published menu did not fail immediately")
    menuNow = 0
    var menuTimeoutReads = 0
    var menuTimeoutPauses = 0
    rejected = false
    do {
        _ = try waitForReadyOpenFolderMenu(
            timeoutNanoseconds: 300_000,
            pollMicroseconds: 100,
            nowNanoseconds: { menuNow },
            validateIdentity: {},
            readSnapshot: {
                menuTimeoutReads += 1
                return otherMenuChildrenSnapshot
            },
            pause: {
                menuTimeoutPauses += 1
                menuNow += UInt64($0) * 1_000
            })
    } catch ProbeError.unavailable(let message) {
        rejected = message == "Open Folder menu did not become ready after 3 polls; " +
            "last pending state: Open Folder menu item is unpublished"
    }
    try require(rejected && menuTimeoutReads == 3 && menuTimeoutPauses == 3,
                "Open Folder menu timeout used the wrong poll bound")
    var duplicateMenuReads = 0
    var duplicateMenuPauses = 0
    rejected = false
    do {
        _ = try waitForReadyOpenFolderMenu(
            timeoutNanoseconds: 1_000_000_000,
            pollMicroseconds: 100,
            nowNanoseconds: { 0 },
            validateIdentity: {},
            readSnapshot: {
                duplicateMenuReads += 1
                return OpenFolderMenuSnapshot<String>(description: duplicateItems, items: [:])
            },
            pause: { _ in duplicateMenuPauses += 1 })
    } catch ProbeError.validation { rejected = true }
    try require(rejected && duplicateMenuReads == 1 && duplicateMenuPauses == 0,
                "duplicate published menu items did not fail immediately")
    menuNow = 0
    var menuDriftChecks = 0
    var menuDriftReads = 0
    var menuDriftPauses = 0
    rejected = false
    do {
        _ = try waitForReadyOpenFolderMenu(
            timeoutNanoseconds: 1_000_000_000,
            pollMicroseconds: 100,
            nowNanoseconds: { menuNow },
            validateIdentity: {
                menuDriftChecks += 1
                if menuDriftChecks == 3 {
                    throw ProbeError.validation("test menu PID drift")
                }
            },
            readSnapshot: {
                menuDriftReads += 1
                return otherMenuChildrenSnapshot
            },
            pause: {
                menuDriftPauses += 1
                menuNow += UInt64($0) * 1_000
            })
    } catch ProbeError.validation { rejected = true }
    try require(rejected && menuDriftChecks == 3 && menuDriftReads == 1 &&
                    menuDriftPauses == 1,
                "menu PID drift did not stop before another AX read")

    let cancel = testElement(role: kAXButtonRole, title: "Cancel", actions: [kAXPressAction])
    let choose = testElement(role: kAXButtonRole, title: "Open", actions: [kAXPressAction])
    let direct = testElement(role: kAXTextFieldRole, identifier: "path", actions: [])
    let fileBrowser = testElement(role: kAXOutlineRole)
    let panel = testElement(role: kAXWindowRole, subrole: kAXStandardWindowSubrole,
                            children: [cancel, choose, direct, fileBrowser])
    try require(try OpenPanelPolicy.plan(windows: [panel], permitKeyFallback: false) ==
                SelectionPlan(navigation: .direct, chooserTitle: "Open"), "direct plan")
    let fallbackPanel = testElement(role: kAXSheetRole, children: [cancel, choose, fileBrowser])
    try require(try OpenPanelPolicy.plan(windows: [fallbackPanel], permitKeyFallback: true).navigation ==
                .commandShiftG, "explicit key fallback")
    rejected = false
    do { _ = try OpenPanelPolicy.plan(windows: [fallbackPanel], permitKeyFallback: false) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "unauthorized fallback passed")
    rejected = false
    do { _ = try OpenPanelPolicy.plan(windows: [panel, panel], permitKeyFallback: false) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "duplicate panels passed")
    let duplicateChooser = testElement(role: kAXWindowRole, subrole: kAXStandardWindowSubrole,
                                       children: [cancel, choose, choose, direct, fileBrowser])
    rejected = false
    do { _ = try OpenPanelPolicy.plan(windows: [duplicateChooser], permitKeyFallback: false) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "duplicate chooser passed")
    let customWindow = testElement(role: kAXWindowRole, children: [cancel, choose, direct])
    rejected = false
    do { _ = try OpenPanelPolicy.plan(windows: [customWindow], permitKeyFallback: false) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "nonstandard custom window passed")
    let wrongSheet = testElement(role: kAXSheetRole, children: [cancel, choose, direct])
    rejected = false
    do { _ = try OpenPanelPolicy.plan(windows: [wrongSheet], permitKeyFallback: true) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "wrong native dialog passed")
    rejected = false
    do { _ = try uniqueOriginal(original: "original", candidates: ["lookalike"], equals: ==) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "lookalike panel replaced original identity")
    try require(try uniqueNewRelated(initial: ["stale"], candidates: ["stale"], equals: ==) == nil,
                "stale child was treated as new")
    rejected = false
    do { _ = try uniqueNewRelated(initial: [String](), candidates: ["one", "two"], equals: ==) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "duplicate new children passed")

    func pathSnapshot(
        panel: String = "panel",
        cancel: String = "cancel",
        chooser: String = "chooser",
        chooserEnabled: Bool = true,
        additionalCancelCount: Int = 0,
        browsers: [String] = ["browser"],
        fields: [String] = ["search"],
        goButtons: [String] = [],
        restoredPlan: SelectionPlan? = SelectionPlan(
            navigation: .commandShiftG, chooserTitle: "Open"),
        destinationPaths: [String] = []
    ) -> PathEntrySnapshot<String> {
        PathEntrySnapshot(panel: panel, cancel: cancel, chooser: chooser,
                          chooserEnabled: chooserEnabled,
                          additionalCancelCount: additionalCancelCount,
                          browserAnchors: browsers, textFields: fields,
                          goButtons: goButtons, restoredPlan: restoredPlan,
                          destinationPaths: destinationPaths)
    }
    try require(try exactAnchor(
        expected: "original-cancel",
        candidates: ["original-cancel", "overlay-cancel"],
        allowAdditional: true, sameElement: sameString) == "original-cancel",
        "transient overlay Cancel replaced the original Cancel anchor")
    let pathBaseline = try PathEntryPolicy.baseline(
        pathSnapshot(),
        expectedPlan: SelectionPlan(navigation: .commandShiftG, chooserTitle: "Open"),
        sameElement: sameString)
    try require(try PathEntryPolicy.navigation(searchableFieldText: ["search"]) ==
                    .commandShiftG,
                "ordinary baseline field acquired direct-path semantics")
    try require(try PathEntryPolicy.navigation(searchableFieldText: ["folder path"]) ==
                    .direct,
                "direct-path field semantics were missed")
    rejected = false
    do {
        _ = try PathEntryPolicy.baseline(
            pathSnapshot(restoredPlan: SelectionPlan(
                navigation: .direct, chooserTitle: "Open")),
            expectedPlan: SelectionPlan(
                navigation: .commandShiftG, chooserTitle: "Open"),
            sameElement: sameString)
    } catch ProbeError.validation { rejected = true }
    try require(rejected, "baseline field semantic drift preserved the old plan")
    try require(!(try PathEntryPolicy.restored(
        baseline: pathBaseline,
        current: pathSnapshot(
            restoredPlan: SelectionPlan(navigation: .direct, chooserTitle: "Open"),
            destinationPaths: ["fixture"]),
        expectedDestination: "fixture", sameElement: sameString)),
        "restored field semantic drift preserved the old plan")
    try require(try PathEntryPolicy.isGoControl(
        role: kAXButtonRole, title: "Go", enabled: true,
        actions: [kAXPressAction]), "valid Go control was rejected")
    for invalidGo in [
        (kAXButtonRole as String?, "Go" as String?, false as Bool?, Set([kAXPressAction])),
        (kAXButtonRole as String?, "Go" as String?, true as Bool?, Set<String>()),
        (kAXTextFieldRole as String?, "Go" as String?, true as Bool?, Set([kAXPressAction])),
    ] {
        rejected = false
        do {
            _ = try PathEntryPolicy.isGoControl(
                role: invalidGo.0, title: invalidGo.1,
                enabled: invalidGo.2, actions: invalidGo.3)
        } catch ProbeError.validation { rejected = true }
        try require(rejected, "disabled, non-button, or non-pressable Go passed")
    }
    try require(try PathEntryPolicy.candidate(
        baseline: pathBaseline,
        current: pathSnapshot(fields: ["search", "path"], goButtons: ["go"],
                              restoredPlan: nil),
        sameElement: sameString)?.field == "path",
        "same-panel path-entry identity delta was not accepted")
    try require(try PathEntryPolicy.candidate(
        baseline: pathBaseline,
        current: pathSnapshot(chooserEnabled: false,
                              fields: ["search", "path"], goButtons: ["go"],
                              restoredPlan: nil),
        sameElement: sameString)?.field == "path",
        "temporarily disabled chooser blocked exact path-entry controls")
    try require(try PathEntryPolicy.candidate(
        baseline: pathBaseline,
        current: pathSnapshot(fields: ["search", "path", "path"],
                              goButtons: ["go", "go"], restoredPlan: nil),
        sameElement: sameString)?.goButton == "go",
        "same child-and-panel identities were not deduplicated")
    try require(try PathEntryPolicy.candidate(
        baseline: pathBaseline,
        current: pathSnapshot(chooserEnabled: false,
                              fields: ["search", "path"], goButtons: [],
                              restoredPlan: nil),
        sameElement: sameString) == nil,
        "field-only staged publication or disabled chooser became ready")
    try require(try PathEntryPolicy.candidate(
        baseline: pathBaseline,
        current: pathSnapshot(fields: ["search"], goButtons: ["go"],
                              restoredPlan: nil),
        sameElement: sameString) == nil,
        "Go-only staged publication became ready")
    rejected = false
    do {
        _ = try PathEntryPolicy.baseline(
            pathSnapshot(goButtons: ["preexisting-go"]),
            expectedPlan: SelectionPlan(
                navigation: .commandShiftG, chooserTitle: "Open"),
            sameElement: sameString)
    } catch ProbeError.validation { rejected = true }
    try require(rejected, "preexisting Go control allowed the shortcut baseline")
    for ambiguous in [
        pathSnapshot(fields: ["search", "path-one", "path-two"], goButtons: ["go"],
                     restoredPlan: nil),
        pathSnapshot(fields: ["search", "path"], goButtons: ["go-one", "go-two"],
                     restoredPlan: nil),
    ] {
        rejected = false
        do {
            _ = try PathEntryPolicy.candidate(
                baseline: pathBaseline, current: ambiguous, sameElement: sameString)
        } catch ProbeError.validation { rejected = true }
        try require(rejected, "ambiguous path-entry identity delta passed")
    }
    rejected = false
    do {
        _ = try PathEntryPolicy.candidate(
            baseline: pathBaseline,
            current: pathSnapshot(chooser: "replacement", fields: ["search", "path"],
                                  goButtons: ["go"], restoredPlan: nil),
            sameElement: sameString)
    } catch ProbeError.validation { rejected = true }
    try require(rejected, "replaced Open-panel anchor passed")
    rejected = false
    do {
        _ = try PathEntryPolicy.candidate(
            baseline: pathBaseline,
            current: pathSnapshot(fields: ["replacement", "path"],
                                  goButtons: ["go"], restoredPlan: nil),
            sameElement: sameString)
    } catch ProbeError.validation { rejected = true }
    try require(rejected, "replaced baseline text field passed")

    var pathNow: UInt64 = 0
    var pathSnapshots = [
        pathSnapshot(fields: ["search", "path"], restoredPlan: nil),
        pathSnapshot(fields: ["search", "path"], goButtons: ["go"], restoredPlan: nil),
    ]
    var pathIdentityChecks = 0
    let pathToken = try waitForUniquePathEntry(
        timeoutNanoseconds: 1_000_000_000,
        pollMicroseconds: 100,
        baseline: pathBaseline,
        nowNanoseconds: { pathNow },
        validateIdentity: { pathIdentityChecks += 1 },
        readSnapshot: { pathSnapshots.removeFirst() },
        sameElement: sameString,
        pause: { pathNow += UInt64($0) * 1_000 })
    try require(pathToken.field == "path" && pathToken.goButton == "go" &&
                    pathToken.pollCount == 2 && pathIdentityChecks == 4,
                "staged path-entry wait did not preserve identity and poll bounds")

    pathNow = 0
    var pathTimeoutReads = 0
    rejected = false
    do {
        _ = try waitForUniquePathEntry(
            timeoutNanoseconds: 300_000,
            pollMicroseconds: 100,
            baseline: pathBaseline,
            nowNanoseconds: { pathNow },
            validateIdentity: {},
            readSnapshot: {
                pathTimeoutReads += 1
                return pathSnapshot(restoredPlan: nil)
            },
            sameElement: sameString,
            pause: { pathNow += UInt64($0) * 1_000 })
    } catch ProbeError.unavailable(let message) {
        rejected = message ==
            "path-entry controls did not become ready after 3 polls; " +
            "last pending state: new-fields=0 go-buttons=0 extra-cancels=0 " +
            "chooser-enabled=true"
    }
    try require(rejected && pathTimeoutReads == 3,
                "path-entry timeout used the wrong monotonic poll bound")

    pathNow = 0
    var pathDriftChecks = 0
    var pathDriftReads = 0
    rejected = false
    do {
        _ = try waitForUniquePathEntry(
            timeoutNanoseconds: 1_000_000_000,
            pollMicroseconds: 100,
            baseline: pathBaseline,
            nowNanoseconds: { pathNow },
            validateIdentity: {
                pathDriftChecks += 1
                if pathDriftChecks == 2 {
                    throw ProbeError.validation("test path-entry PID drift")
                }
            },
            readSnapshot: {
                pathDriftReads += 1
                return pathSnapshot(fields: ["search", "path"],
                                    goButtons: ["go"], restoredPlan: nil)
            },
            sameElement: sameString,
            pause: { pathNow += UInt64($0) * 1_000 })
    } catch ProbeError.validation { rejected = true }
    try require(rejected && pathDriftReads == 1,
                "path-entry PID drift did not stop at the read boundary")

    pathNow = 0
    var restoreSnapshots = [
        pathSnapshot(fields: ["search", "path"], goButtons: ["go"], restoredPlan: nil),
        pathSnapshot(destinationPaths: ["fixture"]),
    ]
    let restored = try waitForRestoredOpenPanel(
        timeoutNanoseconds: 1_000_000_000,
        pollMicroseconds: 100,
        baseline: pathBaseline,
        expectedDestination: "fixture",
        nowNanoseconds: { pathNow },
        validateIdentity: {},
        readSnapshot: { restoreSnapshots.removeFirst() },
        sameElement: sameString,
        pause: { pathNow += UInt64($0) * 1_000 })
    try require(restored.pollCount == 2 && restored.chooser == "chooser",
                "Open-panel restoration did not retain the exact chooser identity")
    try require(!(try PathEntryPolicy.restored(
        baseline: pathBaseline,
        current: pathSnapshot(additionalCancelCount: 1,
                              destinationPaths: ["fixture"]),
        expectedDestination: "fixture", sameElement: sameString)),
        "lingering overlay Cancel allowed Open-panel restoration")

    pathNow = 0
    var destinationlessReads = 0
    rejected = false
    do {
        _ = try waitForRestoredOpenPanel(
            timeoutNanoseconds: 300_000,
            pollMicroseconds: 100,
            baseline: pathBaseline,
            expectedDestination: "fixture",
            nowNanoseconds: { pathNow },
            validateIdentity: {},
            readSnapshot: {
                destinationlessReads += 1
                return pathSnapshot()
            },
            sameElement: sameString,
            pause: { pathNow += UInt64($0) * 1_000 })
    } catch ProbeError.unavailable(let message) {
        rejected = message == "Open panel did not restore after path entry in 3 polls"
    }
    try require(rejected && destinationlessReads == 3,
                "restored controls without exact destination allowed chooser readiness")

    pathNow = 0
    var restoreDriftChecks = 0
    var restoreDriftReads = 0
    rejected = false
    do {
        _ = try waitForRestoredOpenPanel(
            timeoutNanoseconds: 1_000_000_000,
            pollMicroseconds: 100,
            baseline: pathBaseline,
            expectedDestination: "fixture",
            nowNanoseconds: { pathNow },
            validateIdentity: {
                restoreDriftChecks += 1
                if restoreDriftChecks == 2 {
                    throw ProbeError.validation("test restoration PID drift")
                }
            },
            readSnapshot: {
                restoreDriftReads += 1
                return pathSnapshot(destinationPaths: ["fixture"])
            },
            sameElement: sameString,
            pause: { pathNow += UInt64($0) * 1_000 })
    } catch ProbeError.validation { rejected = true }
    try require(rejected && restoreDriftReads == 1,
                "restoration PID drift did not stop at the read boundary")

    let unrelatedWindow = testElement(role: kAXWindowRole,
                                      subrole: kAXStandardWindowSubrole)
    var delayedSnapshots: [[(String, ElementDescription)]] = [
        [], [], [("unrelated", unrelatedWindow)], [("original", panel)],
    ]
    var fakeNow: UInt64 = 0
    var identityChecks = 0
    let delayedPanel = try waitForUniqueOpenPanel(
        timeoutNanoseconds: 1_000_000_000,
        pollMicroseconds: 100,
        permitKeyFallback: false,
        nowNanoseconds: { fakeNow },
        validateIdentity: { identityChecks += 1 },
        readWindows: { delayedSnapshots.removeFirst() },
        sameElement: sameString,
        pause: { fakeNow += UInt64($0) * 1_000 })
    try require(delayedPanel.panel == "original", "delayed Open panel was not selected")
    try require(identityChecks == 8, "PID identity was not checked around every AX snapshot")

    var lookalikeReads = 0
    var lookalikePauses = 0
    rejected = false
    do {
        _ = try waitForUniqueOpenPanel(
            timeoutNanoseconds: 1_000_000_000,
            pollMicroseconds: 100,
            permitKeyFallback: false,
            nowNanoseconds: { 0 },
            validateIdentity: {},
            readWindows: {
                lookalikeReads += 1
                return [("lookalike", wrongSheet)]
            },
            sameElement: sameString,
            pause: { _ in lookalikePauses += 1 })
    } catch ProbeError.validation { rejected = true }
    try require(rejected && lookalikeReads == 1 && lookalikePauses == 0,
                "panel-shaped lookalike did not fail immediately")

    fakeNow = 0
    var timeoutReads = 0
    rejected = false
    do {
        _ = try waitForUniqueOpenPanel(
            timeoutNanoseconds: 300_000,
            pollMicroseconds: 100,
            permitKeyFallback: false,
            nowNanoseconds: { fakeNow },
            validateIdentity: {},
            readWindows: {
                timeoutReads += 1
                return [] as [(String, ElementDescription)]
            },
            sameElement: sameString,
            pause: { fakeNow += UInt64($0) * 1_000 })
    } catch ProbeError.unavailable(let message) {
        rejected = message == "Open panel did not become ready after 3 polls"
    }
    try require(rejected && timeoutReads == 3, "Open panel timeout used the wrong poll bound")

    fakeNow = 0
    var ambiguitySnapshots = [
        [] as [(String, ElementDescription)],
        [("one", panel), ("two", panel)],
    ]
    var ambiguityReads = 0
    var ambiguityPauses = 0
    rejected = false
    do {
        _ = try waitForUniqueOpenPanel(
            timeoutNanoseconds: 1_000_000_000,
            pollMicroseconds: 100,
            permitKeyFallback: false,
            nowNanoseconds: { fakeNow },
            validateIdentity: {},
            readWindows: {
                ambiguityReads += 1
                return ambiguitySnapshots.removeFirst()
            },
            sameElement: sameString,
            pause: {
                ambiguityPauses += 1
                fakeNow += UInt64($0) * 1_000
            })
    } catch ProbeError.validation { rejected = true }
    try require(rejected && ambiguityReads == 2 && ambiguityPauses == 1,
                "absent-then-ambiguous Open panels did not fail immediately")

    fakeNow = 0
    var driftChecks = 0
    var driftReads = 0
    rejected = false
    do {
        _ = try waitForUniqueOpenPanel(
            timeoutNanoseconds: 1_000_000_000,
            pollMicroseconds: 100,
            permitKeyFallback: false,
            nowNanoseconds: { fakeNow },
            validateIdentity: {
                driftChecks += 1
                if driftChecks == 3 { throw ProbeError.validation("test PID drift") }
            },
            readWindows: {
                driftReads += 1
                return [] as [(String, ElementDescription)]
            },
            sameElement: sameString,
            pause: { fakeNow += UInt64($0) * 1_000 })
    } catch ProbeError.validation { rejected = true }
    try require(rejected && driftReads == 1, "PID drift did not stop before another AX read")

    try require(!PathPolicy.contains("/private/tmp/root", "/private/tmp/root2/file"), "prefix collision")
    try require(PathPolicy.contains("/private/tmp/root", "/private/tmp/root/file"), "contained path")
    try require(Phase(rawValue: "select-project") == .selectProject, "phase parsing")
    try require(KeyboardShortcut.pathEntry.virtualKey == 5 &&
                KeyboardShortcut.pathEntry.flags == [.maskCommand, .maskShift], "path-entry shortcut")

    let optionArguments = [
        "--pid", "2",
        "--run-root", "/private/tmp/chatgpt-route-prototype-08.options",
        "--expected-bundle", "/private/tmp/chatgpt-route-prototype-08.options/Probe.app",
        "--expected-executable",
        "/private/tmp/chatgpt-route-prototype-08.options/Probe.app/Contents/MacOS/ChatGPT",
        "--fixture-root", "/private/tmp/chatgpt-route-prototype-08.options/workspace",
        "--phase", "select-project",
        "--event-log", "/private/tmp/chatgpt-route-prototype-08.options/logs/native-gui-probe.jsonl",
    ]
    rejected = false
    do { _ = try parseOptions(optionArguments) }
    catch ProbeError.usage(let message) {
        rejected = message == "select-project requires --press-open-folder-menu-item"
    }
    try require(rejected, "select-project omitted explicit Open Folder authorization")
    let authorizedOptions = try parseOptions(optionArguments + ["--press-open-folder-menu-item"])
    try require(authorizedOptions.pressOpenFolderMenuItem,
                "Open Folder menu authorization was not retained")
    let inspectArguments = optionArguments.map { $0 == "select-project" ? "inspect-project-picker" : $0 }
    rejected = false
    do { _ = try parseOptions(inspectArguments + ["--press-open-folder-menu-item"]) }
    catch ProbeError.usage(let message) {
        rejected = message == "--press-open-folder-menu-item is only valid for select-project"
    }
    try require(rejected, "inspect-project-picker accepted Open Folder authorization")
    rejected = false
    do {
        _ = try parseOptions(optionArguments + ["--press-open-folder-menu-item",
                                                 "--press-open-folder-menu-item"])
    } catch ProbeError.usage(let message) {
        rejected = message == "duplicate --press-open-folder-menu-item"
    }
    try require(rejected, "duplicate Open Folder authorization passed")
    let menuInspectArguments = optionArguments.map {
        $0 == "select-project" ? "inspect-open-folder-menu" : $0
    }
    let menuInspectOptions = try parseOptions(menuInspectArguments)
    try require(menuInspectOptions.phase == .inspectOpenFolderMenu &&
                !menuInspectOptions.pressOpenFolderMenuItem,
                "read-only Open Folder menu inspection options")
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
                                        "keyFallbackAuthorized": options.permitKeyFallback,
                                        "openFolderMenuPressAuthorized":
                                            options.pressOpenFolderMenuItem])
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

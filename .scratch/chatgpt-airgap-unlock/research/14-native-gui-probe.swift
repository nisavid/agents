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
    case retryable(String)
    case unavailable(String)
    case permission(String)

    var description: String {
        switch self {
        case .usage(let message), .validation(let message), .retryable(let message),
             .unavailable(let message),
             .permission(let message): return message
        }
    }

    var exitCode: Int32 {
        switch self {
        case .usage: return 64
        case .validation: return 65
        case .retryable, .unavailable: return 69
        case .permission: return 77
        }
    }
}

enum Phase: String, CaseIterable {
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
    let acceptRendererProjectPickerRequest: Bool
    let validateInputsOnly: Bool
}

struct ValidatedPaths {
    let runRoot: String
    let bundle: String
    let executable: String
    let fixture: String
    let fixtureIdentity: FileSystemIdentity
    let fixtureParent: String
    let fixtureParentIdentity: FileSystemIdentity
    let eventLog: String
}

struct ProcessIdentity: Equatable {
    let pid: pid_t
    let startSeconds: UInt64
    let startMicroseconds: UInt64
    let executable: String
}

struct FileSystemIdentity: Equatable {
    let device: UInt64
    let inode: UInt64
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
        let fixtureParent = try canonicalExisting(
            (canonicalFixture as NSString).deletingLastPathComponent)
        return ValidatedPaths(runRoot: root, bundle: canonicalBundle,
                              executable: canonicalExecutable, fixture: canonicalFixture,
                              fixtureIdentity: try fileSystemIdentity(canonicalFixture),
                              fixtureParent: fixtureParent,
                              fixtureParentIdentity: try fileSystemIdentity(fixtureParent),
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

    static func plan(windows: [ElementDescription]) throws -> SelectionPlan {
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
        let columnViews = all.filter {
            $0.role == kAXBrowserRole && $0.identifier == "ColumnView"
        }
        guard columnViews.count == 1 else {
            throw ProbeError.validation(
                "expected exactly one Open panel ColumnView browser; found \(min(columnViews.count, 2))")
        }
        guard chooser.identifier == nil || chooser.identifier == "OKButton" else {
            throw ProbeError.validation("Open panel chooser is not the exact OKButton")
        }
        return SelectionPlan(chooserTitle: title)
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
    var acceptRendererProjectPickerRequest = false
    var validateInputsOnly = false
    var index = 0
    let orderedValueOptions = ["--pid", "--run-root", "--expected-bundle", "--expected-executable",
                               "--fixture-root", "--phase", "--event-log"]
    let valueOptions = Set(orderedValueOptions)
    while index < arguments.count {
        let argument = arguments[index]
        if argument == "--accept-renderer-project-picker-request" {
            guard !acceptRendererProjectPickerRequest else {
                throw ProbeError.usage(
                    "duplicate --accept-renderer-project-picker-request")
            }
            acceptRendererProjectPickerRequest = true
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
            "--phase must be select-project")
    }
    if phase == .selectProject && !acceptRendererProjectPickerRequest {
        throw ProbeError.usage(
            "select-project requires --accept-renderer-project-picker-request")
    }
    return Options(pid: numericPID, runRoot: values["--run-root"]!,
                   expectedBundle: values["--expected-bundle"]!,
                   expectedExecutable: values["--expected-executable"]!,
                   fixtureRoot: values["--fixture-root"]!, phase: phase,
                   eventLog: values["--event-log"]!,
                   acceptRendererProjectPickerRequest: acceptRendererProjectPickerRequest,
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

func appKitRegistrationSample(
    running: NSRunningApplication
) throws -> AppKitRegistrationSample {
    let bundlePath = try running.bundleURL.map {
        try PathPolicy.canonicalExisting($0.path)
    }
    let executablePath = try running.executableURL.map {
        try PathPolicy.canonicalExisting($0.path)
    }
    return .published(isTerminated: running.isTerminated,
                      bundlePath: bundlePath, executablePath: executablePath)
}

func appKitRegistrationSample(pid: pid_t) throws -> AppKitRegistrationSample {
    guard let running = NSRunningApplication(processIdentifier: pid) else {
        return .unavailable
    }
    return try appKitRegistrationSample(running: running)
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

func strictStringAttribute(
    _ element: AXUIElement,
    _ name: CFString,
    purpose: String
) throws -> String {
    var value: CFTypeRef?
    let status = AXUIElementCopyAttributeValue(element, name, &value)
    if status == .cannotComplete {
        throw ProbeError.retryable(
            "\(purpose) read was temporarily incomplete")
    }
    guard status == .success else {
        throw ProbeError.unavailable(
            "\(purpose) read failed: \(status.rawValue)")
    }
    guard let published = value as? String else {
        throw ProbeError.validation("\(purpose) is malformed")
    }
    return published
}

func strictOptionalStringAttribute(
    _ element: AXUIElement,
    _ name: CFString,
    purpose: String
) throws -> String? {
    var value: CFTypeRef?
    let status = AXUIElementCopyAttributeValue(element, name, &value)
    if status == .attributeUnsupported || status == .noValue { return nil }
    if status == .cannotComplete {
        throw ProbeError.retryable(
            "\(purpose) read was temporarily incomplete")
    }
    guard status == .success else {
        throw ProbeError.unavailable(
            "\(purpose) read failed: \(status.rawValue)")
    }
    guard let published = value as? String else {
        throw ProbeError.validation("\(purpose) is malformed")
    }
    return published
}

func strictOptionalBoolAttribute(
    _ element: AXUIElement,
    _ name: CFString,
    purpose: String
) throws -> Bool? {
    var value: CFTypeRef?
    let status = AXUIElementCopyAttributeValue(element, name, &value)
    if status == .attributeUnsupported || status == .noValue { return nil }
    if status == .cannotComplete {
        throw ProbeError.retryable(
            "\(purpose) read was temporarily incomplete")
    }
    guard status == .success else {
        throw ProbeError.unavailable(
            "\(purpose) read failed: \(status.rawValue)")
    }
    guard let published = value as? Bool else {
        throw ProbeError.validation("\(purpose) is malformed")
    }
    return published
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

func lexicalLocalFileURLPath(_ url: URL) -> String? {
    guard url.isFileURL,
          url.user == nil, url.password == nil,
          url.query == nil, url.fragment == nil,
          url.host == nil || url.host == "" || url.host == "localhost" else {
        return nil
    }
    let publishedPath = url.path
    guard publishedPath.hasPrefix("/"), !publishedPath.isEmpty,
          !publishedPath.unicodeScalars.contains(where: {
            $0.value == 0 || $0.value < 0x20 ||
                (0x7f...0x9f).contains($0.value)
          }) else { return nil }
    var components: [Substring] = []
    for component in publishedPath.split(separator: "/", omittingEmptySubsequences: false) {
        if component.isEmpty || component == "." { continue }
        if component == ".." {
            guard !components.isEmpty else { return nil }
            components.removeLast()
        } else {
            components.append(component)
        }
    }
    return "/" + components.joined(separator: "/")
}

func documentURLPathPublication(
    _ value: CFTypeRef?
) -> AttributePublication<String> {
    guard let publishedString = value as? String else { return .malformed }
    guard !publishedString.isEmpty else { return .missing }
    guard let url = URL(string: publishedString),
          let path = lexicalLocalFileURLPath(url) else { return .malformed }
    return .value(path)
}

func accessibilityURLPathPublication(
    _ value: CFTypeRef?
) -> AttributePublication<String> {
    guard let value, CFGetTypeID(value) == CFURLGetTypeID() else {
        return .malformed
    }
    let url = unsafeBitCast(value, to: CFURL.self) as URL
    guard let path = lexicalLocalFileURLPath(url) else { return .malformed }
    return .value(path)
}

func documentURLPathAttributePublication(
    _ element: AXUIElement,
    _ name: CFString
) -> AttributePublication<String> {
    var value: CFTypeRef?
    let status = AXUIElementCopyAttributeValue(element, name, &value)
    return attributePublication(status: status) {
        documentURLPathPublication(value)
    }
}

func accessibilityURLPathAttributePublication(
    _ element: AXUIElement,
    _ name: CFString
) -> AttributePublication<String> {
    var value: CFTypeRef?
    let status = AXUIElementCopyAttributeValue(element, name, &value)
    return attributePublication(status: status) {
        accessibilityURLPathPublication(value)
    }
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
        throw axPublicationReadError(
            status, purpose: "Open panel \(attributeName) destination")
    }
}

func axPublicationReadError(_ status: Int32, purpose: String) -> ProbeError {
    if status == AXError.cannotComplete.rawValue {
        return .retryable("\(purpose) read was temporarily incomplete")
    }
    return .unavailable("\(purpose) read failed: \(status)")
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

func consumeTraversalNode(depth: Int, remainingNodes: inout Int) throws {
    guard depth <= 16 else {
        throw ProbeError.validation("AX tree exceeded depth limit")
    }
    guard remainingNodes > 0 else {
        throw ProbeError.validation("AX tree exceeded node limit")
    }
    remainingNodes -= 1
}

func describe(_ element: AXUIElement, depth: Int = 0, budget: inout Int) throws -> ElementDescription {
    try consumeTraversalNode(depth: depth, remainingNodes: &budget)
    let children = try strictChildElementsForEvidence(
        element, purpose: "Open panel topology").map {
            try describe($0, depth: depth + 1, budget: &budget)
        }
    return try describeShallow(element, children: children)
}

func describeShallow(
    _ element: AXUIElement,
    children: [ElementDescription] = []
) throws -> ElementDescription {
    let role = try strictStringAttribute(
        element, kAXRoleAttribute as CFString,
        purpose: "Open panel topology role")
    let publishedActions = role == kAXButtonRole ?
        try strictActions(element, purpose: "Open panel topology button") : []
    return ElementDescription(
        role: role,
        subrole: try strictOptionalStringAttribute(
            element, kAXSubroleAttribute as CFString,
            purpose: "Open panel topology subrole"),
        identifier: try strictOptionalStringAttribute(
            element, kAXIdentifierAttribute as CFString,
            purpose: "Open panel topology identifier"),
        title: try strictOptionalStringAttribute(
            element, kAXTitleAttribute as CFString,
            purpose: "Open panel topology title"),
        help: try strictOptionalStringAttribute(
            element, kAXHelpAttribute as CFString,
            purpose: "Open panel topology help"),
        enabled: try strictOptionalBoolAttribute(
            element, kAXEnabledAttribute as CFString,
            purpose: "Open panel topology enabled state"),
        actions: publishedActions,
        children: children)
}

func describedWindows(
    _ windows: [AXUIElement],
    perWindowNodeLimit: Int
) throws -> [(AXUIElement, ElementDescription)] {
    precondition(perWindowNodeLimit > 0)
    return try windows.map { window in
        var budget = perWindowNodeLimit
        return (window, try describe(window, budget: &budget))
    }
}

func liveWindows(_ application: AXUIElement) throws -> [(AXUIElement, ElementDescription)] {
    guard let windows = attribute(application, kAXWindowsAttribute as CFString) as? [AXUIElement] else {
        throw ProbeError.validation("application exposes no AX windows")
    }
    return try describedWindows(windows, perWindowNodeLimit: 500)
}

enum AXWindowReadDisposition: Equatable {
    case published
    case pending
    case unsupported
    case malformed
    case failed
}

enum AXChildrenReadDisposition: Equatable {
    case published
    case leaf
    case malformed
    case failed
}

func classifyAXChildrenRead(
    status: AXError,
    valueIsElementArray: Bool
) -> AXChildrenReadDisposition {
    if status == .success {
        return valueIsElementArray ? .published : .malformed
    }
    if status == .attributeUnsupported || status == .noValue { return .leaf }
    return .failed
}

func classifyAXWindowRead(
    status: AXError,
    valueIsElementArray: Bool
) -> AXWindowReadDisposition {
    if status == .success {
        return valueIsElementArray ? .published : .malformed
    }
    if status == .noValue || status == .cannotComplete { return .pending }
    if status == .attributeUnsupported { return .unsupported }
    return .failed
}

func liveWindowsForOpenPanelReadiness(
    _ application: AXUIElement
) throws -> [(AXUIElement, ElementDescription)]? {
    var value: CFTypeRef?
    let status = AXUIElementCopyAttributeValue(
        application, kAXWindowsAttribute as CFString, &value)
    let windows = value as? [AXUIElement]
    switch classifyAXWindowRead(
        status: status, valueIsElementArray: windows != nil) {
    case .pending:
        return nil
    case .unsupported:
        throw ProbeError.validation("application AX windows are unsupported")
    case .failed:
        throw ProbeError.unavailable(
            "application AX windows read failed: \(status.rawValue)")
    case .malformed:
        throw ProbeError.validation("application AX windows are malformed")
    case .published:
        break
    }
    guard let windows else { preconditionFailure("published AX windows require an element array") }
    return try describedWindows(windows, perWindowNodeLimit: 5_000)
}

func liveBoundedNestedSheetsForOpenPanelReadiness(
    _ application: AXUIElement
) throws -> [(AXUIElement, ElementDescription)]? {
    var windowsValue: CFTypeRef?
    let windowsStatus = AXUIElementCopyAttributeValue(
        application, kAXWindowsAttribute as CFString, &windowsValue)
    guard windowsStatus == .success,
          let windows = windowsValue as? [AXUIElement] else {
        if windowsStatus == .noValue || windowsStatus == .cannotComplete {
            return nil
        }
        throw ProbeError.unavailable(
            "application AX windows read failed before parented sheet lookup: \(windowsStatus.rawValue)")
    }
    guard windows.count <= 8 else {
        throw ProbeError.validation("application exposes too many AX windows")
    }
    var pending = windows.map { (element: $0, depth: 0) }
    var nextIndex = 0
    var visitedCount = 0
    var incompleteRead = false
    var sheets: [AXUIElement] = []
    while nextIndex < pending.count {
        guard visitedCount < 1_000 else {
            throw ProbeError.validation("bounded AX sheet discovery exceeded node limit")
        }
        let current = pending[nextIndex]
        nextIndex += 1
        visitedCount += 1
        let role = try strictStringAttribute(
            current.element, kAXRoleAttribute as CFString,
            purpose: "bounded AX sheet discovery role")
        if role == kAXSheetRole {
            sheets.append(current.element)
            continue
        }
        guard !isNestedApplicationBackReference(
            role: role, depth: current.depth) else { continue }
        var childrenValue: CFTypeRef?
        let childrenStatus = AXUIElementCopyAttributeValue(
            current.element, kAXChildrenAttribute as CFString, &childrenValue)
        if childrenStatus == .success {
            guard let publishedChildren = childrenValue as? [AXUIElement] else {
                throw ProbeError.validation("bounded AX sheet discovery children are malformed")
            }
            guard publishedChildren.count <= 128 else {
                throw ProbeError.validation("bounded AX sheet discovery child fanout is excessive")
            }
            if current.depth == 8 {
                guard publishedChildren.isEmpty else {
                    throw ProbeError.validation(
                        "bounded AX sheet discovery exceeded depth limit: " +
                        "role=\(role) children=\(publishedChildren.count)")
                }
            } else {
                pending.append(contentsOf: publishedChildren.map {
                    (element: $0, depth: current.depth + 1)
                })
            }
        } else if childrenStatus == .cannotComplete {
            incompleteRead = true
        } else if childrenStatus != .noValue && childrenStatus != .attributeUnsupported {
            throw ProbeError.unavailable(
                "bounded AX sheet discovery child read failed: \(childrenStatus.rawValue)")
        }
    }
    if incompleteRead { return nil }
    let uniqueSheets = identityDeduplicated(sheets, sameElement: sameAXElement)
    guard uniqueSheets.count <= 1 else {
        throw ProbeError.validation("multiple bounded AXSheets appeared")
    }
    guard let sheet = uniqueSheets.first else { return nil }
    return try describedWindows([sheet], perWindowNodeLimit: 5_000)
}

func descendants(_ element: AXUIElement, depth: Int = 0, budget: inout Int) throws -> [AXUIElement] {
    try consumeTraversalNode(depth: depth, remainingNodes: &budget)
    let children = try strictChildElementsForEvidence(
        element, purpose: "Open panel live topology")
    return children + (try children.flatMap { try descendants($0, depth: depth + 1, budget: &budget) })
}

func strictChildElementsForEvidence(
    _ element: AXUIElement,
    purpose: String
) throws -> [AXUIElement] {
    var value: CFTypeRef?
    let status = AXUIElementCopyAttributeValue(
        element, kAXChildrenAttribute as CFString, &value)
    let children = value as? [AXUIElement]
    switch classifyAXChildrenRead(
        status: status, valueIsElementArray: children != nil) {
    case .published:
        guard let children else {
            throw ProbeError.validation("\(purpose) AXChildren are malformed")
        }
        return children
    case .leaf:
        return []
    case .malformed:
        throw ProbeError.validation("\(purpose) AXChildren are malformed")
    case .failed:
        if status == .cannotComplete {
            throw ProbeError.retryable(
                "\(purpose) AXChildren read was temporarily incomplete")
        }
        throw ProbeError.unavailable(
            "\(purpose) AXChildren read failed: \(status.rawValue)")
    }
}

func strictEvidenceDescendants(
    _ element: AXUIElement,
    depth: Int = 0,
    budget: inout Int,
    purpose: String = "Open panel row evidence"
) throws -> [AXUIElement] {
    try consumeTraversalNode(depth: depth, remainingNodes: &budget)
    let children = try strictChildElementsForEvidence(
        element, purpose: purpose)
    return children + (try children.flatMap {
        try strictEvidenceDescendants(
            $0, depth: depth + 1, budget: &budget, purpose: purpose)
    })
}

func matchingLiveElements(
    _ root: AXUIElement,
    nodeLimit: Int,
    _ predicate: (AXUIElement) -> Bool
) throws -> [AXUIElement] {
    precondition(nodeLimit > 0)
    var budget = nodeLimit
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
// Covers the renderer's 20-second CDP target acquisition plus three 10-second
// picker-control waits, with a bounded margin for the final request.
let openPanelWaitTimeoutNanoseconds: UInt64 = 60_000_000_000

func waitForUniqueOpenPanel<Element>(
    timeoutNanoseconds: UInt64,
    pollMicroseconds: useconds_t,
    nowNanoseconds: () throws -> UInt64,
    validateIdentity: () throws -> Void,
    readWindows: () throws -> [(Element, ElementDescription)]?,
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
        let readResult: Result<[(Element, ElementDescription)]?, Error>
        do {
            readResult = .success(try readWindows())
        } catch ProbeError.retryable {
            readResult = .success(nil)
        } catch {
            readResult = .failure(error)
        }
        try validateIdentity()
        let windows = try readResult.get()
        pollCount += 1
        if let windows {
            let descriptions = windows.map(\.1)
            let shapedIndices = OpenPanelPolicy.panelShapedIndices(windows: descriptions)
            guard shapedIndices.count <= 1 else {
                throw ProbeError.validation(
                    "expected exactly one panel-shaped Open candidate; found \(shapedIndices.count)")
            }
            if let selectedIndex = shapedIndices.first {
                let plan = try OpenPanelPolicy.plan(
                    windows: [descriptions[selectedIndex]])
                let selected = windows[selectedIndex].0
                guard windows.filter({ sameElement($0.0, selected) }).count == 1 else {
                    throw ProbeError.validation("live Open panel identity is ambiguous")
                }
                return OpenPanelReadiness(panel: selected, plan: plan,
                                          initialElements: windows.map(\.0), pollCount: pollCount)
            }
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
    identity: ProcessIdentity
) throws -> OpenPanelReadiness<AXUIElement> {
    try waitForUniqueOpenPanel(
        timeoutNanoseconds: openPanelWaitTimeoutNanoseconds,
        pollMicroseconds: 100_000,
        nowNanoseconds: monotonicNanoseconds,
        validateIdentity: { try requireSameProcess(identity) },
        readWindows: {
            try liveBoundedNestedSheetsForOpenPanelReadiness(application)
        },
        sameElement: sameAXElement,
        pause: { _ = usleep($0) })
}
// END_READ_ONLY_OPEN_PANEL_WAIT

func requireSameProcess(_ expected: ProcessIdentity) throws {
    guard try processIdentity(expected.pid) == expected else {
        throw ProbeError.validation("PID identity changed before AX mutation")
    }
}

func performAtMutationBoundary(
    validateIdentity: () throws -> Void,
    mutation: () throws -> Void
) throws {
    try validateIdentity()
    try mutation()
    try validateIdentity()
}

func press(
    _ element: AXUIElement,
    purpose: String,
    validateIdentity: () throws -> Void
) throws {
    try performAtMutationBoundary(validateIdentity: validateIdentity) {
        guard actions(element).contains(kAXPressAction) else {
            throw ProbeError.validation("\(purpose) does not advertise AXPress")
        }
        guard AXUIElementPerformAction(element, kAXPressAction as CFString) == .success else {
            throw ProbeError.unavailable("AXPress failed for \(purpose)")
        }
    }
}

func sameAXElement(_ left: AXUIElement, _ right: AXUIElement) -> Bool {
    CFEqual(left, right)
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

func strictElementArrayAttribute(
    _ element: AXUIElement,
    _ name: CFString,
    purpose: String
) throws -> [AXUIElement] {
    var value: CFTypeRef?
    let status = AXUIElementCopyAttributeValue(element, name, &value)
    guard status == .success else {
        if status == .attributeUnsupported || status == .noValue {
            throw ProbeError.validation("\(purpose) is not published")
        }
        if status == .cannotComplete {
            throw ProbeError.retryable(
                "\(purpose) read was temporarily incomplete")
        }
        throw ProbeError.unavailable("\(purpose) read failed: \(status.rawValue)")
    }
    guard let values = value as? [AXUIElement] else {
        throw ProbeError.validation("\(purpose) is malformed")
    }
    return values
}

func strictActions(_ element: AXUIElement, purpose: String) throws -> Set<String> {
    var names: CFArray?
    let status = AXUIElementCopyActionNames(element, &names)
    if status == .cannotComplete {
        throw ProbeError.retryable(
            "\(purpose) action read was temporarily incomplete")
    }
    guard status == .success else {
        throw ProbeError.unavailable("\(purpose) action read failed: \(status.rawValue)")
    }
    guard let values = names as? [String] else {
        throw ProbeError.validation("\(purpose) actions are malformed")
    }
    return Set(values)
}

func requireExactCurrentOpenPanel<Element>(
    _ panel: Element,
    nestedSheets: [Element],
    sameElement: (Element, Element) -> Bool
) throws {
    guard nestedSheets.count == 1, sameElement(nestedSheets[0], panel) else {
        throw ProbeError.validation(
            "original Open panel is missing or ambiguous during selection")
    }
}

func requireExactOpenPanelCurrent(
    application: AXUIElement,
    panel: AXUIElement,
    process: ProcessIdentity
) throws {
    try requireSameProcess(process)
    guard let nestedSheets = try liveBoundedNestedSheetsForOpenPanelReadiness(
        application) else {
        throw ProbeError.retryable(
            "Open panel sheet topology read was temporarily incomplete")
    }
    try requireExactCurrentOpenPanel(
        panel, nestedSheets: nestedSheets.map(\.0), sameElement: sameAXElement)
    try requireSameProcess(process)
}

// BEGIN_PID_OPEN_PANEL_LIST_SELECTION
struct OpenPanelListSelectionToken<Element> {
    let panel: Element
    let browser: Element
    let list: Element
    let candidate: Element
    let chooser: Element
}

struct OpenPanelListSelectionSnapshot<Element> {
    let panels: [Element]
    let browsers: [Element]
    let lists: [Element]
    let candidates: [Element]
    let choosers: [Element]
    let selectedChildrenSettable: AttributePublication<Bool>
    let selectedChildren: ElementArrayPublication<Element>
    let chooserEnabled: Bool
    let chooserActions: Set<String>
    let listChildCount: Int
    let descendantListCount: Int
    let groupChildCount: Int
    let rowEvidenceElementCount: Int
    let urlPublisherCount: Int
    let publishedURLPathHashes: [String]
    let panelDestinationPathHashes: [String]
}

struct OpenPanelListSelectionReadiness<Element> {
    let token: OpenPanelListSelectionToken<Element>
    let pollCount: Int
}

enum ElementArrayPublication<Element> {
    case missing
    case malformed
    case readFailure(Int32)
    case value([Element])
}

enum OpenPanelListSelectionPolicy {
    static func rowMatchesExactFixture(
        role: String,
        pathPublications: [AttributePublication<String>],
        fixture: String
    ) throws -> Bool {
        var publishedPaths = Set<String>()
        for publication in pathPublications {
            switch publication {
            case .missing:
                continue
            case .malformed:
                throw ProbeError.validation(
                    "Open panel row descendant AXURL is malformed")
            case .readFailure(let status):
                throw axPublicationReadError(
                    status, purpose: "Open panel row descendant AXURL")
            case .value(let path):
                publishedPaths.insert(path)
            }
        }
        guard publishedPaths.contains(fixture) else { return false }
        guard role == kAXGroupRole else {
            throw ProbeError.validation(
                "Open panel exact fixture row is not AXGroup")
        }
        guard publishedPaths == Set([fixture]) else {
            throw ProbeError.validation(
                "Open panel exact fixture row publishes ambiguous paths")
        }
        return true
    }

    static func settable<Element>(
        _ snapshot: OpenPanelListSelectionSnapshot<Element>,
        pendingAllowed: Bool
    ) throws -> Bool? {
        switch snapshot.selectedChildrenSettable {
        case .missing:
            if pendingAllowed { return nil }
            throw ProbeError.validation("Open panel AXSelectedChildren writability is unpublished")
        case .malformed:
            throw ProbeError.validation("Open panel AXSelectedChildren writability is malformed")
        case .readFailure(let status):
            throw axPublicationReadError(
                status, purpose: "Open panel AXSelectedChildren writability")
        case .value(let value):
            return value
        }
    }

    static func selectedChildren<Element>(
        _ snapshot: OpenPanelListSelectionSnapshot<Element>,
        pendingAllowed: Bool
    ) throws -> [Element]? {
        switch snapshot.selectedChildren {
        case .missing:
            if pendingAllowed { return nil }
            throw ProbeError.validation("Open panel AXSelectedChildren is unpublished")
        case .malformed:
            throw ProbeError.validation("Open panel AXSelectedChildren is malformed")
        case .readFailure(let status):
            throw axPublicationReadError(
                status, purpose: "Open panel AXSelectedChildren")
        case .value(let children):
            return children
        }
    }

    static func token<Element>(
        _ snapshot: OpenPanelListSelectionSnapshot<Element>
    ) throws -> OpenPanelListSelectionToken<Element> {
        guard snapshot.panels.count == 1,
              snapshot.browsers.count == 1,
              snapshot.lists.count == 1,
              snapshot.candidates.count == 1,
              snapshot.choosers.count == 1 else {
            throw ProbeError.validation(
                "Open panel list selection is missing or ambiguous: " +
                "panel=\(min(snapshot.panels.count, 2)) " +
                "browser=\(min(snapshot.browsers.count, 2)) " +
                "list=\(min(snapshot.lists.count, 2)) " +
                "candidate=\(min(snapshot.candidates.count, 2)) " +
                "chooser=\(min(snapshot.choosers.count, 2))")
        }
        guard try settable(snapshot, pendingAllowed: false) == true else {
            throw ProbeError.validation("Open panel AXSelectedChildren is not writable")
        }
        _ = try selectedChildren(snapshot, pendingAllowed: false)
        return OpenPanelListSelectionToken(
            panel: snapshot.panels[0], browser: snapshot.browsers[0],
            list: snapshot.lists[0], candidate: snapshot.candidates[0],
            chooser: snapshot.choosers[0])
    }

    static func requireSameToken<Element>(
        _ expected: OpenPanelListSelectionToken<Element>,
        _ actual: OpenPanelListSelectionToken<Element>,
        sameElement: (Element, Element) -> Bool
    ) throws {
        guard sameElement(expected.panel, actual.panel),
              sameElement(expected.browser, actual.browser),
              sameElement(expected.list, actual.list),
              sameElement(expected.candidate, actual.candidate),
              sameElement(expected.chooser, actual.chooser) else {
            throw ProbeError.validation(
                "Open panel list-selection identities changed before mutation")
        }
    }

    static func requireSelected<Element>(
        _ snapshot: OpenPanelListSelectionSnapshot<Element>,
        token: OpenPanelListSelectionToken<Element>,
        sameElement: (Element, Element) -> Bool
    ) throws {
        guard let selected = try selectedChildren(
            snapshot, pendingAllowed: false),
              selected.count == 1,
              sameElement(selected[0], token.candidate) else {
            throw ProbeError.validation(
                "Open panel did not retain the exact selected candidate")
        }
    }

    static func pendingToken<Element>(
        _ snapshot: OpenPanelListSelectionSnapshot<Element>
    ) throws -> OpenPanelListSelectionToken<Element>? {
        guard snapshot.panels.count == 1 else {
            throw ProbeError.validation(
                "Open panel identity is missing or ambiguous during list selection")
        }
        guard snapshot.browsers.count <= 1,
              snapshot.lists.count <= 1,
              snapshot.choosers.count <= 1 else {
            throw ProbeError.validation(
                "Open panel list-selection anchors are ambiguous")
        }
        guard snapshot.candidates.count <= 1 else {
            throw ProbeError.validation("Open panel exact fixture candidate is ambiguous")
        }
        guard snapshot.browsers.count == 1,
              snapshot.lists.count == 1,
              snapshot.choosers.count == 1,
              snapshot.candidates.count == 1 else {
            return nil
        }
        guard try settable(snapshot, pendingAllowed: true) == true,
              try selectedChildren(snapshot, pendingAllowed: true) != nil else {
            return nil
        }
        return try token(snapshot)
    }

    static func pressReady<Element>(
        _ snapshot: OpenPanelListSelectionSnapshot<Element>,
        token: OpenPanelListSelectionToken<Element>,
        sameElement: (Element, Element) -> Bool
    ) throws -> Bool {
        guard let selected = try selectedChildren(
            snapshot, pendingAllowed: false) else {
            return false
        }
        if selected.isEmpty || !snapshot.chooserEnabled ||
            !snapshot.chooserActions.contains(kAXPressAction) {
            return false
        }
        try requireSelected(snapshot, token: token, sameElement: sameElement)
        return true
    }
}

func boolPublicationKind(_ publication: AttributePublication<Bool>) -> String {
    switch publication {
    case .missing: return "missing"
    case .malformed: return "malformed"
    case .readFailure: return "read-failure"
    case .value(true): return "true"
    case .value(false): return "false"
    }
}

func elementArrayPublicationKind<Element>(
    _ publication: ElementArrayPublication<Element>
) -> String {
    switch publication {
    case .missing: return "missing"
    case .malformed: return "malformed"
    case .readFailure: return "read-failure"
    case .value: return "value"
    }
}

func waitForReadyOpenPanelListSelection<Element>(
    timeoutNanoseconds: UInt64,
    pollMicroseconds: useconds_t,
    nowNanoseconds: () throws -> UInt64,
    validateIdentity: () throws -> Void,
    readSnapshot: () throws -> OpenPanelListSelectionSnapshot<Element>,
    pause: (useconds_t) -> Void
) throws -> OpenPanelListSelectionReadiness<Element>? {
    let started = try nowNanoseconds()
    let addition = started.addingReportingOverflow(timeoutNanoseconds)
    let deadline = addition.overflow ? UInt64.max : addition.partialValue
    var pollCount = 0
    while try nowNanoseconds() < deadline {
        try validateIdentity()
        let snapshot: OpenPanelListSelectionSnapshot<Element>
        do {
            snapshot = try readSnapshot()
        } catch ProbeError.retryable {
            try validateIdentity()
            pollCount += 1
            let current = try nowNanoseconds()
            guard current < deadline else { break }
            let remainingMicroseconds = (deadline - current + 999) / 1_000
            pause(useconds_t(min(UInt64(pollMicroseconds), remainingMicroseconds)))
            continue
        }
        try validateIdentity()
        pollCount += 1
        if let token = try OpenPanelListSelectionPolicy.pendingToken(snapshot) {
            return OpenPanelListSelectionReadiness(
                token: token, pollCount: pollCount)
        }
        let current = try nowNanoseconds()
        guard current < deadline else { break }
        let remainingMicroseconds = (deadline - current + 999) / 1_000
        pause(useconds_t(min(UInt64(pollMicroseconds), remainingMicroseconds)))
    }
    return nil
}

func waitForSelectedOpenPanelList<Element>(
    timeoutNanoseconds: UInt64,
    pollMicroseconds: useconds_t,
    expected: OpenPanelListSelectionToken<Element>,
    nowNanoseconds: () throws -> UInt64,
    revalidate: () throws -> (OpenPanelListSelectionToken<Element>,
                               OpenPanelListSelectionSnapshot<Element>),
    sameElement: (Element, Element) -> Bool,
    pause: (useconds_t) -> Void
) throws -> Int {
    let started = try nowNanoseconds()
    let addition = started.addingReportingOverflow(timeoutNanoseconds)
    let deadline = addition.overflow ? UInt64.max : addition.partialValue
    var pollCount = 0
    while try nowNanoseconds() < deadline {
        let actual: OpenPanelListSelectionToken<Element>
        let snapshot: OpenPanelListSelectionSnapshot<Element>
        do {
            (actual, snapshot) = try revalidate()
        } catch ProbeError.retryable {
            pollCount += 1
            let current = try nowNanoseconds()
            guard current < deadline else { break }
            let remainingMicroseconds = (deadline - current + 999) / 1_000
            pause(useconds_t(min(UInt64(pollMicroseconds), remainingMicroseconds)))
            continue
        }
        try OpenPanelListSelectionPolicy.requireSameToken(
            expected, actual, sameElement: sameElement)
        pollCount += 1
        if try OpenPanelListSelectionPolicy.pressReady(
            snapshot, token: actual, sameElement: sameElement) {
            return pollCount
        }
        let current = try nowNanoseconds()
        guard current < deadline else { break }
        let remainingMicroseconds = (deadline - current + 999) / 1_000
        pause(useconds_t(min(UInt64(pollMicroseconds), remainingMicroseconds)))
    }
    throw ProbeError.unavailable(
        "Open panel exact selection did not become press-ready after \(pollCount) polls")
}

func performValidatedOpenPanelListSelectionSet<Element>(
    initial: OpenPanelListSelectionToken<Element>,
    sameElement: (Element, Element) -> Bool,
    revalidate: () throws -> (OpenPanelListSelectionToken<Element>,
                               OpenPanelListSelectionSnapshot<Element>),
    setSelection: (OpenPanelListSelectionToken<Element>) throws -> Void
) throws {
    let (preflight, _) = try revalidate()
    try OpenPanelListSelectionPolicy.requireSameToken(
        initial, preflight, sameElement: sameElement)
    try setSelection(preflight)
    let (published, publishedSnapshot) = try revalidate()
    try OpenPanelListSelectionPolicy.requireSameToken(
        preflight, published, sameElement: sameElement)
    try OpenPanelListSelectionPolicy.requireSelected(
        publishedSnapshot, token: published, sameElement: sameElement)
}

func performValidatedOpenPanelListChooserPress<Element>(
    initial: OpenPanelListSelectionToken<Element>,
    sameElement: (Element, Element) -> Bool,
    revalidate: () throws -> (OpenPanelListSelectionToken<Element>,
                               OpenPanelListSelectionSnapshot<Element>),
    pressChooser: (OpenPanelListSelectionToken<Element>) throws -> Void
) throws {
    let (adjacent, snapshot) = try revalidate()
    try OpenPanelListSelectionPolicy.requireSameToken(
        initial, adjacent, sameElement: sameElement)
    guard try OpenPanelListSelectionPolicy.pressReady(
        snapshot, token: adjacent, sameElement: sameElement) else {
        throw ProbeError.validation(
            "Open panel exact selection is not press-ready at mutation boundary")
    }
    try pressChooser(adjacent)
}

func requireElementBelongsToProcess(
    _ element: AXUIElement,
    process: ProcessIdentity,
    purpose: String
) throws {
    var pid: pid_t = 0
    guard AXUIElementGetPid(element, &pid) == .success,
          pid == process.pid else {
        throw ProbeError.validation("\(purpose) does not belong to the exact PID")
    }
}

func fileSystemIdentity(_ path: String) throws -> FileSystemIdentity {
    var info = stat()
    guard stat(path, &info) == 0 else {
        throw ProbeError.validation("filesystem identity is unavailable: \(path)")
    }
    return FileSystemIdentity(
        device: UInt64(info.st_dev), inode: UInt64(info.st_ino))
}

func requireValidatedFixtureIdentity(_ paths: ValidatedPaths) throws {
    guard try fileSystemIdentity(paths.fixture) == paths.fixtureIdentity,
          try fileSystemIdentity(paths.fixtureParent) == paths.fixtureParentIdentity else {
        throw ProbeError.validation(
            "fixture or chooser-root filesystem identity changed")
    }
}

func requireMutationIdentity(
    process: ProcessIdentity,
    paths: ValidatedPaths
) throws {
    try requireValidatedFixtureIdentity(paths)
    try requireSameProcess(process)
    try validateCodeIdentity(pid: process.pid, paths: paths)
    try requireSameProcess(process)
}

func attributeSettablePublication(
    _ element: AXUIElement,
    _ name: CFString
) -> AttributePublication<Bool> {
    var settable = DarwinBoolean(false)
    let status = AXUIElementIsAttributeSettable(element, name, &settable)
    if status == .success { return .value(settable.boolValue) }
    if status == .attributeUnsupported || status == .noValue { return .missing }
    return .readFailure(status.rawValue)
}

func elementArrayAttributePublication(
    _ element: AXUIElement,
    _ name: CFString
) -> ElementArrayPublication<AXUIElement> {
    var value: CFTypeRef?
    let status = AXUIElementCopyAttributeValue(element, name, &value)
    if status == .attributeUnsupported || status == .noValue { return .missing }
    guard status == .success else { return .readFailure(status.rawValue) }
    guard let elements = value as? [AXUIElement] else { return .malformed }
    return .value(elements)
}

func liveOpenPanelListSelectionSnapshot(
    application: AXUIElement,
    panel: AXUIElement,
    chooserTitle: String,
    fixture: String,
    process: ProcessIdentity
) throws -> OpenPanelListSelectionSnapshot<AXUIElement> {
    try requireExactOpenPanelCurrent(
        application: application, panel: panel, process: process)
    let panelElements = try matchingLiveElements(
        panel, nodeLimit: 5_000) { _ in true }
    let browsers = try panelElements.filter {
        try strictStringAttribute(
            $0, kAXRoleAttribute as CFString,
            purpose: "Open panel element role") == kAXBrowserRole &&
            strictOptionalStringAttribute(
                $0, kAXIdentifierAttribute as CFString,
                purpose: "Open panel element identifier") == "ColumnView"
    }
    let descendantLists: [AXUIElement]
    if browsers.count == 1 {
        var browserBudget = 5_000
        descendantLists = try strictEvidenceDescendants(
            browsers[0], budget: &browserBudget).filter {
                try strictStringAttribute(
                    $0, kAXRoleAttribute as CFString,
                    purpose: "ColumnView descendant role") == kAXListRole
            }
    } else {
        descendantLists = []
    }
    var lists: [AXUIElement] = []
    var candidates: [AXUIElement] = []
    var publishedURLPathHashes = Set<String>()
    var listChildCount = 0
    var groupChildCount = 0
    var rowEvidenceElementCount = 0
    var urlPublisherCount = 0
    var rowEvidenceBudget = 5_000
    for list in descendantLists {
        let listChildren = try strictChildElementsForEvidence(
            list, purpose: "ColumnView list evidence")
        listChildCount += listChildren.count
        groupChildCount += try listChildren.filter {
            try strictStringAttribute(
                $0, kAXRoleAttribute as CFString,
                purpose: "ColumnView child role") == kAXGroupRole
        }.count
        var listMatches = 0
        for child in listChildren {
            let role = try strictStringAttribute(
                child, kAXRoleAttribute as CFString,
                purpose: "Open panel row role")
            let rowElements = [child] + (try strictEvidenceDescendants(
                child, budget: &rowEvidenceBudget))
            rowEvidenceElementCount += rowElements.count
            var pathPublications: [AttributePublication<String>] = []
            for rowElement in rowElements {
                try requireElementBelongsToProcess(
                    rowElement, process: process,
                    purpose: "Open panel row evidence element")
                let publication = accessibilityURLPathAttributePublication(
                    rowElement, kAXURLAttribute as CFString)
                pathPublications.append(publication)
                if case .value(let path) = publication {
                    urlPublisherCount += 1
                    publishedURLPathHashes.insert(sha256(path))
                }
            }
            if try OpenPanelListSelectionPolicy.rowMatchesExactFixture(
                role: role, pathPublications: pathPublications,
                fixture: fixture) {
                candidates.append(child)
                listMatches += 1
            }
        }
        if listMatches > 0 { lists.append(list) }
    }
    lists = identityDeduplicated(lists, sameElement: sameAXElement)
    candidates = identityDeduplicated(candidates, sameElement: sameAXElement)
    let choosers = try panelElements.filter {
        try strictStringAttribute(
            $0, kAXRoleAttribute as CFString,
            purpose: "Open panel element role") == kAXButtonRole &&
            strictOptionalStringAttribute(
                $0, kAXIdentifierAttribute as CFString,
                purpose: "Open panel button identifier") == "OKButton" &&
            strictOptionalStringAttribute(
                $0, kAXTitleAttribute as CFString,
                purpose: "Open panel button title")?.lowercased() ==
                    chooserTitle.lowercased()
    }
    for (element, purpose) in
        [(panel, "Open panel"),
         (browsers.first, "ColumnView browser"),
         (lists.first, "ColumnView list"),
         (candidates.first, "fixture candidate"),
         (choosers.first, "Open panel OKButton")]
        .compactMap({ element, purpose in element.map { ($0, purpose) } }) {
        try requireElementBelongsToProcess(
            element, process: process, purpose: purpose)
    }
    let selectedChildrenSettable: AttributePublication<Bool> = lists.count == 1 ?
        attributeSettablePublication(
            lists[0], kAXSelectedChildrenAttribute as CFString) : .missing
    let selectedChildren: ElementArrayPublication<AXUIElement> = lists.count == 1 ?
        elementArrayAttributePublication(
            lists[0], kAXSelectedChildrenAttribute as CFString) : .missing
    let panelDestinationPublications: [(String, AttributePublication<String>)] = [
        ("AXDocument", documentURLPathAttributePublication(
            panel, kAXDocumentAttribute as CFString)),
        ("AXURL", accessibilityURLPathAttributePublication(
            panel, kAXURLAttribute as CFString)),
    ]
    var panelDestinations: [String] = []
    for (name, publication) in panelDestinationPublications {
        if let path = try publishedDestinationPath(
            publication, attributeName: name) {
            panelDestinations.append(path)
        }
    }
    let expectedParent = (fixture as NSString).deletingLastPathComponent
    guard panelDestinations.allSatisfy({ $0 == expectedParent }) else {
        throw ProbeError.validation(
            "Open panel published a destination outside the exact fixture parent")
    }
    let panelDestinationPathHashes = panelDestinations.map(sha256).sorted()
    let chooserEnabled: Bool
    if choosers.count == 1 {
        chooserEnabled = try strictOptionalBoolAttribute(
            choosers[0], kAXEnabledAttribute as CFString,
            purpose: "Open panel OKButton enabled state") == true
    } else {
        chooserEnabled = false
    }
    let snapshot = OpenPanelListSelectionSnapshot(
        panels: [panel], browsers: browsers, lists: lists,
        candidates: candidates, choosers: choosers,
        selectedChildrenSettable: selectedChildrenSettable,
        selectedChildren: selectedChildren,
        chooserEnabled: chooserEnabled,
        chooserActions: choosers.count == 1 ?
            try strictActions(choosers[0], purpose: "Open panel OKButton") : [],
        listChildCount: listChildCount,
        descendantListCount: descendantLists.count,
        groupChildCount: groupChildCount,
        rowEvidenceElementCount: rowEvidenceElementCount,
        urlPublisherCount: urlPublisherCount,
        publishedURLPathHashes: publishedURLPathHashes.sorted(),
        panelDestinationPathHashes: panelDestinationPathHashes.sorted())
    try requireExactOpenPanelCurrent(
        application: application, panel: panel, process: process)
    return snapshot
}

func waitForValidatedOpenPanelListSelection(
    application: AXUIElement,
    panel: AXUIElement,
    chooserTitle: String,
    paths: ValidatedPaths,
    process: ProcessIdentity
) throws -> OpenPanelListSelectionReadiness<AXUIElement>? {
    try waitForReadyOpenPanelListSelection(
        timeoutNanoseconds: 3_000_000_000,
        pollMicroseconds: 100_000,
        nowNanoseconds: monotonicNanoseconds,
        validateIdentity: {
            try requireSameProcess(process)
            try requireValidatedFixtureIdentity(paths)
        },
        readSnapshot: {
            try liveOpenPanelListSelectionSnapshot(
                application: application, panel: panel,
                chooserTitle: chooserTitle, fixture: paths.fixture,
                process: process)
        },
        pause: { _ = usleep($0) })
}

func revalidateOpenPanelListSelectionToken(
    _ expected: OpenPanelListSelectionToken<AXUIElement>?,
    application: AXUIElement,
    panel: AXUIElement,
    chooserTitle: String,
    fixture: String,
    process: ProcessIdentity
) throws -> (OpenPanelListSelectionToken<AXUIElement>,
             OpenPanelListSelectionSnapshot<AXUIElement>) {
    try requireSameProcess(process)
    let snapshot = try liveOpenPanelListSelectionSnapshot(
        application: application, panel: panel, chooserTitle: chooserTitle,
        fixture: fixture, process: process)
    let token = try OpenPanelListSelectionPolicy.token(snapshot)
    if let expected {
        try OpenPanelListSelectionPolicy.requireSameToken(
            expected, token, sameElement: sameAXElement)
    }
    try requireSameProcess(process)
    return (token, snapshot)
}

func performValidatedOpenPanelListSelection(
    initial: OpenPanelListSelectionToken<AXUIElement>,
    application: AXUIElement,
    panel: AXUIElement,
    chooserTitle: String,
    paths: ValidatedPaths,
    process: ProcessIdentity
) throws -> (actionCount: Int, selectedPollCount: Int) {
    let requireFixtureIdentity = {
        try requireValidatedFixtureIdentity(paths)
    }
    let requireCurrentMutationIdentity = {
        try requireMutationIdentity(process: process, paths: paths)
    }
    try requireCurrentMutationIdentity()
    var actionCount = 0
    let revalidate = {
        try requireFixtureIdentity()
        return try revalidateOpenPanelListSelectionToken(
            initial, application: application, panel: panel,
            chooserTitle: chooserTitle, fixture: paths.fixture, process: process)
    }
    try performValidatedOpenPanelListSelectionSet(
        initial: initial, sameElement: sameAXElement,
        revalidate: revalidate,
        setSelection: { token in
            try performAtMutationBoundary(validateIdentity: requireCurrentMutationIdentity) {
                let status = AXUIElementSetAttributeValue(
                    token.list, kAXSelectedChildrenAttribute as CFString,
                    [token.candidate] as CFArray)
                guard status == .success else {
                    throw ProbeError.unavailable(
                        "setting exact Open panel candidate selection failed")
                }
            }
            actionCount += 1
        })
    let selectedPollCount = try waitForSelectedOpenPanelList(
        timeoutNanoseconds: 3_000_000_000,
        pollMicroseconds: 100_000,
        expected: initial,
        nowNanoseconds: monotonicNanoseconds,
        revalidate: revalidate,
        sameElement: sameAXElement,
        pause: { _ = usleep($0) })
    try performValidatedOpenPanelListChooserPress(
        initial: initial, sameElement: sameAXElement,
        revalidate: revalidate,
        pressChooser: { token in
            try press(token.chooser, purpose: "Open panel exact OKButton",
                      validateIdentity: requireCurrentMutationIdentity)
        })
    return (actionCount: actionCount, selectedPollCount: selectedPollCount)
}
// END_PID_OPEN_PANEL_LIST_SELECTION

struct OpenPanelAbsenceEvidence: Equatable {
    let windowCount: Int
    let visitedCount: Int
}

func isNestedApplicationBackReference(role: String, depth: Int) -> Bool {
    role == kAXApplicationRole && depth > 0
}

func requireOpenPanelAbsent(
    application: AXUIElement,
    process: ProcessIdentity
) throws -> OpenPanelAbsenceEvidence {
    try requireSameProcess(process)
    var windowsValue: CFTypeRef?
    let windowsStatus = AXUIElementCopyAttributeValue(
        application, kAXWindowsAttribute as CFString, &windowsValue)
    guard windowsStatus == .success,
          let windows = windowsValue as? [AXUIElement] else {
        throw ProbeError.unavailable(
            "application AX windows are incomplete before picker request: " +
            "\(windowsStatus.rawValue)")
    }
    guard windows.count <= 8 else {
        throw ProbeError.validation(
            "application exposes too many AX windows before picker request")
    }
    var pending = windows.map { (element: $0, depth: 0) }
    var nextIndex = 0
    var visitedCount = 0
    while nextIndex < pending.count {
        guard visitedCount < 1_000 else {
            throw ProbeError.validation(
                "pre-request AX traversal exceeded node limit")
        }
        let current = pending[nextIndex]
        nextIndex += 1
        visitedCount += 1
        try requireElementBelongsToProcess(
            current.element, process: process,
            purpose: "pre-request AX evidence element")
        let role = try strictStringAttribute(
            current.element, kAXRoleAttribute as CFString,
            purpose: "pre-request AX evidence role")
        guard role != kAXSheetRole else {
            throw ProbeError.validation(
                "an AXSheet already exists before the final renderer picker request")
        }
        guard !isNestedApplicationBackReference(
            role: role, depth: current.depth) else { continue }
        var childrenValue: CFTypeRef?
        let childrenStatus = AXUIElementCopyAttributeValue(
            current.element, kAXChildrenAttribute as CFString, &childrenValue)
        if childrenStatus == .success {
            guard let children = childrenValue as? [AXUIElement] else {
                throw ProbeError.validation(
                    "pre-request AX children are malformed")
            }
            guard children.count <= 128 else {
                throw ProbeError.validation(
                    "pre-request AX child fanout is excessive")
            }
            if current.depth == 8 {
                guard children.isEmpty else {
                    throw ProbeError.validation(
                        "pre-request AX traversal exceeded depth limit: " +
                        "role=\(role) children=\(children.count)")
                }
            } else {
                pending.append(contentsOf: children.map {
                    (element: $0, depth: current.depth + 1)
                })
            }
        } else if childrenStatus != .noValue &&
                    childrenStatus != .attributeUnsupported {
            throw ProbeError.unavailable(
                "pre-request AX child read is incomplete: " +
                "\(childrenStatus.rawValue)")
        }
    }
    try requireSameProcess(process)
    return OpenPanelAbsenceEvidence(
        windowCount: windows.count, visitedCount: visitedCount)
}

func execute(options: Options, paths: ValidatedPaths, log: EventLog) throws {
    let verification = try verifyProcess(options: options, paths: paths)
    let identity = verification.identity
    try log.write("process-validated", [
        "pid": Int(identity.pid),
        "startSeconds": identity.startSeconds,
        "executableSha256": sha256(identity.executable),
        "appKitRegistrationPollCount": verification.registrationPollCount,
    ])
    guard AXIsProcessTrusted() else {
        try log.write("accessibility-not-trusted", ["pid": Int(identity.pid)])
        throw ProbeError.permission(
            "Accessibility is not granted to this exact helper artifact")
    }
    let application = AXUIElementCreateApplication(options.pid)
    let absenceEvidence = try requireOpenPanelAbsent(
        application: application, process: identity)
    try log.write("open-panel-absence-validated", [
        "pid": Int(identity.pid),
        "startSeconds": identity.startSeconds,
        "fixtureSha256": sha256(paths.fixture),
        "windowCount": absenceEvidence.windowCount,
        "visitedCount": absenceEvidence.visitedCount,
    ])
    let readiness = try waitForValidatedOpenPanel(
        application: application, identity: identity)
    let panel = readiness.panel
    let plan = readiness.plan
    guard let listSelectionReadiness =
        try waitForValidatedOpenPanelListSelection(
            application: application, panel: panel,
            chooserTitle: plan.chooserTitle, paths: paths,
            process: identity) else {
        let diagnostic = try liveOpenPanelListSelectionSnapshot(
            application: application, panel: panel,
            chooserTitle: plan.chooserTitle, fixture: paths.fixture,
            process: identity)
        try log.write("open-panel-list-selection-unavailable", [
            "browserCount": diagnostic.browsers.count,
            "listCount": diagnostic.lists.count,
            "descendantListCount": diagnostic.descendantListCount,
            "listChildCount": diagnostic.listChildCount,
            "groupChildCount": diagnostic.groupChildCount,
            "rowEvidenceElementCount": diagnostic.rowEvidenceElementCount,
            "urlPublisherCount": diagnostic.urlPublisherCount,
            "candidateCount": diagnostic.candidates.count,
            "chooserCount": diagnostic.choosers.count,
            "chooserEnabled": diagnostic.chooserEnabled,
            "chooserPressPublished":
                diagnostic.chooserActions.contains(kAXPressAction),
            "selectedChildrenSettable":
                boolPublicationKind(diagnostic.selectedChildrenSettable),
            "selectedChildrenPublication":
                elementArrayPublicationKind(diagnostic.selectedChildren),
            "publishedURLPathHashes": diagnostic.publishedURLPathHashes,
            "panelDestinationPathHashes": diagnostic.panelDestinationPathHashes,
            "fixturePathHash": sha256(paths.fixture),
        ])
        throw ProbeError.unavailable(
            "Open panel exact list selection did not become ready")
    }
    try log.write("open-panel-validated", [
        "windowCount": readiness.initialElements.count,
        "pollCount": readiness.pollCount,
        "navigation": "selected-children",
        "chooserTitle": plan.chooserTitle,
    ])
    let result = try performValidatedOpenPanelListSelection(
        initial: listSelectionReadiness.token,
        application: application, panel: panel,
        chooserTitle: plan.chooserTitle, paths: paths,
        process: identity)
    try requireSameProcess(identity)
    try requireValidatedFixtureIdentity(paths)
    try log.write("project-selection-requested", [
        "pid": Int(identity.pid),
        "fixtureSha256": sha256(paths.fixture),
        "navigation": "selected-children",
        "readinessPollCount": listSelectionReadiness.pollCount,
        "selectionActionCount": result.actionCount,
        "selectedPollCount": result.selectedPollCount,
    ])
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
                 actions: Set<String> = [],
                 children: [ElementDescription] = []) -> ElementDescription {
    ElementDescription(role: role, subrole: subrole, identifier: identifier, title: title,
                       help: nil, enabled: enabled, actions: actions,
                       children: children)
}

func runSelfTests() throws {
    func require(_ condition: Bool, _ message: String) throws {
        if !condition { throw ProbeError.validation("self-test failed: \(message)") }
    }
    try require(
        classifyAXChildrenRead(status: .success, valueIsElementArray: true) ==
            .published,
        "published AXChildren were rejected")
    try require(
        classifyAXChildrenRead(status: .cannotComplete, valueIsElementArray: false) ==
            .failed,
        "incomplete AXChildren read became a leaf")
    try require(
        accessibilityURLPathPublication(
            URL(fileURLWithPath: "/private/tmp") as CFURL) ==
            .value("/private/tmp"),
        "AXURL file path was not preserved")
    try require(
        try OpenPanelListSelectionPolicy.rowMatchesExactFixture(
            role: kAXGroupRole,
            pathPublications: [.value("/private/tmp/project")],
            fixture: "/private/tmp/project"),
        "exact fixture row was rejected")
    var rejected = false
    do {
        _ = try OpenPanelListSelectionPolicy.rowMatchesExactFixture(
            role: kAXGroupRole,
            pathPublications: [.value("/private/tmp/project"),
                               .value("/private/tmp/other")],
            fixture: "/private/tmp/project")
    } catch ProbeError.validation { rejected = true }
    try require(rejected, "ambiguous row paths were accepted")

    let chooser = testElement(
        role: kAXButtonRole, title: "Open", identifier: "OKButton",
        enabled: true, actions: [kAXPressAction])
    let panel = testElement(
        role: kAXSheetRole,
        children: [
            testElement(
                role: kAXButtonRole, title: "Cancel",
                actions: [kAXPressAction]),
            chooser,
            testElement(role: kAXBrowserRole, identifier: "ColumnView"),
        ])
    try require(
        try OpenPanelPolicy.plan(windows: [panel]).chooserTitle == "Open",
        "exact Open panel contract was rejected")
    rejected = false
    do { _ = try OpenPanelPolicy.plan(windows: [panel, panel]) }
    catch ProbeError.validation { rejected = true }
    try require(rejected, "ambiguous Open panels were accepted")

    var panelClock: UInt64 = 0
    var panelReads = 0
    let panelReadiness = try waitForUniqueOpenPanel(
        timeoutNanoseconds: 1_000_000_000,
        pollMicroseconds: 100_000,
        nowNanoseconds: { panelClock },
        validateIdentity: {},
        readWindows: {
            panelReads += 1
            if panelReads == 1 {
                throw ProbeError.retryable("synthetic incomplete AX read")
            }
            return [("panel", panel)]
        },
        sameElement: { (left: String, right: String) in left == right },
        pause: { panelClock += UInt64($0) * 1_000 })
    try require(
        panelReadiness.pollCount == 2 && panelReads == 2,
        "transient panel AX read was not retried")
    var delayedPanelClock: UInt64 = 0
    let delayedPanelReadiness = try waitForUniqueOpenPanel(
        timeoutNanoseconds: openPanelWaitTimeoutNanoseconds,
        pollMicroseconds: 1_000_000,
        nowNanoseconds: { delayedPanelClock },
        validateIdentity: {},
        readWindows: {
            delayedPanelClock >= 50_000_000_000 ? [("panel", panel)] : []
        },
        sameElement: { (left: String, right: String) in left == right },
        pause: { delayedPanelClock += UInt64($0) * 1_000 })
    try require(
        delayedPanelReadiness.pollCount == 51,
        "60-second Open panel deadline did not cover delayed renderer restoration")
    try requireExactCurrentOpenPanel(
        "panel", nestedSheets: ["panel"], sameElement: ==)
    rejected = false
    do {
        try requireExactCurrentOpenPanel(
            "panel", nestedSheets: ["replacement"], sameElement: ==)
    } catch ProbeError.validation { rejected = true }
    try require(rejected, "replacement Open panel was accepted from a stale snapshot")
    rejected = false
    do {
        try requireExactCurrentOpenPanel(
            "panel", nestedSheets: ["panel", "replacement"], sameElement: ==)
    } catch ProbeError.validation { rejected = true }
    try require(rejected, "ambiguous current Open panel snapshot was accepted")

    var mutationEvents: [String] = []
    try performAtMutationBoundary(
        validateIdentity: { mutationEvents.append("validate") },
        mutation: { mutationEvents.append("mutate") })
    try require(
        mutationEvents == ["validate", "mutate", "validate"],
        "mutation boundary did not validate before and after mutation")
    var blockedMutation = false
    do {
        try performAtMutationBoundary(
            validateIdentity: { throw ProbeError.validation("synthetic identity drift") },
            mutation: { blockedMutation = true })
    } catch ProbeError.validation {}
    try require(!blockedMutation, "identity drift allowed an AX mutation")

    func snapshot(
        selected: [String], candidate: String = "candidate",
        selectedPublication: ElementArrayPublication<String>? = nil
    ) -> OpenPanelListSelectionSnapshot<String> {
        OpenPanelListSelectionSnapshot(
            panels: ["panel"], browsers: ["browser"], lists: ["list"],
            candidates: [candidate], choosers: ["chooser"],
            selectedChildrenSettable: .value(true),
            selectedChildren: selectedPublication ?? .value(selected),
            chooserEnabled: true,
            chooserActions: [kAXPressAction], listChildCount: 1,
            descendantListCount: 1, groupChildCount: 1,
            rowEvidenceElementCount: 1, urlPublisherCount: 1,
            publishedURLPathHashes: [], panelDestinationPathHashes: [])
    }
    let token = try OpenPanelListSelectionPolicy.token(snapshot(selected: []))
    var retryableReadPreserved = false
    do {
        _ = try OpenPanelListSelectionPolicy.pendingToken(snapshot(
            selected: [], selectedPublication:
                .readFailure(AXError.cannotComplete.rawValue)))
    } catch ProbeError.retryable {
        retryableReadPreserved = true
    }
    try require(
        retryableReadPreserved,
        "AX cannotComplete publication was not preserved as retryable")
    var snapshotClock: UInt64 = 0
    var snapshotReads = 0
    let snapshotReadiness = try waitForReadyOpenPanelListSelection(
        timeoutNanoseconds: 1_000_000_000,
        pollMicroseconds: 100_000,
        nowNanoseconds: { snapshotClock },
        validateIdentity: {},
        readSnapshot: {
            snapshotReads += 1
            if snapshotReads == 1 {
                throw ProbeError.retryable("synthetic incomplete snapshot")
            }
            return snapshot(selected: [])
        },
        pause: { snapshotClock += UInt64($0) * 1_000 })
    try require(
        snapshotReadiness?.pollCount == 2 && snapshotReads == 2,
        "transient list-selection AX read was not retried")
    var selectionActions = 0
    var selectionPublished = false
    var selectionRevalidations = 0
    try performValidatedOpenPanelListSelectionSet(
        initial: token, sameElement: ==,
        revalidate: {
            selectionRevalidations += 1
            return (token, snapshot(
                selected: selectionPublished ? ["candidate"] : []))
        },
        setSelection: { _ in
            selectionActions += 1
            selectionPublished = true
        })
    try require(
        selectionActions == 1 && selectionRevalidations == 2,
        "exact selection mutation was not published and reread once")
    var preselectedActions = 0
    try performValidatedOpenPanelListSelectionSet(
        initial: token, sameElement: ==,
        revalidate: { (token, snapshot(selected: ["candidate"])) },
        setSelection: { _ in preselectedActions += 1 })
    try require(
        preselectedActions == 1,
        "preselected exact candidate skipped the required publication")
    try require(
        isNestedApplicationBackReference(
            role: kAXApplicationRole, depth: 1) &&
        !isNestedApplicationBackReference(
            role: kAXApplicationRole, depth: 0) &&
        !isNestedApplicationBackReference(role: kAXWindowRole, depth: 1),
        "bounded AX traversal application back-reference policy failed")
    var chooserActions = 0
    try performValidatedOpenPanelListChooserPress(
        initial: token, sameElement: ==,
        revalidate: { (token, snapshot(selected: ["candidate"])) },
        pressChooser: { _ in chooserActions += 1 })
    try require(chooserActions == 1, "exact chooser mutation did not run once")

    let arguments = [
        "--pid", "42", "--run-root", "/private/tmp/run",
        "--expected-bundle", "/private/tmp/run/ChatGPT.app",
        "--expected-executable", "/private/tmp/run/ChatGPT.app/Contents/MacOS/ChatGPT",
        "--fixture-root", "/private/tmp/run/project", "--phase", "select-project",
        "--event-log", "/private/tmp/run/logs/native-gui-probe.jsonl",
        "--accept-renderer-project-picker-request",
    ]
    let options = try parseOptions(arguments)
    try require(options.acceptRendererProjectPickerRequest,
                "renderer picker authority was not retained")
    rejected = false
    do { _ = try parseOptions(Array(arguments.dropLast())) }
    catch ProbeError.usage { rejected = true }
    try require(rejected, "missing renderer picker authority was accepted")
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
                                        "rendererProjectPickerRequestAuthorized":
                                            options.acceptRendererProjectPickerRequest])
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

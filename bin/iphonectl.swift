// iphonectl — drive the iPhone Mirroring window while you keep using your Mac.
//
// Coordinates are window-local points: the same frame computer-use app_screenshot reports
// ("coordinate frame: WxH"), (0,0) = window top-left. The window resizes itself per iOS app and
// orientation (Home 456x1002, Settings 734 wide or landscape), so take coordinates from a fresh screenshot.
//
// What reaches iOS (tested 2026-10-23, macOS 27.0.1 / iOS 27.0):
//   menu   — View > Home Screen / App Switcher / Spotlight / Control Center pressed through the AX API: fully background.
//   tap    — SkyLight event records (SLPSPostEventRecordTo) with the real location; only while Mirroring is active.
//   keys   — CGEventPostToPid real keycodes (US layout); only while Mirroring is active; modifiers are HELD keys.
// "Active" is a guarded blip: wait for a pause in the user's typing, hold their keystrokes, activate Mirroring without
// raising its window, send, hand activation back, replay their keys. Nothing else (drags, scrolls, long-press,
// paste, plain CGEvent mouse) reaches iOS from a background process. See ../SKILL.md.
// Technique credit: yabai (focus-without-raise records), ShawnPana/phone-harness (MIT).
//
// Build: swiftc -O iphonectl.swift -o iphonectl -F /System/Library/PrivateFrameworks -framework SkyLight
import AppKit
import ApplicationServices

// MARK: - target window

struct Target { let pid: pid_t; let windowID: UInt32; let bounds: CGRect }

func findTarget() -> Target {
    let list = CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as? [[String: Any]] ?? []
    var best: Target?
    for w in list {
        guard (w[kCGWindowOwnerName as String] as? String) == "iPhone Mirroring",
              (w[kCGWindowLayer as String] as? Int) == 0,
              let b = w[kCGWindowBounds as String] as? [String: CGFloat],
              let pid = w[kCGWindowOwnerPID as String] as? Int32,
              let num = w[kCGWindowNumber as String] as? Int else { continue }
        let rect = CGRect(x: b["X"]!, y: b["Y"]!, width: b["Width"]!, height: b["Height"]!)
        // The phone window is titled exactly "iPhone Mirroring" (the onboarding one is "Welcome to …");
        // untitled menu-bar strips are 33 high. Without readable titles, take the largest window.
        let title = w[kCGWindowName as String] as? String
        guard rect.height > 200, title == nil || title == "iPhone Mirroring" else { continue }
        if best == nil || rect.width * rect.height > best!.bounds.width * best!.bounds.height {
            best = Target(pid: pid, windowID: UInt32(num), bounds: rect)
        }
    }
    guard let t = best else {
        fputs("error: no iPhone Mirroring phone window. Open iPhone Mirroring; the phone must be locked and nearby.\n", stderr)
        exit(2)
    }
    return t
}

let t = findTarget()

func sleepMs(_ ms: Double) { usleep(useconds_t(max(0, ms) * 1000)) }

// MARK: - SkyLight private API

@_silgen_name("GetProcessForPID") func GetProcessForPID(_ pid: pid_t, _ psn: UnsafeMutablePointer<ProcessSerialNumber>) -> OSStatus
@_silgen_name("SLPSPostEventRecordTo") func SLPSPostEventRecordTo(_ psn: UnsafeMutablePointer<ProcessSerialNumber>, _ bytes: UnsafeMutablePointer<UInt8>) -> CGError
@_silgen_name("_SLPSSetFrontProcessWithOptions") func SLPSSetFrontProcessWithOptions(_ psn: UnsafeMutablePointer<ProcessSerialNumber>, _ wid: UInt32, _ mode: UInt32) -> CGError

var psn: ProcessSerialNumber = { var p = ProcessSerialNumber(); _ = GetProcessForPID(t.pid, &p); return p }()

/// One event record (layout from yabai): type 1 down, 2 up. nil point = blank location = "make key" (no click).
func record(_ type: UInt8, _ local: CGPoint?) {
    var b = [UInt8](repeating: 0, count: 0xf8)
    b[0x04] = 0xf8; b[0x08] = type; b[0x3a] = 0x10
    var wid = t.windowID
    withUnsafeBytes(of: &wid) { for (i, x) in $0.enumerated() { b[0x3c + i] = x } }
    if let p = local {
        var g = (Double(t.bounds.minX + p.x), Double(t.bounds.minY + p.y)), l = (Double(p.x), Double(p.y))
        withUnsafeBytes(of: &g) { for (i, x) in $0.enumerated() { b[0x10 + i] = x } }
        withUnsafeBytes(of: &l) { for (i, x) in $0.enumerated() { b[0x20 + i] = x } }
    } else {
        for i in 0x20..<0x30 { b[i] = 0xff }
    }
    _ = b.withUnsafeMutableBufferPointer { SLPSPostEventRecordTo(&psn, $0.baseAddress!) }
}

func makeKey() { record(1, nil); record(2, nil) }

func tap(_ x: Double, _ y: Double) {
    let p = CGPoint(x: x, y: y)
    makeKey(); sleepMs(15)
    record(1, p); sleepMs(50); record(2, p)
}

// MARK: - the guarded focus blip

/// iOS only takes the helper's taps and keystrokes while Mirroring is the ACTIVE app. So: wait for a pause in
/// the user's own typing, hold their keystrokes, activate Mirroring without raising its window, run `body`, give
/// activation back to the app they were in, then replay their held keystrokes there (input buffering).
func withBriefFocus(_ body: () -> Void) {
    let deadline = Date().addingTimeInterval(5)
    while CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: .keyDown) < 0.6 && Date() < deadline { sleepMs(100) }
    let prev = NSWorkspace.shared.frontmostApplication
    let hold = KeyboardHold()
    _ = SLPSSetFrontProcessWithOptions(&psn, t.windowID, 0x200)
    sleepMs(80)
    body()
    sleepMs(30)
    if let p = prev, p.processIdentifier != t.pid {
        var prevPSN = ProcessSerialNumber()
        _ = GetProcessForPID(p.processIdentifier, &prevPSN)
        let wins = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
        let prevWid = wins.first { ($0[kCGWindowOwnerPID as String] as? Int32) == p.processIdentifier && ($0[kCGWindowLayer as String] as? Int) == 0 }?[kCGWindowNumber as String] as? Int ?? 0
        _ = SLPSSetFrontProcessWithOptions(&prevPSN, UInt32(prevWid), 0x200)
        sleepMs(40)
    }
    hold.releaseAndReplay()
}

/// Session-level keyboard tap that swallows the user's physical keystrokes during the blip and replays them in order
/// once their own app is front again. The helper's keys go by pid and never pass through it.
final class KeyboardHold {
    private var tap: CFMachPort?
    private var runLoop: CFRunLoop?
    fileprivate var held: [CGEvent] = []
    private let lock = NSLock()

    init() {
        let mask: CGEventMask = (1 << CGEventType.keyDown.rawValue) | (1 << CGEventType.keyUp.rawValue) | (1 << CGEventType.flagsChanged.rawValue)
        tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .defaultTap, eventsOfInterest: mask,
                                callback: { _, type, event, info in
            let hold = Unmanaged<KeyboardHold>.fromOpaque(info!).takeUnretainedValue()
            if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput { return Unmanaged.passUnretained(event) }
            hold.lock.lock(); hold.held.append(event.copy()!); hold.lock.unlock()
            return nil
        }, userInfo: Unmanaged.passUnretained(self).toOpaque())
        guard let tap = tap else {
            // Without the tap, keys the user types during the blip could reach the phone. Say so loudly.
            fputs("warning: could not create the keyboard event tap (grant Accessibility/Input Monitoring to the app running this); "
                  + "the user's keystrokes are NOT held during the focus blip\n", stderr)
            return
        }
        let ready = DispatchSemaphore(value: 0)
        Thread { [weak self] in
            self?.runLoop = CFRunLoopGetCurrent()
            CFRunLoopAddSource(CFRunLoopGetCurrent(), CFMachPortCreateRunLoopSource(nil, tap, 0), .commonModes)
            CGEvent.tapEnable(tap: tap, enable: true)
            ready.signal()
            CFRunLoopRun()
        }.start()
        ready.wait()
    }

    func releaseAndReplay() {
        guard let tap = tap else { return }
        CGEvent.tapEnable(tap: tap, enable: false)
        if let rl = runLoop { CFRunLoopStop(rl) }
        lock.lock(); let events = held; held.removeAll(); lock.unlock()
        for e in events { e.post(tap: .cgSessionEventTap); sleepMs(2) }
    }
}

// MARK: - keyboard

let keyCodes: [String: CGKeyCode] = [
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
    "left": 123, "right": 124, "down": 125, "up": 126, "forward_delete": 117,
    "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26, "8": 28, "9": 25, "0": 29,
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
    "e": 14, "r": 15, "y": 16, "t": 17, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
    ".": 47, ",": 43, "/": 44, ";": 41, "'": 39, "[": 33, "]": 30, "\\": 42, "-": 27, "=": 24, "`": 50, " ": 49,
]
let shifted: [Character: String] = [
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
    "_": "-", "+": "=", ":": ";", "\"": "'", "<": ",", ">": ".", "?": "/", "~": "`", "{": "[", "}": "]", "|": "\\",
]
let modKey: [String: (CGKeyCode, CGEventFlags)] = [
    "cmd": (55, .maskCommand), "command": (55, .maskCommand), "shift": (56, .maskShift),
    "alt": (58, .maskAlternate), "option": (58, .maskAlternate), "ctrl": (59, .maskControl), "control": (59, .maskControl),
]

func keyEdge(_ code: CGKeyCode, _ down: Bool, _ flags: CGEventFlags) {
    makeKey()
    let e = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down)!
    e.flags = flags
    e.postToPid(t.pid)
    sleepMs(12)
}

func press(_ code: CGKeyCode, mods: [String]) {
    var flags: CGEventFlags = []
    for m in mods { let (c, f) = modKey[m]!; flags.insert(f); keyEdge(c, true, flags) }
    keyEdge(code, true, flags); keyEdge(code, false, flags)
    // Clear each flag before its release, or the modifier latches on.
    for m in mods.reversed() { let (c, f) = modKey[m]!; flags.remove(f); keyEdge(c, false, flags) }
}

func key(_ combo: String) {
    let parts = combo.lowercased().split(separator: "+").map(String.init)
    guard let last = parts.last, let code = keyCodes[last] else { fputs("error: unknown key in \(combo)\n", stderr); exit(1) }
    let mods = Array(parts.dropLast())
    for m in mods where modKey[m] == nil { fputs("error: unknown modifier \(m)\n", stderr); exit(1) }
    press(code, mods: mods)
}

/// Real keystrokes (US layout); `\n` presses Return. Characters with no key (emoji, accents) are refused.
func typeKeys(_ s: String) {
    for ch in s {
        if ch == "\n" { press(36, mods: []); continue }
        let isUpper = ch.isUppercase && ch.isLetter
        let base = isUpper ? String(ch).lowercased() : (shifted[ch] ?? String(ch))
        guard let code = keyCodes[base] else { fputs("error: cannot type \(ch) as a keystroke\n", stderr); exit(1) }
        press(code, mods: (isUpper || shifted[ch] != nil) ? ["shift"] : [])
    }
}

// MARK: - Mirroring's own menu (AX press: background, no keys, no blip)

func axAttr(_ e: AXUIElement, _ a: String) -> AnyObject? { var v: AnyObject?; AXUIElementCopyAttributeValue(e, a as CFString, &v); return v }

func menu(_ path: [String]) {
    let app = AXUIElementCreateApplication(t.pid)
    guard var cur = axAttr(app, kAXMenuBarAttribute) as! AXUIElement? else { fputs("error: no menu bar\n", stderr); exit(4) }
    for (i, name) in path.enumerated() {
        var kids = (axAttr(cur, kAXChildrenAttribute) as? [AXUIElement]) ?? []
        if i > 0, let m = kids.first { kids = (axAttr(m, kAXChildrenAttribute) as? [AXUIElement]) ?? [] }
        guard let hit = kids.first(where: { (axAttr($0, kAXTitleAttribute) as? String) == name }) else {
            fputs("error: menu item \(name) not found\n", stderr); exit(4)
        }
        if i == path.count - 1 { AXUIElementPerformAction(hit, kAXPressAction as CFString) }
        cur = hit
    }
}

// MARK: - capture (needs Screen Recording for the calling app; computer-use app_screenshot is the usual eyes)

func shot(_ out: String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    p.arguments = ["-x", "-o", "-l\(t.windowID)", out]
    p.standardError = FileHandle.nullDevice
    try? p.run(); p.waitUntilExit()
    if let size = try? FileManager.default.attributesOfItem(atPath: out)[.size] as? Int, size > 1000 { print(out); return }
    fputs("error: capture failed (grant Screen Recording, or use computer-use app_screenshot)\n", stderr); exit(3)
}

// MARK: - CLI

let a = Array(CommandLine.arguments.dropFirst())
let needsFocus: Set<String> = ["tap", "key", "keys"]
var focused = false

/// Touch and keyboard steps need the blip; inside `run` a contiguous group shares one.
func active(_ body: () -> Void) { if focused { body() } else { withBriefFocus(body) } }

func exec(_ args: [String]) {
    let cmd = args.first ?? ""
    func n(_ i: Int) -> Double {
        if i < args.count, let v = Double(args[i]) { return v }
        fputs("error: \(cmd) needs a number at position \(i)\n", stderr); exit(1)
    }
    switch cmd {
    case "tap": active { tap(n(1), n(2)) }
    case "key":
        let times = args.count > 2 ? Int(args[2]) ?? 1 : 1
        active { for _ in 0..<times { key(args.count > 1 ? args[1] : "") } }
    case "keys": active { typeKeys(args.count > 1 ? args[1...].joined(separator: " ") : "") }
    case "home": menu(["View", "Home Screen"])
    case "switcher": menu(["View", "App Switcher"])
    case "spotlight": menu(["View", "Spotlight"])
    case "control": menu(["View", "Control Center"])
    case "wait": sleepMs(n(1))
    default: fputs("error: unknown step \(cmd)\n", stderr); exit(1)
    }
}

switch a.first ?? "" {
case "info": print("pid=\(t.pid) window=\(t.windowID) x=\(Int(t.bounds.minX)) y=\(Int(t.bounds.minY)) w=\(Int(t.bounds.width)) h=\(Int(t.bounds.height))")
case "shot": shot(a.count > 1 ? a[1] : NSTemporaryDirectory() + "iphone.png")
case "run":
    let steps = a.dropFirst().map { $0.split(separator: " ", omittingEmptySubsequences: true).map(String.init) }
    var i = 0
    while i < steps.count {
        guard needsFocus.contains(steps[i].first ?? "") else { exec(steps[i]); i += 1; continue }
        // A wait sandwiched between focus steps stays inside the same blip.
        var j = i
        while j < steps.count, needsFocus.contains(steps[j].first ?? "")
                || (steps[j].first == "wait" && j + 1 < steps.count && needsFocus.contains(steps[j + 1].first ?? "")) { j += 1 }
        let group = Array(steps[i..<j])
        withBriefFocus { focused = true; group.forEach(exec); focused = false }
        i = j
    }
case "": print("""
    usage: iphonectl info | shot [file.png]
           iphonectl home | switcher | spotlight | control      (menu presses: fully background)
           iphonectl tap X Y | key COMBO [times] | keys TEXT    (guarded focus blip)
           iphonectl run "step args" "step args" …              (one process; adds `wait MS`)
    """)
default: exec(a)
}

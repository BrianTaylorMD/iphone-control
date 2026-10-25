# Findings: what works, what doesn't, and why

Measured 2026-10 on iPhone 17 Pro Max (iOS 27.0), MacBook Pro M5 Max (macOS 27.0.1), pymobiledevice3 11.43.1,
Xcode 27.1. Treat these as facts for this setup, not guarantees for others.

## Developer channel (pymobiledevice3 CoreDevice)

| Thing | Result |
|---|---|
| One-shot CLI commands (`pymobiledevice3 developer core-device ...`) | 2–5 s each: every call rebuilds the tunnel and the screen media stream. Hence the persistent `phoned`. |
| Touch input | Only reaches UIKit while a **screen media stream** is open (it's the auth gate). With the stream held open: tap 50 ms, drag 270 ms. |
| `swipe` (HID gesture surface) | Moves a pointer and **doesn't scroll**. Use `drag` (touch contact) to scroll. |
| Typing | Virtual HID keyboard, ASCII only, ~50 ms/char. **The first report after the keyboard attaches is dropped**, so send an empty report first. |
| Exact text (emoji, accents) | Set the phone's pasteboard, then send Cmd-V from the virtual keyboard: 0.73 s. Save and restore the old clipboard. |
| Screenshot via CoreDevice capture service | PNG only (JPEG/HEIC refused), 11–15 MB, 0.84 s over USB. **The service hangs up after one screenshot**: open a new channel per shot, or the next one waits ~18 s. |
| Screenshot via DVT (Instruments) | The channel stays open, 0.84 s over USB. **Times out over Wi-Fi** (too much data for the pure-Python network stack). |
| Screenshot from the HEVC stream (in-process VNC server, VideoToolbox decode) | **30–70 ms over USB, Wi-Fi and Tailscale.** The frame is padded to whole blocks (1328 × 2896 for a 1320 × 2868 screen): crop it. |
| Buttons (Indigo HID) | Home, lock, volume: 51 ms. |
| Wi-Fi without the cable | Works through **usbmuxd** (it lists the phone as `ConnectionType: Network`). A process can't connect to the phone's LAN IP directly: macOS Local Network privacy blocks it unless that app has the permission. |
| Phone locked | Pairing and the screen stream still work (you get the lock screen). Touch hits the lock screen, so these tasks need the phone **unlocked**. |
| After a phone restart | Unlock once; the Developer Disk Image was still mounted in our test. |

## Remote over Tailscale (RemotePairing)

| Thing | Result |
|---|---|
| Lockdown (port 62078) over Tailscale | The phone accepts TCP and then **drops it**. iOS won't serve lockdown on the VPN interface. |
| RemotePairing (port 49152) over Tailscale | **Works**: pair-verify 0.14 s, tunnel 0.47 s, daemon start 2.7 s, then the same speeds as USB. Tested with the phone and Mac on the same Wi-Fi. |
| Phone on **cellular only** | Tailscale still reaches it (relay ~270 ms), but **ports 49152 and 62078 are refused**. iOS only opens developer services when it's joined to *some* Wi-Fi. No workaround found. |
| Record creation | `pymobiledevice3 lockdown remotepairing --pair` works with no prompt over a trusted USB link. **It saves the private key world-readable (0644): chmod 600.** |
| "pair-verify TLV error 2" after a network change | pymobiledevice3 sends `uuid3(DNS, hostname)` as the host ID, and macOS changes the hostname per network. **Pin `host_identifier` in the record** (`bin/pin-host-id`). Proven: under the drifted hostname the unpinned record fails with error 2 and the pinned one verifies. |
| Lock state | **Not** a factor: pair-verify succeeds with the phone locked. |
| WebDriverAgent over Tailscale | Answers in 19–33 ms if bound to the Tailscale IP, but WDA has **no auth**, so this repo keeps it on the phone's loopback (reachable over cable or the same Wi-Fi only). Remotely, use the RemotePairing tunnel's screenshots and taps instead of WDA. |

## WebDriverAgent

| Thing | Result |
|---|---|
| Source | `appium/WebDriverAgent` was archived (2026-10). It now lives in `appium/appium-ios` under `packages/webdriveragent`. |
| Signing | A free Personal Team works: `xcodebuild -allowProvisioningUpdates` creates the certificate and profile and registers the device. The profile lasts 7 days. The team ID shows up in `defaults read com.apple.dt.xcodebuild` after one attempted build. |
| First launch | "Developer App Certificate is not trusted". Trust it in Settings → General → VPN & Device Management, then **Verify App**. |
| Verify App does nothing | In our case, the phone log showed the Secure Enclave refusing attestation (`unable to attest key`, `Failed to create BAA info`), so the request never left the phone. **A restart fixed it.** See troubleshooting-verify-app.md. |
| Speed (after `waitForIdleTimeout 0`, `animationCoolOffTimeout 0`) | Whole tree 0.4–0.65 s; launch app by bundle ID 0.34 s; tap by label 1.3 s. Fastest: read the tree once, then tap the coordinates through the HID channel (50 ms). |
| Binding | `TEST_RUNNER_USE_IP=127.0.0.1` binds it to the phone's loopback; reach it with `pymobiledevice3 usbmux forward 8100 8100`. |

## iPhone Mirroring (phone locked, near the Mac)

| Thing | Result |
|---|---|
| Menu actions | View → Home Screen / App Switcher / Spotlight / Control Center (shortcuts Cmd-1/2/3/4) can be pressed through the **accessibility API** with the app in the background. |
| Clicks from a background process | Plain `CGEventPostToPid` mouse events are ignored, even when byte-identical to a working tool's events. SkyLight event records (`SLPSPostEventRecordTo`, the yabai layout) work **only while Mirroring is the active app**. |
| Keys | iOS only receives keystrokes while Mirroring is active. Cmd-combos go to the Mac menu, so Cmd-V and Cmd-A do nothing on the phone. Fix: a ~0.1 s activation "blip" that holds the user's own keystrokes with a session event tap and replays them afterwards. |
| Scroll / swipe / long-press | **None worked** (record drags, CGEvent drags, scroll wheel with trackpad phases, arrow keys). Mirroring only scrolls from the real cursor. Navigate by search, or use the developer channel. |
| Paste | Edit → Paste is disabled in Mirroring. |
| Window | It resizes per app and orientation (Home 456 wide, Settings 734 wide in split view), so take coordinates from a fresh screenshot. |
| Open any app | Spotlight → type the name → wait ~300 ms → Return: 0.95–1.2 s. Without the wait, Return is lost. |

## Home Screen editing (developer channel, iOS 27.0, 2026-10-25)

| Thing | Result |
|---|---|
| Edit mode | `hold` on empty space for 1.3 s enters it; Done is the check mark at top right. |
| Make a folder / add to one | Drag an app onto an app and hover ~1.1 s (iOS names it, e.g. "Utilities", or "Folder"); onto a folder, hover ~0.6 s. Each move re-flows the grid, so take a fresh `shot` before the next drag. |
| Move to another page | Drag to the left edge and hover ~1.5 s: the page flips and the icon lands in a free slot. **Hovering ~3 s flips two pages and drops it on page 1, which re-flows page 1 (widgets included)**: avoid; flip one page at a time. |
| Rename a folder | Open it in edit mode, tap the name's clear (x) button, `type` the name, `key enter`. |
| Folder icon shows only one app | A folder that spilled onto a second page shows that page while in edit mode; it shows the first page again after Done. |
| WebDriverAgent start | iOS 27 asked for the iPhone passcode ("Enter iPhone Passcode for XCTest, Enable UI Automation"). Agents must not type passcodes: dismiss it and use `phone` touches, or have the user enter it. |

## Deep links and settings screens (iOS 27.0, 2026-10-25)

| Thing | Result |
|---|---|
| `devicectl device process launch --payload-url URL BUNDLE` | Opens the URL in the app in 0.17-0.35 s (Settings, Shortcuts, Maps, Safari). Needs `DEVELOPER_DIR` pointing at Xcode. |
| Settings `prefs:root=` that work | General, DISPLAY, BATTERY_USAGE, Privacy, ACCESSIBILITY, Wallpaper, DO_NOT_DISTURB (Focus), SCREEN_TIME, NOTIFICATIONS_ID, SIRI, ACTION_BUTTON. |
| `prefs:root=` that fall back to the Settings root | FOCUS, CONTROL_CENTER, STANDBY, KEYBOARD. |
| `maps://?q=` | Opens Maps but the query was not applied. |
| Action button page | The carousel IS the setting: a swipe changes it at once, a swipe skips 2 items, and leaving "Shortcut" with none chosen falls to "No Action"; the chosen control is forgotten when you pass through other modes. Step one item by tapping beside the page dots. |
| Deep links over the CoreDevice tunnel | `launchapplication` with `options.payloadURL = {"relative": URL}` (what devicectl sends) works through phoned's tunnel, **including RemotePairing over Tailscale**: 0.18-0.24 s. |
| SpringBoard `getWallpaperPreviewImage homescreen` | Renders page 1 of the Home Screen with icons (1320 x 2868 PNG). A way to verify a layout write without the screen. |
| `setIconState` dictionary form `{buttonBar, iconLists, ignored}` | Writing back the identical layout changed nothing (re-read matched, page count unchanged), 2.3 s including backup and verify. `ignored` = every installed User/System app (placeholders and hidden services included) not placed. |

## Widgets by layout write (iOS 27.0, 2026-10-25)

| Thing | Result |
|---|---|
| Create a NEW widget with `setIconState` | **Works**: a widget dict with a fresh UUID `displayIdentifier` appears and renders live (Calendar, Weather, Batteries, Clock, Spotify, Photos, Smart Stacks). Public projects listed this as unproven or impossible. |
| Unsupported size | Silently dropped: Claude's widget vanished from a `large` Smart Stack (re-read shows the stack without it). Re-read after every write. |
| Learning a widget's identity | Add it once from the gallery (or add a Smart Stack: it carries ~7 widgets), then `layout get`. Catalog: `docs/widgets.json`. |
| Photos widget | Shows "Featured Content is Turned Off" when the user turned that off: pick another widget. |

## Approaches not taken

- **Xcode 27's MCP device tools** (DeviceInteraction): Simulator-only on 27.1.
- **Push notification → Shortcuts automation** (for a locked phone on cellular): designed but not built end to end.
  iOS 27 has a Notification trigger and "Allow Running When Locked".

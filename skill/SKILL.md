---
name: iphone
description: Operate the user's own iPhone from their Mac in the BACKGROUND (they keep using the Mac). Open apps, tap, scroll, type, paste, press buttons, read the screen as an image or text, tap by label, via the developer channel (phone unlocked; cable, same Wi-Fi, or Tailscale) or iPhone Mirroring (phone locked, nearby). Use for "on my phone", "open X on my iPhone", "check my phone", or any task that needs their iPhone.
---

# iPhone

Run the user's iPhone fast **without taking over their Mac's screen**. Never use full-screen computer control for this.
The user talks in plain English and never picks a route; you do.

`<skill>` below means the installed skill folder (default `~/.claude/skills/iphone`, made by `install.sh`), or the repo
root when you work from a clone. Tools are in `<skill>/bin`, docs in `<skill>/docs`. Per-user values (UDID, Tailscale IP,
team ID, screen size) live in `~/.config/iphone-control/config.json`. **If `<skill>/LOCAL.md` exists, read it first**:
it holds this user's device facts and notes. Setup: `<skill>/docs/setup.md`. Dead ends, timings and causes:
`<skill>/docs/findings.md`. Don't retry anything that file marks as not working.

## Hard limits

Ask the user in chat and wait for a clear yes before anything that sends a message, posts, buys, submits a form, accepts
terms, deletes, or changes a phone setting (trust, toggles, automations included). Never type passwords or passcodes,
never move money or trade: the user does those. Viewing, opening apps, scrolling and searching need no confirmation.
Don't describe or quote private content you happen to see (messages, mail, photos) beyond what the task needs.
Run `phone stop` / `wda stop` when done: while connected, the phone shows an indicator in the status bar / Dynamic Island.

## 1. Route first: does this need the phone?

If the data syncs to the Mac through iCloud (Messages, Mail, Calendar, Reminders, Notes, Contacts, Safari tabs), use the
Mac app instead. It's faster and doesn't touch the phone. Check once that the Mac copy is current, and record exceptions
in LOCAL.md (e.g. a user whose Photos don't sync). Use the phone for phone-only apps, phone settings, or when asked.

## 2. Pick the mode

| Phone state | Mode | Eyes | Hands |
|---|---|---|---|
| **Unlocked**, paired | **Developer channel** `phone`, preferred (+ `wda` on cable/same Wi-Fi only) | `shot` 30–70 ms; `wda els/text` ~0.5 s | tap, drag/scroll, type, paste, buttons; with wda: tap-by-label, launch by bundle ID |
| **Locked**, near the Mac | **iPhone Mirroring** `iphonectl` + your window-screenshot tool | window screenshot | tap, type, Home/Spotlight; **no scrolling** |

Try `phone start` first (`phone start --remote` over Tailscale when the phone isn't on the cable or the same Wi-Fi).
Then **take a `shot` and look**: a locked phone still connects and streams, but shows the lock screen and touches hit
the lock screen. If it's locked, use Mirroring (if nearby) or ask the user to unlock. You can't unlock the phone.
Off Wi-Fi (cellular only), none of this works: iOS closes the developer ports. Over `--remote`, `wda` isn't available:
use `shot` + coordinate taps.

## 3. Developer channel: `<skill>/bin/phone`

```
phone start                       # 2–4 s; --remote [IP] = Tailscale RemotePairing tunnel; --network = Wi-Fi via usbmuxd
phone "button home" "wait 500" "tap 218 778" "wait 500" "type Weather" "wait 400" "shot"
phone stop                        # also deletes every screenshot it took
```
One JSON line comes back per step, with `ms`; **a chain stops at the first failed step**. `shot` (no path) saves a JPEG in
`~/.cache/iphone/shots/` (user-only), scaled to **iOS points**: a pixel in the picture is a tap coordinate. `Read` the
returned path. `type` is ASCII-only (use `paste` for anything else); text may contain newlines.

| Step | Example | Time |
|---|---|---|
| Tap | `tap X Y` | 50 ms |
| Scroll | `drag 220 750 220 300 250` (finger up = content moves down) | 270 ms |
| Long-press / move an icon | `hold X1 Y1 [X2 Y2 PRESS_MS MOVE_MS HOVER_MS]`: press, hold, slide, hover, release. Long-press: `hold 220 745 220 745 1300 0 0`. In edit mode, drop on an app to make a folder: `hold 169 345 369 345 250 500 1100`; into a folder: hover 600. To the left edge with hover 1500 flips one page | |
| Type ASCII | `type hello world` | ~50 ms/char |
| Exact text (emoji, accents, no autocorrect) | `paste Café ☕ 3:45pm` | 0.73 s; the phone's clipboard is restored |
| Keys | `key enter` · `key backspace 5` · `key escape` · `key down 3` | ~50 ms |
| Buttons | `button home` · `button lock` · `button volup` · `button voldown` | 51 ms |
| Screenshot | `shot` | 30–70 ms |
| Read phone clipboard | `clipboard` | ms |
| Pause | `wait MS` | |

**Chain predictable steps into one `phone` call**: each tool call costs seconds, while each step costs milliseconds.
Typical waits: 500 ms after Home or opening Spotlight, 900 ms after opening an app, 400 ms before a screenshot.
While `phoned` runs, the user can watch and click the phone live at `vnc://127.0.0.1:5901` (loopback only; the
per-session password is in `~/.cache/iphone/vnc-password`).
If remote start fails with "pair-verify error 2", the host ID isn't pinned: see setup §5 (`bin/pin-host-id`).

## 4. WebDriverAgent: `<skill>/bin/wda`

```
wda start                 # ~15 s (phone unlocked); fails after 7 days on a free Apple ID: run `wda build`
wda els                   # visible labeled elements: type | label | x y  (points, same frame as `phone shot`)
wda text                  # visible text only: read a screen without an image
wda tap "Label"           # exact label, then contains; Buttons/Cells preferred
wda launch com.apple.weather
wda app | home | type "text" | tapxy X Y | stop
```
Fastest pattern: `wda els` once, then `phone tap X Y` (51 ms). `wda launch` (0.34 s) is the fastest way to open an app.
Bundle IDs: com.apple.Preferences, com.apple.weather, com.apple.mobilesafari, com.apple.MobileSMS, com.apple.mobilemail,
com.apple.mobilecal, com.apple.mobileslideshow; others via `pymobiledevice3 apps list`.

## 5. iPhone Mirroring: `<skill>/bin/iphonectl` (phone locked, near the Mac)

Open it without bringing it to the front: `open -g -a "iPhone Mirroring"`, then repeat `iphonectl info` until it prints
the window (exit code 2 = no phone window yet: the phone must be locked and nearby). Eyes: your computer-use tool's window
screenshot, or `iphonectl shot FILE` (needs Screen Recording; Retina images are 2× pixels, so **halve them** to get
the points `iphonectl tap` uses). The window resizes per app, so take coordinates from a fresh screenshot.

| Action | Use | Time |
|---|---|---|
| Open any app | `iphonectl run "spotlight" "wait 350" "keys NAME" "wait 300" "key return"` | 0.95–1.2 s |
| Home / App Switcher / Spotlight / Control Center | `iphonectl home` · `switcher` · `spotlight` · `control` | ~0.1 s, background |
| Tap | your computer-use background click, or `iphonectl run "tap X Y" …` in a chain | |
| Type / keys | `iphonectl keys TEXT` · `iphonectl key return` | |

Keys and helper taps use a guarded ~0.1 s focus blip (the user's keystrokes are held and replayed). **Not possible in
Mirroring:** scroll/swipe, long-press, paste, Cmd-A. Navigate by search, or switch to the developer channel.

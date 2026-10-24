# iphone-control

Let your AI agent (Claude Code, Codex, or anything that can run shell commands) operate **your own iPhone** from your
Mac: open apps, tap, scroll, type, read the screen, without taking over your Mac's screen. It also works over
Tailscale while the phone is on Wi-Fi. That's tested with the phone and Mac on the same Wi-Fi; different networks
haven't been tested yet.

The point is to skip the weeks of experimenting. Everything here was **measured on real hardware**, and
[`docs/findings.md`](docs/findings.md) lists what works, what doesn't, and why, so your agent doesn't retry dead ends.

## What your agent gets

| Mode | When | Eyes | Hands |
|---|---|---|---|
| **Developer channel** (`bin/phone`) | phone **unlocked**, paired, Developer Mode on (cable, same Wi-Fi, or Tailscale with the phone on Wi-Fi) | screenshot **30–70 ms** (live HEVC stream) | tap **50 ms**, drag/scroll, type, exact paste (emoji OK), Home/lock/volume |
| **WebDriverAgent** (`bin/wda`) | same, on cable or same Wi-Fi (not `--remote`), plus a one-time WDA build | the screen as **text/elements** in ~0.5 s | tap by label, launch any app by bundle ID in **0.34 s** |
| **iPhone Mirroring** (`bin/iphonectl`) | phone **locked**, near the Mac | computer-use window screenshot | open any app in ~1 s, tap, type, Home/Spotlight; **no scrolling** |

Measured on iPhone 17 Pro Max, iOS 27.0, macOS 27.0.1, MacBook Pro M5 Max (2026-10). Example: open Weather, scroll,
screenshot = 2.8 s, of which 1.5 s is waiting for animations.

**Not possible:** control over **cellular only**. iOS only opens its developer services when the phone is on *some*
Wi-Fi network. Tailscale reaches the phone on cellular, but the ports are refused.

## How it works (one paragraph)

`phoned.py` keeps one [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) tunnel open to the phone's
CoreDevice services. It runs pymobiledevice3's VNC server in-process, so the phone's screen stream (which also unlocks
touch input) is decoded on the Mac by VideoToolbox, and a screenshot becomes a memory read. Touches and keys go through
the CoreDevice "universal HID" service. `wda` runs Appium's WebDriverAgent bound to the **phone's loopback** and reaches
it through usbmuxd. `iphonectl` drives Apple's iPhone Mirroring window **in the background**: it uses the accessibility
API for menu actions, plus a ~0.1 s "focus blip" for keys, during which your own keystrokes are held and replayed so
none reach the phone.

## Setup

[`docs/setup.md`](docs/setup.md): pair once by USB, turn on Developer Mode, fill in `~/.config/iphone-control/config.json`,
and optionally build WebDriverAgent and set up the Tailscale remote. Then `./install.sh` installs it as a Claude Code skill
(`~/.claude/skills/iphone`). Other agents can read [`skill/SKILL.md`](skill/SKILL.md) directly.

```bash
bin/phone start                 # or: bin/phone start --remote   (Tailscale)
bin/phone "button home" "wait 500" "tap 70 565" "wait 900" "shot"
bin/phone stop                  # also deletes the screenshots it took
```

## Safety model

- Your agent's instructions ([`skill/SKILL.md`](skill/SKILL.md)) require asking you before anything that sends, posts,
  buys, submits, deletes or changes a setting, and forbid typing passwords or moving money.
- Remote control uses the **RemotePairing** protocol: the tunnel only opens with **this Mac's pairing keys**. Network
  position alone gets nothing.
- WebDriverAgent (which has no authentication) is bound to the phone's loopback, so nothing on your LAN or tailnet can
  reach it.
- Screenshots go to `~/.cache/iphone/shots` (mode 0700) and are deleted on `phone stop`. Pairing keys are kept 0600.
  The live-view port (`vnc://127.0.0.1:5901`) accepts touches, so it gets a random per-session password; typed and pasted
  text is never written to logs.
- **Developer Mode lowers your phone's security.** Only paired computers can use these services, but read Apple's warning
  and turn it off if you stop using this. To make the phone forget every trusted computer: Settings → General → Transfer
  or Reset iPhone → Reset → Reset Location & Privacy.

## Caveats

- Uses private macOS APIs (SkyLight) for Mirroring control, plus reverse-engineered iOS developer protocols. Updates can
  break things.
- A free Apple ID signs WebDriverAgent for **7 days**, so you rebuild with `wda build`.
- For your own devices only. Not affiliated with Apple or Anthropic.

## Credits and licenses

This repo is **GPL-3.0** (it builds on pymobiledevice3, which is GPL-3.0).
- [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) (GPL-3.0): tunnels, HID, VNC stream, pasteboard.
- [WebDriverAgent](https://github.com/appium/appium-ios) (Apache-2.0): fetched and built locally by `wda build`, not vendored.
- [yabai](https://github.com/asmvik/yabai) (MIT) and [phone-harness](https://github.com/ShawnPana/phone-harness) (MIT):
  the focus-without-raise event-record technique behind `iphonectl` (reimplemented, not copied).

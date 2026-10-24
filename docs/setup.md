# Setup

Tested on macOS 27.0.1 (Apple silicon) with iOS 27.0. Steps marked **(phone)** need you to tap on the phone.

## 0. Tools on the Mac

```bash
brew install uv                                   # if you don't have it
uv tool install --python 3.13 'pymobiledevice3==11.43.1'   # the version this was tested with (phoned uses its internals)
```
Install **Xcode** (App Store). Pairing below uses `devicectl`, which ships with Xcode, and WebDriverAgent needs Xcode's
**iOS platform** component. On first launch, accept the license and tick iOS (about 16 GB). Then sign in at Xcode →
Settings → Apple Accounts (a free Apple ID works). Upgrade pymobiledevice3 deliberately and retest; its internals change.

## 1. Pair the phone (once, by cable)

Plug the phone in with a data-capable USB-C cable and keep it **unlocked**. iOS won't show the Trust prompt on the lock
screen. Find the UDID with `pymobiledevice3 usbmux list`, then:
```bash
DEVELOPER_DIR="$(xcode-select -p | grep -q Xcode && xcode-select -p || echo /Applications/Xcode.app/Contents/Developer)" \
  xcrun devicectl manage pair --device <UDID>
```
**(phone)** Tap **Trust** and enter your passcode. If it's refused instantly and no prompt appears, unplug, replug and
retry.

## 2. Developer Mode

```bash
pymobiledevice3 amfi reveal-developer-mode
```
**(phone)** Settings → Privacy & Security → **Developer Mode** → On → Restart. After the restart, unlock → **Turn On** →
passcode. Then, with the phone unlocked:
```bash
pymobiledevice3 mounter auto-mount                # Developer Disk Image; remount after iOS updates/reboots if needed
```

## 3. Config

```bash
mkdir -p ~/.config/iphone-control && chmod 700 ~/.config/iphone-control
cp config.example.json ~/.config/iphone-control/config.json && chmod 600 ~/.config/iphone-control/config.json
```
Fill in `udid`. Leave `team_id`, `wda_bundle_id`, `phone_tailscale_ip` and `host_identifier` blank for now; later steps
fill them in. Then set the screen size: start the tool (phone unlocked) and ask the phone.
```bash
bin/phone start && bin/phone display      # pixel size of the screen
```
`screen_points` is pixels ÷ `screen_scale`. The scale is 3 on current iPhones; on older or SE/XR models check Apple's specs.
Example: iPhone 17 Pro Max is 1320 × 2868 px → 440 × 956 pt at scale 3. If the config is wrong, phoned's log warns that
the stream size doesn't match. Restart after editing (`bin/phone stop && bin/phone start`), then test with `bin/phone
"shot"`.

**Same Wi-Fi without the cable:** check that the Mac sees the phone over Wi-Fi with `pymobiledevice3 usbmux list
--network`. If it's missing, run `pymobiledevice3 lockdown wifi-connections on` once with the phone on USB.

## 4. WebDriverAgent (optional: element tree, tap by name)

1. Run `bin/wda build` with `team_id` empty. It prints the team IDs of the Apple ID signed into Xcode. Put yours in the
   config as `team_id`, with a unique `wda_bundle_id` (e.g. `com.yourname.WebDriverAgentRunner`).
2. `bin/wda build` (about 15 s). This fetches appium/appium-ios, then signs and installs WebDriverAgent.
3. **(phone)** Settings → General → VPN & Device Management → your developer → **Trust**, then **Verify App** (needs
   internet).
4. `bin/wda start` → `bin/wda els`.

If Verify App does nothing, see [troubleshooting-verify-app.md](troubleshooting-verify-app.md). On our phone the Secure
Enclave refused attestation, and a **phone restart** fixed it. Free Apple IDs expire the signature every **7 days**;
just run `bin/wda build` again.

## 5. Remote control over Tailscale (optional)

Install Tailscale on the Mac and the phone, and set `phone_tailscale_ip` (`tailscale status`). With the phone on USB:
```bash
pymobiledevice3 lockdown remotepairing --pair      # promptless over the trusted USB link
chmod 700 ~/.pymobiledevice3 && chmod 600 ~/.pymobiledevice3/remote_*.plist   # it holds a private key
bin/pin-host-id                                    # IMPORTANT: run on the same network, right after pairing
bin/phone start --remote                           # phone on Wi-Fi with Tailscale on
```
Why `pin-host-id`: pymobiledevice3 names your Mac by a hash of its hostname, and macOS changes the hostname per network.
Without the pin, remote control breaks with "pair-verify error 2" whenever the hostname changes.
Tested with the phone and Mac on the same Wi-Fi; phone and Mac on different networks hasn't been tested yet. It doesn't
work with the phone on cellular only (iOS closes the ports). WebDriverAgent isn't available over `--remote`.

## 6. iPhone Mirroring helper (optional: phone locked, near the Mac)

```bash
cd bin && swiftc -O iphonectl.swift -o iphonectl -F /System/Library/PrivateFrameworks -framework SkyLight
```
Grant **Accessibility** (and Input Monitoring, if macOS asks) to the app your agent runs in: System Settings → Privacy &
Security. Without them `iphonectl` can't hold your keystrokes during its focus blip, and it prints a warning. For
`iphonectl shot` also grant **Screen Recording**, or use your agent's own window-screenshot tool. Check: `bin/iphonectl
info` prints the Mirroring window size once iPhone Mirroring is open (`open -g -a "iPhone Mirroring"`, phone locked and
nearby).

## 7. Install as a Claude Code skill

```bash
./install.sh            # copies skill/SKILL.md, docs and bin to ~/.claude/skills/iphone (keeps your LOCAL.md)
```
Optional: put your own device notes in `~/.claude/skills/iphone/LOCAL.md` (never committed). The skill reads it if present.

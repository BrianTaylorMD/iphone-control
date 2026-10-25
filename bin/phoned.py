#!/usr/bin/env python3
"""phoned — keep ONE developer connection to your iPhone open and serve fast commands.

The pymobiledevice3 CLI rebuilds the tunnel + the screen media stream (which gates touch input) on every
call, ~2-3 s each. This daemon builds them once, then each command costs milliseconds plus the gesture.
Needs: phone paired with this Mac, Developer Mode on, Developer Disk Image mounted, phone UNLOCKED.

Run with pymobiledevice3's own interpreter:
  ~/.local/share/uv/tools/pymobiledevice3/bin/python phoned.py serve [--udid UDID] [--network]
(--remote [HOST[:49152]] = encrypted RemotePairing tunnel to that address, e.g. the phone's Tailscale IP; with no HOST
 it uses phone_tailscale_ip from ~/.config/iphone-control/config.json;
 --network forces Wi-Fi even when the cable is in; --no-vnc skips the in-process VNC stream and uses PNG screenshots.)
While it runs, vnc://127.0.0.1:5901 (Finder Cmd+K, loopback only) shows the phone live and accepts clicks; the
per-session password is in ~/.cache/iphone/vnc-password (mode 0600).
Talk to it with ./phone (stdlib client). Socket: ~/.cache/iphone/phoned.sock (mode 0600, user-only).

Coordinates are iOS POINTS (config screen_points, e.g. 440 x 956 on iPhone 17 Pro Max), the same frame as `shot`'s image.
Commands (one per line; a line may be a JSON string so text can contain newlines):
  tap X Y | drag X1 Y1 X2 Y2 [MS] | type TEXT | paste TEXT | key NAME [N] | button home|lock|volup|voldown
  shot [PATH] | clipboard | display | wait MS | ping   (shot with no PATH -> ~/.cache/iphone/shots/, wiped by `phone stop`)
"""
import asyncio
import contextlib
import json
import os
import secrets
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SOCK = Path.home() / ".cache/iphone/phoned.sock"
# Screenshots live here (mode 0700) and are wiped by `phone stop`; anything older than an hour is pruned on start.
SHOTS = Path.home() / ".cache/iphone/shots"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import phonecfg  # noqa: E402  (per-user values live outside the repo)

CFG = phonecfg.load()
SCALE = CFG.get("screen_scale", 3)                      # pixels per point (3 on Pro/Pro Max models)
HOST_ID = CFG.get("host_identifier")                     # the host ID this Mac registered with the phone (see docs/setup.md)
VNC_PASSWORD_FILE = Path.home() / ".cache/iphone/vnc-password"
KEY_NAMES = ("enter", "return", "escape", "esc", "backspace", "delete", "tab", "space", "left", "right", "up", "down")
BUTTONS = {"home": (0x0C, 0x40, 0.05), "lock": (0x0C, 0x30, 0.5), "volup": (0x0C, 0xE9, 0.05), "voldown": (0x0C, 0xEA, 0.05)}


class BadInput(Exception):
    """A step the phone can't do as written. Reported to the caller; never triggers a reconnect."""


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class Phone:
    def __init__(self, udid, network=False, use_vnc=True, remote=None):
        self.udid = udid or CFG.get("udid")   # always target the configured phone, even with several attached
        self.network, self.use_vnc, self.remote = network, use_vnc, remote
        self.stack = None
        self.size_pt = tuple(CFG.get("screen_points", (440, 956)))

    async def connect(self) -> None:
        from pymobiledevice3.remote.core_device.hid_service import IndigoHIDService, touch_session
        from pymobiledevice3.remote.core_device.pasteboard_service import PasteboardService
        from pymobiledevice3.remote.userspace_tunnel import establish_userspace_rsd

        await self.close()
        t0 = time.monotonic()
        if self.remote:
            # Route B: dial the phone's RemotePairing port at a fixed address (its Tailscale IP), authenticated by the
            # pairing record in ~/.pymobiledevice3 (pair-verify only; autopair=False never creates a new pairing).
            from pymobiledevice3.remote import tunnel_service, userspace_tunnel
            host, _, port = self.remote.partition(":")
            udid = phonecfg.require(CFG, "udid", "see docs/setup.md §3") if not self.udid else self.udid
            # The phone knows this Mac by the host ID it registered at pairing. pymobiledevice3 derives that ID from the
            # Mac's network hostname, which macOS changes with the network (e.g. "Mac" vs "Name-MacBook-Pro.local"), and a
            # different ID gets TLV error 2 (unknown host). The record pins `host_identifier`; pin the generator too.
            from pymobiledevice3 import pair_records
            if not HOST_ID:
                raise RuntimeError("host_identifier missing from config; see docs/setup.md (pin the host ID)")
            pinned = HOST_ID
            pair_records.generate_host_id = lambda hostname=None: pinned
            tunnel_service.generate_host_id = lambda hostname=None: pinned
            async def remote_provider(serial, autopair, remotepairing_fallback=True):
                delays = [0, 5, 15, 30]          # refused/timeout = phone not on Wi-Fi (yet); auth errors are not retried
                for i, delay in enumerate(delays):
                    await asyncio.sleep(delay)
                    svc = tunnel_service.RemotePairingTunnelService(udid, host, int(port or 49152))
                    try:
                        await svc.connect(autopair=False)
                        return svc, None
                    except (ConnectionRefusedError, asyncio.TimeoutError, OSError) as e:
                        log(f"remote: {type(e).__name__} on try {i + 1} (phone off Wi-Fi or Tailscale down?)")
                        if i == len(delays) - 1:
                            raise
                    except Exception as e:
                        raise RuntimeError(f"phone rejected this Mac (pair-verify, sent host id {svc.identifier}); "
                                           f"check host_identifier in ~/.pymobiledevice3/remote_{udid}.plist: {e!r}") from e
            userspace_tunnel._create_no_root_tunnel_provider = remote_provider
        elif self.network:
            # Force the Wi-Fi route even with the cable in: usbmuxd lists the phone on both transports.
            from pymobiledevice3 import lockdown as lockdown_mod
            from pymobiledevice3.remote import userspace_tunnel
            real = lockdown_mod.create_using_usbmux
            async def over_wifi(*args, **kwargs):
                kwargs["connection_type"] = "Network"
                return await real(*args, **kwargs)
            userspace_tunnel.create_using_usbmux = over_wifi
        rsd = await establish_userspace_rsd(serial=self.udid, remotepairing_fallback=False)
        stack = contextlib.AsyncExitStack()
        self.vnc = None
        if self.use_vnc:
            # The VNC server owns the screen media stream (which also opens the touch gate) and decodes it with
            # VideoToolbox, keeping the latest frame in memory: screenshots become a memory read, and you can
            # watch live at vnc://127.0.0.1:5901 while phoned runs.
            from pymobiledevice3.remote.core_device.hid_service import UniversalHIDServiceService
            from pymobiledevice3.remote.core_device.vnc_server import VncStreamServer
            try:
                # A fresh random password per session: the viewer port accepts touches, so don't leave it open to
                # every local process. (VNC auth only uses the first 8 characters.)
                password = secrets.token_urlsafe(6)[:8]
                VNC_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
                VNC_PASSWORD_FILE.write_text(password + "\n")
                os.chmod(VNC_PASSWORD_FILE, 0o600)
                self.vnc = VncStreamServer(rsd, bind="127.0.0.1", port=5901, password=password)
                self.vnc_task = asyncio.create_task(self.vnc.serve())
                await asyncio.wait_for(self.vnc._ready.wait(), timeout=15)
                want_w, want_h = self.size_pt[0] * SCALE, self.size_pt[1] * SCALE
                w, h = self.vnc._fb_width, self.vnc._fb_height
                if not (want_w <= w <= want_w + 64 and want_h <= h <= want_h + 64):
                    log(f"WARNING: stream is {w}x{h} px but config says {self.size_pt} pt x{SCALE}; fix screen_points/"
                        f"screen_scale (try `phone display`) or taps will land in the wrong place")
                await asyncio.sleep(0.5)  # same settle touch_session uses before touches are accepted
                stack.push_async_callback(self._stop_vnc)
                self.hid = await stack.enter_async_context(UniversalHIDServiceService(rsd))
            except Exception as e:
                log(f"VNC stream unavailable ({e!r}); using touch_session + PNG screenshots")
                await self._stop_vnc()
                self.vnc = None
        if self.vnc is None:
            self.hid = await stack.enter_async_context(touch_session(rsd))
        self.kb = await self.hid.create_keyboard_service()
        await self.hid.send_keyboard(self.kb, ())  # the first report after attach is dropped; spend it on "no keys"
        await asyncio.sleep(0.05)
        self.buttons = await stack.enter_async_context(IndigoHIDService(rsd))
        self.pasteboard = await stack.enter_async_context(PasteboardService(rsd))
        # Instruments (DVT) screenshot channel stays open across shots, unlike the CoreDevice capture service.
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.screenshot import Screenshot
        dvt = await stack.enter_async_context(DvtProvider(rsd))
        self.dvt_shot = await stack.enter_async_context(Screenshot(dvt))
        self.rsd, self.stack = rsd, stack
        log(f"connected in {time.monotonic() - t0:.2f}s")

    async def _stop_vnc(self) -> None:
        task = getattr(self, "vnc_task", None)
        if task:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        self.vnc_task = None

    async def close(self) -> None:
        if self.stack:
            with contextlib.suppress(Exception):
                await self.stack.aclose()
        self.stack = None

    def norm(self, x: float, y: float) -> tuple:
        w, h = self.size_pt
        return (max(0, min(65535, round(x * 65535 / w))), max(0, min(65535, round(y * 65535 / h))))

    async def tap(self, x, y) -> None:
        from pymobiledevice3.remote.core_device.hid_service import TOUCHSCREEN_STATE_CONTACT, TOUCHSCREEN_STATE_RELEASE
        nx, ny = self.norm(x, y)
        await self.hid.send_touchscreen(TOUCHSCREEN_STATE_CONTACT, nx, ny)
        await asyncio.sleep(0.05)
        await self.hid.send_touchscreen(TOUCHSCREEN_STATE_RELEASE, nx, ny)

    async def drag(self, x1, y1, x2, y2, ms=300) -> None:
        from pymobiledevice3.remote.core_device.hid_service import TOUCHSCREEN_STATE_CONTACT, TOUCHSCREEN_STATE_RELEASE
        (a, b), (c, d) = self.norm(x1, y1), self.norm(x2, y2)
        steps = max(6, int(ms / 16))
        for i in range(steps):
            t = i / steps
            await self.hid.send_touchscreen(TOUCHSCREEN_STATE_CONTACT, round(a + (c - a) * t), round(b + (d - b) * t))
            await asyncio.sleep(ms / 1000 / steps)
        await self.hid.send_touchscreen(TOUCHSCREEN_STATE_CONTACT, c, d)
        await self.hid.send_touchscreen(TOUCHSCREEN_STATE_RELEASE, c, d)

    async def hold(self, x1, y1, x2, y2, press_ms=600, move_ms=400, hover_ms=0) -> None:
        """Press and hold, then move and hover before releasing: long-press, icon drags, drop-onto-icon folders."""
        from pymobiledevice3.remote.core_device.hid_service import TOUCHSCREEN_STATE_CONTACT, TOUCHSCREEN_STATE_RELEASE
        (a, b), (c, d) = self.norm(x1, y1), self.norm(x2, y2)

        async def stay(x, y, ms):  # keep reporting the contact so the finger counts as still down
            for _ in range(max(1, int(ms / 16))):
                await self.hid.send_touchscreen(TOUCHSCREEN_STATE_CONTACT, x, y)
                await asyncio.sleep(0.016)
        await stay(a, b, press_ms)
        steps = max(1, int(move_ms / 16))
        for i in range(1, steps + 1):
            t = i / steps
            await self.hid.send_touchscreen(TOUCHSCREEN_STATE_CONTACT, round(a + (c - a) * t), round(b + (d - b) * t))
            await asyncio.sleep(move_ms / 1000 / steps)
        await stay(c, d, hover_ms)
        await self.hid.send_touchscreen(TOUCHSCREEN_STATE_RELEASE, c, d)

    async def open_url(self, bundle, url) -> None:
        """CoreDevice launchapplication with payloadURL, the field devicectl --payload-url sends. Settings is relaunched
        so it follows the new link; other apps are only brought forward."""
        import plistlib
        from pymobiledevice3.remote.core_device.app_service import AppServiceService
        async with AppServiceService(self.rsd) as apps:
            await apps.invoke("com.apple.coredevice.feature.launchapplication", {
                "applicationSpecifier": {"bundleIdentifier": {"_0": bundle}},
                "options": {"arguments": [], "environmentVariables": {}, "standardIOUsesPseudoterminals": True,
                            "startStopped": False, "terminateExisting": bundle == "com.apple.Preferences",
                            "activates": True, "user": {"shortName": "mobile"}, "payloadURL": {"relative": url},
                            "platformSpecificOptions": plistlib.dumps({})},
                "standardIOIdentifiers": {}})

    async def keys(self, usages) -> None:
        await self.hid.send_keyboard(self.kb, usages)
        await asyncio.sleep(0.03)
        await self.hid.send_keyboard(self.kb, ())
        await asyncio.sleep(0.015)

    async def type(self, text: str) -> None:
        from pymobiledevice3.remote.core_device.hid_service import ASCII_TO_HID, KEY_LEFT_SHIFT
        bad = sorted({ch for ch in text if ch not in ASCII_TO_HID})
        if bad:
            raise BadInput(f"type is ASCII-only (can't type {bad[:5]}); use paste")
        for ch in text:
            usage, shift = ASCII_TO_HID[ch]
            if shift:
                await self.hid.send_keyboard(self.kb, (KEY_LEFT_SHIFT,))
                await asyncio.sleep(0.02)
                await self.keys((KEY_LEFT_SHIFT, usage))
            else:
                await self.keys((usage,))

    async def paste(self, text: str) -> None:
        # Exact text (emoji, accents, no autocorrect): set the PHONE's clipboard, then Cmd-V.
        # The phone's existing clipboard is saved first and put back afterwards.
        from pymobiledevice3.remote.core_device.hid_service import KEY_LEFT_GUI
        from pymobiledevice3.remote.core_device.pasteboard_service import snapshot_text
        prev = None
        with contextlib.suppress(Exception):
            prev = await asyncio.wait_for(self.pasteboard.get(), timeout=3)
        await self.pasteboard.set_text(text)
        await asyncio.sleep(0.1)
        await self.hid.send_keyboard(self.kb, (KEY_LEFT_GUI,))
        await asyncio.sleep(0.03)
        await self.keys((KEY_LEFT_GUI, 0x19))  # 0x19 = V
        await asyncio.sleep(0.4)
        if prev is not None:
            items = prev.get("pasteboard", {}).get("items")
            try:
                await self.pasteboard.set(items or [])
            except Exception:
                old = snapshot_text(prev)
                if old is not None:
                    await self.pasteboard.set_text(old)

    async def key(self, name: str, n: int = 1) -> None:
        from pymobiledevice3.remote.core_device import hid_service as h
        table = {"enter": h.KEY_ENTER, "return": h.KEY_ENTER, "escape": h.KEY_ESC, "esc": h.KEY_ESC,
                 "backspace": h.KEY_BACKSPACE, "delete": h.KEY_BACKSPACE, "tab": h.KEY_TAB, "space": h.KEY_SPACE,
                 "left": h.KEY_LEFT, "right": h.KEY_RIGHT, "up": h.KEY_UP, "down": h.KEY_DOWN}
        if name not in table:
            raise BadInput(f"unknown key {name!r}; one of {', '.join(KEY_NAMES)}")
        for _ in range(n):
            await self.keys((table[name],))

    async def button(self, name: str) -> None:
        from pymobiledevice3.remote.core_device.hid_service import HID_BUTTON_STATE_DOWN, HID_BUTTON_STATE_UP
        if name not in BUTTONS:
            raise BadInput(f"unknown button {name!r}; one of {', '.join(BUTTONS)}")
        page, code, hold = BUTTONS[name]
        await self.buttons.send_button(page, code, HID_BUTTON_STATE_DOWN)
        await asyncio.sleep(hold)
        await self.buttons.send_button(page, code, HID_BUTTON_STATE_UP)

    async def shot(self, path) -> dict:
        if not path:
            SHOTS.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = str(SHOTS / (time.strftime("shot-%H%M%S-") + f"{int(time.time() * 1000) % 1000:03d}.jpg"))
        if self.vnc is not None and self.vnc._latest_frame:
            frame, w, h = self.vnc._latest_frame, self.vnc._fb_width, self.vnc._fb_height
            cw, ch = min(w, self.size_pt[0] * SCALE), min(h, self.size_pt[1] * SCALE)  # drop HEVC block padding
            if (cw, ch) != (w, h):
                frame = b"".join(frame[r * w * 4:(r * w + cw) * 4] for r in range(ch))
            # Temp files are our own (mkstemp in the private shots dir): never derived from the caller's path.
            SHOTS.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, bmp = tempfile.mkstemp(suffix=".bmp", dir=SHOTS)
            with os.fdopen(fd, "wb") as fh:
                fh.write(struct.pack("<2sIHHI", b"BM", 54 + len(frame), 0, 0, 54)
                         + struct.pack("<IiiHHIIiiII", 40, cw, -ch, 1, 32, 0, len(frame), 2835, 2835, 0, 0) + frame)
            fmt = "jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "png"
            r = subprocess.run(["sips", "-s", "format", fmt, "--resampleWidth", str(self.size_pt[0]), bmp, "--out", path],
                               capture_output=True)
            os.unlink(bmp)
            if r.returncode != 0 or not Path(path).exists():
                raise BadInput(f"could not write {path}: {r.stderr.decode(errors='replace')[:200]}")
            return {"path": path, "size_pt": list(self.size_pt), "source": "stream"}
        try:
            image = await asyncio.wait_for(self.dvt_shot.get_screenshot(), timeout=5)
        except Exception as e:
            log(f"dvt screenshot failed ({e!r}); using the capture service")
            from pymobiledevice3.remote.core_device.screen_capture_service import ScreenCaptureService
            async with ScreenCaptureService(self.rsd) as capture:
                image = (await asyncio.wait_for(capture.capture_screenshot(), timeout=8))["image"]
        SHOTS.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, full = tempfile.mkstemp(suffix=".png", dir=SHOTS)
        with os.fdopen(fd, "wb") as fh:
            fh.write(image)
        # Downscale to points so the picture's pixels ARE tap coordinates; the full-size copy is not kept.
        r = subprocess.run(["sips", "-Z", str(self.size_pt[1]), full, "--out", path], capture_output=True)
        os.unlink(full)
        if r.returncode != 0 or not Path(path).exists():
            raise BadInput(f"could not write {path}: {r.stderr.decode(errors='replace')[:200]}")
        return {"path": path, "size_pt": list(self.size_pt)}

    async def run(self, step: str) -> dict:
        parts = step.split()
        if not parts:
            raise BadInput("empty step")
        cmd, a = parts[0], parts[1:]
        rest = step.lstrip()[len(cmd):].lstrip(" ")       # keeps newlines and inner spacing for type/paste

        def f(i, d=None):
            if i < len(a):
                try:
                    return float(a[i])
                except ValueError:
                    raise BadInput(f"{cmd}: {a[i]!r} is not a number") from None
            if d is None:
                raise BadInput(f"{cmd}: missing argument {i + 1}")
            return d
        if cmd == "ping": return {"ok": True}
        if cmd == "tap": await self.tap(f(0), f(1))
        elif cmd == "drag": await self.drag(f(0), f(1), f(2), f(3), f(4, 300))
        elif cmd == "hold": await self.hold(f(0), f(1), f(2, f(0)), f(3, f(1)), f(4, 600), f(5, 400), f(6, 0))
        elif cmd == "type": await self.type(rest)
        elif cmd == "paste": await self.paste(rest)
        elif cmd == "key": await self.key(a[0] if a else "", int(f(1, 1)))
        elif cmd == "button": await self.button(a[0] if a else "")
        elif cmd == "wait": await asyncio.sleep(f(0) / 1000)
        elif cmd == "clipboard":  # read the phone's clipboard text
            from pymobiledevice3.remote.core_device.pasteboard_service import snapshot_text
            return {"ok": True, "text": snapshot_text(await self.pasteboard.get())}
        elif cmd == "shot": return {"ok": True, **await self.shot(a[0] if a else None)}
        elif cmd == "url":  # url BUNDLE_ID URL: open a deep link through this tunnel (works over --remote too)
            if len(a) < 2:
                raise BadInput("url BUNDLE_ID URL")
            if a[1].split(":", 1)[0].lower() in ("tel", "telprompt", "facetime", "facetime-audio", "facetime-prompt"):
                raise BadInput("calls are blocked: tel:/facetime: dial immediately")
            await self.open_url(a[0], a[1])
        elif cmd == "display":  # the phone's real screen size, for screen_points / screen_scale in the config
            from pymobiledevice3.remote.core_device.device_info import DeviceInfoService
            async with DeviceInfoService(self.rsd) as info:
                return {"ok": True, "display": await info.get_display_info()}
        else: return {"ok": False, "error": f"unknown command {cmd}"}
        return {"ok": True}


def prune_shots(max_age_s: float = 3600) -> None:
    if SHOTS.exists():
        for f in SHOTS.iterdir():
            if time.time() - f.stat().st_mtime > max_age_s:
                f.unlink(missing_ok=True)


async def serve(udid, network=False, use_vnc=True, remote=None) -> None:
    os.umask(0o077)   # everything phoned writes (socket, screenshots) is readable by your user only
    prune_shots()
    phone = Phone(udid, network, use_vnc, remote)
    await phone.connect()
    lock = asyncio.Lock()

    async def handle(reader, writer) -> None:
        while raw := (await reader.readline()).decode().rstrip("\n"):
            # `phone` sends each step as a JSON string so typed/pasted text can contain newlines.
            step = json.loads(raw) if raw.startswith('"') else raw
            cmd = (step.split() or ["?"])[0]
            t0 = time.monotonic()
            async with lock:
                try:
                    res = await phone.run(step)
                except (BadInput, KeyError, ValueError, TypeError, IndexError) as e:   # the step's fault: don't reconnect
                    res = {"ok": False, "error": str(e)}
                except Exception as e:  # stream or tunnel dropped: rebuild once and retry (never log the step's text)
                    log(f"{cmd} failed ({type(e).__name__}); reconnecting")
                    try:
                        await phone.connect()
                        res = await phone.run(step)
                    except Exception as e2:
                        res = {"ok": False, "error": repr(e2)}
            res["cmd"], res["ms"] = cmd, round((time.monotonic() - t0) * 1000)
            writer.write((json.dumps(res) + "\n").encode())
            await writer.drain()
        writer.close()

    SOCK.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        SOCK.unlink()
    server = await asyncio.start_unix_server(handle, path=str(SOCK))
    os.chmod(SOCK, 0o600)
    log(f"listening on {SOCK}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        udid = sys.argv[sys.argv.index("--udid") + 1] if "--udid" in sys.argv else None
        remote = None
        if "--remote" in sys.argv:
            nxt = sys.argv[sys.argv.index("--remote") + 1:sys.argv.index("--remote") + 2]
            remote = nxt[0] if nxt and not nxt[0].startswith("--") else phonecfg.require(
                CFG, "phone_tailscale_ip", "set it to the phone's Tailscale IP (docs/setup.md §5)")
        asyncio.run(serve(udid, "--network" in sys.argv, "--no-vnc" not in sys.argv, remote))
    else:
        print(__doc__)

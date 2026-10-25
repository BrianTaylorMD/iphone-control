#!/bin/sh
# Install the iphone skill for Claude Code: ~/.claude/skills/iphone (SKILL.md, docs, bin). Never touches LOCAL.md,
# your config (~/.config/iphone-control) or your pairing keys (~/.pymobiledevice3).
set -e
cd "$(dirname "$0")"
DEST="${1:-$HOME/.claude/skills/iphone}"
mkdir -p "$DEST/bin" "$DEST/docs"
cp skill/SKILL.md "$DEST/SKILL.md"
cp docs/*.md docs/*.json "$DEST/docs/"
cp bin/phone bin/phoned.py bin/phonecfg.py bin/wda bin/pin-host-id bin/layout bin/open bin/iphonectl.swift "$DEST/bin/"
chmod +x "$DEST/bin/phone" "$DEST/bin/phoned.py" "$DEST/bin/wda" "$DEST/bin/pin-host-id" "$DEST/bin/layout" "$DEST/bin/open"
if command -v swiftc >/dev/null 2>&1; then
  swiftc -O "$DEST/bin/iphonectl.swift" -o "$DEST/bin/iphonectl" -F /System/Library/PrivateFrameworks -framework SkyLight \
    && echo "built iphonectl" || echo "iphonectl build failed (Mirroring mode only; the rest works)"
fi
[ -f "$HOME/.config/iphone-control/config.json" ] || echo "next: create ~/.config/iphone-control/config.json (docs/setup.md §3)"
echo "installed to $DEST"

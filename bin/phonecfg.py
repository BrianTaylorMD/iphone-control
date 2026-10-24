"""Per-user settings for the iphone tools, kept OUT of the repo: ~/.config/iphone-control/config.json (mode 0600).

Keys: udid, phone_tailscale_ip, host_identifier, team_id, wda_bundle_id, wda_dir, screen_points, screen_scale.
See config.example.json in the repo. Empty strings mean "not set yet".
"""
import json
import sys
from pathlib import Path

PATH = Path.home() / ".config/iphone-control/config.json"


def load() -> dict:
    try:
        raw = json.loads(PATH.read_text())
    except FileNotFoundError:
        sys.exit(f"missing {PATH}: copy config.example.json there (chmod 600) and fill in your phone's values")
    # Treat blanks as unset so the "missing" checks fire instead of sending placeholder values to the phone.
    return {k: v for k, v in raw.items() if v not in ("", None)}


def require(cfg: dict, key: str, hint: str):
    if key not in cfg:
        sys.exit(f"config is missing {key!r} ({PATH}): {hint}")
    return cfg[key]

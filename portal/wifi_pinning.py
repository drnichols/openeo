"""Shared Wi-Fi pinning helpers for the portal and the Wi-Fi watchdog.

Every nmcli, ping and iw call goes through a Runner: a callable taking an argument
list and returning a subprocess.CompletedProcess, which never raises. Tests inject a
fake one, so nothing here needs real hardware.
"""

import subprocess

# =========================
# Constants (not user config, see spec §11)
# =========================

SCAN_SAMPLES = 3
SCAN_INTERVAL = 3
PIN_MIN_SIGNAL = 30
HEALTH_PINGS = 10
HEALTH_MAX_LOSS = 20
HEALTH_SETTLE = 120
HEALTH_INTERVAL = 600
HEALTH_FAILS_TO_UNPIN = 2
FAILOVER_AFTER = 60
BLOCKED_RECOVERY_AFTER = 300
RECONNECT_MIN_GAP = 300
REPIN_STABLE = 600
REPIN_RETRY = 3600
REPIN_MAX_REJECTIONS = 3
LOCK_STALE = 300
SETUP_LOCK = "/run/openeo-wifi-setup.lock"
STATE_FILE = "/run/openeo-wifi-watchdog/state.json"
CONNECTION_NAME = "custom-wifi"
WIFI_INTERFACE = "wlan0"


# =========================
# Runner
# =========================

def make_runner(sudo, timeout=120):
    """Return a Runner. Missing binaries give rc 127 and timeouts rc 124."""
    def run(args):
        cmd = (['sudo'] if sudo else []) + list(args)
        try:
            return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
        except FileNotFoundError:
            return subprocess.CompletedProcess(cmd, 127, "", "")
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(cmd, 124, "", "timeout")
    return run


# =========================
# Parsing and formatting
# =========================

def split_terse(line):
    """Split nmcli terse output on unescaped colons, un-escaping each field."""
    fields = [""]
    escaped = False
    for ch in line:
        if escaped:
            fields[-1] += ch
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("")
        else:
            fields[-1] += ch
    return fields


def unescape(value):
    """Un-escape a single nmcli -g value, e.g. a BSSID printed as AA\\:BB\\:..."""
    out = ""
    escaped = False
    for ch in value:
        if escaped or ch != "\\":
            out += ch
            escaped = False
        else:
            escaped = True
    return out


def format_event(event, fields):
    """Format a log line as 'EVENT key=value ...', leaving out None values."""
    parts = [event] + [f"{k}={v}" for k, v in fields.items() if v is not None]
    return " ".join(parts)

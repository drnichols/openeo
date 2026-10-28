"""Shared Wi-Fi pinning helpers for the portal and the Wi-Fi watchdog.

Every nmcli, ping and iw call goes through a Runner: a callable taking an argument
list and returning a subprocess.CompletedProcess, which never raises. Tests inject a
fake one, so nothing here needs real hardware.
"""

import math
import statistics
import subprocess
import time
from dataclasses import dataclass

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


# =========================
# Scan sampling and selection rule (§4)
# =========================

@dataclass
class Selection:
    bssid: str = None
    signal: int = None
    runner_up: int = None
    samples: int = 0
    reason: str = "single"    # pin | single | weak | noscan


def parse_scan(stdout):
    """Parse 'nmcli -t -f BSSID,SSID,SIGNAL' output into (bssid, ssid, signal) tuples."""
    rows = []
    for line in stdout.splitlines():
        fields = split_terse(line)
        if len(fields) != 3 or not fields[0]:
            continue
        try:
            rows.append((fields[0], fields[1], int(fields[2])))
        except ValueError:
            continue
    return rows


def select_bssid(samples, ssid):
    """Apply the selection rule to scan samples for the target SSID."""
    if not samples:
        return Selection(reason="noscan")

    seen = {}    # bssid -> list of per-sample signals
    for sample in samples:
        best_in_sample = {}
        for bssid, row_ssid, signal in sample:
            if row_ssid == ssid and bssid:
                best_in_sample[bssid] = max(signal, best_in_sample.get(bssid, signal))
        for bssid, signal in best_in_sample.items():
            seen.setdefault(bssid, []).append(signal)

    needed = math.ceil(len(samples) / 2)
    candidates = [(statistics.median_low(sigs), len(sigs), bssid)
                  for bssid, sigs in seen.items() if len(sigs) >= needed]
    # Strongest first; ties go to the node seen most often, then the lowest BSSID
    candidates.sort(key=lambda c: (-c[0], -c[1], c[2]))

    if len(candidates) < 2:
        return Selection(samples=len(samples), reason="single")
    best, second = candidates[0], candidates[1]
    if best[0] < PIN_MIN_SIGNAL:
        return Selection(signal=best[0], runner_up=second[0], samples=len(samples), reason="weak")
    return Selection(best[2], best[0], second[0], len(samples), "pin")


def scan_samples(run, sleep=time.sleep):
    """Take SCAN_SAMPLES scans. Failed scans are skipped; if all fail, use NM's cached results."""
    cmd = ['nmcli', '-t', '-f', 'BSSID,SSID,SIGNAL', 'dev', 'wifi', 'list',
           'ifname', WIFI_INTERFACE, '--rescan']
    samples = []
    for i in range(SCAN_SAMPLES):
        if i:
            sleep(SCAN_INTERVAL)
        result = run(cmd + ['yes'])
        if result.returncode == 0:
            samples.append(parse_scan(result.stdout))
    if not samples:
        result = run(cmd + ['no'])
        if result.returncode == 0:
            samples.append(parse_scan(result.stdout))
    return samples


def choose_pin(run, ssid, sleep=time.sleep):
    return select_bssid(scan_samples(run, sleep), ssid)

#################################################################################
"""
OpenEO Module: Log Viewer helpers
Helpers for the /getlogs endpoint served by the configserver module. Kept free of
globalState imports so that they can be unit tested off-device.

Sources:
  app     - the application event log held in the sqlite "log" table
  journal - the systemd journal for the openeo service
  wifi    - Wi-Fi event lines from the watchdog, setup-portal and wpa_supplicant journals
"""
#################################################################################

import re, subprocess, urllib.parse

LOG_SOURCES = ("app", "journal", "wifi")
DEFAULT_SOURCE = "app"
DEFAULT_LINES = 500
MIN_LINES = 50
MAX_LINES = 5000
JOURNAL_TIMEOUT = 10

WIFI_UNITS = ("openeo_wifi_watchdog", "openeo_portal", "wpa_supplicant")
WIFI_SCAN_LINES = 20000
# Allowlist: old setup-portal request lines contain the Wi-Fi password, so only
# lines whose message starts with a known event name may ever be shown.
WIFI_EVENTS = frozenset({"PIN", "PIN_REJECTED", "UNPIN", "REPIN", "REPIN_GIVEUP", "RECONNECT", "HEALTH_FAIL", "ERROR"})
_JOURNAL_MESSAGE = re.compile(r"^\S+ \S+ [^\s\[:]+(?:\[\d+\])?: (.*)$")
_WPA_EVENT = re.compile(r"^wlan0: CTRL-EVENT-(DISCONNECTED|ASSOC-REJECT|SSID-TEMP-DISABLED|CONNECTED)\b")


def parse_log_query(path):
    """
    Parses the query string of a /getlogs request, returning (source, lines).
    Unknown sources fall back to the default, and the line count is clamped.
    """
    query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)

    source = query.get("source", [DEFAULT_SOURCE])[0]
    if source not in LOG_SOURCES:
        source = DEFAULT_SOURCE

    try:
        lines = int(query.get("lines", [DEFAULT_LINES])[0])
    except ValueError:
        lines = DEFAULT_LINES
    lines = max(MIN_LINES, min(MAX_LINES, lines))

    return source, lines


def read_journal(lines, units=("openeo",)):
    """
    Returns the last n lines of the given service journals as a string. Arguments are
    passed as a list (no shell), so nothing from the request can reach a shell.
    """
    unit_args = [arg for unit in units for arg in ("-u", unit)]
    try:
        result = subprocess.run(
            ["journalctl", *unit_args, "-n", str(int(lines)), "--no-pager", "-o", "short-iso"],
            capture_output=True,
            text=True,
            timeout=JOURNAL_TIMEOUT,
        )
    except FileNotFoundError:
        return "Error: journalctl is not available on this system"
    except subprocess.TimeoutExpired:
        return f"Error: journalctl did not respond within {JOURNAL_TIMEOUT}s"

    if result.returncode != 0:
        return f"Error: {result.stderr.strip() or 'journalctl exited with code ' + str(result.returncode)}"
    return result.stdout


def is_wifi_event(line):
    """
    True only for a short-iso journal line whose message is a known Wi-Fi event. The
    decision is made on the message's first token, never on substrings, so a request
    line that merely mentions an event name (or carries a password) is rejected.
    """
    match = _JOURNAL_MESSAGE.match(line)
    if not match:
        return False
    message = match.group(1)
    if message.startswith(("[STARTUP]", "[SHUTDOWN]")):
        return True
    if message.split(" ", 1)[0] in WIFI_EVENTS:
        return True
    return bool(_WPA_EVENT.match(message))


def filter_wifi_lines(text, lines):
    """Keeps the Wi-Fi event lines, then the last n of them, as newline-terminated text."""
    kept = [line for line in text.splitlines() if is_wifi_event(line)][-int(lines):]
    return "\n".join(kept) + "\n" if kept else ""


def read_wifi_log(lines):
    """Returns the last n Wi-Fi event lines, or the Error: text if the journal could not be read."""
    text = read_journal(WIFI_SCAN_LINES, WIFI_UNITS)
    if text.startswith("Error:"):
        return text
    return filter_wifi_lines(text, lines)

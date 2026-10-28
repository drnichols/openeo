from conftest import FakeRunner, ok, fail, cp
from wifi_pinning import split_terse, unescape, format_event, make_runner


# =========================
# Parser and helpers
# =========================

def test_split_terse_escaped_bssid():
    assert split_terse(r"AA\:BB\:CC\:DD\:EE\:01:MyMesh:82") == ["AA:BB:CC:DD:EE:01", "MyMesh", "82"]


def test_split_terse_ssid_with_colon_and_backslash():
    assert split_terse(r"AA\:BB\:CC\:DD\:EE\:02:Caf\\e\: Net:40") == ["AA:BB:CC:DD:EE:02", "Caf\\e: Net", "40"]


def test_split_terse_empty_ssid():
    assert split_terse(r"AA\:BB\:CC\:DD\:EE\:03::55") == ["AA:BB:CC:DD:EE:03", "", "55"]


def test_unescape():
    assert unescape(r"AA\:BB\:CC\:DD\:EE\:01") == "AA:BB:CC:DD:EE:01"


def test_format_event_omits_none_and_keeps_order():
    assert format_event("PIN", {"bssid": "AA:BB", "signal": 82, "loss": None, "samples": 3}) == "PIN bssid=AA:BB signal=82 samples=3"


def test_make_runner_missing_binary_returns_127():
    assert make_runner(sudo=False)(["definitely-not-a-binary-xyz"]).returncode == 127

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


# =========================
# Selection rule and sampling (§4)
# =========================

from wifi_pinning import select_bssid, scan_samples, choose_pin

A, B = "AA:AA:AA:AA:AA:01", "BB:BB:BB:BB:BB:02"
SCAN = ("nmcli", "-t", "-f", "BSSID,SSID,SIGNAL", "dev", "wifi", "list", "ifname", "wlan0", "--rescan")


def s(*rows):
    return [(b, "Mesh", sig) for b, sig in rows]


def no_sleep(_):
    pass


def test_select_zero_or_one_bssid_no_pin():
    assert select_bssid([], "Mesh").bssid is None
    assert select_bssid([s((A, 80))] * 3, "Mesh").reason == "single"


def test_select_close_signals_pins_stronger():
    r = select_bssid([s((A, 70), (B, 68))] * 3, "Mesh")
    assert (r.bssid, r.signal, r.runner_up, r.reason) == (A, 70, 68, "pin")


def test_select_min_signal_boundary():
    assert select_bssid([s((A, 29), (B, 10))] * 3, "Mesh").reason == "weak"
    assert select_bssid([s((A, 30), (B, 10))] * 3, "Mesh").bssid == A


def test_select_median_ignores_outlier():
    samples = [s((A, 50), (B, 60)), s((A, 50), (B, 60)), s((A, 95), (B, 60))]
    assert select_bssid(samples, "Mesh").bssid == B


def test_select_drops_bssid_seen_once():
    samples = [s((A, 60), (B, 90)), s((A, 60)), s((A, 60))]
    assert select_bssid(samples, "Mesh").reason == "single"


def test_select_collapses_duplicates_keeping_max():
    samples = [s((A, 40), (A, 70), (B, 50))] * 3
    assert select_bssid(samples, "Mesh").signal == 70


def test_select_exact_ssid_match():
    samples = [[(A, "Mesh ", 90), (B, "mesh", 80), ("CC:CC:CC:CC:CC:03", "Mesh", 50)]] * 3
    assert select_bssid(samples, "Mesh").reason == "single"


def test_select_tie_is_deterministic():
    assert select_bssid([s((B, 70), (A, 70))] * 3, "Mesh").bssid == A


def test_scan_skips_failed_samples_and_falls_back_to_cache():
    line = "AA\\:AA\\:AA\\:AA\\:AA\\:01:Mesh:70\nBB\\:BB\\:BB\\:BB\\:BB\\:02:Mesh:40\nbad line\n"
    run = FakeRunner([(SCAN + ("yes",), fail()), (SCAN + ("no",), ok(line))])
    samples = scan_samples(run, sleep=no_sleep)
    assert len(samples) == 1 and len(samples[0]) == 2
    assert choose_pin(run, "Mesh", sleep=no_sleep).bssid == A


def test_scan_all_failed_including_cache_is_noscan():
    run = FakeRunner([(("nmcli",), fail())])
    assert choose_pin(run, "Mesh", sleep=no_sleep).reason == "noscan"

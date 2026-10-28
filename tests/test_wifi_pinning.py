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


# =========================
# Health check, station dump, nmcli wrappers (§5.1, §5.2, §6.3)
# =========================

from wifi_pinning import health_check, station_info, get_pin, link_up

GW = ("nmcli", "-g", "IP4.GATEWAY", "device", "show", "wlan0")
GW_OK = (GW, ok("192.168.1.1\n"))


def ping_out(loss):
    return ok(f"10 packets transmitted, {10 - loss // 10} received, {loss}% packet loss, time 1803ms\n")


def test_health_pass_fail_boundaries():
    for loss, passed in [(0, True), (20, True), (30, False)]:
        r = health_check(FakeRunner([GW_OK, (("ping",), ping_out(loss))]))
        assert (r.passed, r.loss, r.skipped) == (passed, loss, False)


def test_health_100_percent_loss_fails_even_with_rc_1():
    out = "10 packets transmitted, 0 received, 100% packet loss, time 9ms\n"
    r = health_check(FakeRunner([GW_OK, (("ping",), cp(1, out))]))
    assert (r.passed, r.loss) == (False, 100)


def test_health_pings_gateway_on_wlan0():
    run = FakeRunner([GW_OK, (("ping",), ping_out(0))])
    health_check(run)
    assert run.calls[-1] == ["ping", "-c", "10", "-i", "0.2", "-W", "1", "-I", "wlan0", "192.168.1.1"]


def test_health_skipped_cases_count_as_pass():
    for runner in [FakeRunner([(GW, ok("\n"))]),
                   FakeRunner([GW_OK, (("ping",), fail(127))]),
                   FakeRunner([GW_OK, (("ping",), ok("garbage"))])]:
        r = health_check(runner)
        assert (r.passed, r.skipped, r.loss) == (True, True, None)


def test_station_info_parses_real_output():
    dump = ("Station aa:bb:cc:dd:ee:01 (on wlan0)\n\tinactive time:\t120 ms\n\ttx retries:\t3\n\ttx failed:\t0\n"
            "\tsignal:  \t-52 [-52] dBm\n\ttx bitrate:\t65.0 MBit/s MCS 7\n")
    assert station_info(FakeRunner([(("iw",), ok(dump))])) == {"rssi": -52, "tx_bitrate": 65.0, "tx_retries": 3, "tx_failed": 0}


def test_station_info_missing_fields_and_failure():
    assert station_info(FakeRunner([(("iw",), ok("Station x (on wlan0)\n\tsignal:\t-60 dBm\n"))])) == {"rssi": -60}
    assert station_info(FakeRunner([(("iw",), fail(127))])) == {}


def test_get_pin_unescapes_colons():
    run = FakeRunner([(("nmcli", "-g", "802-11-wireless.bssid"), ok("AA\\:BB\\:CC\\:DD\\:EE\\:01\n"))])
    assert get_pin(run) == "AA:BB:CC:DD:EE:01"


def test_get_pin_failure_is_empty():
    assert get_pin(FakeRunner([(("nmcli",), fail(10))])) == ""


def test_link_up():
    q = ("nmcli", "-g", "GENERAL.STATE,GENERAL.CONNECTION")
    assert link_up(FakeRunner([(q, ok("100 (connected)\ncustom-wifi\n"))]))
    assert not link_up(FakeRunner([(q, ok("60 (need-auth)\ncustom-wifi\n"))]))
    assert not link_up(FakeRunner([(q, ok("100 (connected)\nother\n"))]))

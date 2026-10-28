from openeo_wifi_watchdog import decide, after_health, default_state

P = "AA:BB:CC:DD:EE:01"


def step(state, now, up, pinned=P, profile=True, lock_age=None):
    return decide(state, now, link_up=up, pinned=pinned, profile_exists=profile, lock_age=lock_age)


def kinds(actions):
    return [a.kind for a in actions]


def events(actions):
    return [a.fields["event"] for a in actions if a.kind == "LOG"]


# =========================
# decide() / after_health() (§6.4)
# =========================

def test_steady_up_no_action_before_settle():
    s = default_state()
    for t in (0, 60, 119):
        a, s = step(s, t, True)
        assert a == []
    assert s["up_since"] == 0 and s["down_since"] is None


def test_short_blip_no_action():
    s = default_state()
    _, s = step(s, 0, True)
    a, s = step(s, 60, False)
    a, s = step(s, 100, False)
    assert a == []


def test_pinned_outage_fails_over_at_60s():
    s = default_state()
    _, s = step(s, 0, False)
    a, s = step(s, 59, False)
    assert a == []
    a, s = step(s, 60, False)
    assert kinds(a) == ["CLEAR_PIN", "LOG", "CON_UP"] and a[2].fields["reason"] == "failover"
    assert a[1].fields == {"event": "UNPIN", "bssid": P, "reason": "down", "down_for": 60, "roam_count": 1}
    assert s["unpinned_by_watchdog"] and s["roam_count"] == 1 and s["up_since"] is None
    assert s["last_reconnect"] == 60


def test_long_outage_blocked_recovery_then_rate_limited():
    s = default_state()
    _, s = step(s, 0, False, pinned="")
    a, s = step(s, 300, False, pinned="")
    assert kinds(a) == ["CON_UP"] and a[0].fields["reason"] == "blocked"
    a, s = step(s, 360, False, pinned="")
    assert a == []
    a, s = step(s, 600, False, pinned="")
    assert a[0].fields["reason"] == "blocked"


def test_repin_after_10_min_stable_then_retry_after_60():
    s = default_state() | {"unpinned_by_watchdog": True}
    _, s = step(s, 0, True, pinned="")
    a, s = step(s, 599, True, pinned="")
    assert a == []
    a, s = step(s, 600, True, pinned="")
    assert kinds(a) == ["REPIN_SCAN"] and s["last_repin_attempt"] == 600
    a, s = step(s, 900, True, pinned="")     # no qualifying node last time, wait an hour
    assert a == []
    a, s = step(s, 600 + 3600, True, pinned="")
    assert kinds(a) == ["REPIN_SCAN"]


def test_health_due_rules():
    s = default_state()
    _, s = step(s, 0, True)
    a, s = step(s, 119, True)                # inside HEALTH_SETTLE
    assert a == []
    a, s = step(s, 120, True)
    assert kinds(a) == ["CHECK_HEALTH"] and a[0].fields["source"] == "periodic"
    _, s = after_health(s, 120, "periodic", True)
    a, s = step(s, 700, True)
    assert a == []
    a, s = step(s, 720, True)
    assert kinds(a) == ["CHECK_HEALTH"]
    a, _ = step(s, 2000, True, pinned="")    # unpinned: never checked
    assert a == []


def test_one_failure_then_pass_no_unpin():
    s = default_state()
    _, s = step(s, 0, True)
    _, s = step(s, 120, True)
    a, s = after_health(s, 120, "periodic", False)
    assert events(a) == ["HEALTH_FAIL"] and a[0].fields["failures"] == 1
    a, s = step(s, 180, True)                # confirming check on the next run
    assert kinds(a) == ["CHECK_HEALTH"]
    a, s = after_health(s, 180, "periodic", True)
    assert s["health_failures"] == 0 and "CLEAR_PIN" not in kinds(a)


def test_two_failures_unpin_unhealthy():
    s = default_state()
    _, s = step(s, 0, True)
    _, s = after_health(s, 120, "periodic", False)
    a, s = after_health(s, 180, "periodic", False)
    assert kinds(a) == ["LOG", "CLEAR_PIN", "LOG", "CON_UP"]
    assert events(a) == ["HEALTH_FAIL", "UNPIN"] and a[2].fields["reason"] == "unhealthy"
    assert a[3].fields["reason"] == "unhealthy"
    assert s["unpinned_by_watchdog"] and s["up_since"] is None and s["health_failures"] == 0
    assert s["roam_count"] == 1 and s["repin_rejections"] == 0


def test_skipped_check_counts_as_pass():
    # The script passes passed=True for a skipped check; health_failures resets
    s = default_state() | {"health_failures": 1}
    _, s = after_health(s, 100, "periodic", True)
    assert s["health_failures"] == 0


def test_repin_pass_sets_health_clock_and_resets():
    s = default_state() | {"unpinned_by_watchdog": True, "repin_rejections": 2, "up_since": 0}
    a, s = after_health(s, 5000, "repin", True)
    assert events(a) == ["REPIN"] and s["last_health_check"] == 5000
    assert s["repin_rejections"] == 0 and not s["unpinned_by_watchdog"]
    assert s["up_since"] is None and s["last_reconnect"] == 5000


def test_repin_rejected_then_gives_up_on_third():
    s = default_state() | {"unpinned_by_watchdog": True}
    for n in (1, 2):
        a, s = after_health(s, n * 3600, "repin", False)
        assert kinds(a) == ["CLEAR_PIN", "LOG", "CON_UP"] and a[2].fields["reason"] == "rejected"
        assert events(a) == ["PIN_REJECTED"] and s["unpinned_by_watchdog"] and s["repin_rejections"] == n
    a, s = after_health(s, 3 * 3600, "repin", False)
    assert events(a) == ["PIN_REJECTED", "REPIN_GIVEUP"] and not s["unpinned_by_watchdog"]
    _, s = step(s, 3 * 3600 + 1, True, pinned="")
    a, _ = step(s, 9 * 3600, True, pinned="")
    assert a == []


def test_repin_no_node_does_not_count_as_rejection():
    s = default_state() | {"unpinned_by_watchdog": True}
    _, s = step(s, 0, True, pinned="")
    _, s = step(s, 600, True, pinned="")     # REPIN_SCAN; script found no node, so no after_health
    assert s["repin_rejections"] == 0 and s["unpinned_by_watchdog"]


def test_new_unpin_resets_rejections():
    s = default_state() | {"repin_rejections": 2}
    _, s = step(s, 0, False)
    _, s = step(s, 60, False)
    assert s["repin_rejections"] == 0


def test_guards_lock_and_profile():
    s = default_state()
    _, s = step(s, 0, False)
    assert step(s, 100, False, lock_age=10) == ([], s)
    assert kinds(step(s, 100, False, lock_age=301)[0])[:1] == ["CLEAR_PIN"]
    assert step(s, 100, False, profile=False) == ([], s)


def test_decide_does_not_mutate_input():
    s = default_state()
    snapshot = dict(s)
    step(s, 0, False)
    assert s == snapshot


# =========================
# State file and run_once (§6.1, §6.7)
# =========================

import json
import time

import openeo_wifi_watchdog as wd
from conftest import FakeRunner, ok

UP = (("nmcli", "-g", "GENERAL.STATE,GENERAL.CONNECTION"), ok("100 (connected)\ncustom-wifi\n"))
DOWN = (("nmcli", "-g", "GENERAL.STATE,GENERAL.CONNECTION"), ok("30 (disconnected)\n\n"))
PIN = (("nmcli", "-g", "802-11-wireless.bssid"), ok("AA\\:BB\\:CC\\:DD\\:EE\\:01\n"))
NOPIN = (("nmcli", "-g", "802-11-wireless.bssid"), ok("\n"))
CLEAR = ("nmcli", "connection", "modify", "custom-wifi", "802-11-wireless.bssid", "")


def is_con_up(call):
    return call[:3] == ["nmcli", "connection", "up"]


def test_load_state_corrupt_and_partial(tmp_path):
    p = tmp_path / "state.json"
    assert wd.load_state(p) == wd.default_state()
    p.write_text("{not json")
    assert wd.load_state(p) == wd.default_state()
    p.write_text("[1, 2]")
    assert wd.load_state(p) == wd.default_state()
    p.write_text(json.dumps({"roam_count": 4}))
    assert wd.load_state(p) == wd.default_state() | {"roam_count": 4}


def test_save_state_round_trip_creates_dir(tmp_path):
    p = tmp_path / "sub" / "state.json"
    wd.save_state(p, wd.default_state() | {"roam_count": 2})
    assert wd.load_state(p)["roam_count"] == 2


def test_run_once_quiet_when_nothing_to_do(tmp_path):
    assert wd.run_once(FakeRunner([UP, NOPIN]), 0, 1e9, tmp_path / "s.json", tmp_path / "lock") == []


def test_run_once_failover_logs_and_persists(tmp_path):
    st, lock = tmp_path / "s.json", tmp_path / "lock"
    run = FakeRunner([DOWN, PIN])
    wd.run_once(run, 0, 1e9, st, lock)
    lines = wd.run_once(run, 60, 1e9 + 60, st, lock)
    assert lines == ["UNPIN bssid=AA:BB:CC:DD:EE:01 reason=down down_for=60 roam_count=1",
                     "RECONNECT reason=failover rc=0"]
    assert list(CLEAR) in run.calls and any(is_con_up(c) for c in run.calls)
    assert wd.load_state(st)["unpinned_by_watchdog"] is True


def test_run_once_stops_when_setup_lock_appears(tmp_path):
    st, lock = tmp_path / "s.json", tmp_path / "lock"

    def touch_lock_on_clear(args):
        lock.touch()
        return ok("")

    run = FakeRunner([DOWN, PIN, (CLEAR, touch_lock_on_clear)])
    wd.run_once(run, 0, time.time(), st, lock)
    wd.run_once(run, 60, time.time(), st, lock)   # real wall time, so the freshly touched lock is fresh
    assert list(CLEAR) in run.calls
    assert not any(is_con_up(c) for c in run.calls)


def test_run_once_ignores_stale_lock(tmp_path):
    st, lock = tmp_path / "s.json", tmp_path / "lock"
    lock.touch()
    run = FakeRunner([DOWN, PIN])
    wd.run_once(run, 0, time.time() + 400, st, lock)
    wd.run_once(run, 60, time.time() + 460, st, lock)
    assert any(is_con_up(c) for c in run.calls)


def test_run_once_periodic_health_fail_logs_bssid_and_loss(tmp_path, monkeypatch):
    st, lock = tmp_path / "s.json", tmp_path / "lock"
    wd.save_state(st, wd.default_state() | {"up_since": 0})
    monkeypatch.setattr(wd.wp, "health_check", lambda run: wd.wp.HealthResult(False, 40, False))
    lines = wd.run_once(FakeRunner([UP, PIN]), 200, 1e9, st, lock)
    assert lines == ["HEALTH_FAIL bssid=AA:BB:CC:DD:EE:01 loss=40% failures=1"]
    assert wd.load_state(st)["health_failures"] == 1


def test_run_once_repin_passes_and_logs_repin(tmp_path, monkeypatch):
    st, lock = tmp_path / "s.json", tmp_path / "lock"
    wd.save_state(st, wd.default_state() | {"unpinned_by_watchdog": True, "up_since": 0})
    sel = wd.wp.Selection("AA:BB:CC:DD:EE:02", 74, 60, 3, "pin")
    monkeypatch.setattr(wd.wp, "choose_pin", lambda run, ssid, sleep=None: sel)
    monkeypatch.setattr(wd.wp, "health_check", lambda run: wd.wp.HealthResult(True, 0, False))
    monkeypatch.setattr(wd.wp, "station_info", lambda run: {"rssi": -58})
    run = FakeRunner([UP, NOPIN])
    lines = wd.run_once(run, 600, 1e9, st, lock)
    assert lines == ["REPIN bssid=AA:BB:CC:DD:EE:02 signal=74 loss=0% rssi=-58"]
    assert ["nmcli", "connection", "modify", "custom-wifi", "802-11-wireless.bssid", "AA:BB:CC:DD:EE:02"] in run.calls
    assert any(is_con_up(c) for c in run.calls)    # always re-activates to apply the pin
    assert wd.load_state(st)["unpinned_by_watchdog"] is False


def test_run_once_repin_rejected_runs_clear_and_reconnect(tmp_path, monkeypatch):
    st, lock = tmp_path / "s.json", tmp_path / "lock"
    wd.save_state(st, wd.default_state() | {"unpinned_by_watchdog": True, "up_since": 0})
    sel = wd.wp.Selection("AA:BB:CC:DD:EE:02", 74, 60, 3, "pin")
    monkeypatch.setattr(wd.wp, "choose_pin", lambda run, ssid, sleep=None: sel)
    monkeypatch.setattr(wd.wp, "health_check", lambda run: wd.wp.HealthResult(False, 100, False))
    run = FakeRunner([UP, NOPIN])
    lines = wd.run_once(run, 600, 1e9, st, lock)
    assert lines == ["PIN_REJECTED bssid=AA:BB:CC:DD:EE:02 signal=74 loss=100% source=repin",
                     "RECONNECT reason=rejected rc=0"]
    assert list(CLEAR) in run.calls
    assert wd.load_state(st)["repin_rejections"] == 1


def test_run_once_repin_no_node_stays_unpinned(tmp_path, monkeypatch):
    st, lock = tmp_path / "s.json", tmp_path / "lock"
    wd.save_state(st, wd.default_state() | {"unpinned_by_watchdog": True, "up_since": 0})
    monkeypatch.setattr(wd.wp, "choose_pin", lambda run, ssid, sleep=None: wd.wp.Selection(reason="single"))
    run = FakeRunner([UP, NOPIN])
    assert wd.run_once(run, 600, 1e9, st, lock) == []
    assert not any(is_con_up(c) for c in run.calls)
    assert wd.load_state(st)["last_repin_attempt"] == 600


def test_run_once_exception_is_logged_not_raised(tmp_path):
    def broken(args):
        raise RuntimeError("nmcli exploded")
    lines = wd.run_once(broken, 0, 1e9, tmp_path / "s.json", tmp_path / "lock")
    assert len(lines) == 1 and lines[0].startswith("ERROR")


def test_run_once_uses_injected_clock(tmp_path, monkeypatch):
    def no_wall_clock():
        raise AssertionError("wall clock used")
    monkeypatch.setattr(wd.time, "time", no_wall_clock)
    monkeypatch.setattr(wd.time, "monotonic", no_wall_clock)
    wd.run_once(FakeRunner([UP, PIN]), 0, 1e9, tmp_path / "s.json", tmp_path / "lock")


def test_run_once_repin_failed_activation_is_a_rejection(tmp_path, monkeypatch):
    # A node that refuses the association must not stay pinned, even though the
    # health check would be skipped (no gateway) and count as a pass
    st, lock = tmp_path / "s.json", tmp_path / "lock"
    wd.save_state(st, wd.default_state() | {"unpinned_by_watchdog": True, "up_since": 0})
    sel = wd.wp.Selection("AA:BB:CC:DD:EE:02", 74, 60, 3, "pin")
    monkeypatch.setattr(wd.wp, "choose_pin", lambda run, ssid, sleep=None: sel)
    monkeypatch.setattr(wd.wp, "health_check", lambda run: wd.wp.HealthResult(True, None, True))
    from conftest import fail
    run = FakeRunner([UP, NOPIN, (("nmcli", "connection", "up"), [fail(4), ok("")])])
    lines = wd.run_once(run, 600, 1e9, st, lock)
    assert lines[0] == "PIN_REJECTED bssid=AA:BB:CC:DD:EE:02 signal=74 source=repin"
    assert list(CLEAR) in run.calls

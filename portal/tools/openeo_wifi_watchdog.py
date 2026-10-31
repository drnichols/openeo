#!/usr/bin/env python3
"""openeo Wi-Fi watchdog. Run every 60 s by openeo_wifi_watchdog.timer, as root.

Unpins custom-wifi when its pinned node goes away or stops carrying traffic, reconnects
when NM has given up, and re-pins once the link has been stable again. The rules live in
two pure functions, decide() and after_health(), so they can be unit-tested; the code
around them gathers inputs and carries out the actions. See the design spec §6.
"""

import json
import os
import sys
import tempfile
import time
from collections import namedtuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
import wifi_pinning as wp

# kind: CLEAR_PIN | CON_UP | CHECK_HEALTH | REPIN_SCAN | LOG
Action = namedtuple("Action", "kind fields")


def default_state():
    return {
        "down_since": None,
        "up_since": None,
        "last_reconnect": None,
        "unpinned_by_watchdog": False,
        "last_repin_attempt": None,
        "roam_count": 0,
        "last_health_check": None,
        "health_failures": 0,
        "repin_rejections": 0,
        "profile_uuid": None,
    }


def _log(event, **fields):
    return Action("LOG", {"event": event, **fields})


def _con_up(reason):
    return Action("CON_UP", {"reason": reason})


def _since(now, then):
    """Seconds since 'then', or None if it never happened."""
    return None if then is None else now - then


def _unpin(state, now, bssid, reason, **extra):
    """Clear the pin and reconnect by SSID (rules 1 and 3)."""
    state["unpinned_by_watchdog"] = True
    state["roam_count"] += 1
    state["last_reconnect"] = now
    state["up_since"] = None
    state["health_failures"] = 0
    state["repin_rejections"] = 0
    return [Action("CLEAR_PIN", {}),
            _log("UNPIN", bssid=bssid, reason=reason, **extra, roam_count=state["roam_count"]),
            _con_up("failover" if reason == "down" else reason)]


def decide(state, now, *, link_up, pinned, profile_exists, lock_age):
    """Decide this run's actions from the link state. Returns (actions, new_state)."""
    # Guards: unconfigured charger, or the portal is setting up Wi-Fi right now
    if not profile_exists or (lock_age is not None and lock_age < wp.LOCK_STALE):
        return [], state

    state = dict(state)
    if link_up:
        state["down_since"] = None
        if state["up_since"] is None:
            state["up_since"] = now
    else:
        state["up_since"] = None
        if state["down_since"] is None:
            state["down_since"] = now

    down_for = _since(now, state["down_since"])
    up_for = _since(now, state["up_since"])

    # Rule 1: failover. A pinned node that has gone away must not strand the charger
    if not link_up and pinned and down_for >= wp.FAILOVER_AFTER:
        return _unpin(state, now, pinned, "down", down_for=int(down_for)), state

    # Rule 2: blocked-recovery. An explicit activation clears NM's no-secrets block
    if not link_up and down_for >= wp.BLOCKED_RECOVERY_AFTER:
        since_reconnect = _since(now, state["last_reconnect"])
        if since_reconnect is None or since_reconnect >= wp.RECONNECT_MIN_GAP:
            state["last_reconnect"] = now
            return [_con_up("blocked")], state

    # Rule 3: periodic health check of a pinned link
    if link_up and pinned and up_for >= wp.HEALTH_SETTLE:
        since_check = _since(now, state["last_health_check"])
        if state["health_failures"] > 0 or since_check is None or since_check >= wp.HEALTH_INTERVAL:
            return [Action("CHECK_HEALTH", {"source": "periodic"})], state

    # Rule 4: re-pin once the link has been stable after a watchdog unpin
    if link_up and not pinned and state["unpinned_by_watchdog"] and up_for >= wp.REPIN_STABLE:
        since_attempt = _since(now, state["last_repin_attempt"])
        if since_attempt is None or since_attempt >= wp.REPIN_RETRY:
            state["last_repin_attempt"] = now
            return [Action("REPIN_SCAN", {})], state

    return [], state


def after_health(state, now, source, passed):
    """Act on a health check result. source is 'periodic' (rule 3) or 'repin' (rule 4)."""
    state = dict(state)

    if source == "periodic":
        state["last_health_check"] = now
        if passed:
            state["health_failures"] = 0
            return [], state
        state["health_failures"] += 1
        actions = [_log("HEALTH_FAIL", failures=state["health_failures"])]
        if state["health_failures"] >= wp.HEALTH_FAILS_TO_UNPIN:
            actions += _unpin(state, now, None, "unhealthy")
        return actions, state

    # Re-pin: the script has already set the pin and re-activated
    state["last_reconnect"] = now
    state["up_since"] = None
    if passed:
        state["unpinned_by_watchdog"] = False
        state["last_health_check"] = now
        state["health_failures"] = 0
        state["repin_rejections"] = 0
        return [_log("REPIN")], state

    state["repin_rejections"] += 1
    actions = [Action("CLEAR_PIN", {}), _log("PIN_REJECTED", source="repin"), _con_up("rejected")]
    if state["repin_rejections"] < wp.REPIN_MAX_REJECTIONS:
        state["unpinned_by_watchdog"] = True
    else:
        # A router that ignores ping would otherwise cost two blips an hour forever
        state["unpinned_by_watchdog"] = False
        actions.append(_log("REPIN_GIVEUP", rejections=state["repin_rejections"]))
    return actions, state


# =========================
# State file
# =========================

def load_state(path):
    """Read the state file. Missing, corrupt or partial files fall back to the defaults."""
    state = default_state()
    try:
        with open(path) as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            state.update({k: v for k, v in saved.items() if k in state})
    except (OSError, ValueError):
        pass
    return state


def save_state(path, state):
    directory = os.path.dirname(os.fspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def lock_age(path, now_wall):
    """Age of the portal's setup lock in seconds, or None if there is none.
    File times are wall-clock, so this is the one place wall time is used."""
    try:
        return now_wall - os.stat(path).st_mtime
    except OSError:
        return None


# =========================
# Running the actions
# =========================

# Fields the executor knows from this run, per event, placed before the event's own fields
CONTEXT_FIELDS = {
    "HEALTH_FAIL": ("bssid", "loss"),
    "UNPIN": ("bssid",),
    "PIN_REJECTED": ("bssid", "signal", "loss"),
    "REPIN": ("bssid", "signal", "loss"),
}
MUTATING = ("CLEAR_PIN", "CON_UP")


class SetupStarted(Exception):
    """The portal took the setup lock while this run was acting."""


def _loss(result):
    return None if result.skipped else f"{result.loss}%"


class Executor:
    def __init__(self, run, now, now_wall, lock_path, sleep, pinned):
        self.run, self.now, self.now_wall = run, now, now_wall
        self.lock_path, self.sleep = lock_path, sleep
        self.ctx = {"bssid": pinned or None}
        self.lines = []

    def emit(self, line):
        print(line, flush=True)
        self.lines.append(line)

    def check_lock(self):
        age = lock_age(self.lock_path, self.now_wall)
        if age is not None and age < wp.LOCK_STALE:
            raise SetupStarted()

    def log(self, fields):
        event = fields["event"]
        merged = {k: self.ctx.get(k) for k in CONTEXT_FIELDS.get(event, ())}
        for key, value in fields.items():
            if key != "event" and (value is not None or key not in merged):
                merged[key] = value
        if event == "REPIN":
            merged.update(self.ctx.get("station", {}))
        self.emit(wp.format_event(event, merged))

    def execute(self, actions, state):
        for action in actions:
            if action.kind in MUTATING:
                self.check_lock()
            if action.kind == "CLEAR_PIN":
                wp.clear_pin(self.run)
            elif action.kind == "CON_UP":
                rc = wp.con_up(self.run).returncode
                self.emit(wp.format_event("RECONNECT", {"reason": action.fields["reason"], "rc": rc}))
            elif action.kind == "LOG":
                self.log(action.fields)
            elif action.kind == "CHECK_HEALTH":
                result = wp.health_check(self.run)
                self.ctx["loss"] = _loss(result)
                more, state = after_health(state, self.now, "periodic", result.passed)
                state = self.execute(more, state)
            elif action.kind == "REPIN_SCAN":
                state = self.repin(state)
        return state

    def repin(self, state):
        selection = wp.choose_pin(self.run, wp.profile_ssid(self.run), self.sleep)
        if not selection.bssid:
            return state    # stay unpinned, try again in an hour
        self.check_lock()
        wp.set_pin(self.run, selection.bssid)
        # Always re-activate: a profile change doesn't reach the running connection
        if wp.con_up(self.run).returncode == 0:
            result = wp.health_check(self.run)
        else:
            # The node refused us; never keep a pin on it (as at setup, §5 step 7)
            result = wp.HealthResult(False, None, True)
        self.ctx.update(bssid=selection.bssid, signal=selection.signal, loss=_loss(result))
        if result.passed:
            self.ctx["station"] = wp.station_info(self.run)
        more, state = after_health(state, self.now, "repin", result.passed)
        return self.execute(more, state)


def run_once(run, now, now_wall, state_path=wp.STATE_FILE, lock_path=wp.SETUP_LOCK, sleep=time.sleep):
    """One watchdog run. 'now' is monotonic time; returns the log lines it printed."""
    executor = None
    try:
        state = load_state(state_path)
        # A new portal setup re-creates the profile; nothing learned about the old one applies
        uuid = wp.profile_uuid(run)
        if uuid and uuid != state["profile_uuid"]:
            state = default_state() | {"profile_uuid": uuid}
        pinned = wp.get_pin(run)
        executor = Executor(run, now, now_wall, lock_path, sleep, pinned)
        link = wp.link_state(run)
        actions, state = decide(state, now,
                                link_up=link.up,
                                pinned=pinned,
                                profile_exists=wp.profile_exists(run),
                                lock_age=lock_age(lock_path, now_wall))
        save_state(state_path, state)
        try:
            state = executor.execute(actions, state)
        except SetupStarted:
            pass
        save_state(state_path, state)
        return executor.lines
    except Exception as e:
        line = f"ERROR {type(e).__name__}: {e}"
        print(line, flush=True)
        return (executor.lines if executor else []) + [line]


def main():
    run_once(wp.make_runner(sudo=False), time.monotonic(), time.time())


if __name__ == "__main__":
    main()

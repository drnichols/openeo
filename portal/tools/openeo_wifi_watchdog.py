#!/usr/bin/env python3
"""openeo Wi-Fi watchdog. Run every 60 s by openeo_wifi_watchdog.timer, as root.

Unpins custom-wifi when its pinned node goes away or stops carrying traffic, reconnects
when NM has given up, and re-pins once the link has been stable again. The rules live in
two pure functions, decide() and after_health(), so they can be unit-tested; the code
around them gathers inputs and carries out the actions. See the design spec §6.
"""

import os
import sys
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

#!/usr/bin/env python3
"""Tests for the /getlogs log viewer helpers and openeoConfigClass.logget(limit=)."""

import os, sqlite3, subprocess, sys, unittest
from threading import Lock
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import logviewer
from openeoConfig import openeoConfigClass


class ParseLogQueryTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(logviewer.parse_log_query("/getlogs"), ("app", 500))

    def test_journal_source(self):
        self.assertEqual(logviewer.parse_log_query("/getlogs?source=journal&lines=200"), ("journal", 200))

    def test_unknown_source_falls_back_to_app(self):
        self.assertEqual(logviewer.parse_log_query("/getlogs?source=etc_passwd")[0], "app")

    def test_lines_clamped(self):
        self.assertEqual(logviewer.parse_log_query("/getlogs?lines=0")[1], 50)
        self.assertEqual(logviewer.parse_log_query("/getlogs?lines=-10")[1], 50)
        self.assertEqual(logviewer.parse_log_query("/getlogs?lines=99999")[1], 5000)

    def test_non_numeric_lines_uses_default(self):
        self.assertEqual(logviewer.parse_log_query("/getlogs?lines=abc")[1], 500)


class ReadJournalTests(unittest.TestCase):
    def test_runs_journalctl_without_shell(self):
        completed = subprocess.CompletedProcess([], 0, stdout="line1\nline2\n", stderr="")
        with mock.patch("lib.logviewer.subprocess.run", return_value=completed) as run:
            self.assertEqual(logviewer.read_journal(100), "line1\nline2\n")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["journalctl", "-u", "openeo", "-n", "100", "--no-pager", "-o", "short-iso"])
        self.assertNotIn("shell", kwargs)

    def test_missing_journalctl(self):
        with mock.patch("lib.logviewer.subprocess.run", side_effect=FileNotFoundError):
            self.assertTrue(logviewer.read_journal(100).startswith("Error:"))

    def test_timeout(self):
        with mock.patch("lib.logviewer.subprocess.run", side_effect=subprocess.TimeoutExpired("journalctl", 10)):
            self.assertIn("did not respond", logviewer.read_journal(100))

    def test_non_zero_exit(self):
        completed = subprocess.CompletedProcess([], 1, stdout="", stderr="No journal files were found.")
        with mock.patch("lib.logviewer.subprocess.run", return_value=completed):
            self.assertEqual(logviewer.read_journal(100), "Error: No journal files were found.")


PFX = "2026-10-31T15:30:57+0000 openeo-test"
WD = PFX + " openeo-wifi-watchdog[812]: UNPIN bssid=02:EC:DA:FD:20:A6 reason=flapping drops=3 window=600 roam_count=1"
WPA = PFX + " wpa_supplicant[402]: wlan0: CTRL-EVENT-DISCONNECTED bssid=02:ec:da:fd:20:a6 reason=0"
REQ = PFX + ' portal.py[512]: 192.168.4.2 - "GET /api/set_wifi?ssid=x&password=hunter22 HTTP/1.1" 200 -'
SNEAKY = PFX + ' portal.py[512]: 192.168.4.2 - "GET /api/set_wifi?ssid=PIN&password=hunter22 HTTP/1.1" 200 -'


class WifiLogTests(unittest.TestCase):
    def test_wifi_source_parsed(self):
        self.assertEqual(logviewer.parse_log_query("/getlogs?source=wifi")[0], "wifi")

    def test_allowlist(self):
        for l in (WD, WPA, PFX + " portal.py[5]: [STARTUP] Network startup",
                  PFX + " openeo-wifi-watchdog[1]: ERROR OSError: x",
                  PFX + " openeo-wifi-watchdog[1]: RECONNECT reason=weak rc=0"):
            self.assertTrue(logviewer.is_wifi_event(l), l)
        for l in (REQ, SNEAKY, "-- Boot 1234 --", PFX + " portal.py[5]: response=200 result={}",
                  PFX + " wpa_supplicant[1]: wlan0: WPA: Key negotiation completed",
                  PFX + " openeo-wifi-watchdog[1]: PINNED x", ""):
            self.assertFalse(logviewer.is_wifi_event(l), l)

    def test_wifi_filter_drops_password_request_line(self):
        out = logviewer.filter_wifi_lines("\n".join([REQ, WD, SNEAKY, WPA]), 500)
        self.assertNotIn("password", out)
        self.assertEqual(out, f"{WD}\n{WPA}\n")

    def test_wifi_filter_rejects_lookalikes(self):
        for l in (PFX + ' python3[512]: 192.168.4.2 - "GET /api/set_wifi?ssid=My PIN&password=x HTTP/1.1" 200 -',
                  PFX + ' python3[512]: 192.168.4.2 - "GET /api/set_wifi?ssid=a&password=x HTTP/1.1" 500 ERROR',
                  PFX + " openeo-wifi-watchdog[1]:  PIN x",
                  "    UNPIN bssid=02:EC:DA:FD:20:A6 reason=flapping"):
            self.assertEqual(logviewer.filter_wifi_lines(l, 10), "", l)

    def test_wifi_filter_does_not_split_a_line_on_other_separators(self):
        # splitlines() would break these inside one journal line and judge the tail on its own
        event = PFX + " openeo-wifi-watchdog[1]: UNPIN bssid=02:EC:DA:FD:20:A6 reason=flapping"
        for sep in ("\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            line = PFX + ' python3[512]: 192.168.4.2 - "GET /api/set_wifi?ssid=a&password=x' + sep + event
            self.assertEqual(logviewer.filter_wifi_lines(f"{line}\n{WD}\n", 10), f"{WD}\n", repr(sep))

    def test_wifi_filter_accepts_identifier_without_pid(self):
        line = PFX + " openeo-wifi-watchdog: UNPIN bssid=02:EC:DA:FD:20:A6 reason=flapping"
        self.assertEqual(logviewer.filter_wifi_lines(line, 10), f"{line}\n")

    def test_filter_keeps_last_n(self):
        text = "\n".join(f"{PFX} openeo-wifi-watchdog[1]: RECONNECT reason=r{i} rc=0" for i in range(5))
        out = logviewer.filter_wifi_lines(text, 2)
        self.assertEqual(out.splitlines(), [f"{PFX} openeo-wifi-watchdog[1]: RECONNECT reason=r{i} rc=0" for i in (3, 4)])
        self.assertTrue(out.endswith("\n"))
        self.assertEqual(logviewer.filter_wifi_lines(REQ, 10), "")

    def test_read_wifi_log_units_and_error_passthrough(self):
        completed = subprocess.CompletedProcess([], 0, stdout=f"{REQ}\n{WD}\n", stderr="")
        with mock.patch("lib.logviewer.subprocess.run", return_value=completed) as run:
            self.assertEqual(logviewer.read_wifi_log(100), f"{WD}\n")
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:7], ["journalctl", "-u", "openeo_wifi_watchdog", "-u", "openeo_portal", "-u", "wpa_supplicant"])
        self.assertEqual(cmd[cmd.index("-n") + 1], "20000")
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
        with mock.patch("lib.logviewer.subprocess.run", return_value=failed):
            self.assertTrue(logviewer.read_wifi_log(100).startswith("Error:"))


class LoggetLimitTests(unittest.TestCase):
    def setUp(self):
        # Bypass __init__, which creates the database under /home/pi
        self.cfg = openeoConfigClass.__new__(openeoConfigClass)
        self.cfg.lock = Lock()
        self.cfg.conn = sqlite3.connect(":memory:")
        self.cfg.cursor = self.cfg.conn.cursor()
        self.cfg.cursor.execute("CREATE TABLE log (timestamp INTEGER NOT NULL, message TEXT NOT NULL)")
        # Two entries share a timestamp to check insertion order is kept
        rows = [(1000, "one"), (1001, "two"), (1001, "three"), (1002, "four")]
        self.cfg.cursor.executemany("INSERT INTO log (timestamp, message) VALUES (?, ?)", rows)
        self.cfg.conn.commit()

    def tearDown(self):
        self.cfg.conn.close()

    def messages(self, text):
        return [line.split(" ", 2)[2] for line in text.splitlines()]

    def test_no_limit_returns_everything(self):
        self.assertEqual(self.messages(self.cfg.logget()), ["one", "two", "three", "four"])

    def test_limit_returns_newest_oldest_first(self):
        self.assertEqual(self.messages(self.cfg.logget(limit=3)), ["two", "three", "four"])

    def test_limit_larger_than_table(self):
        self.assertEqual(self.messages(self.cfg.logget(limit=50)), ["one", "two", "three", "four"])

    def test_limit_follows_insertion_order_when_clock_steps_back(self):
        # e.g. NTP correcting the clock after boot: the newest entry has an older timestamp
        self.cfg.cursor.execute("INSERT INTO log (timestamp, message) VALUES (500, 'after clock step')")
        self.cfg.conn.commit()
        self.assertEqual(self.messages(self.cfg.logget(limit=2)), ["four", "after clock step"])


if __name__ == "__main__":
    unittest.main()

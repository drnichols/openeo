import logging
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# openeoCharger imports hardware modules and globalState (opens /home/pi/etc) at load
# time; stub them for the test.
for name in ("RPi", "RPi.GPIO", "spidev", "serial", "globalState"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["RPi"].GPIO = sys.modules["RPi.GPIO"]

from openeoCharger import openeoChargerClass  # noqa: E402


def make_charger(**fields):
    charger = openeoChargerClass.__new__(openeoChargerClass)
    defaults = dict(current_switch_setting="F", charger_state="0A", relay_state="0",
                    plug_state="1", state_of_mains="00", cp_line_state="2", eco_7_switch="0",
                    control_pilot_voltage="2A0", plug_present_voltage="100")
    defaults.update(fields)
    for k, v in defaults.items():
        setattr(charger, k, v)
    return charger


def test_logs_first_status_with_raw_packet(caplog):
    charger = make_charger()
    with caplog.at_level(logging.INFO):
        assert charger.log_status_change("!RAWPACKET") is True
    assert "charger_state=0A" in caplog.text
    assert "cp_line_state=2" in caplog.text
    assert "raw=!RAWPACKET" in caplog.text


def test_unchanged_status_is_not_logged_again(caplog):
    charger = make_charger()
    charger.log_status_change("!A")
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert charger.log_status_change("!B") is False
    assert caplog.text == ""


def test_volatile_fields_do_not_trigger_logging():
    charger = make_charger()
    charger.log_status_change("!A")
    charger.control_pilot_voltage = "2B1"
    assert charger.log_status_change("!B") is False


def test_fault_field_change_is_logged():
    charger = make_charger()
    charger.log_status_change("!A")
    charger.state_of_mains = "04"
    assert charger.log_status_change("!B") is True

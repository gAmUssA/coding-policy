"""Host thresholds use the measured M5 string and brief-supplied M1 target."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from teamlead.config import parse_config
from teamlead.errors import ConfigError
from teamlead.host import check_hosts


def pi_entry():
    return {"name": "local", "kind": "pi", "clear_prompt": "/new",
            "capabilities": ["repo-read", "command-run"],
            "host_requirements": {"min_apple_m_generation": 5, "min_memory_gib": 32},
            "tiers": {name: {"model": "ollama/flint:40b-a2b-ctx64k", "effort": "off"} for name in ("consultation", "test_plan")}}


def agent():
    return parse_config({"schema_version": 5, "agents": [pi_entry()]})[0]


def sysctl(chip="Apple M5 Max", memory="68719476736"):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, chip if argv[-1] == "machdep.cpu.brand_string" else memory, "")
    return run


class HostTests(unittest.TestCase):
    def test_measured_host_and_byte_boundary(self):
        example = json.loads((Path(__file__).parents[1] / "config.example.json").read_text())
        seats = [agent(), next(seat for seat in parse_config(example) if seat.kind == "pi")]
        for seat in seats:
            for chip, memory, eligible in (("Apple M5", "34359738368", True),
                                           ("Apple M5", "34359738367", False),
                                           ("Apple M5 Max", "68719476736", True),
                                           ("Apple M1", "68719476736", False)):
                with self.subTest(seat=seat.name, chip=chip, memory=memory), patch("subprocess.run", side_effect=sysctl(chip, memory)):
                    observation = check_hosts([seat])[seat.name]
                    self.assertIs(observation["eligible"], eligible)
                    self.assertEqual(observation["reason"], "host_eligible" if eligible else "host_ineligible")
        for chip, memory, reason in (("Intel", "68719476736", "host_ineligible"),
                                     ("Apple M5 Ma", "68719476736", "host_probe_failed"),
                                     ("", "68719476736", "host_probe_failed"),
                                     ("AMD EPYC", "68719476736", "host_ineligible"),
                                     ("Apple M5 Max", "", "host_probe_failed")):
            with self.subTest(chip=chip, memory=memory), patch("subprocess.run", side_effect=sysctl(chip, memory)):
                self.assertEqual(check_hosts([agent()])["local"]["reason"], reason)

    def test_probe_failure_is_unknown(self):
        for exc in (OSError("missing"), subprocess.TimeoutExpired("sysctl", 5)):
            with patch("subprocess.run", side_effect=exc):
                self.assertIsNone(check_hosts([agent()])["local"]["eligible"])

    def test_config_guards(self):
        for field, values in (("min_memory_gib", [True, 0, 32.5, "32 GiB"]), ("min_apple_m_generation", [1, False])):
            for value in values:
                entry = pi_entry()
                entry["host_requirements"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ConfigError):
                    parse_config({"schema_version": 5, "agents": [entry]})
        for version in (1, 2, 3, 4):
            with self.assertRaisesRegex(ConfigError, "schema_version 5"):
                parse_config({"schema_version": version, "agents": [pi_entry()]})
        self.assertEqual(agent().allowed_roles, ("investigator", "tester"))
        for change in ({"host_requirements": None}, {"tiers": None}, {"allowed_roles": ["reviewer"]},
                       {"allowed_roles": ["tester", "tester"]}, {"window_group": "paid"}, {"clear_prompt": "/clear"}):
            with self.subTest(change=change), self.assertRaises(ConfigError):
                parse_config({"schema_version": 5, "agents": [{**pi_entry(), **change}]})


if __name__ == "__main__":
    unittest.main()

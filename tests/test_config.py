import os
import tempfile
import unittest
from unittest.mock import patch

from evoagent.config import Settings, load_dotenv


class DotenvTests(unittest.TestCase):
    def test_loads_valid_assignments_and_quoted_values(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("# comment\n")
            handle.write("export EVOAGENT_LLM_PROVIDER=deepseek\n")
            handle.write('EVOAGENT_DEEPSEEK_API_KEY="test-key"\n')
            handle.write("invalid line\n")
            path = handle.name
        try:
            with patch.dict(os.environ, {}, clear=True):
                load_dotenv([path])
                self.assertEqual("deepseek", os.environ["EVOAGENT_LLM_PROVIDER"])
                self.assertEqual("test-key", os.environ["EVOAGENT_DEEPSEEK_API_KEY"])
        finally:
            os.unlink(path)

    def test_process_environment_has_priority(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("EVOAGENT_LLM_PROVIDER=deepseek\n")
            path = handle.name
        try:
            with patch.dict(os.environ, {"EVOAGENT_LLM_PROVIDER": "custom"}, clear=True):
                load_dotenv([path])
                self.assertEqual("custom", os.environ["EVOAGENT_LLM_PROVIDER"])
        finally:
            os.unlink(path)


class SettingsEnvTests(unittest.TestCase):
    def test_evolution_switches_default_to_off(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
            # Conservative code defaults: the new paths do not change behavior
            # until explicitly enabled.
            self.assertEqual("off", settings.experience_mode)
            self.assertEqual("off", settings.skill_marginal_gate)

    def test_curator_switch_accepts_plan_env_name(self):
        with patch.dict(
            os.environ, {"EVOAGENT_SKILL_CURATOR_ENABLED": "false"}, clear=True
        ):
            self.assertFalse(Settings.from_env().curator_enabled)
        with patch.dict(
            os.environ, {"EVOAGENT_CURATOR_ENABLED": "false"}, clear=True
        ):
            self.assertFalse(Settings.from_env().curator_enabled)

    def test_unknown_gate_mode_fails_at_startup(self):
        with patch.dict(
            os.environ, {"EVOAGENT_SKILL_MARGINAL_GATE": "aggressive"}, clear=True
        ):
            with self.assertRaises(ValueError):
                Settings.from_env().validate_evolution()
        with patch.dict(
            os.environ, {"EVOAGENT_EXPERIENCE_MODE": "always-on"}, clear=True
        ):
            with self.assertRaises(ValueError):
                Settings.from_env().validate_evolution()

import json
import os
import subprocess
import sys
import unittest

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcpcheck.checks import run_checks  # noqa: E402


def fixture(name):
    return [sys.executable, os.path.join(FIXTURES, name)]


class GoodServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_checks(fixture("good_server.py"), timeout=10)

    def test_no_errors(self):
        self.assertFalse(self.report.spawn_error)
        errors = [f for f in self.report.findings if f.severity == "error"]
        self.assertEqual(errors, [], f"unexpected errors: {errors}")

    def test_exit_code_zero(self):
        self.assertEqual(self.report.exit_code, 0)

    def test_tools_seen(self):
        ok = any(f.code == "C07" and "1 tool(s)" in f.message for f in self.report.findings)
        self.assertTrue(ok)


class SloppyServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_checks(fixture("sloppy_server.py"), timeout=10)

    def codes(self, code):
        return [f for f in self.report.findings if f.code == code]

    def test_exit_code_one(self):
        self.assertEqual(self.report.exit_code, 1)

    def test_flags_bad_protocol_version(self):
        self.assertTrue(any("1999-01-01" in f.message and f.severity == "warning"
                            for f in self.codes("C03")))

    def test_flags_input_schema_type(self):
        self.assertTrue(any("'a'" in f.message and "object" in f.message
                            for f in self.codes("C08")))

    def test_flag_required_not_in_properties(self):
        self.assertTrue(any("ghost" in f.message for f in self.codes("C08")))

    def test_flag_duplicate_tool_names(self):
        self.assertTrue(any(f.severity == "error" for f in self.codes("C09")))

    def test_flag_missing_version(self):
        self.assertTrue(any(f.severity == "warning" for f in self.codes("C04")))


class BrokenServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_checks(fixture("broken_server.py"), timeout=3)

    def test_handshake_failure_is_error(self):
        self.assertTrue(any(f.code == "C01" for f in self.report.findings))

    def test_pollution_detected(self):
        p = [f for f in self.report.findings if f.code == "P01"]
        self.assertTrue(p and p[0].severity == "error")
        self.assertIn("not JSON-RPC", p[0].message)

    def test_exit_code_one(self):
        self.assertEqual(self.report.exit_code, 1)


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(os.path.dirname(__file__), "..", "src")
        return subprocess.run(
            [sys.executable, "-m", "mcpcheck", *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    def test_good_server_json_output(self):
        r = self.run_cli(
            "run", "--json", "--", sys.executable, os.path.join(FIXTURES, "good_server.py")
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["exit_code"], 0)
        self.assertEqual(doc["summary"]["errors"], 0)

    def test_spawn_failure_exit_two(self):
        r = self.run_cli("run", "--", "/nonexistent/binary/xyz")
        self.assertEqual(r.returncode, 2)
        self.assertIn("could not run server", r.stdout)


if __name__ == "__main__":
    unittest.main()

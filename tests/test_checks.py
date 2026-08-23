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

    def test_annotations_hint_must_be_boolean(self):
        hits = [f for f in self.codes("C12") if "readOnlyHint" in f.message]
        self.assertTrue(hits and hits[0].severity == "error")

    def test_unknown_annotation_key_warns(self):
        hits = [f for f in self.codes("C12") if "madeUpHint" in f.message]
        self.assertTrue(hits and hits[0].severity == "warning")

    def test_tool_title_must_be_string(self):
        hits = [f for f in self.codes("C12") if "title must be a string" in f.message]
        self.assertTrue(hits and hits[0].severity == "error")

    def test_output_schema_type(self):
        hits = [f for f in self.codes("C08") if "outputSchema.type" in f.message]
        self.assertTrue(hits and hits[0].severity == "error")

    def test_nested_required_violation(self):
        hits = [f for f in self.codes("C08") if "'missing'" in f.message]
        self.assertTrue(hits and "obj.required" in hits[0].message)

    def test_invalid_nested_type(self):
        self.assertTrue(any("'strng'" in f.message for f in self.codes("C08")))

    def test_enum_type_mismatch_warns(self):
        hits = [f for f in self.codes("C08") if "enum" in f.message]
        self.assertTrue(hits and hits[0].severity == "warning")

    def test_default_type_mismatch_warns(self):
        hits = [f for f in self.codes("C08") if "default" in f.message]
        self.assertTrue(hits and hits[0].severity == "warning")


class PagedServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_checks(fixture("paged_server.py"), timeout=10)

    def test_no_errors(self):
        errors = [f for f in self.report.findings if f.severity == "error"]
        self.assertEqual(errors, [], f"unexpected errors: {errors}")

    def test_pagination_round_trip_ok(self):
        ok = [
            f
            for f in self.report.findings
            if f.code == "C11" and "pagination round-trip OK (2 page(s), 3 tool(s))" in f.message
        ]
        self.assertEqual(len(ok), 1)

    def test_all_pages_counted(self):
        self.assertTrue(any("tools/list OK (3 tool(s))" in f.message for f in self.report.findings))


class LoopingServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_checks(fixture("looping_server.py"), timeout=10)

    def test_cursor_loop_is_error(self):
        loops = [f for f in self.report.findings if f.code == "C11" and "loop" in f.message]
        self.assertTrue(loops and loops[0].severity == "error")

    def test_exit_code_one(self):
        self.assertEqual(self.report.exit_code, 1)


class MalformedServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_checks(fixture("malformed_server.py"), timeout=10)

    def test_ping_still_correlates(self):
        c06 = [f for f in self.report.findings if f.code == "C06"]
        self.assertFalse(any(f.severity == "error" for f in c06))

    def test_p02_envelope_error(self):
        p02 = [f for f in self.report.findings if f.code == "P02"]
        self.assertTrue(p02 and p02[0].severity == "error")
        self.assertIn("jsonrpc", p02[0].message)

    def test_exit_code_one(self):
        self.assertEqual(self.report.exit_code, 1)


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

    def test_fail_on_warning_default_passes(self):
        r = self.run_cli(
            "run", "--json", "--", sys.executable, os.path.join(FIXTURES, "warny_server.py")
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["fail_on"], "error")
        self.assertEqual(doc["summary"]["errors"], 0)
        self.assertGreater(doc["summary"]["warnings"], 0)

    def test_fail_on_warning_flag_fails(self):
        r = self.run_cli(
            "run",
            "--json",
            "--fail-on",
            "warning",
            "--",
            sys.executable,
            os.path.join(FIXTURES, "warny_server.py"),
        )
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["fail_on"], "warning")
        self.assertEqual(doc["exit_code"], 1)


if __name__ == "__main__":
    unittest.main()

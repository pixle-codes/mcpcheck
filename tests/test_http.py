import os
import sys
import unittest

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcpcheck.checks import run_checks  # noqa: E402
from mcpcheck.checks_http import parse_www_authenticate  # noqa: E402
from fixtures.http_server import McpHttpServer  # noqa: E402


def run(url):
    return run_checks(None, timeout=5.0, url=url)


def findings(report, code=None, severity=None):
    return [
        f
        for f in report.findings
        if (code is None or f.code == code) and (severity is None or f.severity == severity)
    ]


class HttpJsonServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = McpHttpServer(mode="json").start()
        cls.report = run(cls.server.url)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_no_errors(self):
        self.assertFalse(self.report.spawn_error)
        errs = findings(self.report, severity="error")
        self.assertEqual(errs, [], f"unexpected errors: {errs}")

    def test_exit_zero(self):
        self.assertEqual(self.report.exit_code, 0)

    def test_tools_listed_over_http(self):
        self.assertTrue(any("tools/list OK" in f.message for f in findings(self.report, "C07")))

    def test_stateless_session_info(self):
        self.assertTrue(any("stateless" in f.message for f in findings(self.report, "H02")))

    def test_content_types_ok(self):
        self.assertTrue(any(f.severity == "info" and "Content-Type" in f.message
                            for f in findings(self.report, "H03")))

    def test_get_stream_404_warns_or_ok(self):
        # default fixture GET → 404 → warning expected but not fatal
        codes = [f.code for f in findings(self.report, "H04")]
        self.assertIn("H04", codes)


class HttpSseServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = McpHttpServer(mode="sse").start()
        cls.report = run(cls.server.url)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_sse_responses_parse(self):
        errs = [f for f in findings(self.report, severity="error") if f.code != "P02"]
        self.assertEqual(errs, [], f"unexpected errors: {errs}")

    def test_handshake_works_via_sse(self):
        self.assertTrue(any(f.code == "C03" and "negotiated" in f.message
                            for f in self.report.findings))


class HttpSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = McpHttpServer(mode="session").start()
        cls.report = run(cls.server.url)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_session_echo_recorded(self):
        ok = [f for f in findings(self.report, "H02") if "echoed it" in f.message]
        self.assertTrue(ok)

    def test_no_errors_when_session_respected(self):
        errs = findings(self.report, severity="error")
        self.assertEqual(errs, [], f"unexpected errors: {errs}")


class HttpAuthTests(unittest.TestCase):
    """401 with a well-formed challenge + valid PRM document."""

    @classmethod
    def setUpClass(cls):
        cls.server = McpHttpServer(mode="with_auth_header").start()
        cls.report = run(cls.server.url)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_challenge_detected(self):
        ok = [f for f in findings(self.report, "A01") if f.severity == "info"]
        self.assertTrue(ok)

    def test_prm_validated(self):
        ok = [f for f in findings(self.report, "A02") if f.severity == "info"]
        self.assertTrue(ok, "expected A02 info findings from PRM validation")

    def test_handshake_not_flagged_error_for_protected_server(self):
        c01 = findings(self.report, "C01")
        self.assertFalse(any(f.severity == "error" for f in c01))

    def test_auth_gate_is_only_failure_source(self):
        errs = findings(self.report, severity="error")
        self.assertEqual(errs, [], f"a correct auth gate should pass clean: {errs}")


class HttpAuthNoHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = McpHttpServer(mode="no_auth_header").start()
        cls.report = run(cls.server.url)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_missing_challenge_is_error(self):
        errs = findings(self.report, "A01", severity="error")
        self.assertTrue(errs, "missing WWW-Authenticate must be an error")

    def test_exit_one(self):
        self.assertEqual(self.report.exit_code, 1)


class PrmBadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = McpHttpServer(mode="prm_bad").start()
        cls.report = run(cls.server.url)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_bad_prm_fields_flagged(self):
        errs = findings(self.report, "A02", severity="error")
        self.assertTrue(any("resource" in f.message for f in errs))
        self.assertTrue(any("scopes_supported" in f.message for f in errs))


class HtmlResponseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = McpHttpServer(mode="html").start()
        cls.report = run(cls.server.url)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_html_body_is_pollution(self):
        errs = findings(self.report, "P01", severity="error")
        self.assertTrue(errs)

    def test_content_type_violation(self):
        errs = findings(self.report, "H03", severity="error")
        self.assertTrue(errs)

    def test_handshake_fails(self):
        self.assertTrue(findings(self.report, "C01", severity="error"))


class MalformedHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = McpHttpServer(mode="malformed").start()
        cls.report = run(cls.server.url)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_envelope_violation_reported(self):
        errs = findings(self.report, "P02", severity="error")
        self.assertTrue(errs)


class GetStreamTests(unittest.TestCase):
    def test_405_allowed(self):
        srv = McpHttpServer(mode="get_405").start()
        try:
            report = run(srv.url)
            oks = [f for f in findings(report, "H04") if f.severity == "info"]
            self.assertTrue(oks)
        finally:
            srv.stop()

    def test_weird_status_warns(self):
        srv = McpHttpServer(mode="get_weird").start()
        try:
            report = run(srv.url)
            warns = findings(report, "H04", severity="warning")
            self.assertTrue(warns)
        finally:
            srv.stop()


class UnreachableTests(unittest.TestCase):
    def test_connection_refused_is_infra_error(self):
        report = run("http://127.0.0.1:9/mcp")
        self.assertTrue(report.spawn_error)
        self.assertEqual(report.exit_code_for("error"), 2)


class ChallengeParserTests(unittest.TestCase):
    def test_bearer_with_params(self):
        scheme, params = parse_www_authenticate(
            'Bearer realm="mcp", resource_metadata="https://x.example/.well-known/oauth-protected-resource", error="invalid_token"'
        )
        self.assertEqual(scheme, "Bearer")
        self.assertEqual(params["resource_metadata"], "https://x.example/.well-known/oauth-protected-resource")
        self.assertEqual(params["error"], "invalid_token")

    def test_quoted_commas(self):
        scheme, params = parse_www_authenticate('Bearer error="a,b", scope="mcp:read, mcp:write"')
        self.assertEqual(params["error"], "a,b")
        self.assertEqual(params["scope"], "mcp:read, mcp:write")

    def test_empty(self):
        self.assertEqual(parse_www_authenticate(""), ("", {}))


if __name__ == "__main__":
    unittest.main()

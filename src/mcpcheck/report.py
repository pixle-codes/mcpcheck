"""Rendering of check reports to text or JSON."""

import json
import sys

SYMBOLS = {"error": "FAIL", "warning": "WARN", "info": "ok  "}


def render_text(report):
    lines = []
    if report.spawn_error:
        lines.append(f"mcpcheck: could not run server: {report.spawn_error}")
        return "\n".join(lines)
    for f in report.findings:
        lines.append(f"  [{SYMBOLS[f.severity]}] {f.code} {f.message}")
    s = report.summary()
    verdict = "FAILED" if report.has_errors else "PASSED"
    lines.append("")
    lines.append(
        f"mcpcheck: {verdict} — {s['errors']} error(s), {s['warnings']} warning(s), {s['passed']} passed"
    )
    if report.has_errors and report.stderr_tail:
        lines.append("server stderr (tail):")
        for line in report.stderr_tail.splitlines()[-10:]:
            lines.append(f"    {line}")
    return "\n".join(lines)


def emit(report, as_json=False, stream=None):
    stream = stream or sys.stdout
    if as_json:
        doc = report.as_dict()
        doc["exit_code"] = report.exit_code
        stream.write(json.dumps(doc, indent=2) + "\n")
    else:
        stream.write(render_text(report) + "\n")
    stream.flush()
    return report.exit_code

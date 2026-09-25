import contextlib
import io
import os
import tempfile
import unittest

from yamlgotcha import cli

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    return os.path.join(FIXTURES, name)


class ScanFileTests(unittest.TestCase):
    def test_duplicate_keys_at_top_level_and_nested(self):
        findings = cli.scan_file(fixture("duplicate_keys.yaml"))
        rules_by_line = {f.line: f.rule for f in findings}
        self.assertEqual(rules_by_line, {3: "YG001", 6: "YG001"})

    def test_tab_indentation(self):
        findings = cli.scan_file(fixture("tabs.yaml"))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "YG002")
        self.assertEqual(findings[0].line, 2)

    def test_ambiguous_scalars(self):
        findings = cli.scan_file(fixture("ambiguous_scalars.yaml"))
        self.assertEqual([f.rule for f in findings], ["YG003"] * 4)
        self.assertEqual([f.line for f in findings], [2, 3, 4, 5])

    def test_flow_mapping_duplicate(self):
        findings = cli.scan_file(fixture("flow_duplicates.yaml"))
        self.assertEqual([f.rule for f in findings], ["YG004"])

    def test_octal_lookalike(self):
        findings = cli.scan_file(fixture("octal.yaml"))
        self.assertEqual([f.rule for f in findings], ["YG005"])

    def test_sexagesimal_lookalike(self):
        findings = cli.scan_file(fixture("sexagesimal.yaml"))
        self.assertEqual([f.rule for f in findings], ["YG006"])

    def test_quoted_values_are_not_flagged(self):
        findings = cli.scan_file(fixture("clean.yaml"))
        self.assertEqual(findings, [])

    def test_document_boundary_resets_duplicate_tracking(self):
        findings = cli.scan_file(fixture("multi_document.yaml"))
        self.assertEqual(findings, [])


class FixLineTests(unittest.TestCase):
    def test_quotes_bare_boolean_value(self):
        fixed, count = cli.fix_line("notify_on_failure: NO\n")
        self.assertEqual(fixed, 'notify_on_failure: "NO"\n')
        self.assertEqual(count, 1)

    def test_quotes_octal_lookalike_value(self):
        fixed, count = cli.fix_line("mode: 0755\n")
        self.assertEqual(fixed, 'mode: "0755"\n')
        self.assertEqual(count, 1)

    def test_quotes_sexagesimal_lookalike_value(self):
        fixed, count = cli.fix_line("backoff: 1:30\n")
        self.assertEqual(fixed, 'backoff: "1:30"\n')
        self.assertEqual(count, 1)

    def test_leaves_already_quoted_value_untouched(self):
        fixed, count = cli.fix_line('enabled: "no"\n')
        self.assertEqual(fixed, 'enabled: "no"\n')
        self.assertEqual(count, 0)

    def test_does_not_touch_duplicate_keys(self):
        fixed, count = cli.fix_line("retries: 3\n")
        self.assertEqual(fixed, "retries: 3\n")
        self.assertEqual(count, 0)

    def test_preserves_trailing_newline_style(self):
        fixed, count = cli.fix_line("flag: OFF")
        self.assertEqual(fixed, 'flag: "OFF"')
        self.assertEqual(count, 1)


class FixFileTests(unittest.TestCase):
    def test_fix_file_rewrites_in_place_until_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "deploy.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("notify_on_failure: NO\nmode: 0755\nbackoff: 1:30\n")

            fixed_count = cli.fix_file(path)

            self.assertEqual(fixed_count, 3)
            self.assertEqual(cli.scan_file(path), [])

    def test_fix_file_leaves_clean_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clean.yaml")
            original = "region: eu-west-1\nretries: 3\n"
            with open(path, "w", encoding="utf-8") as f:
                f.write(original)
            before_mtime = os.path.getmtime(path)

            fixed_count = cli.fix_file(path)

            self.assertEqual(fixed_count, 0)
            self.assertEqual(os.path.getmtime(path), before_mtime)
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), original)


class MainTests(unittest.TestCase):
    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_exit_code_1_when_issues_found(self):
        code, _ = self._run([fixture("duplicate_keys.yaml")])
        self.assertEqual(code, 1)

    def test_exit_code_0_when_clean(self):
        code, _ = self._run([fixture("clean.yaml")])
        self.assertEqual(code, 0)

    def test_exit_code_2_on_missing_file(self):
        code, _ = self._run([fixture("does-not-exist.yaml")])
        self.assertEqual(code, 2)

    def test_json_output_is_well_formed(self):
        import json

        code, output = self._run(["--json", fixture("octal.yaml")])
        self.assertEqual(code, 1)
        payload = json.loads(output)
        self.assertEqual(payload["issue_count"], 1)
        self.assertEqual(payload["findings"][0]["rule"], "YG005")


if __name__ == "__main__":
    unittest.main()

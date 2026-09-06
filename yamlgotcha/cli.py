"""Line-based scanner for common YAML footguns.

Deliberately does not parse YAML into a document tree. A real parser would
need a third-party library (PyYAML/ruamel), and pulling one in for a tool
whose whole job is "catch mistakes before they bite you at parse time" felt
backwards. Block-mapping structure is tracked with an indent stack instead,
which covers the common case (nested key: value config files). Flow-style
mappings (`{a: 1, b: 2}`) are handled separately with a single-pass
character scan per line, so a flow mapping split across multiple lines
isn't caught.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass

# YAML 1.1 core schema treats these bare words as booleans. PyYAML's
# SafeLoader (still the default for most config loaders) follows that
# schema, so `country: NO` silently becomes `country: False`. This is
# the "Norway problem".
AMBIGUOUS_SCALARS = {
    "y", "Y", "yes", "Yes", "YES",
    "n", "N", "no", "No", "NO",
    "true", "True", "TRUE",
    "false", "False", "FALSE",
    "on", "On", "ON",
    "off", "Off", "OFF",
}

KEY_LINE = re.compile(
    r'^(?P<indent>[ \t]*)'
    r'(?P<key>"[^"]*"|\'[^\']*\'|[^\s:#][^:]*?)'
    r':(?:\s+(?P<value>.*)|\s*)$'
)


@dataclass
class Finding:
    file: str
    line: int
    column: int
    rule: str
    message: str


def _strip_comment(text):
    # Naive: does not understand quoted '#' characters mid-string. Good
    # enough for the common "trailing # comment" case; documented as a
    # known limitation.
    in_single = in_double = False
    for i, ch in enumerate(text):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or text[i - 1] in " \t":
                return text[:i]
    return text


def _is_quoted(value):
    return len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"')


def _find_flow_mapping_duplicates(text):
    """Find duplicate keys inside `{...}` flow mappings on a single line.

    Single-pass character scan rather than a regex: flow mappings can
    nest (`{a: {b: 1, b: 2}}`) and each `{}` has its own independent set
    of sibling keys, which needs a stack, not a flat pattern. `[...]`
    is tracked too, only so a comma inside a flow sequence doesn't get
    mistaken for an entry separator in an enclosing flow mapping.
    """
    findings = []
    stack = []
    in_single = in_double = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "{":
            stack.append({"type": "{", "keys": set(), "entry_start": i + 1, "seen_colon": False})
        elif ch == "[":
            stack.append({"type": "[", "entry_start": i + 1})
        elif ch in "}]":
            if stack:
                stack.pop()
        elif ch == "," and stack and stack[-1]["type"] == "{":
            stack[-1]["entry_start"] = i + 1
            stack[-1]["seen_colon"] = False
        elif ch == ":" and stack and stack[-1]["type"] == "{" and not stack[-1]["seen_colon"]:
            frame = stack[-1]
            frame["seen_colon"] = True
            key_start = frame["entry_start"]
            while key_start < i and text[key_start] in " \t":
                key_start += 1
            key_raw = text[key_start:i].strip()
            if key_raw:
                key_text = key_raw[1:-1] if _is_quoted(key_raw) else key_raw
                if key_text in frame["keys"]:
                    findings.append((key_start + 1, key_text))
                else:
                    frame["keys"].add(key_text)
        i += 1
    return findings


def scan_lines(lines, filename):
    findings = []
    # stack of {"indent": int, "keys": set()} tracking sibling keys at
    # each nesting level seen so far.
    stack = [{"indent": -1, "keys": set()}]

    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip("\n")
        if not line.strip():
            continue

        stripped_for_comment = _strip_comment(line)
        if not stripped_for_comment.strip():
            continue

        if stripped_for_comment.strip() == "---":
            # Start of a new document in a `---`-separated stream. Keys
            # in the new document are independent of the previous one,
            # so the duplicate-key tracking has to start over too.
            stack = [{"indent": -1, "keys": set()}]
            continue

        leading = line[: len(line) - len(line.lstrip(" \t"))]
        if "\t" in leading:
            findings.append(Finding(
                filename, lineno, leading.index("\t") + 1, "YG002",
                "tab used for indentation (YAML forbids tabs here)",
            ))

        for col, key in _find_flow_mapping_duplicates(stripped_for_comment):
            findings.append(Finding(
                filename, lineno, col, "YG004",
                "duplicate key %r in flow mapping (last one silently wins)" % key,
            ))

        content = stripped_for_comment
        # Sequence items ("- foo" / "- key: value") are not tracked for
        # duplicate-key purposes; only their inline mapping value (if any)
        # is checked for ambiguous scalars.
        list_prefix_len = 0
        body = content.lstrip(" \t")
        indent = len(content) - len(body)
        while body.startswith("- "):
            list_prefix_len += 2
            body = body[2:]
            indent += 2

        match = KEY_LINE.match(" " * indent + body)
        if not match:
            continue

        key_raw = match.group("key").strip()
        value_raw = (match.group("value") or "").strip()
        key_indent = len(match.group("indent"))

        key_text = key_raw[1:-1] if _is_quoted(key_raw) else key_raw

        if list_prefix_len == 0:
            while len(stack) > 1 and stack[-1]["indent"] > key_indent:
                stack.pop()
            if stack[-1]["indent"] == key_indent:
                level = stack[-1]
            else:
                level = {"indent": key_indent, "keys": set()}
                stack.append(level)

            if key_text in level["keys"]:
                findings.append(Finding(
                    filename, lineno, key_indent + 1, "YG001",
                    "duplicate key %r at this level (last one silently wins)" % key_text,
                ))
            else:
                level["keys"].add(key_text)

        if not _is_quoted(key_raw) and key_raw in AMBIGUOUS_SCALARS:
            findings.append(Finding(
                filename, lineno, key_indent + 1, "YG003",
                "key %r will be parsed as a boolean, not the string %r" % (key_raw, key_raw),
            ))

        if value_raw and not _is_quoted(value_raw) and value_raw in AMBIGUOUS_SCALARS:
            value_col = match.start("value") + 1
            findings.append(Finding(
                filename, lineno, value_col, "YG003",
                "value %r will be parsed as a boolean, not the string %r" % (value_raw, value_raw),
            ))

    return findings


def scan_file(path):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return scan_lines(lines, path)


def print_human(findings, files_scanned):
    by_file = {}
    for f in findings:
        by_file.setdefault(f.file, []).append(f)

    for path in files_scanned:
        file_findings = by_file.get(path, [])
        if not file_findings:
            continue
        print(path)
        for f in sorted(file_findings, key=lambda x: (x.line, x.column)):
            print("  %d:%d  %s  %s" % (f.line, f.column, f.rule, f.message))
        print()

    clean = [p for p in files_scanned if not by_file.get(p)]
    if clean and findings:
        print("no issues: " + ", ".join(clean))
        print()

    if findings:
        print("%d issue(s) across %d file(s)" % (len(findings), len(by_file)))
    else:
        print("no issues found in %d file(s)" % len(files_scanned))


def print_json(findings, files_scanned):
    payload = {
        "files_scanned": files_scanned,
        "issue_count": len(findings),
        "findings": [
            {
                "file": f.file,
                "line": f.line,
                "column": f.column,
                "rule": f.rule,
                "message": f.message,
            }
            for f in findings
        ],
    }
    print(json.dumps(payload, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="yamlgotcha",
        description="Scan YAML files for footguns that parse cleanly but mean the wrong thing "
                     "(the Norway problem, silent duplicate keys, tab indentation).",
    )
    parser.add_argument("files", nargs="+", help="YAML files to scan")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of a text report")
    args = parser.parse_args(argv)

    all_findings = []
    for path in args.files:
        try:
            all_findings.extend(scan_file(path))
        except OSError as exc:
            print("%s: %s" % (path, exc.strerror or exc), file=sys.stderr)
            return 2

    if args.json:
        print_json(all_findings, args.files)
    else:
        print_human(all_findings, args.files)

    return 1 if all_findings else 0


if __name__ == "__main__":
    sys.exit(main())

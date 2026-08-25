# yamlgotcha

YAML files that parse without error can still mean something different
from what the author typed. `yamlgotcha` scans config files for the
specific mistakes that cause that: values silently coerced to booleans,
duplicate keys where the second one wins with no warning, and tabs mixed
into indentation. It's meant to run as a pre-commit check or a CI step
next to whatever already validates your config's schema.

## The problem

Given this file:

```yaml
# deploy.yaml
region: eu-west-1
notify_on_failure: NO
retries: 3
retries: 5
```

Most YAML loaders (PyYAML's `SafeLoader`, Ruby's `Psych`, and others that
follow the YAML 1.1 core schema) parse `notify_on_failure: NO` as
`False`, not the string `"NO"` — this is widely known as the Norway
problem, since it also bites two-letter country codes. And `retries` is
defined twice; nothing errors, the second definition just wins and the
first is gone. Both of these load cleanly and fail quietly, usually
nowhere near the config file itself.

## Usage

```
$ yamlgotcha deploy.yaml
deploy.yaml
  3:20  YG003  value 'NO' will be parsed as a boolean, not the string 'NO'
  5:1   YG001  duplicate key 'retries' at this level (last one silently wins)

2 issue(s) across 1 file(s)
```

```
$ yamlgotcha --json deploy.yaml
{
  "files_scanned": [
    "deploy.yaml"
  ],
  "issue_count": 2,
  "findings": [
    {
      "file": "deploy.yaml",
      "line": 3,
      "column": 20,
      "rule": "YG003",
      "message": "value 'NO' will be parsed as a boolean, not the string 'NO'"
    },
    {
      "file": "deploy.yaml",
      "line": 5,
      "column": 1,
      "rule": "YG001",
      "message": "duplicate key 'retries' at this level (last one silently wins)"
    }
  ]
}
```

The `--json` mode is meant for wiring into other tooling (CI annotations,
a pre-commit hook that posts a PR comment, etc.) without scraping the
text report. Exit code is `0` when a scan finds nothing, `1` when it
finds issues, `2` on a file error (missing file, permission denied).

Scan as many files as you want in one call:

```
$ yamlgotcha config/*.yaml
```

## Rules

| ID    | Meaning |
|-------|---------|
| YG001 | duplicate key at the same nesting level |
| YG002 | tab character used in leading indentation |
| YG003 | bare scalar that YAML 1.1 loaders coerce to a boolean (Norway problem) |

## Install

No dependencies, standard library only.

```
git clone <this repo>
cd yamlgotcha
pip install -e .
```

or just run it directly with `python -m yamlgotcha.cli <files>`.

## How it works, and what it doesn't do yet

`yamlgotcha` does not build a full YAML document tree — it tracks
mapping structure with an indentation stack while reading line by line.
That covers plain nested `key: value` config files, which is most of
what people hand-edit, but it does not currently understand flow-style
mappings (`{a: 1, b: 2}`), multi-document streams (`---` separators), or
duplicate keys inside block scalars. See the roadmap in commit history
for what's planned next.

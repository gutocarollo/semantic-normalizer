"""Every CLI command written in SKILL.md and README.md must actually run.

0.2.0 documented `normalize --lang pt --pretty`, `export-skos` and `evaluate --documents`;
none of them existed in the CLI. The docs were the skill's operating contract and every line
of it was false. Worse, while fixing that, the replacement text was written from memory and
got two subcommands wrong again (`export --format skos` instead of the positional
`export skos`). Prose cannot be trusted here — the commands are extracted from the Markdown
and executed.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = (ROOT / "SKILL.md", ROOT / "README.md")
INVOCATION = re.compile(
    r"(?:PYTHONPATH=src\s+)?(?:python3?|semantic-normalizer)\s+"
    r"(?:-m\s+semantic_normalizer\s+)?([a-z-]+)\b"
)
SUBCOMMANDS = {
    "validate-registry",
    "normalize",
    "query",
    "index",
    "evaluate",
    "reconcile-request",
    "reconcile-apply",
    "export",
    "init-workspace",
}


def documented_subcommands() -> set[str]:
    found: set[str] = set()
    for doc in DOCS:
        for block in re.findall(r"```bash\n(.*?)```", doc.read_text(encoding="utf-8"), re.S):
            # Join backslash continuations so a wrapped command is read as one line.
            for line in block.replace("\\\n", " ").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                match = INVOCATION.match(line)
                if match and match.group(1) in SUBCOMMANDS:
                    found.add(match.group(1))
    return found


class DocumentedCommandTests(unittest.TestCase):
    def cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "semantic_normalizer", *arguments],
            cwd=ROOT,
            env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
            text=True,
            capture_output=True,
            check=False,
        )

    def test_every_documented_subcommand_exists(self):
        documented = documented_subcommands()
        self.assertTrue(documented, "no bash invocations found in SKILL.md or README.md")
        self.assertEqual(set(), documented - SUBCOMMANDS)
        for name in sorted(documented):
            with self.subTest(subcommand=name):
                result = self.cli(name, "--help")
                self.assertEqual(0, result.returncode, result.stderr)

    def test_documented_subcommands_cover_the_cli(self):
        """The docs must not quietly omit a subcommand the CLI exposes."""
        self.assertEqual(SUBCOMMANDS, documented_subcommands())

    def test_read_only_commands_run_with_their_documented_syntax(self):
        cases = [
            ("validate-registry",),
            ("normalize", "--text", "Remove the panel."),
            ("query", "--text", "Como iniciar o servidor?"),
            ("evaluate", "tests/fixtures/dev_retrieval.json"),
        ]
        for arguments in cases:
            with self.subTest(command=shlex.join(arguments)):
                result = self.cli(*arguments)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertTrue(result.stdout.strip())

    def test_export_uses_a_positional_format(self):
        """Guards the exact mistake made once: `export --format skos` is not the syntax."""
        for fmt in ("skos", "synonym-graph"):
            with self.subTest(format=fmt):
                self.assertEqual(0, self.cli("export", fmt, "--output", "/dev/null").returncode)
        self.assertNotEqual(
            0, self.cli("export", "--format", "skos", "--output", "/dev/null").returncode
        )

    def test_no_document_shows_a_flag_the_cli_rejects(self):
        retired = ("--lang ", "--pretty", "--target-lang", "--documents", "--queries")
        for doc in DOCS:
            text = doc.read_text(encoding="utf-8")
            for flag in retired:
                with self.subTest(doc=doc.name, flag=flag.strip()):
                    self.assertNotIn(flag, text)

    def test_no_document_names_a_retired_subcommand(self):
        for doc in DOCS:
            text = doc.read_text(encoding="utf-8")
            for name in ("export-skos", "export-synonyms", "propose-term"):
                with self.subTest(doc=doc.name, subcommand=name):
                    self.assertNotIn(name, text)

    def test_documented_flags_exist_on_their_subcommand(self):
        """Every `--flag` written next to a subcommand must appear in its own --help.

        This is the generalisation of three rounds of the same defect: `--path`, `--format`,
        `--dataset` and finally `reconcile-apply` missing two *required* arguments. Checking
        four hand-picked commands was never going to catch the fifth.
        """
        for doc in DOCS:
            text = doc.read_text(encoding="utf-8").replace("\\\n", " ")
            for block in re.findall(r"```bash\n(.*?)```", text, re.S):
                for line in block.splitlines():
                    line = line.strip()
                    if line.startswith("#") or not line:
                        continue
                    match = INVOCATION.match(line)
                    if not match or match.group(1) not in SUBCOMMANDS:
                        continue
                    name = match.group(1)
                    usage = self.cli(name, "--help").stdout
                    for flag in re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", line):
                        with self.subTest(doc=doc.name, subcommand=name, flag=flag):
                            self.assertIn(flag, usage)

    def test_documented_required_arguments_are_all_present(self):
        """A documented example must supply every argument its subcommand requires."""
        for doc in DOCS:
            text = doc.read_text(encoding="utf-8").replace("\\\n", " ")
            for block in re.findall(r"```bash\n(.*?)```", text, re.S):
                for line in block.splitlines():
                    line = line.strip()
                    if line.startswith("#") or not line:
                        continue
                    match = INVOCATION.match(line)
                    if not match or match.group(1) not in SUBCOMMANDS:
                        continue
                    name = match.group(1)
                    usage = self.cli(name, "--help").stdout
                    # argparse prints optional arguments in brackets; a flag shown bare in
                    # the usage line is required.
                    required = set(re.findall(r"(?<![\[\w-])(--[a-z][a-z-]+)", usage.split("\n\n")[0]))
                    supplied = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", line))
                    with self.subTest(doc=doc.name, subcommand=name):
                        self.assertEqual(set(), required - supplied)

    def test_makefile_targets_use_the_current_cli(self):
        """The Makefile is documentation too — round 2 found all of its targets broken."""
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        for retired in ("--lang ", "--pretty", "--documents", "--queries", "export-skos"):
            with self.subTest(flag=retired.strip()):
                self.assertNotIn(retired, makefile)

    def test_reconcile_request_example_is_a_real_ambiguous_span(self):
        """The documented example must actually be ambiguous, or the command errors out.

        The first version used `"Remove it."` span 0-6, which stopped being ambiguous once the
        importer refused to attach `remove` to `action.delete`.
        """
        import json
        import tempfile

        for doc in DOCS:
            text = doc.read_text(encoding="utf-8")
            if "reconcile-request" not in text:
                continue
            match = re.search(
                r'--context "([^"]+)" --start (\d+) --end (\d+) --language (\S+)',
                text.replace("\\\n", " "),
            )
            with self.subTest(doc=doc.name):
                self.assertIsNotNone(match, "no reconcile-request example found")
                assert match
                context, start, end, language = match.groups()
                with tempfile.TemporaryDirectory() as directory:
                    self.assertEqual(0, self.cli("init-workspace", directory).returncode)
                    result = self.cli(
                        "reconcile-request",
                        "--workspace", directory,
                        "--context", context,
                        "--start", start,
                        "--end", end,
                        "--language", language,
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    request = json.loads(result.stdout)
                    self.assertGreater(len(request["allowed_candidates"]), 1)


if __name__ == "__main__":
    unittest.main()

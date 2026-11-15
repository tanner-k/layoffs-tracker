"""Tests for check_scaffold.py. Stdlib only.

Run: python3 -m unittest discover -s scripts -v
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_scaffold as cs  # noqa: E402

# Built by concatenation so this file never trips the check it tests.
TOKEN = "__" + "PROJECT_NAME" + "__"
PKG_TOKEN = "__" + "PKG" + "__"


class CheckScaffoldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)  # registered first, so it runs after chmod restores
        self.root = Path(self.tmp.name)
        for name in ("CLAUDE.md", "README.md", "CHANGELOG.md", ".onigiri.toml"):
            (self.root / name).write_text(f"# {name}\n")
        os.symlink("CLAUDE.md", self.root / "AGENTS.md")

    def quiet_main(self, *args: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cs.main(list(args))

    def test_clean_scaffold_has_no_problems(self) -> None:
        self.assertEqual(cs.find_problems(self.root), [])

    def test_reports_leftover_token_with_file_and_line(self) -> None:
        (self.root / "README.md").write_text(f"# Title\n\nby {TOKEN}\n")
        self.assertEqual(
            cs.find_problems(self.root),
            [f"README.md:3: placeholder {TOKEN} left unfilled"],
        )

    def test_reports_token_in_folder_name(self) -> None:
        (self.root / "src" / PKG_TOKEN).mkdir(parents=True)
        self.assertEqual(
            cs.find_problems(self.root),
            [f"src/{PKG_TOKEN}: placeholder {PKG_TOKEN} in a file or folder name"],
        )

    def test_ignores_lowercase_python_dunders(self) -> None:
        (self.root / "app.py").write_text('if __name__ == "__main__":\n    print(__file__)\n')
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "__init__.py").write_text("")
        self.assertEqual(cs.find_problems(self.root), [])

    def test_ignores_unknown_uppercase_dunders(self) -> None:
        # Real code uses these (Next.js, build-time constants); they are not template tokens.
        next_data = "__" + "NEXT_DATA" + "__"
        (self.root / "page.tsx").write_text(f"const d = window.{next_data};\n")
        (self.root / next_data).write_text("x\n")
        self.assertEqual(cs.find_problems(self.root), [])

    def test_skips_generated_trees(self) -> None:
        (self.root / "node_modules" / "lib").mkdir(parents=True)
        (self.root / "node_modules" / "lib" / "x.js").write_text(TOKEN)
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "y.py").write_text(TOKEN)
        self.assertEqual(cs.find_problems(self.root), [])

    def test_skips_every_skip_dir_at_any_depth(self) -> None:
        for name in sorted(cs.SKIP_DIRS):
            with self.subTest(skip_dir=name):
                for parent in (self.root, self.root / "pkg"):
                    (parent / name / TOKEN).mkdir(parents=True, exist_ok=True)
                    (parent / name / "x.txt").write_text(TOKEN)
                self.assertEqual(cs.find_problems(self.root), [])
        (self.root / "src").mkdir()
        (self.root / "src" / "x.txt").write_text(TOKEN)  # control: not a skip dir
        self.assertEqual(len(cs.find_problems(self.root)), 1)

    def test_skips_binary_files(self) -> None:
        (self.root / "favicon.ico").write_bytes(b"\xff\xfe\x00" + TOKEN.encode())
        self.assertEqual(cs.find_problems(self.root), [])

    def test_reports_broken_agents_symlink(self) -> None:
        (self.root / "CLAUDE.md").unlink()
        self.assertEqual(
            cs.find_problems(self.root),
            [
                "CLAUDE.md: required file is missing",
                "AGENTS.md: missing or a broken symlink (should point to CLAUDE.md)",
            ],
        )

    def test_accepts_agents_as_identical_copy(self) -> None:
        (self.root / "AGENTS.md").unlink()
        (self.root / "AGENTS.md").write_text("# CLAUDE.md\n")
        self.assertEqual(cs.find_problems(self.root), [])

    def test_reports_agents_copy_that_diverged(self) -> None:
        (self.root / "AGENTS.md").unlink()
        (self.root / "AGENTS.md").write_text("# something else\n")
        self.assertEqual(
            cs.find_problems(self.root),
            ["AGENTS.md: differs from CLAUDE.md (should be a symlink to it)"],
        )

    def test_reports_missing_required_file(self) -> None:
        (self.root / ".onigiri.toml").unlink()
        self.assertEqual(cs.find_problems(self.root), [".onigiri.toml: required file is missing"])

    def test_reports_unreadable_text_file(self) -> None:
        secret = self.root / "secret.txt"
        secret.write_text(TOKEN)
        secret.chmod(0)
        self.addCleanup(secret.chmod, 0o644)
        if os.access(secret, os.R_OK):
            self.skipTest("file permissions are not enforced (running as root?)")
        self.assertEqual(cs.find_problems(self.root), ["secret.txt: unreadable (PermissionError)"])

    def test_unreadable_claude_md_is_reported_not_raised(self) -> None:
        claude = self.root / "CLAUDE.md"
        claude.chmod(0)
        self.addCleanup(claude.chmod, 0o644)
        if os.access(claude, os.R_OK):
            self.skipTest("file permissions are not enforced (running as root?)")
        problems = cs.find_problems(self.root)
        self.assertIn("CLAUDE.md: unreadable (PermissionError)", problems)
        self.assertIn("AGENTS.md: cannot compare with CLAUDE.md (PermissionError)", problems)

    def test_reports_agents_md_that_is_a_directory(self) -> None:
        (self.root / "AGENTS.md").unlink()
        (self.root / "AGENTS.md").mkdir()
        self.assertEqual(cs.find_problems(self.root), ["AGENTS.md: not a file"])
        self.assertEqual(self.quiet_main(str(self.root)), 1)

    def test_reports_agents_symlink_to_a_directory(self) -> None:
        (self.root / "AGENTS.md").unlink()
        (self.root / "docs").mkdir()
        os.symlink("docs", self.root / "AGENTS.md")
        self.assertEqual(cs.find_problems(self.root), ["AGENTS.md: not a file"])

    def test_reports_claude_md_that_is_a_directory(self) -> None:
        (self.root / "CLAUDE.md").unlink()
        (self.root / "CLAUDE.md").mkdir()
        (self.root / "AGENTS.md").unlink()
        (self.root / "AGENTS.md").write_text("# CLAUDE.md\n")
        self.assertEqual(cs.find_problems(self.root), ["CLAUDE.md: required file is missing"])
        self.assertEqual(self.quiet_main(str(self.root)), 1)

    def test_main_exit_codes(self) -> None:
        self.assertEqual(self.quiet_main(str(self.root)), 0)
        (self.root / "README.md").write_text(TOKEN)
        self.assertEqual(self.quiet_main(str(self.root)), 1)
        self.assertEqual(self.quiet_main(str(self.root / "missing")), 2)

    def run_main(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cs.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_main_clean_prints_only_success_line(self) -> None:
        self.assertEqual(self.run_main(str(self.root)), (0, "✓ scaffold clean\n", ""))

    def test_main_prints_problem_lines_then_count_to_stderr(self) -> None:
        (self.root / "README.md").write_text(f"# Title\n\nby {TOKEN}\n")
        (self.root / ".onigiri.toml").unlink()
        self.assertEqual(
            self.run_main(str(self.root)),
            (
                1,
                "",
                "✗ .onigiri.toml: required file is missing\n"
                + f"✗ README.md:3: placeholder {TOKEN} left unfilled\n"
                + "2 scaffold problem(s). Fill or remove them, then re-run.\n",
            ),
        )

    def test_main_bad_root_message(self) -> None:
        missing = self.root / "missing"
        self.assertEqual(self.run_main(str(missing)), (2, "", f"✗ {missing} is not a directory\n"))

    def test_main_survives_a_stream_that_cannot_encode_marks(self) -> None:
        (self.root / "README.md").write_text(TOKEN)
        raw_out, raw_err = io.BytesIO(), io.BytesIO()
        out = io.TextIOWrapper(raw_out, encoding="ascii", write_through=True)
        err = io.TextIOWrapper(raw_err, encoding="ascii", write_through=True)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(cs.main([str(self.root)]), 1)
            (self.root / "README.md").write_text("ok\n")
            self.assertEqual(cs.main([str(self.root)]), 0)
        self.assertTrue(raw_err.getvalue().startswith(b"? README.md:1:"))
        self.assertEqual(raw_out.getvalue(), b"? scaffold clean\n")

    def test_default_root_is_the_parent_of_scripts(self) -> None:
        (self.root / "scripts").mkdir()
        script = self.root / "scripts" / "check_scaffold.py"
        shutil.copy(Path(cs.__file__), script)

        def run(cwd: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-I", str(script)],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as elsewhere:
            clean = run(elsewhere)
            (self.root / "README.md").write_text(TOKEN)
            dirty = run(elsewhere)
        self.assertEqual(
            (clean.returncode, clean.stdout, clean.stderr), (0, "✓ scaffold clean\n", "")
        )
        self.assertEqual(dirty.returncode, 1)
        self.assertIn(f"✗ README.md:1: placeholder {TOKEN} left unfilled", dirty.stderr)


if __name__ == "__main__":
    unittest.main()

"""Agents installed in the usual folders (mise, npm, bun …) are found and started by their full path."""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import agent_paths  # noqa: E402


class AgentPathsTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        shims = self.home / ".local/share/mise/shims"
        shims.mkdir(parents=True)
        self.tool = shims / "codex"
        self.tool.write_text("#!/bin/sh\n")
        self.tool.chmod(0o755)

    def test_found_in_mise_shims_when_not_on_path(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(agent_paths.find("codex", self.home), str(self.tool))
            argv, env = agent_paths.resolve(["codex", "exec", "-"], {"PATH": "/usr/bin"}, self.home)
        self.assertEqual(argv, [str(self.tool), "exec", "-"])
        self.assertTrue(env["PATH"].startswith(str(self.tool.parent) + os.pathsep))

    def test_on_path_stays_as_it_is(self):
        with mock.patch("shutil.which", return_value="/usr/bin/codex"):
            argv, env = agent_paths.resolve(["codex", "exec"], {"PATH": "/usr/bin"}, self.home)
        self.assertEqual(argv, ["codex", "exec"])
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_not_executable_or_names_with_a_slash_are_not_taken(self):
        self.tool.chmod(stat.S_IRUSR | stat.S_IWUSR)
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(agent_paths.find("codex", self.home))
            self.assertIsNone(agent_paths.find("../codex", self.home))
            self.assertIsNone(agent_paths.find("nothing-here", self.home))


if __name__ == "__main__":
    unittest.main()

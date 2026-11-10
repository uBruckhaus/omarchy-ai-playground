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

    def test_folder_others_may_change_is_refused(self):
        shared = self.home / ".local/share/mise/shims"
        shared.chmod(0o777)                                   # another account could swap the program
        self.addCleanup(lambda: shared.chmod(0o755))
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(agent_paths.find("codex", self.home))

    def test_link_into_a_folder_others_may_change_is_refused(self):
        elsewhere = self.home / "shared"
        elsewhere.mkdir()
        real = elsewhere / "codex-real"
        real.write_text("#!/bin/sh\n")
        real.chmod(0o755)
        self.tool.unlink()
        self.tool.symlink_to(real)
        elsewhere.chmod(0o777)
        self.addCleanup(lambda: elsewhere.chmod(0o755))
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(agent_paths.find("codex", self.home))

    def test_link_owned_by_someone_else_is_refused(self):
        real = self.home / "codex-real"
        real.write_text("#!/bin/sh\n")
        real.chmod(0o755)
        self.tool.unlink()
        self.tool.symlink_to(real)
        original = agent_paths.os.lstat

        def lstat(path, *a, **k):
            info = original(path, *a, **k)
            if str(path) == str(self.tool):
                return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink, os.getuid() + 4242,
                                       info.st_gid, info.st_size, int(info.st_atime), int(info.st_mtime),
                                       int(info.st_ctime)))
            return info
        with mock.patch("shutil.which", return_value=None), mock.patch.object(agent_paths.os, "lstat", side_effect=lstat):
            self.assertIsNone(agent_paths.find("codex", self.home))
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(agent_paths.find("codex", self.home), str(self.tool))   # our own link is fine

    def test_link_reached_through_another_link_is_not_followed(self):
        real = self.home / "codex-real"
        real.write_text("#!/bin/sh\n")
        real.chmod(0o755)
        middle = self.home / "middle"
        middle.symlink_to(real)                      # a second link someone could repoint
        self.tool.unlink()
        self.tool.symlink_to(middle)
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(agent_paths.find("codex", self.home))
        self.tool.unlink()
        self.tool.symlink_to(real)                   # one link straight to the file: fine
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(agent_paths.find("codex", self.home), str(self.tool))


if __name__ == "__main__":
    unittest.main()

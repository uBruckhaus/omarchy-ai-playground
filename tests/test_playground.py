"""AI Playground: how agents are asked (argv, stdin, pictures), how answers are read, the local
LLM checks and the command line. No agent and no server is started."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
if not (HERE / "ai_core.py").is_file():
    sys.path.insert(0, str(HERE.parent))           # Asterism's playground/ folder: the core is one up

import agent_run  # noqa: E402
import local_llm  # noqa: E402


def png(folder: str, name: str = "p.png") -> str:
    path = os.path.join(folder, name)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    return path


class AskTest(unittest.TestCase):
    def test_claude_answers_as_json_and_takes_pictures_on_stdin(self):
        argv, stdin = agent_run.build("claude", "Hello?", [], "/tmp/x")
        self.assertEqual(argv[0], "claude")
        self.assertEqual(argv[-1], "Hello?")
        self.assertIn("--output-format", argv)
        self.assertIsNone(stdin)
        with tempfile.TemporaryDirectory() as folder:
            argv, stdin = agent_run.build("claude", "What is it?", [png(folder)], folder)
        self.assertNotIn("What is it?", argv)                 # the question goes on stdin
        self.assertIn("stream-json", argv)
        message = json.loads(stdin)
        kinds = [block["type"] for block in message["message"]["content"]]
        self.assertEqual(kinds, ["image", "text"])

    def test_codex_and_pi_get_pictures_before_the_question(self):
        argv, _stdin = agent_run.build("codex", "Q", ["/f/picture-1.png", "/f/picture-2.png"], "/f")
        self.assertEqual(argv[-5:], ["-i", "/f/picture-1.png", "-i", "/f/picture-2.png", "Q"])
        argv, _stdin = agent_run.build("pi", "Q", ["/f/picture-1.png"], "/f")
        self.assertEqual(argv[-2:], ["@/f/picture-1.png", "Q"])

    def test_who_takes_pictures(self):
        self.assertTrue(agent_run.takes_images("claude"))
        self.assertFalse(agent_run.takes_images("hermes"))
        self.assertFalse(agent_run.takes_images("no-such-agent"))

    def test_claude_tokens_and_cost(self):
        out = json.dumps({"type": "result", "result": "A blue circle.", "total_cost_usd": 0.018,
                          "usage": {"input_tokens": 3, "cache_read_input_tokens": 100, "output_tokens": 17}})
        result = agent_run.parse("claude", out)
        self.assertEqual((result.text, result.tokens_in, result.tokens_out, result.cost),
                         ("A blue circle.", 103, 17, 0.018))
        stream = "\n".join([json.dumps({"type": "system"}), out])
        self.assertEqual(agent_run.parse("claude", stream).tokens_out, 17)
        self.assertEqual(agent_run.parse("codex", "  plain  ").text, "plain")

    def test_ask_copies_pictures_and_refuses_others(self):
        seen = {}

        def run(argv, max_bytes, timeout, stdin, cwd, env):
            seen["argv"], seen["cwd"] = argv, cwd
            seen["copied"] = sorted(os.listdir(cwd))
            return b"It is red.", True
        with tempfile.TemporaryDirectory() as folder:
            picture = png(folder, "-rf.png")                    # a name like an option stays out of argv
            result = agent_run.ask("codex", "Q", [picture], runner=run)
            self.assertEqual(result.text, "It is red.")
            self.assertEqual(seen["copied"], ["picture-1.png"])
            self.assertNotIn(picture, seen["argv"])
            self.assertFalse(os.path.exists(seen["cwd"]))      # the private folder is gone
            text = Path(folder, "notes.txt")
            text.write_text("x")
            self.assertTrue(agent_run.ask("codex", "Q", [str(text)], runner=run).error)
            self.assertTrue(agent_run.ask("hermes", "Q", [picture], runner=run).error)

    def test_no_answer(self):
        result = agent_run.ask("pi", "Q", runner=lambda *a, **k: (b"", False))
        self.assertTrue(result.error)


class LocalTest(unittest.TestCase):
    def test_only_own_servers_are_asked(self):
        other = os.getuid() + 1
        listeners = lambda port: {11434: [other], 8080: [os.getuid()]}.get(port, [])
        with mock.patch("netfetch.fetch_json", return_value={"data": [{"id": "qwen3"}, {"id": "bad\nname"}]}) as fetch:
            servers = local_llm.running(listeners)
        by_key = {s.key: s for s in servers}
        self.assertEqual(by_key["ollama"].error, "another account")
        self.assertEqual(by_key["llamacpp"].models, ("qwen3",))
        self.assertEqual(fetch.call_count, 1)                   # Ollama's port was not asked

    def test_ask_refuses_a_foreign_listener(self):
        with mock.patch("loopback.loopback_listeners", return_value=[os.getuid() + 1]), \
                mock.patch("netfetch.fetch") as fetch:
            answer = local_llm.ask(8080, "m", "Q")
        self.assertTrue(answer.error)
        fetch.assert_not_called()

    def test_chat_answer_and_speed(self):
        answer = local_llm.chat_answer({"choices": [{"message": {"content": " Hi "}}],
                                        "usage": {"prompt_tokens": 5, "completion_tokens": 20}})
        answer.seconds = 2.0
        self.assertEqual((answer.text, answer.tokens_out, answer.per_second), ("Hi", 20, 10.0))
        self.assertIsNone(local_llm.chat_answer({"choices": []}).per_second)


class GpuCheckTest(unittest.TestCase):
    GIB = 1024 ** 3

    def test_two_models_that_do_not_fit(self):
        import gpu_check
        files = {"qwen-27b": 16 * self.GIB, "gemma-12b": 7 * self.GIB}
        result = gpu_check.check([("Pi: qwen-27b", "llamacpp", "qwen-27b"), ("Hermes: gemma-12b", "llamacpp", "gemma-12b")],
                                 totals=(1 * self.GIB, 24 * self.GIB, None), files=files)
        self.assertFalse(result.fits)
        self.assertEqual(result.free, 23 * self.GIB)
        small = {"a": 4 * self.GIB, "b": 4 * self.GIB}
        self.assertTrue(gpu_check.check([("a", "ollama", "a"), ("b", "ollama", "b")],
                                        totals=(0, 24 * self.GIB, None), files=small).fits)

    def test_one_model_unknown_sizes_and_no_gpu(self):
        import gpu_check
        self.assertIsNone(gpu_check.check([("a", "ollama", "a"), ("A again", "ollama", "A")], files={}))
        unknown = gpu_check.check([("a", "ollama", "a"), ("b", "ollama", "b")], totals=(0, 24 * self.GIB, None),
                                  files={"a": self.GIB})
        self.assertIsNone(unknown.fits)
        self.assertEqual([n.model for n in unknown.unknown], ["b"])
        no_gpu = gpu_check.check([("a", "ollama", "a"), ("b", "ollama", "b")], totals=(0, 0, None), files={})
        self.assertIsNone(no_gpu.fits)

    def test_sizes_from_files(self):
        import gpu_check
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "Model-A.gguf").write_bytes(b"x" * 100)
            Path(folder, "mmproj-Model-A.gguf").write_bytes(b"x" * 50)
            Path(folder, "big-00001-of-00002.gguf").write_bytes(b"x" * 30)
            Path(folder, "big-00002-of-00002.gguf").write_bytes(b"x" * 20)
            os.mkdir(Path(folder, "router"))
            os.symlink(Path(folder, "Model-A.gguf"), Path(folder, "router", "Model-A.gguf"))
            self.assertEqual(gpu_check.gguf_sizes([Path(folder)]), {"model-a": 100, "big": 50})

    def test_agent_model_from_its_settings(self):
        import gpu_check
        from types import SimpleNamespace
        with mock.patch("agent_details.details_for",
                        return_value=SimpleNamespace(model="Qwen3.8-27B-Q4_K_M (llama-local)")):
            self.assertEqual(gpu_check.agent_model("pi"), ("llamacpp", "Qwen3.8-27B-Q4_K_M"))
        with mock.patch("agent_details.details_for", return_value=SimpleNamespace(model="opus")):
            self.assertIsNone(gpu_check.agent_model("claude"))


class ArgsTest(unittest.TestCase):
    def test_agent_and_test(self):
        import ai_playground
        self.assertEqual(ai_playground.parse_args(["--agent", "claude", "--test=chess"]),
                         {"agent": "claude", "test": "chess"})
        self.assertEqual(ai_playground.parse_args(["--agent", "rm -rf", "--test", "nope"]),
                         {"agent": "", "test": ""})


class PackagingTest(unittest.TestCase):
    CORE = ("adapter_data", "agent_details", "agent_status", "ai_core", "claude_sessions", "corners", "desktop",
            "desktop_generic", "desktop_omarchy", "gpu_usage", "harnesses", "i18n", "icons", "loopback", "more_plugins",
            "netfetch", "palette", "runtime_ctl", "settings_schema", "signin", "theme_css")

    def test_every_core_module_is_there(self):
        import importlib
        for name in self.CORE:
            importlib.import_module(name)

    def test_every_text_translated(self):
        import ast
        import i18n
        ids = set()
        for source in HERE.glob("*.py"):              # every _("…") and N_("…"), joined lines too
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("_", "N_") and node.args \
                        and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    ids.add(node.args[0].value)
        for code in ("de", "es", "fr", "it", "nl", "pl", "pt"):
            missing = ids - set(i18n.STRINGS[code])
            self.assertFalse(missing, (code, missing))


if __name__ == "__main__":
    unittest.main()

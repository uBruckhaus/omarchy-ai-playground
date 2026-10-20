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
        self.assertNotIn("Hello?", argv)                      # on stdin: argv is visible to other accounts
        self.assertEqual(stdin, b"Hello?")
        self.assertIn("--output-format", argv)
        with tempfile.TemporaryDirectory() as folder:
            argv, stdin = agent_run.build("claude", "What is it?", [png(folder)], folder)
        self.assertNotIn("What is it?", argv)                 # the question goes on stdin
        self.assertIn("stream-json", argv)
        message = json.loads(stdin)
        kinds = [block["type"] for block in message["message"]["content"]]
        self.assertEqual(kinds, ["image", "text"])

    def test_codex_and_pi_get_pictures_before_the_question(self):
        argv, stdin = agent_run.build("codex", "Q", ["/f/picture-1.png", "/f/picture-2.png"], "/f")
        self.assertEqual(argv[-5:], ["-i", "/f/picture-1.png", "-i", "/f/picture-2.png", "-"])
        self.assertEqual(stdin, b"Q")
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
                         {"agent": "claude", "test": "chess", "toggle": False})
        self.assertEqual(ai_playground.parse_args(["--agent", "rm -rf", "--test", "nope"]),
                         {"agent": "", "test": "", "toggle": False})
        self.assertTrue(ai_playground.parse_args(["--toggle"])["toggle"])


class LocalHoldTest(unittest.TestCase):
    """A chess video read by a local model: the server kept up, no thinking, models without eyes left out."""

    def test_a_local_frame_is_asked_without_thinking(self):
        with tempfile.TemporaryDirectory() as folder:
            argv, _stdin = agent_run.build("pi", "Q", [png(folder)], folder, "quick", think=False)
            self.assertEqual(argv[argv.index("--thinking") + 1], "off")
            argv, _stdin = agent_run.build("pi", "Q", [png(folder)], folder, "quick")
            self.assertEqual(argv[argv.index("--thinking") + 1], "low")

    def test_models_ini_tells_which_llama_models_see(self):
        import local_hold
        with tempfile.TemporaryDirectory() as folder:
            ini = Path(folder, "models.ini")
            ini.write_text("[*]\nctx-size = 4096\n\n[Eyes-12B]\nmmproj = /m/mmproj.gguf\nctx-size = 1\n\n"
                           "[Blind-26B]\nctx-size = 98304\n# mmproj = /m/x.gguf\n")
            self.assertTrue(local_hold.sees_pictures("llamacpp", "eyes-12b", ini))
            self.assertFalse(local_hold.sees_pictures("llamacpp", "Blind-26B", ini))
            self.assertIsNone(local_hold.sees_pictures("llamacpp", "Other", ini))

    def test_start_waits_until_the_server_answers(self):
        import local_hold
        import runtime_ctl
        up = []
        with mock.patch.object(local_hold.loopback, "loopback_listeners", side_effect=lambda _p: [os.getuid()] if up else []), \
                mock.patch.object(local_hold.loopback, "require_own_listener",
                                  side_effect=lambda _p: None if up else (_ for _ in ()).throw(ConnectionError())), \
                mock.patch.object(local_hold, "service_of", return_value="llama-server"), \
                mock.patch.object(runtime_ctl, "start", side_effect=lambda _rt: (up.append(1), (True, ""))[1]) as start, \
                mock.patch.object(local_hold, "ready", side_effect=[False, True]):
            self.assertEqual(local_hold.start("llamacpp", sleep=lambda _s: None), "")
        self.assertEqual(start.call_count, 1)

    def test_a_server_that_does_not_answer_is_reported(self):
        import local_hold
        clock = iter(range(0, 1000, 10))
        with mock.patch.object(local_hold.loopback, "require_own_listener"), \
                mock.patch.object(local_hold.loopback, "loopback_listeners", return_value=[os.getuid()]), \
                mock.patch.object(local_hold, "ready", return_value=False):
            self.assertTrue(local_hold.start("llamacpp", wait=30, clock=lambda: next(clock), sleep=lambda _s: None))

    def test_another_accounts_server_is_not_used(self):
        import local_hold
        with mock.patch.object(local_hold.loopback, "require_own_listener", side_effect=PermissionError("other")):
            self.assertEqual(local_hold.start("llamacpp"), "other")


class VideoReaderTest(unittest.TestCase):
    """A video's description, captions and frames come from the web: only agents without tools read them."""

    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")

    def test_only_tool_less_agents_read_web_content(self):
        self.assertTrue(agent_run.reads_untrusted("claude"))
        self.assertTrue(agent_run.reads_untrusted("pi"))
        self.assertTrue(agent_run.reads_untrusted("hermes"))
        self.assertFalse(agent_run.reads_untrusted("codex"))         # its read-only sandbox still reads files
        self.assertFalse(agent_run.reads_untrusted("opencode"))

    def test_their_ask_commands_really_have_no_tools(self):
        import chess_agent
        argv = {c: " ".join(chess_agent.ask_argv(c)) for c in agent_run.NO_TOOLS}
        self.assertIn('--tools  --strict-mcp-config', argv["claude"])
        self.assertIn("--no-tools", argv["pi"])
        self.assertIn("-t todo", argv["hermes"])

    def test_codex_never_gets_a_videos_text(self):
        import chess_import
        from types import SimpleNamespace
        with mock.patch.object(chess_import.chess_video, "video_info", side_effect=AssertionError("fetched")), \
                mock.patch.object(chess_import.agent_run, "ask", side_effect=AssertionError("asked")):
            found = chess_import.ChessImport.chess_read_text(None, "https://www.youtube.com/watch?v=x",
                                                             SimpleNamespace(command="codex", name="Codex"),
                                                             lambda _t: None)
        self.assertIsNone(found)

    def test_the_readers_leave_codex_out(self):
        import chess_import
        from types import SimpleNamespace
        host = SimpleNamespace(ready_agents=[SimpleNamespace(command=c, name=c) for c in ("claude", "codex", "pi", "hermes")])
        host.video_readers = lambda: chess_import.ChessImport.video_readers(host)
        self.assertEqual([a.command for a in chess_import.ChessImport.video_readers(host)], ["claude", "pi", "hermes"])
        self.assertEqual([a.command for a in chess_import.ChessImport.chess_readers(host)], ["claude", "pi"])


class FrameCheckTest(unittest.TestCase):
    """frame_check: an agent on a local model is set up for frames where it can be, else told why."""

    def test_the_vision_line_goes_into_the_models_section(self):
        import frame_check
        text = "[*]\nctx-size = 1\n\n[Gemma-26B]\nctx-size = 98304\n\n[Other]\n"
        new = frame_check.ini_with_mmproj(text, "gemma-26b", Path("/m/mmproj-x.gguf"))
        self.assertIn("[Gemma-26B]\nmmproj = /m/mmproj-x.gguf\nctx-size = 98304", new)
        self.assertIsNone(frame_check.ini_with_mmproj(text, "Missing", Path("/m/x.gguf")))

    def test_a_vision_file_beside_the_model_is_found(self):
        import frame_check
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder, "unsloth", "gemma-GGUF")
            repo.mkdir(parents=True)
            (repo / "gemma-Q4.gguf").write_bytes(b"x")
            router = Path(folder, "router")
            router.mkdir()
            (router / "gemma-Q4.gguf").symlink_to(repo / "gemma-Q4.gguf")
            found = frame_check.model_file("gemma-Q4", [router])
            self.assertEqual(found, (repo / "gemma-Q4.gguf").resolve())
            self.assertEqual(frame_check.repo_of(found), "unsloth/gemma-GGUF")
            self.assertIsNone(frame_check.local_mmproj(found))
            (repo / "mmproj-BF16.gguf").write_bytes(b"x")
            self.assertEqual(frame_check.local_mmproj(found).name, "mmproj-BF16.gguf")

    def test_the_repository_offers_its_bf16_vision_file_with_its_checksum(self):
        import frame_check
        sha = "a" * 64
        rows = [{"path": "mmproj-F32.gguf", "lfs": {"size": 2000, "oid": sha}},
                {"path": "mmproj-BF16.gguf", "lfs": {"size": 1000, "oid": sha}},
                {"path": "mmproj-F16.gguf", "lfs": {"size": 1000, "oid": sha}},
                {"path": "sub/mmproj-Q8.gguf", "lfs": {"size": 10, "oid": sha}},          # not at the top
                {"path": "mmproj-bad.gguf", "lfs": {"size": 10, "oid": "not-a-sha"}},
                {"path": "model-Q4.gguf", "lfs": {"size": 10, "oid": sha}}]
        fetch = lambda *_a, **_k: json.dumps(rows).encode()          # noqa: E731
        with mock.patch.object(frame_check, "own_folder", return_value=True):
            d = frame_check.hf_mmproj("unsloth/x", Path("/m/unsloth/x"), "x-Q4", fetch=fetch)
            self.assertEqual((d.path, d.size, d.sha256), ("mmproj-BF16.gguf", 1000, sha))
            self.assertEqual(d.target.parent, Path("/m/unsloth/x"))
            self.assertTrue(d.target.name.startswith("mmproj-x-Q4"))
            odd = frame_check.hf_mmproj("unsloth/x", Path("/m/unsloth/x"), "../../.bashrc\n[x]", fetch=fetch)
            self.assertEqual(odd.target.parent, Path("/m/unsloth/x"))          # a section name cannot leave it
            self.assertNotIn("/", odd.target.name.removesuffix(".gguf").replace("-", ""))
            self.assertNotIn("\n", odd.target.name)
        self.assertIsNone(frame_check.hf_mmproj("unsloth/x", Path("/m/unsloth/x"), "x-Q4", fetch=fetch))  # not ours

    def test_only_own_plain_vision_files_and_one_line_paths(self):
        import frame_check
        text = "[M]\nctx-size = 1\n"
        self.assertIsNone(frame_check.ini_with_mmproj(text, "M", Path("/m/x\n[evil]\nmodel = /tmp/y.gguf")))
        self.assertIsNone(frame_check.ini_with_mmproj(text, "M", Path("relative.gguf")))
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder, "pub", "repo")
            repo.mkdir(parents=True)
            model = repo / "m-Q4.gguf"
            model.write_bytes(b"x")
            elsewhere = Path(folder, "planted.gguf")
            elsewhere.write_bytes(b"x")
            (repo / "mmproj-F16.gguf").symlink_to(elsewhere)                  # a link is not taken
            self.assertIsNone(frame_check.local_mmproj(model))
            (repo / "mmproj-F16.gguf").unlink()
            (repo / "mmproj-F16.gguf").write_bytes(b"x")
            self.assertIsNotNone(frame_check.local_mmproj(model))
            repo.chmod(0o777)                                                 # others can write there
            try:
                self.assertIsNone(frame_check.local_mmproj(model))
            finally:
                repo.chmod(0o755)

    def test_pi_gets_pictures_and_the_thinking_switch_once(self):
        import frame_check
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(frame_check, "BACKUPS", Path(folder, "backups")):
            path = Path(folder, "models.json")
            path.write_text(json.dumps({"providers": {"llama-local": {"models": [
                {"id": "Gemma", "input": ["text"]}, {"id": "Other"}]}}}))
            self.assertTrue(frame_check.fix_pi("llamacpp", "gemma", path, switch=lambda _m: True))
            data = json.loads(path.read_text())["providers"]["llama-local"]["models"]
            self.assertEqual(data[0]["input"], ["text", "image"])
            self.assertTrue(data[0]["reasoning"])
            self.assertEqual(data[0]["compat"]["thinkingFormat"], "chat-template")
            self.assertNotIn("input", data[1])
            self.assertFalse(frame_check.fix_pi("llamacpp", "gemma", path, switch=lambda _m: True))   # done already
            self.assertTrue(any(Path(folder, "backups").iterdir()))

    def test_no_thinking_switch_where_the_template_has_none(self):
        import frame_check
        entry = {"id": "m"}
        self.assertTrue(frame_check.pi_entry_fixed(entry, False))
        self.assertNotIn("compat", entry)
        self.assertNotIn("reasoning", entry)

    def test_the_test_picture_is_a_png(self):
        import frame_check
        with tempfile.TemporaryDirectory() as folder:
            path = frame_check.probe_png(os.path.join(folder, "p.png"))
            self.assertEqual(agent_run.check_image(path), "")
            self.assertTrue(Path(path).read_bytes().startswith(b"\x89PNG"))

    def test_an_agent_on_a_cloud_model_is_not_checked(self):
        import frame_check
        from types import SimpleNamespace
        with mock.patch.object(frame_check.gpu_check, "agent_model", return_value=None):
            self.assertEqual(frame_check.check(SimpleNamespace(name="Claude", command="claude")).state, "cloud")

    def test_a_model_without_a_vision_file_offers_the_download(self):
        import frame_check
        from types import SimpleNamespace
        agent = SimpleNamespace(name="Pi", command="pi")
        offer = frame_check.Download("u/r", "mmproj-BF16.gguf", 2 * 1024 ** 3, "a" * 64, Path("/m/x"), "M")
        with mock.patch.object(frame_check.local_hold, "sees_pictures", return_value=False), \
                mock.patch.object(frame_check, "section_of", return_value="M"), \
                mock.patch.object(frame_check, "model_file", return_value=Path("/m/u/r/M.gguf")), \
                mock.patch.object(frame_check, "local_mmproj", return_value=None), \
                mock.patch.object(frame_check, "hf_mmproj", return_value=offer):
            result = frame_check._check(agent, "llamacpp", "M")
        self.assertEqual(result.state, "download")
        self.assertIs(result.download, offer)

    def test_ollama_without_vision_is_told(self):
        import frame_check
        from types import SimpleNamespace
        hold = mock.MagicMock()
        hold.__enter__.return_value.error = ""
        with mock.patch.object(frame_check.local_hold, "Hold", return_value=hold), \
                mock.patch.object(frame_check.local_hold, "sees_pictures", return_value=False):
            result = frame_check._check(SimpleNamespace(name="Pi", command="pi"), "ollama", "llama3")
        self.assertEqual(result.state, "no")
        self.assertIn("Ollama", result.message)

    def test_the_answer_to_the_test_picture_decides(self):
        import frame_check
        from types import SimpleNamespace
        hold = mock.MagicMock()
        hold.__enter__.return_value.error = ""
        agent = SimpleNamespace(name="Pi", command="pi")
        for text, state in (("Red.", "ready"), ("I cannot see images.", "no")):
            with mock.patch.object(frame_check.local_hold, "Hold", return_value=hold), \
                    mock.patch.object(frame_check.local_hold, "sees_pictures", return_value=True), \
                    mock.patch.object(frame_check, "fix_pi", return_value=False), \
                    mock.patch.object(frame_check, "probe", return_value=agent_run.Result(text=text, seconds=3)):
                self.assertEqual(frame_check._check(agent, "llamacpp", "M").state, state, text)


class AnalyzeTest(unittest.TestCase):
    """The Analyze tab: an agent's models from its own lists, and one card per capability."""

    PI_LIST = (b"provider     model                          context  max-out  thinking  images\n"
               b"llama-local  gemma-4-26B-A4B-it-UD-Q4_K_M   98.3K    8.2K     yes       yes   \n"
               b"llama-local  qwen3-tts-12hz-0.6b-base-q8_0  131.1K   8.2K     no        no    \n"
               b"anthropic    claude-sonnet-5                1M       64K      yes       yes   \n")

    def test_pi_lists_its_models_with_what_they_can(self):
        import analyze
        found = analyze.pi_models(runner=lambda *_a, **_k: (self.PI_LIST, True))
        self.assertEqual([c.arg for c in found], ["llama-local/gemma-4-26B-A4B-it-UD-Q4_K_M",
                                                  "llama-local/qwen3-tts-12hz-0.6b-base-q8_0",
                                                  "anthropic/claude-sonnet-5"])
        self.assertEqual(found[0].local, ("llamacpp", "gemma-4-26B-A4B-it-UD-Q4_K_M"))
        self.assertEqual((found[0].images, found[0].thinking, found[0].context), (True, True, 98300))
        self.assertEqual((found[1].images, found[1].thinking), (False, False))
        self.assertIsNone(found[2].local)                     # online
        self.assertEqual(found[2].context, 1_000_000)

    def test_codex_models_come_with_their_input_kinds_and_levels(self):
        import analyze
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, ".codex").mkdir()
            Path(folder, ".codex/models_cache.json").write_text(json.dumps({"models": [
                {"slug": "gpt-a", "display_name": "GPT A", "visibility": "list", "input_modalities": ["text", "image"],
                 "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}], "context_window": 272000},
                {"slug": "hidden", "visibility": "hide"},
                {"slug": "gpt-b", "visibility": "list", "input_modalities": ["text"]}]}))
            found = analyze.codex_models(Path(folder))
        self.assertEqual([c.arg for c in found], ["gpt-a", "gpt-b"])
        self.assertEqual((found[0].images, found[0].levels, found[0].context), (True, ["low", "high"], 272000))
        self.assertFalse(found[1].images)

    def run_analysis(self, answers, agent=None, choice=None, takes=True):
        import analyze
        from types import SimpleNamespace
        agent = agent or SimpleNamespace(name="Claude Code", command="claude")
        choice = choice or analyze.Choice("Sonnet", "sonnet")
        found = {}
        asked = []

        def ask(command, prompt, images=(), **kw):
            asked.append((prompt[:20], kw.get("model")))
            return answers.pop(0)
        with mock.patch.object(analyze.agent_run, "ask", side_effect=ask), \
                mock.patch.object(analyze.agent_run, "takes_images", return_value=takes), \
                mock.patch.object(analyze.frame_check, "probe", side_effect=lambda c, model="": answers.pop(0)):
            analyze.run(agent, choice, lambda f: found.__setitem__(f.key, f))
        return found, asked

    def test_every_card_gets_a_finding(self):
        import analyze
        found, asked = self.run_analysis([agent_run.Result(text="391", seconds=2, cost=0.01),
                                          agent_run.Result(text="Red", seconds=3),
                                          agent_run.Result(text="e4\nThe centre.", seconds=4)])
        self.assertEqual(set(found), {key for key, _t, _m in analyze.CARDS})
        self.assertEqual({k: f.state for k, f in found.items()},
                         {"text": "yes", "pictures": "yes", "video": "yes", "thinking": "yes", "chess": "yes",
                          "context": "unknown", "making": "no"})
        self.assertTrue(all(model == "sonnet" for _p, model in asked))       # the chosen model each time
        self.assertIn("$0.0100", found["text"].summary)

    def test_no_text_answer_skips_the_rest(self):
        found, _asked = self.run_analysis([agent_run.Result(error="not signed in")])
        self.assertEqual(found["text"].state, "no")
        self.assertEqual((found["pictures"].state, found["chess"].state), ("skipped", "skipped"))

    def test_an_agent_without_pictures_cannot_read_videos(self):
        from types import SimpleNamespace
        found, _asked = self.run_analysis([agent_run.Result(text="391"), agent_run.Result(text="Nf3")],
                                          agent=SimpleNamespace(name="Hermes", command="hermes"), takes=False)
        self.assertEqual((found["pictures"].state, found["video"].state), ("no", "no"))

    def test_a_slow_picture_answer_is_marked(self):
        found, _asked = self.run_analysis([agent_run.Result(text="391"), agent_run.Result(text="red", seconds=30),
                                           agent_run.Result(text="d4")])
        self.assertEqual(found["video"].state, "slow")


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
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("_", "N_", "ngettext") \
                        and node.args and isinstance(node.args[0], ast.Constant) \
                        and isinstance(node.args[0].value, str):
                    plural = node.func.id == "ngettext"
                    ids.add((i18n.PLURAL_PREFIX if plural else "") + node.args[0].value)
        for code in ("de", "es", "fr", "it", "nl", "pl", "pt"):
            missing = ids - set(i18n.STRINGS[code])
            self.assertFalse(missing, (code, missing))


if __name__ == "__main__":
    unittest.main()

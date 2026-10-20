"""Reading a game from video frames: links, positions in answers, moves from position changes,
and the saved games. No video, agent or download is started."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if not (ROOT / "ai_core.py").is_file():
    sys.path.insert(0, str(ROOT.parent))           # Asterism's playground/ folder: the core is one up

import chess_rules as rules  # noqa: E402
import chess_video as cv  # noqa: E402


def board_after(*sans):
    pos = rules.start()
    for text in sans:
        pos = rules.apply(pos, rules.parse(pos, text))
    return pos.board


class LinkTest(unittest.TestCase):
    def test_only_youtube_over_https(self):
        self.assertEqual(cv.youtube_url("look: https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s"),
                         "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s")
        self.assertEqual(cv.youtube_url("https://youtu.be/dQw4w9WgXcQ"), "https://youtu.be/dQw4w9WgXcQ")
        for bad in ("http://youtube.com/watch?v=dQw4w9WgXcQ", "https://evil.com/watch?v=dQw4w9WgXcQ",
                    "https://youtube.com.evil.com/watch?v=abcdefg", "--exec rm https://youtu.be/",
                    "https://www.youtube.com/watch?v=dQw4w9WgXcQ;rm"):
            self.assertEqual(cv.youtube_url(bad), "", bad)

    def test_download_argv(self):
        seen = {}

        def run(argv, max_bytes, timeout, cwd):
            seen["argv"] = argv
            Path(cwd, "A_game.mp4").write_bytes(b"x")
            return f"{cwd}/A_game.mp4\n".encode(), True
        with tempfile.TemporaryDirectory() as folder:
            from unittest import mock
            with mock.patch("shutil.which", return_value="/usr/bin/yt-dlp"):
                path, title = cv.download("https://youtu.be/dQw4w9WgXcQ", folder, runner=run)
            self.assertEqual((Path(path).name, title), ("A_game.mp4", "A game"))
        argv = seen["argv"]
        self.assertIn("--ignore-config", argv)
        self.assertIn("--no-exec", argv)
        self.assertEqual(argv[-2:], ["--", "https://youtu.be/dQw4w9WgXcQ"])

    def test_download_outside_the_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            from unittest import mock
            with mock.patch("shutil.which", return_value="/usr/bin/yt-dlp"):
                path, why = cv.download("https://youtu.be/dQw4w9WgXcQ", folder,
                                        runner=lambda *a, **k: (b"/etc/passwd\n", True))
            self.assertEqual(path, "")


class ReadingTest(unittest.TestCase):
    def test_placement_in_an_answer(self):
        start = rules.start().board
        self.assertEqual(cv.placement("`rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR`"), start)
        self.assertEqual(cv.placement("Here: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"), start)
        self.assertIsNone(cv.placement("NONE"))
        self.assertEqual(cv.placement("r n b q k b n r / p p p p p p p p / 8 / 8 / 8 / 8 / P P P P P P P P / "
                                      "R N B Q K B N R w KQkq - 0 1"), start)
        self.assertIsNone(cv.placement("8/8/8/8/8/8/8/8"))                       # no kings

    def test_moves_from_positions(self):
        reading = cv.Reading()
        for board in (rules.start().board, rules.start().board, board_after("e4"), board_after("e4", "e5", "Nf3"),
                      board_after("e4", "e5", "Nf3"), rules.start().board, board_after("e4", "e5", "Nf3", "Nc6")):
            cv.follow(reading, board)
        self.assertEqual([m.uci() for m in reading.moves], ["e2e4", "e7e5", "g1f3", "b8c6"])
        self.assertEqual(reading.unmatched, 1)                  # the start position again (an analysis board)

    def test_mid_game_with_black_to_move(self):
        reading = cv.Reading()
        cv.follow(reading, board_after("d4"))
        cv.follow(reading, board_after("d4", "d5"))
        self.assertEqual([m.uci() for m in reading.moves], ["d7d5"])
        self.assertEqual(reading.start.turn, "b")


class FrameTest(unittest.TestCase):
    def test_a_moved_piece_counts_noise_does_not(self):
        still = bytes([100] * 4096)
        noisy = bytes(100 + (i % 7) for i in range(4096))                      # compression noise
        moved = bytes([100] * 4000 + [200] * 20 + [100] * 76)                  # one piece somewhere else
        self.assertFalse(cv.changed(still, noisy))
        self.assertTrue(cv.changed(still, moved))
        self.assertTrue(cv.changed(b"", still))                                # the first frame


class StoreTest(unittest.TestCase):
    def test_save_load_replay(self):
        reading = cv.Reading()
        for board in (rules.start().board, board_after("e4"), board_after("e4", "c5")):
            cv.follow(reading, board)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "games.json")
            cv.save_games([cv.game_record("Sicilian", "https://youtu.be/x", reading)], path)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            games = cv.load_games(path)
        start, moves = cv.replay(games[0])
        self.assertEqual((games[0]["title"], [m.uci() for m in moves]), ("Sicilian", ["e2e4", "c7c5"]))
        self.assertEqual(start, rules.start())
        self.assertEqual(cv.replay({"title": "x", "start": "bad", "moves": []}), (None, []))


class ManyGamesTest(unittest.TestCase):
    def games(self):
        reading = cv.Reading()
        for board in (rules.start().board, board_after("e4"), board_after("e4", "c5")):
            cv.follow(reading, board)
        return [cv.game_record("Carlsen vs Nakamura, blitz", "https://youtu.be/abc", reading),
                cv.game_record("Sicilian lesson", "/videos/sicilian.mp4", reading)]

    def test_search_rename_delete_same_source(self):
        games = self.games()
        self.assertEqual([g["title"] for g in cv.matching(games, "nakamura BLITZ")], ["Carlsen vs Nakamura, blitz"])
        self.assertEqual(len(cv.matching(games, "")), 2)
        first = games[0]["id"]
        self.assertEqual(cv.renamed(games, first, "  Blitz   game ")[0]["title"], "Blitz game")
        self.assertEqual(cv.renamed(games, first, "  ")[0]["title"], "Carlsen vs Nakamura, blitz")
        self.assertEqual([g["title"] for g in cv.without(games, first)], ["Sicilian lesson"])
        self.assertIs(cv.same_source(games, "/videos/sicilian.mp4"), games[1])
        self.assertIsNone(cv.same_source(games, ""))

    def test_ids_are_given_to_old_games(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "games.json")
            path.write_text('[{"title": "old", "start": "%s", "moves": ["e2e4"]}]' % rules.START_FEN)
            self.assertRegex(cv.load_games(path)[0]["id"], r"^[0-9a-f]{32}$")

    def test_pgn(self):
        text = cv.pgn(self.games()[0])
        self.assertIn('[Event "Carlsen vs Nakamura, blitz"]', text)
        self.assertIn('[Site "https://youtu.be/abc"]', text)
        self.assertTrue(text.rstrip().endswith("1. e4 c5 *"))
        self.assertNotIn("[FEN", text)
        mid = cv.Reading()
        cv.follow(mid, board_after("d4"))
        cv.follow(mid, board_after("d4", "d5"))
        game = cv.game_record('A "quoted" title', "x", mid)
        text = cv.pgn(game)
        self.assertIn('[SetUp "1"]', text)
        self.assertIn("[Event \"A 'quoted' title\"]", text)
        self.assertIn("1... d5", text)


class DuplicateTest(unittest.TestCase):
    def test_one_video_many_links(self):
        key = cv.video_key("https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(key, "youtube:dQw4w9WgXcQ")
        for link in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
                     "https://www.youtube.com/shorts/dQw4w9WgXcQ"):
            self.assertEqual(cv.video_key(link), key, link)
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder, "game.mp4")
            video.write_bytes(b"x")
            os.symlink(video, Path(folder, "link.mp4"))
            self.assertEqual(cv.video_key(str(Path(folder, "link.mp4"))), cv.video_key(str(video)))
        games = [{"id": "a" * 32, "source": "https://youtu.be/dQw4w9WgXcQ", "title": "x", "start": "s", "moves": []}]
        self.assertIs(cv.same_source(games, "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1s"), games[0])

    def test_same_moves_are_one_game(self):
        first = cv.played_record("A", rules.start(), [rules.parse(rules.start(), "e4")])
        second = cv.played_record("B", rules.start(), [rules.parse(rules.start(), "e4")], source="https://youtu.be/xyzxyz1")
        self.assertIs(cv.duplicate_of([first], second), first)
        self.assertIsNone(cv.duplicate_of([first], first))                     # itself: an update
        other = cv.played_record("C", rules.start(), [rules.parse(rules.start(), "d4")])
        self.assertIsNone(cv.duplicate_of([first], other))


class FastWayTest(unittest.TestCase):
    def test_moves_in_a_description(self):
        text = ("Great game! Timestamps 0:00 intro 3:12 opening\n[Event \"Europe\"]\n"
                "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4.Nxd4 Nf6 5. Nc3 a6 6. Be3 e5 7. Nb3 1-0\nFollow me on Twitch")
        _start, moves = cv.moves_in_text(text)
        self.assertEqual([m.uci() for m in moves][:4], ["e2e4", "c7c5", "g1f3", "d7d6"])
        self.assertEqual(len(moves), 13)
        self.assertEqual(cv.moves_in_text("No moves here, 1. just talk")[1], [])
        self.assertEqual(len(cv.moves_in_text("1.e4 e5 2.Qh5 Nc6 3.Bc4 Nf6 4.Qxf7#")[1]), 7)

    def test_caption_choice_and_text(self):
        info = {"language": "de", "subtitles": {"live_chat": [{"ext": "json", "url": "x"}]},
                "automatic_captions": {"en": [{"ext": "json3", "url": "https://www.youtube.com/api/en"}],
                                       "de-orig": [{"ext": "json3", "url": "https://www.youtube.com/api/de"}]}}
        self.assertEqual(cv.caption_track(info), ("json3", "https://www.youtube.com/api/de"))
        events = {"events": [{"segs": [{"utf8": "Springer"}, {"utf8": " f3"}]}, {"segs": [{"utf8": "Springer f3"}]},
                             {"segs": [{"utf8": "[Musik]"}]}, {"segs": [{"utf8": "Läufer c4"}]}]}
        text = cv.caption_text(info, fetch=lambda url, **k: json.dumps(events).encode())
        self.assertEqual(text, "Springer f3 Läufer c4")
        bad = {"automatic_captions": {"de-orig": [{"ext": "json3", "url": "https://evil.example/x"}]}, "language": "de"}
        self.assertEqual(cv.caption_text(bad, fetch=lambda url, **k: b"{}"), "")

    def test_video_info_argv(self):
        seen = {}

        def run(argv, max_bytes, timeout):
            seen["argv"] = argv
            return b'{"title": "T", "description": "1. e4"}', True
        from unittest import mock
        with mock.patch("shutil.which", return_value="/usr/bin/yt-dlp"):
            info = cv.video_info("https://youtu.be/dQw4w9WgXcQ", runner=run)
        self.assertEqual(info["title"], "T")
        self.assertIn("--skip-download", seen["argv"])
        self.assertIn("--ignore-config", seen["argv"])
        self.assertEqual(seen["argv"][-2:], ["--", "https://youtu.be/dQw4w9WgXcQ"])


class PlayedTest(unittest.TestCase):
    def test_played_game_saved_and_updated(self):
        pos, moves = rules.start(), []
        for text in ("f3", "e5", "g4", "Qh4"):                         # fool's mate
            move = rules.parse(pos, text)
            moves.append(move)
            pos = rules.apply(pos, move)
        self.assertEqual(cv.result_of(pos), "0-1")
        self.assertEqual(cv.result_of(rules.start()), "*")
        game = cv.played_record("You – Claude Code", rules.start(), moves[:2], "You", "Claude Code")
        games = cv.stored([], game)
        again = cv.played_record("You – Claude Code", rules.start(), moves, "You", "Claude Code", "0-1",
                                 game_id=game["id"])
        games = cv.stored(games, again)
        self.assertEqual(len(games), 1)
        self.assertEqual((len(games[0]["moves"]), games[0]["result"], games[0]["kind"]), (4, "0-1", "played"))
        text = cv.pgn(games[0])
        self.assertIn('[White "You"]', text)
        self.assertIn('[Black "Claude Code"]', text)
        self.assertIn('[Result "0-1"]', text)
        self.assertTrue(text.rstrip().endswith("Qh4# 0-1"))


if __name__ == "__main__":
    unittest.main()

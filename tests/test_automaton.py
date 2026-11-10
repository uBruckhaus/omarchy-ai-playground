"""The Automaton tab's engine (the same cases as Maprisma Playground's automaton_test.dart)."""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from automaton import MIXER_NAME, NAMED_RULES, PATTERNS, Automaton, Rule, find_pattern, is_materials_rule, rule_name  # noqa: E402
from automaton_info import PATTERN_INFO, RULE_INFO  # noqa: E402


def grid(rows, rule="B3/S23", wrap=False):
    a = Automaton(len(rows[0]), len(rows), Rule.parse(rule), wrap=wrap)
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            if c == "O":
                a.set(x, y, 1)
    return a


class RuleTest(unittest.TestCase):
    def test_parses_bs_and_old_notation(self):
        self.assertEqual(Rule.parse("B3/S23").notation, "B3/S23")
        self.assertEqual(Rule.parse("b36/s23").notation, "B36/S23")
        self.assertEqual(Rule.parse("23/3").notation, "B3/S23")
        self.assertEqual(Rule.parse("B2/S").notation, "B2/S")
        self.assertEqual(Rule.parse("B2/S/C3").states, 3)

    def test_refuses_what_is_not_a_rule(self):
        for bad in ("", "B9/S23", "hello", "B3/S23/C1", "B3/S23/C99"):
            with self.assertRaises(ValueError, msg=bad):
                Rule.parse(bad)

    def test_named_rules(self):
        for name, text in NAMED_RULES.items():
            self.assertEqual(rule_name(Rule.parse(text)), name)
        self.assertIsNone(rule_name(Rule.parse("B1/S1")))


class LifeTest(unittest.TestCase):
    def test_blinker_turns(self):
        a = grid([".....", ".....", ".OOO.", ".....", "....."])
        a.step()
        self.assertEqual(a.rows, [".....", "..O..", "..O..", "..O..", "....."])
        a.step()
        self.assertEqual(a.rows, [".....", ".....", ".OOO.", ".....", "....."])
        self.assertEqual(a.generation, 2)
        self.assertEqual(a.history, [3, 3])

    def test_block_stays(self):
        a = grid(["....", ".OO.", ".OO.", "...."])
        self.assertFalse(a.step())
        self.assertEqual(a.population, 4)

    def test_glider_wraps_around(self):
        a = Automaton(8, 8, Rule.parse("B3/S23"))
        a.stamp(PATTERNS[0], 0, 0)
        start = a.rows
        for _ in range(32):
            a.step()
        self.assertEqual(a.rows, start)

    def test_dead_edges_stop_a_glider(self):
        a = Automaton(6, 6, Rule.parse("B3/S23"), wrap=False)
        a.stamp(PATTERNS[0], 2, 2)
        for _ in range(40):
            a.step()
        self.assertEqual(a.population, 4)


class MoreTest(unittest.TestCase):
    def test_brians_brain(self):
        a = grid(["....", ".OO.", "....", "...."], rule="B2/S/C3")
        a.step()
        self.assertEqual(a.rows, [".OO.", ".22.", ".OO.", "...."])
        a.step()
        self.assertEqual(a.cell(1, 1), 0)
        self.assertEqual(a.cell(1, 0), 2)

    def test_fewer_states_clear_dying_cells(self):
        a = grid(["....", ".OO.", "....", "...."], rule="B2/S/C3")
        a.step()
        a.set_rule(Rule.parse("B3/S23"))
        self.assertNotIn("2", "".join(a.rows))

    def test_resize_keeps_top_left(self):
        a = grid(["O..", ".O.", "..O"])
        a.resize(2, 4)
        self.assertEqual(a.rows, ["O.", ".O", "..", ".."])

    def test_randomize_and_clear(self):
        a = Automaton(20, 20, Rule.parse("B3/S23"))
        a.randomize(.3, seed=1)
        b = Automaton(20, 20, Rule.parse("B3/S23"))
        b.randomize(.3, seed=1)
        self.assertEqual(a.rows, b.rows)
        self.assertTrue(60 <= a.population <= 180)
        a.clear()
        self.assertTrue(a.empty)
        self.assertEqual(a.generation, 0)

    def test_patterns_well_formed(self):
        for p in PATTERNS:
            self.assertTrue(list(p.cells()), p.name)
            self.assertTrue(all(set(r) <= {".", "O"} for r in p.rows), p.name)
        gun = next(p for p in PATTERNS if p.name == "Gosper glider gun")
        self.assertEqual(len(list(gun.cells())), 36)


class AgeTest(unittest.TestCase):
    def test_block_grows_old_blinker_stays_young_and_leaves_traces(self):
        block = grid(["....", ".OO.", ".OO.", "...."])
        self.assertEqual(block.age(1, 1), 1)
        for _ in range(5):
            block.step()
        self.assertEqual(block.age(1, 1), 6)
        blinker = grid([".....", ".....", ".OOO.", ".....", "....."])
        blinker.step()
        self.assertEqual(blinker.age(2, 2), 2)
        self.assertEqual(blinker.age(2, 1), 1)
        self.assertTrue(blinker.just_died(1, 2))
        self.assertFalse(blinker.just_died(2, 2))
        blinker.step()
        self.assertFalse(blinker.just_died(1, 2))

    def test_draw_erase_clear(self):
        a = Automaton(4, 4, Rule.parse("B3/S23"))
        a.set(1, 1, 1)
        self.assertEqual(a.age(1, 1), 1)
        a.set(1, 1, 0)
        self.assertEqual(a.age(1, 1), 0)
        a.randomize(.5, seed=2)
        a.clear()
        self.assertEqual(sum(a.ages), 0)


class InfoTest(unittest.TestCase):
    def test_every_scenario_is_explained_and_can_be_tried(self):
        self.assertTrue(set(NAMED_RULES) | {"own", MIXER_NAME} <= set(RULE_INFO))
        self.assertTrue({p.name for p in PATTERNS} | {"draw"} <= set(PATTERN_INFO))
        for info in list(RULE_INFO.values()) + list(PATTERN_INFO.values()):
            self.assertTrue(info["what"] and info["watch"] and info["try"])
            setup = info["setup"]
            if "rule" in setup:
                Rule.parse(setup["rule"])
            if "pattern" in setup:
                self.assertIsNotNone(find_pattern(setup["pattern"]), setup["pattern"])
            self.assertFalse("density" in setup and "pattern" in setup)
            self.assertTrue(info["links"])
            for _title, url in info["links"]:
                self.assertTrue(url.startswith("https://en.wikipedia.org/wiki/"))


class MixerTest(unittest.TestCase):
    def test_layers_double_until_one_cell_thin(self):
        a = Automaton(16, 16, Rule.parse("B3/S23"))
        a.mixing = True
        a.fill_halves()
        self.assertEqual(a.layers, 2)
        for n in (4, 8, 16):
            self.assertTrue(a.step())
            self.assertEqual(a.layers, n)
            self.assertEqual(a.population, 128)
        self.assertFalse(a.step())
        self.assertEqual(a.layers, 16)

    def test_materials_rules(self):
        for name in ("Plastics: demixing of a blend", "Plastics: crystallization", "Adhesive: curing"):
            self.assertEqual(rule_name(Rule.parse(NAMED_RULES[name])), name)
            self.assertTrue(is_materials_rule(name))


if __name__ == "__main__":
    unittest.main()

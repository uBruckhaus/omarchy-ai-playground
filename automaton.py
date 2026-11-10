"""The Automaton tab's engine: a grid of cells and a Life-like rule (the same engine as Maprisma
Playground's automaton.dart).

A rule is written B<born>/S<survive>, optionally /C<states> for "Generations" rules (Brian's Brain
is B2/S/C3): a live cell that does not survive starts dying and goes through the states 2 … C-1
before it is dead; only state 1 counts as a live neighbour. Pure Python, no GTK.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

_RULE = re.compile(r"^B([0-8]*)/S([0-8]*)(?:/C([0-9]{1,2}))?$", re.IGNORECASE)
_OLD_RULE = re.compile(r"^([0-8]*)/([0-8]+)$")


@dataclass(frozen=True)
class Rule:
    born: frozenset
    survive: frozenset
    states: int = 2

    def __post_init__(self):
        if not 2 <= self.states <= 25:
            raise ValueError("2 to 25 states")

    @staticmethod
    def parse(text: str) -> "Rule":
        """"B3/S23", "b36/s23", "B2/S/C3"; also the old "23/3" (survive/born). ValueError when not."""
        t = (text or "").replace(" ", "")
        m = _RULE.match(t)
        if m:
            return Rule(frozenset(int(c) for c in m[1]), frozenset(int(c) for c in m[2]),
                        int(m[3]) if m[3] else 2)
        m = _OLD_RULE.match(t)
        if m:
            return Rule(frozenset(int(c) for c in m[2]), frozenset(int(c) for c in m[1]))
        raise ValueError("Not a rule: write it as B3/S23 (born with / survive with neighbours).")

    @property
    def notation(self) -> str:
        born = "".join(str(n) for n in sorted(self.born))
        survive = "".join(str(n) for n in sorted(self.survive))
        return f"B{born}/S{survive}" + (f"/C{self.states}" if self.states > 2 else "")


# The rules offered by name (their usual names, not translated).
NAMED_RULES = {
    "Conway's Life": "B3/S23",
    "HighLife": "B36/S23",
    "Seeds": "B2/S",
    "Day & Night": "B3678/S34678",
    "Maze": "B3/S12345",
    "Life without Death": "B3/S012345678",
    "Replicator": "B1357/S1357",
    "Brian's Brain": "B2/S/C3",
    "Star Wars": "B2/S345/C4",
}


def rule_name(rule: Rule):
    """The name of a rule, or None for one typed by the user."""
    for name, text in NAMED_RULES.items():
        if Rule.parse(text) == rule:
            return name
    return None


@dataclass(frozen=True)
class CellPattern:
    """A pattern to stamp, as rows of "." (dead) and "O" (alive)."""
    name: str
    rows: tuple
    about: str = ""

    @property
    def width(self) -> int:
        return max(len(r) for r in self.rows)

    @property
    def height(self) -> int:
        return len(self.rows)

    def cells(self):
        for y, row in enumerate(self.rows):
            for x, c in enumerate(row):
                if c == "O":
                    yield x, y


PATTERNS = (
    CellPattern("Glider", (".O.", "..O", "OOO"), "flies diagonally, 4 generations per step"),
    CellPattern("Lightweight spaceship", (".O..O", "O....", "O...O", "OOOO."), "flies sideways"),
    CellPattern("Pulsar", (
        "..OOO...OOO..", ".............", "O....O.O....O", "O....O.O....O", "O....O.O....O",
        "..OOO...OOO..", ".............", "..OOO...OOO..", "O....O.O....O", "O....O.O....O",
        "O....O.O....O", ".............", "..OOO...OOO.."), "blinks with period 3"),
    CellPattern("Gosper glider gun", (
        "........................O...........",
        "......................O.O...........",
        "............OO......OO............OO",
        "...........O...O....OO............OO",
        "OO........O.....O...OO..............",
        "OO........O...O.OO....O.O...........",
        "..........O.....O.......O...........",
        "...........O...O....................",
        "............OO......................"), "shoots a glider every 30 generations"),
    CellPattern("R-pentomino", (".OO", "OO.", ".O."), "five cells, settles after 1103 generations"),
    CellPattern("Acorn", (".O.....", "...O...", "OO..OOO"), "seven cells, 5206 generations"),
    CellPattern("Diehard", ("......O.", "OO......", ".O...OOO"), "vanishes after 130 generations"),
)

HISTORY = 200        # generations of population kept for the sparkline


class Automaton:
    """The grid and its history. Cells: 0 dead, 1 alive, 2 … states-1 dying (Generations rules)."""

    def __init__(self, width: int, height: int, rule: Rule, wrap: bool = True):
        if width < 1 or height < 1:
            raise ValueError("empty grid")
        self.width, self.height, self.rule, self.wrap = width, height, rule, wrap
        self.cells = bytearray(width * height)
        # Generations each live cell has been alive (1: just born), and where a cell just died:
        # the colours by age (what moves is young, what stands still grows old).
        self.ages = [0] * (width * height)
        self.died = bytearray(width * height)
        self.generation = 0
        self.history: list = []

    def cell(self, x: int, y: int) -> int:
        return self.cells[y * self.width + x]

    def age(self, x: int, y: int) -> int:
        return self.ages[y * self.width + x]

    def just_died(self, x: int, y: int) -> bool:
        return self.died[y * self.width + x] == 1

    def set(self, x: int, y: int, state: int) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            i = y * self.width + x
            v = max(0, min(state, self.rule.states - 1))
            if v == 1 and self.cells[i] != 1:
                self.ages[i] = 1                 # drawn: a newborn
            if v != 1:
                self.ages[i] = 0
            self.died[i] = 0
            self.cells[i] = v

    @property
    def population(self) -> int:
        return self.cells.count(1)

    @property
    def empty(self) -> bool:
        return not any(self.cells)

    def step(self) -> bool:
        """One generation; whether anything changed."""
        w, h, cells, rule = self.width, self.height, self.cells, self.rule
        nxt = bytearray(len(cells))
        ages = [0] * len(cells)
        died = bytearray(len(cells))
        old_ages = self.ages
        changed = False
        for y in range(h):
            for x in range(w):
                i = y * w + x
                c = cells[i]
                if c <= 1:
                    n = 0
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dx == 0 and dy == 0:
                                continue
                            nx, ny = x + dx, y + dy
                            if self.wrap:
                                nx %= w
                                ny %= h
                            elif not (0 <= nx < w and 0 <= ny < h):
                                continue
                            if cells[ny * w + nx] == 1:
                                n += 1
                    if c == 0:
                        v = 1 if n in rule.born else 0
                    else:
                        v = 1 if n in rule.survive else (2 if rule.states > 2 else 0)
                else:
                    v = 0 if c + 1 >= rule.states else c + 1
                nxt[i] = v
                if v == 1:
                    ages[i] = min(old_ages[i] + 1, 65535) if c == 1 else 1
                elif c == 1:
                    died[i] = 1
                if v != c:
                    changed = True
        self.cells, self.ages, self.died = nxt, ages, died
        self.generation += 1
        self._remember()
        return changed

    def _remember(self) -> None:
        self.history.append(self.population)
        del self.history[:-HISTORY]

    def clear(self) -> None:
        self.cells = bytearray(self.width * self.height)
        self.ages = [0] * (self.width * self.height)
        self.died = bytearray(self.width * self.height)
        self.generation = 0
        self.history.clear()

    def randomize(self, density: float, seed=None) -> None:
        r = random.Random(seed)
        self.cells = bytearray(1 if r.random() < density else 0 for _ in range(self.width * self.height))
        self.ages = list(self.cells)
        self.died = bytearray(self.width * self.height)
        self.generation = 0
        self.history = [self.population]

    def stamp(self, pattern: CellPattern, x: int, y: int) -> None:
        for px, py in pattern.cells():
            cx, cy = x + px, y + py
            if self.wrap:
                cx %= self.width
                cy %= self.height
            self.set(cx, cy, 1)

    def resize(self, width: int, height: int) -> None:
        """A new size; cells stay where they are from the top left (what falls outside is lost)."""
        if width < 1 or height < 1 or (width == self.width and height == self.height):
            return
        nxt, ages, died = bytearray(width * height), [0] * (width * height), bytearray(width * height)
        for y in range(min(self.height, height)):
            for x in range(min(self.width, width)):
                nxt[y * width + x] = self.cells[y * self.width + x]
                ages[y * width + x] = self.ages[y * self.width + x]
                died[y * width + x] = self.died[y * self.width + x]
        self.cells, self.ages, self.died, self.width, self.height = nxt, ages, died, width, height

    def set_rule(self, rule: Rule) -> None:
        """A new rule; cells in states the new rule does not have become dead."""
        self.rule = rule
        self.cells = bytearray(0 if c >= rule.states else c for c in self.cells)

    @property
    def rows(self) -> list:
        """The grid as text rows ("." dead, "O" alive, digits for dying states): for tests."""
        return ["".join("." if (c := self.cell(x, y)) == 0 else "O" if c == 1 else str(c) for x in range(self.width))
                for y in range(self.height)]

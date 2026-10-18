"""Downacross: an offline crossword toolkit driven by plain-text grid files.

A puzzle file has optional metadata lines, a [grid] section where letters are
filled cells, '#' is a black square and '.' is an empty cell, and [across] and
[down] sections of clues written as `NUMBER | clue` or `NUMBER | clue | ANSWER`.
Run `python3 downacross.py -h` for the commands.
"""

from __future__ import annotations

import argparse
import html
import random
import sys
from collections import Counter, deque
from dataclasses import dataclass, field

LETTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
BLOCK = "#"
EMPTY = "."
DIRECTIONS = ("across", "down")
METADATA_KEYS = ("title", "author", "notes")
DEFAULT_SCORE = 50
DEFAULT_MAX_NODES = 20000
NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass(frozen=True)
class Issue:
    level: str  # "error" or "warning"
    code: str
    message: str
    line: int | None = None

    def format(self, path):
        where = f"{path}:{self.line}" if self.line else str(path)
        return f"{where}: {self.level}: [{self.code}] {self.message}"


@dataclass(frozen=True)
class Clue:
    text: str
    answer: str | None
    line: int


@dataclass
class Puzzle:
    rows: list[str]
    grid_lines: list[int]
    title: str = ""
    author: str = ""
    notes: str = ""
    clues: dict = field(default_factory=lambda: {"across": {}, "down": {}})

    @property
    def height(self):
        return len(self.rows)

    @property
    def width(self):
        return len(self.rows[0]) if self.rows else 0


@dataclass(frozen=True)
class Slot:
    direction: str
    number: int
    cells: tuple

    @property
    def length(self):
        return len(self.cells)

    @property
    def label(self):
        return f"{self.number}-{self.direction.capitalize()}"

    def letters(self, rows):
        return "".join(rows[r][c] for r, c in self.cells)


@dataclass(frozen=True)
class FillResult:
    status: str  # "solved", "no-solution" or "budget"
    rows: list[str] | None
    nodes: int


class _OutOfBudget(Exception):
    pass


# ---------------------------------------------------------------- parsing

def parse_puzzle(text):
    """Parse puzzle text into a Puzzle plus a list of line-numbered Issues."""
    issues = []
    meta = {}
    section = None
    seen_sections = set()
    grid_rows, grid_lines = [], []
    clues = {"across": {}, "down": {}}

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip().lower()
            if name not in ("grid",) + DIRECTIONS:
                issues.append(Issue("error", "unknown-section", f"unknown section [{name}]", lineno))
                section = None
            elif name in seen_sections:
                issues.append(Issue("error", "duplicate-section", f"section [{name}] appears twice", lineno))
                section = None
            else:
                seen_sections.add(name)
                section = name
            continue

        if section is None:
            key, sep, value = line.partition(":")
            key = key.strip().lower()
            if seen_sections:
                issues.append(Issue("error", "syntax", "text outside a section", lineno))
            elif sep and key in METADATA_KEYS:
                meta[key] = value.strip()
            else:
                issues.append(Issue("error", "syntax", "expected 'title:', 'author:', 'notes:' or a [section]", lineno))
        elif section == "grid":
            row = "".join(line.split()).upper()
            bad = sorted({ch for ch in row if ch not in LETTERS and ch not in (BLOCK, EMPTY)})
            if bad:
                issues.append(Issue("error", "bad-char", f"invalid character(s) {' '.join(bad)} in grid row", lineno))
                row = "".join(ch if ch in LETTERS or ch in (BLOCK, EMPTY) else BLOCK for ch in row)
            grid_rows.append(row)
            grid_lines.append(lineno)
        else:
            _parse_clue_line(line, lineno, section, clues[section], issues)

    if not grid_rows:
        issues.append(Issue("error", "missing-grid", "no [grid] section or it has no rows"))
    width = max((len(row) for row in grid_rows), default=0)
    for row, lineno in zip(grid_rows, grid_lines):
        if len(row) != width:
            issues.append(Issue("error", "ragged-row", f"row has {len(row)} cells, expected {width}", lineno))
    rows = [row.ljust(width, BLOCK) for row in grid_rows]
    puzzle = Puzzle(rows, grid_lines, meta.get("title", ""), meta.get("author", ""),
                    meta.get("notes", ""), clues)
    return puzzle, issues


def _parse_clue_line(line, lineno, direction, clues, issues):
    parts = [p.strip() for p in line.split("|")]
    if len(parts) not in (2, 3) or not parts[0].isdigit() or int(parts[0]) < 1 or not parts[1]:
        issues.append(Issue("error", "clue-syntax",
                            "expected 'NUMBER | clue' or 'NUMBER | clue | ANSWER'", lineno))
        return
    number = int(parts[0])
    answer = None
    if len(parts) == 3 and parts[2]:
        answer = parts[2].upper().replace(" ", "")
        if any(ch not in LETTERS for ch in answer):
            issues.append(Issue("error", "answer-letters", f"answer {parts[2]!r} must be letters only", lineno))
            return
    if number in clues:
        issues.append(Issue("error", "duplicate-clue", f"{direction} clue {number} is already defined", lineno))
        return
    clues[number] = Clue(parts[1], answer, lineno)


# ---------------------------------------------------------------- geometry

def is_open(rows, r, c):
    return 0 <= r < len(rows) and 0 <= c < len(rows[r]) and rows[r][c] != BLOCK


def number_cells(rows):
    """Standard crossword numbering: cells that start an across or down entry, in reading order."""
    numbers = {}
    for r in range(len(rows)):
        for c in range(len(rows[r])):
            if not is_open(rows, r, c):
                continue
            starts_across = not is_open(rows, r, c - 1) and is_open(rows, r, c + 1)
            starts_down = not is_open(rows, r - 1, c) and is_open(rows, r + 1, c)
            if starts_across or starts_down:
                numbers[(r, c)] = len(numbers) + 1
    return numbers


def _run(rows, r, c, dr, dc):
    cells = []
    while is_open(rows, r, c):
        cells.append((r, c))
        r += dr
        c += dc
    return tuple(cells)


def find_slots(rows):
    """Every across and down entry (two or more cells), sorted across-then-down."""
    slots = []
    for (r, c), number in number_cells(rows).items():
        if not is_open(rows, r, c - 1) and is_open(rows, r, c + 1):
            slots.append(Slot("across", number, _run(rows, r, c, 0, 1)))
        if not is_open(rows, r - 1, c) and is_open(rows, r + 1, c):
            slots.append(Slot("down", number, _run(rows, r, c, 1, 0)))
    return sorted(slots, key=lambda s: (DIRECTIONS.index(s.direction), s.number))


def count_regions(rows):
    """Number of edge-connected groups of open cells (1 means the grid is connected)."""
    seen = set()
    regions = 0
    for r in range(len(rows)):
        for c in range(len(rows[r])):
            if not is_open(rows, r, c) or (r, c) in seen:
                continue
            regions += 1
            seen.add((r, c))
            queue = deque([(r, c)])
            while queue:
                cr, cc = queue.popleft()
                for dr, dc in NEIGHBOURS:
                    nxt = (cr + dr, cc + dc)
                    if is_open(rows, *nxt) and nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
    return regions


def merged_rows(puzzle):
    """Grid letters with any clue answers written into the empty cells they cover."""
    grid = [list(row) for row in puzzle.rows]
    for slot in find_slots(puzzle.rows):
        clue = puzzle.clues[slot.direction].get(slot.number)
        if clue and clue.answer and len(clue.answer) == slot.length:
            for (r, c), ch in zip(slot.cells, clue.answer):
                if grid[r][c] == EMPTY:
                    grid[r][c] = ch
    return ["".join(row) for row in grid]


# ---------------------------------------------------------------- linting

def check_puzzle(puzzle):
    """Return structural and clue problems as Issues, each anchored to a grid line where possible."""
    rows = puzzle.rows
    if not rows or puzzle.width == 0:
        return [Issue("error", "empty-grid", "grid has no cells")]
    issues = []
    h, w = puzzle.height, puzzle.width
    line_of = puzzle.grid_lines.__getitem__

    for r in range(h):
        for c in range(w):
            mr, mc = h - 1 - r, w - 1 - c
            if (r, c) < (mr, mc) and is_open(rows, r, c) != is_open(rows, mr, mc):
                issues.append(Issue("warning", "symmetry",
                                    f"cell {r + 1},{c + 1} and its 180° partner {mr + 1},{mc + 1} differ",
                                    line_of(r)))

    slots = find_slots(rows)
    cell_slots = {}
    for slot in slots:
        for cell in slot.cells:
            cell_slots.setdefault(cell, []).append(slot)
    for r in range(h):
        for c in range(w):
            if not is_open(rows, r, c):
                continue
            owners = cell_slots.get((r, c), [])
            if not owners:
                issues.append(Issue("error", "isolated-cell",
                                    f"cell {r + 1},{c + 1} is in no across or down entry", line_of(r)))
            elif len(owners) == 1:
                issues.append(Issue("warning", "unchecked-cell",
                                    f"cell {r + 1},{c + 1} is only checked by {owners[0].label}", line_of(r)))

    regions = count_regions(rows)
    if regions > 1:
        issues.append(Issue("warning", "disconnected", f"open cells form {regions} separate regions"))

    by_key = {(s.direction, s.number): s for s in slots}
    for direction in DIRECTIONS:
        clues = puzzle.clues[direction]
        for slot in slots:
            if slot.direction == direction and slot.number not in clues:
                r0 = slot.cells[0][0]
                issues.append(Issue("warning", "missing-clue", f"{slot.label} has no clue", line_of(r0)))
        for number, clue in clues.items():
            slot = by_key.get((direction, number))
            if slot is None:
                issues.append(Issue("error", "orphan-clue",
                                    f"{direction} clue {number} does not match any entry", clue.line))
                continue
            if not clue.answer:
                continue
            if len(clue.answer) != slot.length:
                issues.append(Issue("error", "answer-length",
                                    f"{slot.label}: answer has {len(clue.answer)} letters, entry has {slot.length}",
                                    clue.line))
                continue
            for pos, ((r, c), ch) in enumerate(zip(slot.cells, clue.answer), start=1):
                grid_ch = rows[r][c]
                if grid_ch in LETTERS and grid_ch != ch:
                    issues.append(Issue("error", "answer-conflict",
                                        f"{slot.label}: letter {pos} is {ch} in the clue but {grid_ch} in the grid",
                                        clue.line))
                    break

    first_slot = {}
    for slot in slots:
        letters = slot.letters(rows)
        if EMPTY in letters:
            continue
        if letters in first_slot:
            issues.append(Issue("warning", "duplicate-answer",
                                f"{letters} appears in {first_slot[letters].label} and {slot.label}",
                                line_of(slot.cells[0][0])))
        else:
            first_slot[letters] = slot
    return issues


# ---------------------------------------------------------------- statistics

def grid_stats(puzzle):
    rows = puzzle.rows
    cells = puzzle.height * puzzle.width
    blocks = sum(row.count(BLOCK) for row in rows)
    slots = find_slots(rows)
    lengths = [s.length for s in slots]
    letter_counts = Counter(ch for row in rows for ch in row if ch in LETTERS)
    return {
        "size": f"{puzzle.width}x{puzzle.height}",
        "open cells": cells - blocks,
        "black squares": blocks,
        "black %": round(100 * blocks / cells, 1) if cells else 0.0,
        "filled cells": sum(letter_counts.values()),
        "across entries": sum(s.direction == "across" for s in slots),
        "down entries": sum(s.direction == "down" for s in slots),
        "mean entry length": round(sum(lengths) / len(lengths), 2) if lengths else 0,
        "longest entry": max(lengths, default=0),
        "entry lengths": ", ".join(f"{n}:{k}" for n, k in sorted(Counter(lengths).items())) or "none",
        "top letters": " ".join(f"{ch}:{k}" for ch, k in letter_counts.most_common(5)) or "none",
    }


# ---------------------------------------------------------------- word lists and filling

def load_wordlist(text):
    """Parse `WORD` or `WORD SCORE` lines into a {word: score} dict plus Issues."""
    scores, issues = {}, []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        word = parts[0].upper()
        if len(word) < 2 or any(ch not in LETTERS for ch in word) or len(parts) > 2:
            issues.append(Issue("warning", "word-syntax",
                                f"skipped {line!r}: use WORD or WORD SCORE, 2+ letters A-Z", lineno))
            continue
        try:
            score = int(parts[1]) if len(parts) == 2 else DEFAULT_SCORE
        except ValueError:
            issues.append(Issue("warning", "word-syntax", f"score for {word} is not a number", lineno))
            continue
        if word not in scores or score > scores[word]:
            scores[word] = score
    return scores, issues


class Lexicon:
    """Indexed word list: candidates for a pattern come back best-score-first, ties shuffled by seed."""

    def __init__(self, scores, seed=0, min_score=0):
        rng = random.Random(seed)
        words = sorted(w for w, s in scores.items() if s >= min_score)
        rng.shuffle(words)
        words.sort(key=lambda w: -scores[w])
        self.rank = {w: i for i, w in enumerate(words)}
        self.by_length = {}
        self.index = {}
        for word in words:
            n = len(word)
            self.by_length.setdefault(n, []).append(word)
            for pos, ch in enumerate(word):
                self.index.setdefault((n, pos, ch), set()).add(word)

    def candidates(self, pattern, used=frozenset()):
        length = len(pattern)
        fixed = []
        for pos, ch in enumerate(pattern):
            if ch != EMPTY:
                bucket = self.index.get((length, pos, ch))
                if not bucket:
                    return []
                fixed.append(bucket)
        if fixed:
            base = min(fixed, key=len)
            pool = [w for w in base if all(w in bucket for bucket in fixed)]
            pool.sort(key=self.rank.__getitem__)
        else:
            pool = self.by_length.get(length, [])
        return [w for w in pool if w not in used]


def coverage(puzzle, lexicon):
    """For each entry, its current pattern and how many words in the list fit it."""
    rows = merged_rows(puzzle)
    report = []
    for slot in find_slots(puzzle.rows):
        pattern = slot.letters(rows)
        report.append((slot, pattern, len(lexicon.candidates(pattern))))
    return report


def solve(puzzle, lexicon, max_nodes=DEFAULT_MAX_NODES):
    """Backtracking fill that always extends the entry with the fewest candidates."""
    grid = [list(row) for row in merged_rows(puzzle)]
    slots = find_slots(puzzle.rows)

    def read(slot):
        return "".join(grid[r][c] for r, c in slot.cells)

    used = {read(s) for s in slots if EMPTY not in read(s)}
    touching = {}
    for slot in slots:
        for cell in slot.cells:
            touching.setdefault(cell, []).append(slot)
    nodes = 0

    def search():
        nonlocal nodes
        open_slots = [s for s in slots if EMPTY in read(s)]
        if not open_slots:
            return True
        best, best_words = None, None
        for slot in open_slots:
            options = lexicon.candidates(read(slot), used)
            if not options:
                return False
            if best_words is None or len(options) < len(best_words):
                best, best_words = slot, options
        for word in best_words:
            nodes += 1
            if nodes > max_nodes:
                raise _OutOfBudget
            placed = []
            for (r, c), ch in zip(best.cells, word):
                if grid[r][c] == EMPTY:
                    grid[r][c] = ch
                    placed.append((r, c))
            used.add(word)
            # Entries this placement just completed crossed the new letters, so they must
            # also be real, unused words from the list.
            closed = {s for r, c in placed for s in touching[(r, c)]
                      if s != best and EMPTY not in read(s)}
            closed_words = []
            valid = True
            for slot in closed:
                crossed = read(slot)
                if crossed not in lexicon.rank or crossed in used:
                    valid = False
                    break
                used.add(crossed)
                closed_words.append(crossed)
            if valid and search():
                return True
            for crossed in closed_words:
                used.discard(crossed)
            used.discard(word)
            for r, c in placed:
                grid[r][c] = EMPTY
        return False

    try:
        found = search()
    except _OutOfBudget:
        return FillResult("budget", None, nodes)
    if found:
        return FillResult("solved", ["".join(row) for row in grid], nodes)
    return FillResult("no-solution", None, nodes)


# ---------------------------------------------------------------- output

def serialize(puzzle, rows):
    """Write a puzzle file for `rows`, adding each entry's answer to its clue when it is complete."""
    solved = {(s.direction, s.number): s for s in find_slots(rows)}
    lines = [f"{key}: {value}" for key, value in
             (("title", puzzle.title), ("author", puzzle.author), ("notes", puzzle.notes)) if value]
    if lines:
        lines.append("")
    lines += ["[grid]", *rows]
    for direction in DIRECTIONS:
        lines += ["", f"[{direction}]"]
        for number in sorted(puzzle.clues[direction]):
            clue = puzzle.clues[direction][number]
            answer = clue.answer
            slot = solved.get((direction, number))
            if slot is not None and EMPTY not in slot.letters(rows):
                answer = slot.letters(rows)
            entry = f"{number} | {clue.text}"
            if answer:
                entry += f" | {answer}"
            lines.append(entry)
    return "\n".join(lines) + "\n"


def render_text(rows):
    """Box-drawn grid: numbers on the top line of each cell, letters (or '.') below."""
    numbers = number_cells(rows)
    width = len(rows[0]) if rows else 0
    border = "+" + "+".join("---" for _ in range(width)) + "+"
    out = [border]
    for r, row in enumerate(rows):
        num_line, let_line = "|", "|"
        for c, ch in enumerate(row):
            if ch == BLOCK:
                num_line += "###|"
                let_line += "###|"
            else:
                n = numbers.get((r, c))
                num_line += f"{n:<3}|" if n else "   |"
                let_line += f" {ch} |"
        out += [num_line, let_line, border]
    return "\n".join(out)


def render_clues(puzzle):
    out = []
    for direction in DIRECTIONS:
        out.append(direction.upper())
        slots = {s.number: s for s in find_slots(puzzle.rows) if s.direction == direction}
        for number in sorted(puzzle.clues[direction]):
            clue = puzzle.clues[direction][number]
            length = slots[number].length if number in slots else "?"
            out.append(f"  {number}. {clue.text} ({length})")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


_HTML_STYLE = """
body { font-family: Georgia, serif; margin: 2rem; color: #111; }
table.grid { border-collapse: collapse; margin-bottom: 1.5rem; }
table.grid td { width: 2.2em; height: 2.2em; border: 1px solid #333; position: relative;
  text-align: center; font-size: 1.25em; font-family: monospace; }
table.grid td.block { background: #111; }
table.grid .n { position: absolute; top: 1px; left: 3px; font-size: .5em; }
.clues { display: flex; gap: 3rem; }
.clues ol { padding-left: 1.5rem; }
@media print { body { margin: 0.5in; } }
"""


def render_html(puzzle):
    numbers = number_cells(puzzle.rows)
    body_rows = []
    for r, row in enumerate(puzzle.rows):
        cells = []
        for c, ch in enumerate(row):
            if ch == BLOCK:
                cells.append('<td class="block"></td>')
                continue
            n = numbers.get((r, c))
            num = f'<span class="n">{n}</span>' if n else ""
            letter = "" if ch == EMPTY else ch
            cells.append(f"<td>{num}{letter}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    slots = {(s.direction, s.number): s for s in find_slots(puzzle.rows)}
    columns = []
    for direction in DIRECTIONS:
        items = []
        for number in sorted(puzzle.clues[direction]):
            clue = puzzle.clues[direction][number]
            slot = slots.get((direction, number))
            length = f" ({slot.length})" if slot else ""
            items.append(f"<li value=\"{number}\">{html.escape(clue.text)}{length}</li>")
        columns.append(f"<div><h2>{direction.capitalize()}</h2><ol>{''.join(items)}</ol></div>")

    title = html.escape(puzzle.title or "Crossword")
    author = f"<p>by {html.escape(puzzle.author)}</p>" if puzzle.author else ""
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{title}</title>\n<style>{_HTML_STYLE}</style>\n</head>\n<body>\n"
        f"<h1>{title}</h1>\n{author}\n"
        f"<table class=\"grid\">{''.join(body_rows)}</table>\n"
        f"<div class=\"clues\">{''.join(columns)}</div>\n</body>\n</html>\n"
    )


# ---------------------------------------------------------------- command line

def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _load_puzzle(path):
    return parse_puzzle(_read(path))


def _print_issues(issues, path):
    for issue in sorted(issues, key=lambda i: (i.line or 0, i.level != "error")):
        print(issue.format(path))


def cmd_check(args):
    puzzle, issues = _load_puzzle(args.file)
    issues += check_puzzle(puzzle)
    _print_issues(issues, args.file)
    errors = sum(i.level == "error" for i in issues)
    warnings = len(issues) - errors
    print(f"{errors} error(s), {warnings} warning(s)")
    if errors or (args.strict and warnings):
        return 1
    return 0


def cmd_show(args):
    puzzle, issues = _load_puzzle(args.file)
    _print_issues([i for i in issues if i.level == "error"], args.file)
    print(render_text(puzzle.rows))
    print()
    print(render_clues(puzzle), end="")
    return 0


def cmd_stats(args):
    puzzle, _ = _load_puzzle(args.file)
    for key, value in grid_stats(puzzle).items():
        print(f"{key}: {value}")
    return 0


def _load_lexicon(args):
    scores, issues = load_wordlist(_read(args.words))
    _print_issues(issues, args.words)
    return Lexicon(scores, seed=getattr(args, "seed", 0), min_score=args.min_score)


def cmd_coverage(args):
    puzzle, _ = _load_puzzle(args.file)
    lexicon = _load_lexicon(args)
    report = coverage(puzzle, lexicon)
    for slot, pattern, count in report:
        print(f"{slot.label:<12} {pattern:<14} {count} candidate(s)")
    empty = sum(1 for _, _, count in report if count == 0)
    print(f"{len(report)} entries, {empty} with no candidates")
    return 1 if empty else 0


def cmd_fill(args):
    puzzle, issues = _load_puzzle(args.file)
    issues += check_puzzle(puzzle)
    blocking = [i for i in issues if i.level == "error"]
    if blocking:
        _print_issues(blocking, args.file)
        print("fill needs a grid without errors; run `check` first", file=sys.stderr)
        return 1

    lexicon = _load_lexicon(args)
    for slot, pattern, count in coverage(puzzle, lexicon):
        if count == 0:
            print(f"warning: {slot.label} ({slot.length}) pattern {pattern} has no candidates")

    result = solve(puzzle, lexicon, max_nodes=args.max_nodes)
    if result.status == "budget":
        print(f"stopped after {result.nodes} placements; raise --max-nodes to keep searching")
        return 1
    if result.status == "no-solution":
        print(f"no fill exists with this word list ({result.nodes} placements tried)")
        return 1
    print(render_text(result.rows))
    print(f"solved in {result.nodes} placement(s), seed {args.seed}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(serialize(puzzle, result.rows))
        print(f"wrote {args.out}")
    return 0


def cmd_html(args):
    puzzle, _ = _load_puzzle(args.file)
    document = render_html(puzzle)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(document)
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(document)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="downacross", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="lint a puzzle file with file:line messages")
    p.add_argument("file")
    p.add_argument("--strict", action="store_true", help="treat warnings as failures")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("show", help="print the grid with numbers and the clue lists")
    p.add_argument("file")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("stats", help="grid statistics: black %, entry lengths, letter counts")
    p.add_argument("file")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("coverage", help="how many words in a list fit each entry")
    p.add_argument("file")
    p.add_argument("--words", required=True, help="word list file")
    p.add_argument("--min-score", type=int, default=0)
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("fill", help="fill the grid from a word list")
    p.add_argument("file")
    p.add_argument("--words", required=True, help="word list file")
    p.add_argument("--seed", type=int, default=0, help="tie-break order among equal scores")
    p.add_argument("--max-nodes", type=int, default=DEFAULT_MAX_NODES)
    p.add_argument("--min-score", type=int, default=0)
    p.add_argument("-o", "--out", help="write the solved puzzle file here")
    p.set_defaults(func=cmd_fill)

    p = sub.add_parser("html", help="render a printable single-file HTML page")
    p.add_argument("file")
    p.add_argument("-o", "--out", help="output path (default: stdout)")
    p.set_defaults(func=cmd_html)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

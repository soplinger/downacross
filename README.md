# Downacross

**An offline crossword toolkit: check your grid, number it, fill it from a word list, and print it, all from one plain-text file.**

Downacross reads a crossword as a readable `.dac` text file. It lints the grid and clues with `file:line` messages, works out the standard numbering and every entry, reports which entries your word list can fill, runs a backtracking fill, and renders the result as text or a printable HTML page. It uses only the Python 3 standard library, makes no network calls, and needs no accounts.

## Features

- **Readable puzzle format.** Metadata lines, a `[grid]` of letters, `#` for black squares and `.` for empty cells, and `[across]` / `[down]` clue sections.
- **Line-numbered parsing.** Bad characters, ragged grid rows, malformed clues, unknown or repeated sections and non-letter answers are all reported with the line they came from.
- **Standard numbering.** Cells are numbered in reading order exactly as published crosswords number them.
- **Entry extraction.** Every across and down entry, with its cells and length.
- **Symmetry check.** Flags grids that are not 180° rotationally symmetric.
- **Cell checks.** Finds isolated cells (in no entry) and unchecked cells (in only one entry).
- **Connectivity check.** Warns when the open cells split into separate regions.
- **Clue cross-check.** Missing clues, orphan clues, answers of the wrong length, and clue answers that contradict letters already in the grid.
- **Duplicate answers.** Flags the same completed word in two entries.
- **Word-list coverage.** Shows, for each entry, its current pattern and how many words in your list fit it, and warns about entries with no candidates.
- **Backtracking fill.** Always extends the entry with the fewest candidates. Clue answers are pinned in place before the search starts.
- **Fill controls.** Scored word lists (`WORD 80`), a minimum score, a seed to vary tie-breaking, and a node budget so long searches stop cleanly.
- **Save the solution.** `fill -o` writes a complete puzzle file with each clue's answer filled in.
- **Text view.** A box-drawn grid with numbers, followed by clue lists with enumerations such as `(5)`.
- **Grid statistics.** Open and black squares, black percentage, entry counts and lengths, and the most common letters.
- **Printable HTML.** A single self-contained page with the grid and two clue columns. All clue text is HTML-escaped.
- **CI-friendly exit codes.** `check` exits 1 on errors, and with `--strict` also on warnings. `fill` and `coverage` exit 1 when no solution is found or entries have no candidates.

## Quick start

The repository includes a 3×3 word square in `examples/`.

```text
$ python3 downacross.py check examples/square.dac
0 error(s), 0 warning(s)

$ python3 downacross.py show examples/square.dac        # grid with numbers, then clues

$ python3 downacross.py coverage examples/square.dac --words examples/square-words.txt

$ python3 downacross.py fill examples/square.dac --words examples/square-words.txt -o solved.dac
```

A puzzle file looks like this:

```text
title: Square One
author: Example

[grid]
...
...
...

[across]
1 | Furious
4 | Mined metal
5 | Writing tool

[down]
1 | Swab the floor
2 | Plural of is
3 | Animal's lair
```

A word list is one word per line, with an optional score. Higher scores are tried first:

```text
MAD 80
ORE 60
PEN 70
```

To pin an answer, add it as a third field, for example `1 | Furious | MAD`. The fill treats it as a fixed entry.

## Commands

| Command | What it does |
| --- | --- |
| `check FILE [--strict]` | Lint the puzzle; exit 1 on errors (or warnings with `--strict`) |
| `show FILE` | Print the numbered grid and the clue lists |
| `stats FILE` | Print grid statistics |
| `coverage FILE --words WORDS [--min-score N]` | Show how many words fit each entry |
| `fill FILE --words WORDS [--seed N] [--max-nodes N] [--min-score N] [-o OUT]` | Fill the grid and optionally save the solved file |
| `html FILE [-o OUT]` | Render a printable HTML page (stdout if no `-o`) |

Run `python3 downacross.py <command> -h` for the full options.

## Requirements

- Python 3.10 or newer (standard library only)
- `pytest`, only if you want to run the test suite

## Running

```bash
python3 downacross.py check examples/square.dac
python3 downacross.py html examples/square.dac -o square.html
```

## Testing

```bash
pip install pytest
python3 -m pytest tests
```

The tests cover parsing and line numbers, numbering, symmetry, cell and clue checks, the word-list parser, the lexicon and its candidate filtering, the solver (solved, no solution, budget, clue pins and seeds), coverage, serialization, HTML escaping, statistics, and the command-line exit codes.

## Project layout

```text
downacross.py          the whole toolkit: parser, linter, solver, renderers, CLI
examples/square.dac    a 3x3 example puzzle
examples/square-words.txt  its word list
tests/test_downacross.py   the test suite
```

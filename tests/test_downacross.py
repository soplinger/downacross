import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import downacross as dx  # noqa: E402

SQUARE_TEXT = (ROOT / "examples" / "square.dac").read_text(encoding="utf-8")
SQUARE_WORDS = (ROOT / "examples" / "square-words.txt").read_text(encoding="utf-8")


def parse_ok(text):
    puzzle, issues = dx.parse_puzzle(text)
    assert not [i for i in issues if i.level == "error"], issues
    return puzzle


def test_parse_reads_metadata_grid_and_clues():
    puzzle = parse_ok(SQUARE_TEXT)
    assert puzzle.title == "Square One"
    assert puzzle.author == "Example"
    assert puzzle.rows == ["...", "...", "..."]
    assert puzzle.clues["across"][1].text == "Furious"
    assert puzzle.clues["down"][3].text == "Animal's lair"


def test_parse_reports_bad_character_with_line_number():
    text = "[grid]\nA.#\nB?C\n[across]\n1 | x\n"
    _, issues = dx.parse_puzzle(text)
    bad = [i for i in issues if i.code == "bad-char"]
    assert len(bad) == 1
    assert bad[0].line == 3


def test_parse_flags_ragged_rows_and_unknown_sections():
    text = "[grid]\n...\n..\n[bogus]\n"
    _, issues = dx.parse_puzzle(text)
    codes = {i.code for i in issues}
    assert "ragged-row" in codes
    assert "unknown-section" in codes


def test_parse_rejects_clue_with_non_letter_answer():
    text = "[grid]\n...\n[across]\n1 | Thing | AB3\n"
    _, issues = dx.parse_puzzle(text)
    assert any(i.code == "answer-letters" and i.line == 4 for i in issues)


def test_numbering_follows_standard_rules():
    rows = ["...", ".#.", "..."]
    slots = dx.find_slots(rows)
    across = [(s.number, s.length) for s in slots if s.direction == "across"]
    down = [(s.number, s.length) for s in slots if s.direction == "down"]
    assert across == [(1, 3), (3, 3)]
    assert down == [(1, 3), (2, 3)]
    assert dx.number_cells(rows) == {(0, 0): 1, (0, 2): 2, (2, 0): 3}


def test_symmetry_warning_points_at_grid_line():
    puzzle = parse_ok("[grid]\n#..\n...\n...\n")
    issues = dx.check_puzzle(puzzle)
    sym = [i for i in issues if i.code == "symmetry"]
    assert sym and all(i.level == "warning" for i in sym)
    assert sym[0].line == 2


def test_check_flags_isolated_cell_and_missing_and_orphan_clues():
    text = "[grid]\n.#.\n###\n...\n[across]\n9 | Nowhere\n"
    puzzle = parse_ok(text)
    codes = [i.code for i in dx.check_puzzle(puzzle)]
    assert "isolated-cell" in codes
    assert "orphan-clue" in codes
    assert "missing-clue" in codes


def test_check_reports_answer_length_and_letter_conflicts():
    text = (
        "[grid]\nCAT\n...\n...\n"
        "[across]\n1 | Feline | DOG\n4 | Row two | XY\n"
    )
    puzzle = parse_ok(text)
    issues = dx.check_puzzle(puzzle)
    assert any(i.code == "answer-conflict" and i.line == 6 for i in issues)
    assert any(i.code == "answer-length" and i.line == 7 for i in issues)


def test_check_reports_duplicate_completed_answers():
    text = "[grid]\nMAD\nOOO\nMAD\n"
    puzzle = parse_ok(text)
    issues = dx.check_puzzle(puzzle)
    assert any(i.code == "duplicate-answer" for i in issues)


def test_connectivity_detects_split_grid():
    rows = ["..#..", "..#..", "#####"]
    assert dx.count_regions(rows) == 2


def test_clean_square_passes_check():
    puzzle = parse_ok(SQUARE_TEXT)
    errors = [i for i in dx.check_puzzle(puzzle) if i.level == "error"]
    assert errors == []


def test_lexicon_candidates_respect_pattern_and_used_words():
    lex = dx.Lexicon({"MAD": 10, "MAP": 20, "ORE": 5})
    assert lex.candidates("MA.") == ["MAP", "MAD"]
    assert lex.candidates("MA.", used={"MAP"}) == ["MAD"]
    assert lex.candidates("Z..") == []


def test_wordlist_parsing_scores_and_skips_bad_lines():
    scores, issues = dx.load_wordlist("mad 80\n# comment\na\nx1\nore\nore 90\n")
    assert scores == {"MAD": 80, "ORE": 90}
    assert len(issues) == 2


def test_fill_solves_square_with_valid_words():
    puzzle = parse_ok(SQUARE_TEXT)
    scores, _ = dx.load_wordlist(SQUARE_WORDS)
    result = dx.solve(puzzle, dx.Lexicon(scores))
    assert result.status == "solved"
    rows = result.rows
    words = [rows[i] for i in range(3)] + ["".join(rows[r][c] for r in range(3)) for c in range(3)]
    assert len(set(words)) == 6
    assert all(w in scores for w in words)


def test_fill_reports_no_solution_when_columns_missing():
    puzzle = parse_ok(SQUARE_TEXT)
    result = dx.solve(puzzle, dx.Lexicon({"MAD": 1, "ORE": 1, "PEN": 1}))
    assert result.status == "no-solution"


def test_fill_rejects_crossings_that_spell_non_words():
    # The only fill for this 2x2 needs column BD, which is not in the list.
    puzzle = parse_ok("[grid]\n..\n..\n")
    lexicon = dx.Lexicon({"AB": 90, "CD": 80, "AC": 70, "BX": 60})
    assert dx.solve(puzzle, lexicon).status == "no-solution"


def test_fill_stops_at_node_budget():
    puzzle = parse_ok(SQUARE_TEXT)
    scores, _ = dx.load_wordlist(SQUARE_WORDS)
    result = dx.solve(puzzle, dx.Lexicon(scores), max_nodes=1)
    assert result.status == "budget"


def test_fill_respects_clue_answers_as_pins():
    text = SQUARE_TEXT.replace("1 | Furious", "1 | Furious | BIT")
    puzzle = parse_ok(text)
    scores, _ = dx.load_wordlist(SQUARE_WORDS)
    merged = dx.merged_rows(puzzle)
    assert merged[0] == "BIT"
    assert dx.solve(puzzle, dx.Lexicon(scores)).status == "no-solution"


def test_seed_changes_order_of_equal_score_words():
    scores = {w: 50 for w in ["CAT", "BAT", "RAT", "HAT", "MAT", "VAT"]}
    orders = {tuple(dx.Lexicon(scores, seed=s).candidates("...")) for s in range(5)}
    assert len(orders) > 1
    assert dx.Lexicon(scores, seed=3).candidates("...") == dx.Lexicon(scores, seed=3).candidates("...")


def test_coverage_counts_each_entry():
    puzzle = parse_ok(SQUARE_TEXT)
    scores, _ = dx.load_wordlist(SQUARE_WORDS)
    report = dx.coverage(puzzle, dx.Lexicon(scores))
    assert len(report) == 6
    assert all(count >= 1 for _, _, count in report)


def test_serialize_round_trips_and_fills_clue_answers():
    puzzle = parse_ok(SQUARE_TEXT)
    rows = ["MAD", "ORE", "PEN"]
    text = dx.serialize(puzzle, rows)
    again = parse_ok(text)
    assert again.rows == rows
    assert again.clues["across"][1].answer == "MAD"
    assert again.clues["down"][1].answer == "MOP"
    assert again.clues["across"][1].text == "Furious"


def test_html_escapes_clue_text_and_marks_blocks():
    text = "[grid]\n#.\n..\n[across]\n1 | <b>x</b> & y\n"
    puzzle = parse_ok(text)
    page = dx.render_html(puzzle)
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in page
    assert 'class="block"' in page


def test_stats_counts_blocks_and_entries():
    puzzle = parse_ok("[grid]\n...\n.#.\n...\n")
    stats = dx.grid_stats(puzzle)
    assert stats["black squares"] == 1
    assert stats["across entries"] == 2
    assert stats["down entries"] == 2


def test_cli_check_exit_codes(tmp_path, capsys):
    good = tmp_path / "good.dac"
    good.write_text(SQUARE_TEXT, encoding="utf-8")
    bad = tmp_path / "bad.dac"
    bad.write_text("[grid]\n..\n.?\n", encoding="utf-8")
    assert dx.main(["check", str(good)]) == 0
    assert dx.main(["check", str(bad)]) == 1
    assert "bad-char" in capsys.readouterr().out


def test_cli_fill_writes_solved_file(tmp_path):
    puzzle_path = tmp_path / "square.dac"
    puzzle_path.write_text(SQUARE_TEXT, encoding="utf-8")
    words_path = tmp_path / "words.txt"
    words_path.write_text(SQUARE_WORDS, encoding="utf-8")
    out_path = tmp_path / "solved.dac"
    code = dx.main(["fill", str(puzzle_path), "--words", str(words_path), "-o", str(out_path)])
    assert code == 0
    solved, issues = dx.parse_puzzle(out_path.read_text(encoding="utf-8"))
    assert not [i for i in issues if i.level == "error"]
    assert all("." not in row for row in solved.rows)
    assert solved.clues["across"][4].answer == solved.rows[1]
    assert not [i for i in dx.check_puzzle(solved) if i.level == "error"]

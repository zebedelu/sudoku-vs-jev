"""Bridge between the Sudoku game and the Jev 1.13 (TypeSafe) decision model via OpenRouter.

Jev does not generate text: it answers typed questions (noul/choice/score) about a
state. The bridge offers every non-given cell as an option: an empty cell offers all
digits 1-9 (legal or not, on purpose) and a filled non-given cell offers an erase.
Jev's choice is applied as-is, so it can make an illegal move; showing the mistake is
the game's job, and no error is ever reported back to the model.

Use as a library:
    from jev_bridge import choose_move
    move = choose_move(numbers, fixed)
    if move:
        print(move.row, move.col, move.num)

Diagnostics:
    python jev_bridge.py --print      # only prints the payload (no network)
    python jev_bridge.py --selftest   # tests the logic offline
    python jev_bridge.py --debug      # makes a real call (needs the key)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field

try:
    import requests
except ImportError:
    requests = None


MODEL = "typesafe/jev-1.13"
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
API_KEY_ENV = "OPENROUTER_API_KEY"
ENV_FILE = ".env"


def load_env(path=None):
    """Loads variables from a .env file (KEY=VALUE) without overriding the environment.

    This lets you set OPENROUTER_API_KEY in .env instead of exporting it in the shell.
    """
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ENV_FILE)
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8-sig") as file:
            for line in file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        return True
    except OSError:
        return False


load_env()

# A TypeSafe Choice question accepts at most 255 options (see
# https://docs.typesafe.ai/primitives/choice). Above that, the model needs a
# multi-question hierarchy, which this bridge does not build. So MAX_OPTIONS is
# the budget we aim for and CHOICE_LIMIT is the model's hard cap: the payload is
# always clamped to min(MAX_OPTIONS, CHOICE_LIMIT). Lower MAX_OPTIONS for
# cheaper/faster calls; the most restricted cells (erasable cells, then empty
# ones) go first.
CHOICE_LIMIT = 255
MAX_OPTIONS = CHOICE_LIMIT

COLUMNS = "ABCDEFGHI"
MOVE_PATTERN = re.compile(r"^R([1-9])([A-I])=([0-9])$")

RETRYABLE_STATUS = {429, 500, 502, 503, 524, 529}


class JevError(Exception):
    """Generic error from the Jev bridge."""


class JevMissingKey(JevError):
    """The environment variable with the API key was not set."""


@dataclass
class JevMove:
    row: int
    col: int
    num: int
    confidence: float | None = None
    probabilities: dict = field(default_factory=dict)
    options: int = 0
    cost: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency: float = 0.0
    raw: str = ""

    @property
    def cell(self) -> str:
        return f"{COLUMNS[self.col]}{self.row + 1}"


def _is_valid(board, row, col, num):
    if num in board[row]:
        return False
    for i in range(9):
        if board[i][col] == num:
            return False
    base_row = (row // 3) * 3
    base_col = (col // 3) * 3
    for i in range(base_row, base_row + 3):
        for j in range(base_col, base_col + 3):
            if board[i][j] == num:
                return False
    return True


def compute_candidates(numbers):
    """For each empty cell, returns the list of valid digits (1-9)."""
    candidates = {}
    for r in range(9):
        for c in range(9):
            if numbers[r][c] == 0:
                candidates[(r, c)] = [
                    n for n in range(1, 10) if _is_valid(numbers, r, c, n)
                ]
    return candidates


def no_legal_moves(numbers):
    """True for a dead end: the board is not full but no empty cell has a
    legal digit left (every candidate list is empty)."""
    candidates = compute_candidates(numbers)
    if not candidates:
        return False
    return all(not options for options in candidates.values())


def available_cells(numbers, fixed=None, skip=None):
    """Playable cells with the values Jev may choose for each.

    Fixed (given) cells are never offered. An empty cell offers every digit 1-9,
    including digits that break the Sudoku rules (on purpose: Jev is allowed to
    play illegal moves). A non-empty, non-fixed cell offers only 0 (erase it).

    `fixed` marks protected cells: pass just the givens when Jev's own played
    cells must stay erasable. `skip` is a set of (row, col) cells to hide for
    this turn, used to forbid the inverse of the previous move (anti-loop).
    """
    fixed = fixed or set()
    skip = skip or set()
    available = {}
    for r in range(9):
        for c in range(9):
            if (r, c) in fixed or (r, c) in skip:
                continue
            available[(r, c)] = list(range(1, 10)) if numbers[r][c] == 0 else [0]
    return available


def _cell_label(r, c):
    return f"R{r + 1}{COLUMNS[c]}"


def _sort_cells(cells):
    # Cells with fewest options (erasable cells, then empty ones) first.
    return sorted(cells.items(), key=lambda item: (len(item[1]), item[0][0], item[0][1]))


def select_cells(cells, max_options=MAX_OPTIONS):
    """Keeps the cells that fit in the option budget, never exceeding the cap.

    The budget can be lowered per call but is always clamped to CHOICE_LIMIT,
    the model's hard limit of 255 options per Choice. Cells are added cheapest
    first (erase = 1 option, empty = 9), so as many moves as possible fit.
    """
    budget = CHOICE_LIMIT if max_options is None else min(max_options, CHOICE_LIMIT)
    selected = {}
    total = 0
    for (r, c), options in _sort_cells(cells):
        if total + len(options) > budget:
            continue
        selected[(r, c)] = options
        total += len(options)
    return selected


def build_state(cells, numbers, fixed=None, conflicts=None):
    """State sent to Jev (in English: the model's primary language)."""
    conflicts = conflicts or set()
    legal = compute_candidates(numbers)
    cell_list = []
    for (r, c), options in _sort_cells(cells):
        if options == list(range(1, 10)):
            entry = {
                "cell": _cell_label(r, c),
                "status": "empty",
                "legal_candidates": legal.get((r, c), []),
                "placeable_digits": list(options),
            }
        else:
            entry = {
                "cell": _cell_label(r, c),
                "status": "filled",
                "value": numbers[r][c],
                "action": "erase",
            }
            if (r, c) in conflicts:
                entry["conflict"] = True
        cell_list.append(entry)
    board = [
        "".join(str(numbers[r][c]) if numbers[r][c] else "." for c in range(9))
        for r in range(9)
    ]
    return {
        "task": (
            "Choose the single best move for this 9x9 Sudoku puzzle. Rows are "
            "numbered 1-9 top to bottom; columns are letters A-I left to right."
        ),
        "board": board,
        "notation": (
            "board rows are strings, '.' is an empty cell. Cell ids look like R1C: "
            "R + row (1-9) + column letter (A-I). A move option looks like R1C=5, "
            "meaning put digit 5 in row 1, column C, and R1C=0, meaning erase the "
            "number in that cell. Fixed cells are not listed and must not be changed. "
            "An empty cell lists the digits you may place; a filled cell can be erased. "
            "A filled cell marked conflict:true repeats a digit in its row, column or "
            "box and is wrong."
        ),
        "guidance": (
            "Prefer a cell whose legal_candidates has a single digit: that placement "
            "is forced (a naked single). If any filled cell is marked conflict:true, "
            "erasing one of those conflicting cells is a strong move."
        ),
        "available_cells": cell_list,
    }


def build_question(cells, max_options=MAX_OPTIONS):
    """A single Choice question with one option per offered move (<= 255)."""
    cells = select_cells(cells, max_options)
    criteria = {}
    for (r, c), options in _sort_cells(cells):
        for num in options:
            key = f"{_cell_label(r, c)}={num}"
            if num == 0:
                criteria[key] = (
                    f"Erase row {r + 1}, column {COLUMNS[c]} "
                    f"(remove the number currently in that cell)."
                )
            else:
                criteria[key] = f"Put {num} in row {r + 1}, column {COLUMNS[c]}."
    return {
        "move": {
            "type": "choice",
            "instructions": (
                "Pick exactly one move option. Choose the move a strong player "
                "would make right now."
            ),
            "criteria": criteria,
        }
    }


def build_payload(numbers, fixed=None, session_id=None, max_options=MAX_OPTIONS,
                  skip=None, conflicts=None):
    cells = select_cells(available_cells(numbers, fixed, skip), max_options)
    payload = {
        "model": MODEL,
        "state": build_state(cells, numbers, fixed, conflicts),
        "questions": build_question(cells, max_options),
    }
    if session_id:
        payload["session_id"] = session_id[:256]
    return payload


def _get_key():
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise JevMissingKey(
            f"Set {API_KEY_ENV} in the .env file (format {API_KEY_ENV}=your_key) "
            f"or as an environment variable."
        )
    return key


def _call_api(payload, key, timeout, attempts=3):
    if requests is None:
        raise JevError("The 'requests' library is not installed. Run: pip install requests")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost/sudoku-vs-jev",
        "X-Title": "Sudoku vs Jev",
    }
    last_error = None
    for attempt in range(attempts):
        try:
            response = requests.post(
                ENDPOINT, headers=headers, data=json.dumps(payload), timeout=timeout
            )
        except requests.exceptions.RequestException as error:
            last_error = JevError(f"Network failure calling Jev: {error}")
            time.sleep(0.5 * (2 ** attempt))
            continue

        if response.status_code == 200:
            try:
                return response.json()
            except ValueError as error:
                raise JevError(f"Invalid response from Jev (not JSON): {error}") from error

        if response.status_code == 401:
            raise JevMissingKey("Invalid or missing API key (401 Unauthorized).")
        if response.status_code == 402:
            raise JevError("No credits on OpenRouter (402 Payment Required).")
        if response.status_code in RETRYABLE_STATUS and attempt < attempts - 1:
            time.sleep(0.5 * (2 ** attempt))
            continue

        detail = response.text[:300]
        raise JevError(f"Jev error {response.status_code}: {detail}")

    raise last_error or JevError("Failed to call Jev after several attempts.")


def _validate_response(state, criteria, result):
    answers = result.get("answers") or {}
    answer = answers.get("move") or {}
    choice = answer.get("choice")
    if not choice or choice not in criteria:
        raise JevError(f"Jev did not return a valid move: {choice!r}")

    match = MOVE_PATTERN.match(choice)
    if not match:
        raise JevError(f"Move in unexpected format: {choice!r}")

    row = int(match.group(1)) - 1
    col = COLUMNS.index(match.group(2))
    num = int(match.group(3))

    candidates = state.get((row, col))
    if candidates is None:
        raise JevError(f"Jev chose an unavailable cell: {choice!r}")
    if num not in candidates:
        raise JevError(f"Jev chose a number that is not offered: {choice!r}")

    usage = result.get("usage") or {}
    cost = usage.get("cost")
    return row, col, num, answer, usage, cost


def request_move(numbers, fixed=None, *, session_id=None, timeout=15.0,
                 validation_attempts=2, max_options=MAX_OPTIONS, skip=None,
                 conflicts=None):
    """Calls Jev and returns a validated JevMove. Raises JevError on failure.

    `skip` hides cells for this call (anti-loop); if it would leave no move at
    all, the call is retried without it so the endgame never gets stuck.
    `conflicts` marks filled cells that break the rules so Jev can choose to
    erase them.
    """
    key = _get_key()
    cells = select_cells(available_cells(numbers, fixed, skip), max_options)
    if not cells and skip:
        skip = None
        cells = select_cells(available_cells(numbers, fixed, skip), max_options)
    if not cells:
        return None

    payload = {
        "model": MODEL,
        "state": build_state(cells, numbers, fixed, conflicts),
        "questions": build_question(cells, max_options),
    }
    if session_id:
        payload["session_id"] = session_id[:256]

    criteria = payload["questions"]["move"]["criteria"]
    options = len(criteria)

    last_error = None
    for _ in range(max(1, validation_attempts)):
        start = time.time()
        result = _call_api(payload, key, timeout)
        latency = time.time() - start
        try:
            row, col, num, answer, usage, cost = _validate_response(
                cells, criteria, result
            )
        except JevError as error:
            last_error = error
            continue

        probabilities = answer.get("probabilities") or {}
        return JevMove(
            row=row,
            col=col,
            num=num,
            confidence=answer.get("confidence"),
            probabilities=probabilities,
            options=options,
            cost=cost,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            latency=latency,
            raw=answer.get("choice", ""),
        )

    raise last_error or JevError("Jev did not return a valid move.")


def choose_move(numbers, fixed=None, **kwargs):
    """Same as request_move, but returns None on error (instead of raising)."""
    try:
        return request_move(numbers, fixed, **kwargs)
    except JevError:
        return None


def board_hash(board):
    """Stable hashable snapshot of a board, for cycle detection."""
    return tuple(tuple(row) for row in board)


def _apply(board, row, col, num):
    copy = [row_values[:] for row_values in board]
    copy[row][col] = num
    return copy


def _parse_option(key):
    match = MOVE_PATTERN.match(key)
    if not match:
        return None
    return int(match.group(1)) - 1, COLUMNS.index(match.group(2)), int(match.group(3))


def resolve_cycle(numbers, move, seen):
    """Returns a move that does not recreate an already seen board.

    `move` is applied to a copy: if the resulting board is new, `move` is
    returned unchanged. If it repeats a seen board, the options are tried in
    order of decreasing probability and the first one leading to a new board is
    returned. Returns None when every option repeats a seen board (the caller
    should then stop instead of looping).
    """
    if move is None:
        return None
    if board_hash(_apply(numbers, move.row, move.col, move.num)) not in seen:
        return move

    ranked = sorted(move.probabilities.items(), key=lambda item: item[1], reverse=True)
    for key, _ in ranked:
        parsed = _parse_option(key)
        if parsed is None:
            continue
        row, col, num = parsed
        if board_hash(_apply(numbers, row, col, num)) not in seen:
            return JevMove(
                row=row,
                col=col,
                num=num,
                confidence=move.confidence,
                probabilities=move.probabilities,
                options=move.options,
                cost=move.cost,
                input_tokens=move.input_tokens,
                output_tokens=move.output_tokens,
                latency=move.latency,
                raw=key,
            )
    return None


# --------------------------------------------------------------------------
# Diagnostics / offline tests
# --------------------------------------------------------------------------

def _example_board():
    return [
        [5, 3, 0, 0, 7, 0, 0, 0, 0],
        [6, 0, 0, 1, 9, 5, 0, 0, 0],
        [0, 9, 8, 0, 0, 0, 0, 6, 0],
        [8, 0, 0, 0, 6, 0, 0, 0, 3],
        [4, 0, 0, 8, 0, 3, 0, 0, 1],
        [7, 0, 0, 0, 2, 0, 0, 0, 6],
        [0, 6, 0, 0, 0, 0, 2, 8, 0],
        [0, 0, 0, 4, 1, 9, 0, 0, 5],
        [0, 0, 0, 0, 8, 0, 0, 7, 9],
    ]


def _cmd_print():
    board = _example_board()
    fixed = {(r, c) for r in range(9) for c in range(9) if board[r][c] != 0}
    payload = build_payload(board, fixed, session_id="sudoku-print")
    available = available_cells(board, fixed)
    offered = select_cells(available)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    options = payload["questions"]["move"]["criteria"]
    total_available = sum(len(v) for v in available.values())
    print(f"\n# available cells: {len(available)}", file=sys.stderr)
    print(f"# offered cells: {len(offered)}", file=sys.stderr)
    print(f"# options in the Choice: {len(options)} (limit {CHOICE_LIMIT})", file=sys.stderr)
    if len(options) < total_available:
        print(f"# truncated: {total_available} options were available", file=sys.stderr)
    fixed_offered = [
        key for key in options
        if MOVE_PATTERN.match(key)
        and (int(MOVE_PATTERN.match(key).group(1)) - 1, COLUMNS.index(MOVE_PATTERN.match(key).group(2))) in fixed
    ]
    print(f"# fixed cells offered (must be 0): {len(fixed_offered)}", file=sys.stderr)


def _cmd_selftest():
    board = _example_board()
    fixed = {(r, c) for r in range(9) for c in range(9) if board[r][c] != 0}
    available = available_cells(board, fixed)
    assert not (set(available) & fixed), "fixed cell offered"
    for (r, c), options in available.items():
        if board[r][c] == 0:
            assert options == list(range(1, 10)), f"empty cell must offer 1-9: {(r, c)}"
        else:
            assert options == [0], f"filled non-fixed cell must offer erase: {(r, c)}"

    offered = select_cells(available)
    question = build_question(available)
    question_options = question["move"]["criteria"]
    for key in question_options:
        m = MOVE_PATTERN.match(key)
        assert m, f"invalid key {key}"
        r, c, n = int(m.group(1)) - 1, COLUMNS.index(m.group(2)), int(m.group(3))
        assert (r, c) in offered, f"option {key} points to a cell that was not offered"
        assert n in offered[(r, c)], f"option {key} is not offered for the cell"

    assert len(question_options) <= CHOICE_LIMIT, "exceeded the 255-option limit"
    expected = sum(len(options) for options in offered.values())
    assert len(question_options) == expected, "missing or extra options"
    assert len(offered) <= len(available), "offered more cells than available"

    # The hard cap must hold even if a caller asks for more options.
    assert len(build_question(available, 100000)["move"]["criteria"]) <= CHOICE_LIMIT

    payload = build_payload(board, fixed)
    assert payload["model"] == MODEL
    assert payload["state"]["available_cells"]
    assert len(payload["state"]["available_cells"]) == len(offered), "state/question mismatch"

    # skip hides a cell for one call
    target = next(iter(available))
    assert target not in available_cells(board, fixed, skip={target})
    assert target in available_cells(board, fixed)

    # the state carries the board and the legal candidates
    state = build_state(select_cells(available), board, fixed)
    assert len(state["board"]) == 9, "board missing from state"
    assert any("legal_candidates" in entry for entry in state["available_cells"])

    # resolve_cycle keeps a fresh move and nudges away from a repeated board
    r, c = 0, 2
    assert board[r][c] == 0
    place = JevMove(row=r, col=c, num=1, probabilities={"R1C=1": 1.0})
    assert resolve_cycle(board, place, set()) is place
    after = _apply(board, r, c, 1)
    seen = {board_hash(board), board_hash(after)}
    erase = JevMove(row=r, col=c, num=0, probabilities={"R1C=0": 0.9, "R1D=1": 0.1})
    nudged = resolve_cycle(after, erase, seen)
    assert nudged is not None and nudged.raw != "R1C=0", "cycle was not nudged"
    assert board_hash(_apply(after, nudged.row, nudged.col, nudged.num)) not in seen
    stuck = JevMove(row=r, col=c, num=0, probabilities={"R1C=0": 1.0})
    assert resolve_cycle(after, stuck, seen) is None, "should report a dead end"

    # a filled cell in conflict is marked so Jev knows it can erase it
    cboard = [row[:] for row in board]
    cboard[r][c] = 1
    entries = {
        entry["cell"]: entry
        for entry in build_state(select_cells(available_cells(cboard, fixed)),
                                 cboard, fixed, conflicts={(r, c)})["available_cells"]
    }
    assert entries["R1C"].get("conflict") is True, "conflict not marked"
    clean = {
        entry["cell"]: entry
        for entry in build_state(select_cells(available_cells(cboard, fixed)),
                                 cboard, fixed, conflicts=set())["available_cells"]
    }
    assert "conflict" not in clean["R1C"], "conflict marked without a conflict"

    # dead-end detection: no empty cell has any legal digit
    assert no_legal_moves(board) is False
    dead = [[0, 1, 2, 3, 4, 5, 6, 7, 8]] + [[9] * 9 for _ in range(8)]
    assert no_legal_moves(dead) is True

    print("selftest OK: options within the limit, state context, conflicts and anti-cycle work.")


def _cmd_debug():
    board = _example_board()
    fixed = {(r, c) for r in range(9) for c in range(9) if board[r][c] != 0}
    cells = available_cells(board, fixed)
    print(f"{len(cells)} available cells; calling Jev...")
    move = request_move(board, fixed, session_id="sudoku-debug")
    if move is None:
        print("Jev did not return any move.")
        return
    print(f"move: {move.raw}  (row {move.row + 1}, column {COLUMNS[move.col]}, "
          f"number {move.num})")
    print(f"confidence: {move.confidence}")
    print(f"options: {move.options}  tokens: {move.input_tokens}/{move.output_tokens}  "
          f"cost: {move.cost}  latency: {move.latency:.2f}s")


def main(argv):
    if "--selftest" in argv:
        _cmd_selftest()
    elif "--print" in argv:
        _cmd_print()
    elif "--debug" in argv:
        _cmd_debug()
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])

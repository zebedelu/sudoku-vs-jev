"""Bridge between the Sudoku game and the Jev 1.13 (TypeSafe) decision model via OpenRouter.

Jev does not generate text: it answers typed questions (noul/choice/score) about a
state. All Sudoku logic (which digits are legal in each cell) lives here, in code;
Jev only chooses among the legal moves we offer.

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

# Maximum number of options (moves) sent to Jev. None = all cells.
# On an empty board the first move can exceed 700 options; lower it here
# if you want cheaper/faster responses (always keeping the cells with fewest
# candidates first).
MAX_OPTIONS = None

COLUMNS = "ABCDEFGHI"
MOVE_PATTERN = re.compile(r"^R([1-9])([A-I])=([1-9])$")

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


def available_cells(numbers, fixed=None):
    """Empty playable cells with their candidates. Never includes fixed cells."""
    fixed = fixed or set()
    available = {}
    for (r, c), cands in compute_candidates(numbers).items():
        if (r, c) in fixed:
            continue
        available[(r, c)] = cands
    return available


def _cell_label(r, c):
    return f"R{r + 1}{COLUMNS[c]}"


def _sort_cells(cells):
    # Most restricted cells (fewest candidates) first.
    return sorted(cells.items(), key=lambda item: (len(item[1]), item[0][0], item[0][1]))


def _select_cells(cells, max_options=MAX_OPTIONS):
    if max_options is None:
        return dict(cells)
    selected = {}
    total = 0
    for (r, c), cands in _sort_cells(cells):
        if total + len(cands) > max_options and selected:
            break
        selected[(r, c)] = cands
        total += len(cands)
    return selected


def build_state(cells):
    """State sent to Jev (in English: the model's primary language)."""
    cell_list = [
        {"cell": _cell_label(r, c), "candidates": list(cands)}
        for (r, c), cands in _sort_cells(cells)
    ]
    return {
        "task": "Choose the single best legal move for this 9x9 Sudoku puzzle.",
        "notation": (
            "Cell ids look like R1C: R + row (1-9) + column letter (A-I). "
            "A move option looks like R1C=5, meaning put digit 5 in row 1, column C. "
            "Only the cells listed as available are empty; every other cell is "
            "already fixed and must not be changed. Each available cell lists the "
            "digits that are legal for it (its candidates)."
        ),
        "guidance": (
            "Prefer a cell with few candidates, especially a cell with a single "
            "candidate, since that placement is forced."
        ),
        "available_cells": cell_list,
    }


def build_question(cells, max_options=MAX_OPTIONS):
    """A single Choice question with every legal move as an option."""
    cells = _select_cells(cells, max_options)
    criteria = {}
    for (r, c), cands in _sort_cells(cells):
        for num in cands:
            key = f"{_cell_label(r, c)}={num}"
            criteria[key] = (
                f"Put {num} in row {r + 1}, column {COLUMNS[c]} "
                f"(this cell's candidates: {', '.join(str(x) for x in cands)})"
            )
    return {
        "move": {
            "type": "choice",
            "instructions": (
                "Pick exactly one move option. Each option is a legal Sudoku move. "
                "Choose the move a strong player would make right now."
            ),
            "criteria": criteria,
        }
    }


def build_payload(numbers, fixed=None, session_id=None, max_options=MAX_OPTIONS):
    cells = available_cells(numbers, fixed)
    payload = {
        "model": MODEL,
        "state": build_state(cells),
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
        raise JevError(f"Jev chose a number that is not a candidate: {choice!r}")

    usage = result.get("usage") or {}
    cost = usage.get("cost")
    return row, col, num, answer, usage, cost


def request_move(numbers, fixed=None, *, session_id=None, timeout=15.0,
                 validation_attempts=2, max_options=MAX_OPTIONS):
    """Calls Jev and returns a validated JevMove. Raises JevError on failure."""
    key = _get_key()
    cells = available_cells(numbers, fixed)
    if not cells:
        return None

    payload = {
        "model": MODEL,
        "state": build_state(cells),
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
    cells = available_cells(board, fixed)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\n# available cells: {len(cells)}", file=sys.stderr)
    print(f"# options in the Choice: {len(payload['questions']['move']['criteria'])}", file=sys.stderr)
    fixed_offered = [
        key for key in payload["questions"]["move"]["criteria"]
        if MOVE_PATTERN.match(key)
        and (int(MOVE_PATTERN.match(key).group(1)) - 1, COLUMNS.index(MOVE_PATTERN.match(key).group(2))) in fixed
    ]
    print(f"# fixed cells offered (must be 0): {len(fixed_offered)}", file=sys.stderr)


def _cmd_selftest():
    board = _example_board()
    fixed = {(r, c) for r in range(9) for c in range(9) if board[r][c] != 0}
    cells = available_cells(board, fixed)
    assert all(board[r][c] == 0 for (r, c) in cells), "non-empty cell offered"
    assert not (set(cells) & fixed), "fixed cell offered"
    for (r, c), cands in cells.items():
        for n in cands:
            assert _is_valid(board, r, c, n), f"invalid candidate {n} at {(r, c)}"
    assert len(cells) == sum(1 for r in range(9) for c in range(9) if board[r][c] == 0)

    question = build_question(cells)
    for key in question["move"]["criteria"]:
        m = MOVE_PATTERN.match(key)
        assert m, f"invalid key {key}"
        r, c, n = int(m.group(1)) - 1, COLUMNS.index(m.group(2)), int(m.group(3))
        assert n in cells[(r, c)], f"option {key} is not a candidate of the cell"
        assert (r, c) in cells, f"option {key} points to an unavailable cell"

    payload = build_payload(board, fixed)
    assert payload["model"] == MODEL
    assert payload["state"]["available_cells"]
    print("selftest OK: cells, candidates and options consistent.")


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

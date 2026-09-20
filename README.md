# Sudoku vs Jev

A terminal Sudoku game whose real purpose is to put a model to the test: it
was built mainly to **probe the thinking of Jev**, TypeSafe's decision model,
by making it play Sudoku move by move. `sudoku.py` is a complete game you can
play yourself; `jev_bridge.py` lets Jev play instead, and `benchmark.py`
measures how well it does.

## Why this project

The goal is not to ship a Sudoku app. The goal is a clean, well-defined
playground where a model has to make one decision at a time against a state
that pure code controls:

- All the arithmetic and rule-checking (rows, columns, 3x3 boxes) stays in
  Python. The bridge offers every non-given cell; Jev may still choose an
  out-of-rules digit, and the game shows the mistake without telling the model.
- Each turn is a single, atomic question: *pick the best move from this list*.
- The result is easy to grade: either the board gets solved or it does not.

That makes it a small but honest way to observe how a decision model behaves,
where it is strong (spotting forced cells) and where it breaks down (guessing
on hard boards).

## About Jev

[Jev](https://openrouter.ai/typesafe/jev-1.13) is TypeSafe AI's flagship model
and the first **System One model**. Instead of generating text, it evaluates a
*state* against typed *questions* and returns structured, type-safe answers
with calibrated probabilities. Nothing has to be parsed out of prose.

It exposes three primitives, which can be mixed in one call:

| Primitive | Question | Returns |
|-----------|----------|---------|
| **Choice** | Pick one option from a list | `choice`, `probabilities`, `confidence` |
| **Score**  | Score the state on a rubric | `score`, `probabilities`, `confidence` |
| **Noul**   | Is this statement true? | `noul` (0–1) |

This project uses the **Choice** primitive: one option per move - a digit for
an empty cell, or an erase for a filled one - and Jev returns the move plus a
probability distribution over the alternatives.

- OpenRouter model page: <https://openrouter.ai/typesafe/jev-1.13>
- Official site: <https://typesafe.ai>
- Documentation: <https://docs.typesafe.ai>

## Requirements

- Python 3.8 or newer
- `requests` (third-party, required only for Jev mode)

Install dependencies:

```
pip install -r requirements.txt
```

## Setup

1. Get an API key at <https://openrouter.ai/settings/keys>.
2. Copy `.env.example` to `.env` and paste your key:

   ```
   OPENROUTER_API_KEY=sk-or-...
   ```

   `jev_bridge.py` loads `.env` automatically. An already-exported environment
   variable wins over the file, so you can also just `set OPENROUTER_API_KEY=...`.

Human mode works without any key.

## Running

```
python sudoku.py
```

Choose a mode:

- `1` / `Human` — you play.
- `2` / `Jev` — the model plays; you watch.

Then choose a difficulty (`1` Easy, `2` Medium, `3` Hard).

**Human controls**

| Key | Action |
|-----|--------|
| Arrows / WASD | move the cursor |
| `1`–`9` | place a number |
| `0` / Backspace | erase the cell |
| `N` / `R` | new game / clear your moves |
| `H` / `Q` | help / quit |

**Jev mode controls**

| Key | Action |
|-----|--------|
| SPACE | request the next move |
| `A` | toggle automatic play |
| `+` / `-` | adjust the delay between automatic moves |
| `N` / `R` / `H` / `Q` | new game / clear moves / help / quit |

While Jev thinks, the panel shows the number of offered cells and options (at
most 255), then the chosen move, its confidence, the top probabilities, tokens,
per-move cost and latency. Jev's own cells stay erasable; only the original
givens are protected.

## How the Jev integration works

For every turn, `jev_bridge.py`:

1. Lists every non-given cell: an empty cell offers digits 1-9 (legal or, on
   purpose, illegal) and a filled cell offers an erase (`R4C=0`). The state also
   carries the board and each cell's legal candidates, so the model can prefer
   forced cells (naked singles).
2. Builds one `Choice` question where each option is a move, e.g. `R4C=7`.
3. Sends the board state plus that question to
   `POST https://openrouter.ai/api/alpha/decisions`.
4. Applies the answer as-is. An illegal placement is kept and shown red, and no
   error is reported back to the model. Fixed cells are never offered.

Because Jev is deterministic and does not remember past turns, it could otherwise
oscillate (`place d` / `erase d` on the same cell). Two guards prevent that: the
cell played on the previous turn is hidden from the next call (`skip`), and if the
chosen move would recreate a board already seen, the game nudges Jev to its
next-best option (`resolve_cycle`). Only when every option would repeat a position
does automatic play pause. Skip is dropped while the board has conflicts, so the
cell to fix is always offered.

Cells that break the rules are marked `conflict: true` in the state, and the
guidance tells Jev to erase one of them; without that signal it had no reason to
undo. A board that fills up is not the end either: `try_move` still offers the
erasable cells, so Jev keeps trying. While conflicts remain it gets
`RECOVERY_LIMIT` (30) extra moves before the game declares a dead end; a dead end
with no conflicts (empty cells, no legal digit) pauses right away.

The model never sees the answer key and never edits a fixed cell. A TypeSafe
Choice accepts at most 255 options, so `MAX_OPTIONS` (default 255) caps how many
are sent and the payload is always clamped to that limit: cells with the fewest
options (erasable cells first) are kept, the rest are dropped for that turn and
reconsidered on later turns. Lower it for cheaper, faster calls.

## Benchmarks

`benchmark.py` plays full games with Jev, one decision per move, and reports
solve rate, latency, tokens and cost:

```
python benchmark.py --games 5 --level hard
python benchmark.py --games 3 --level easy --max-options 40
```

Results below were measured on 2026-09-19 with `typesafe/jev-1.13`, 5 games per
difficulty. "Forced" counts moves placed in a cell with a single candidate (a
naked single). Latency includes network time; output tokens are free, so cost
is input-only.

| Level  | Solved | Moves | Forced moves | Avg latency | Avg confidence | Input/output tokens | Cost |
|--------|--------|-------|--------------|-------------|----------------|---------------------|------|
| Easy   | 5/5    | 180   | 176 (98%)    | 0.56 s      | 80%            | 374,268 / 67,327    | $0.0157 |
| Medium | 5/5    | 235   | 227 (97%)    | 0.73 s      | 80%            | 746,873 / 146,296   | $0.0314 |
| Hard   | 1/5    | 252   | 189 (75%)    | 0.51 s      | 67%            | 1,157,068 / 233,663 | $0.0486 |

What the numbers say:

- **It spots forced moves.** On Easy and Medium, 97–98% of moves were naked
  singles, and every game was solved. This is exactly the behavior the bridge's
  guidance asks for, and Jev follows it without any search.
- **Hard boards expose the limit.** When no forced move exists it must guess.
  The forced share drops to 75% and games often reach a dead end, so only 1 of
  5 Hard games finished. The bridge only offers locally legal moves and Jev has
  no lookahead or backtracking, so a legal guess can still paint the board into
  a corner. This is a fair illustration of what a System One model is and is
  not meant to do.
- **Confidence tracks difficulty**, falling from ~80% on Easy/Medium to ~67% on
  Hard, which is the calibrated-uncertainty behavior TypeSafe advertises.
- **Cost is tiny.** A full game costs roughly $0.003 (Easy) to $0.010 (Hard)
  and runs at well under a second per move on average.

These are a small sample intended to characterize behavior, not a rigorous
evaluation. Run the script yourself to reproduce or extend them.

## Diagnostics

```
python jev_bridge.py --selftest   # offline logic checks
python jev_bridge.py --print      # print the payload, no network
python jev_bridge.py --debug      # one real call (needs the key)
```

## Project layout

```
sudoku.py         the game (human and Jev modes, ANSI terminal UI)
jev_bridge.py     builds the state/question, calls Jev, validates the move
benchmark.py      plays full games with Jev and reports metrics
.env.example      API key template
```

## Notes

- Windows-flavored terminal: uses raw ANSI escapes for clearing and color.
- Board state is held in memory only; there is no save/persistence.
- `R` clears only non-fixed cells, `N` regenerates the board.
- Rows and columns are 1-indexed, columns are letters `A`–`I`.

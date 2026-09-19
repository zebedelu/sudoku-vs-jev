# AGENTS.md

Terminal Sudoku game in Python. `sudoku.py` is the game; `jev_bridge.py` is the
bridge to the Jev 1.13 decision model (TypeSafe) via OpenRouter.

## Run

```
python sudoku.py
```

Choose mode `1` (Human) or `2` (Jev). The Jev mode needs `requests` and an
OpenRouter key. Put the key in `.env` (see `.env.example`) as
`OPENROUTER_API_KEY=...`; `jev_bridge.py` loads `.env` automatically, and an
already-exported environment variable wins over the file.

Only third-party dependency is `requests` (optional: Jev mode fails gracefully
without it). No requirements file, test suite, linter, or CI exists, and there
is no git repo. For verification, use `python jev_bridge.py --selftest`.

## Jev bridge

- Model: `typesafe/jev-1.13`; endpoint `POST https://openrouter.ai/api/alpha/decisions`.
- Jev is a decision model, not a chat LLM: it returns typed answers (choice/noul/score),
  never text. Sudoku arithmetic/validity stays in code; Jev only picks from legal moves.
- The bridge sends one combined `choice` question: one option per (empty cell, legal digit).
  It never offers a fixed cell, and validates the returned move before applying it.
- `MAX_OPTIONS` in `jev_bridge.py` caps options sent; `None` sends all (an empty board can
  exceed 700), so lower it for cheaper/faster calls. Cost per move is tracked and shown in-game.
- Diagnostics: `python jev_bridge.py --selftest` (offline), `--print` (payload, no network),
  `--debug` (one real call, needs the key).

## Gotchas

- Windows-flavoured terminal: uses raw ANSI escapes for clearing/color, not `os.system`.
- UI text and prompts are in English.
- Board state lives in the module-level `numbers` list and is mutated in place; there is no persistence.
  `R` clears only non-fixed cells, `N` regenerates the board.
- `fixed` holds the original givens; the game and the bridge must never overwrite those cells.
- Jev moves are added to `fixed` as they are played, so the bridge never re-offers them.
- Rows/cols are 1-indexed, columns are letters `A`-`I`; `0` removes a cell.

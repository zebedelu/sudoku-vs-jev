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
  never text. The bridge computes the options; Jev's choice is applied as-is, so it can
  play an illegal move. Validity is not enforced on Jev's choice.
- The bridge sends one combined `choice` question: an empty non-given cell offers digits
  1-9 (legal or illegal, on purpose) and a filled non-given cell offers `=0` (erase).
  Fixed cells are never offered. Erasing is legal; an out-of-rules placement is applied
  and shown red, and no error/feedback is ever sent back to Jev.
- A TypeSafe Choice accepts at most 255 options. `CHOICE_LIMIT` (hard cap) and
  `MAX_OPTIONS` (default budget) in `jev_bridge.py` enforce it: `select_cells` keeps the
  cheapest cells first and the payload never exceeds the limit. Lower `MAX_OPTIONS` for
  cheaper/faster calls. Cost per move is tracked and shown in-game.
- Anti-loop: `play_jev` hides the last played cell for one turn (`skip`) and calls
  `resolve_cycle` so Jev is nudged to its next-best option when the choice would recreate
  a board already seen; if every option repeats, automatic play pauses. `build_state`
  includes the board and each cell's `legal_candidates` so the model can prefer forced
  cells instead of nine indistinguishable options. `conflicts` is passed too, marking
  wrong filled cells with `conflict: true` so Jev can choose to erase them.
- A full board is **not** a terminal gate: `try_move` checks for any playable cell
  (empty or erasable), so Jev may keep fixing conflicts. While conflicts persist it has
  `RECOVERY_LIMIT` moves, then the game pauses as a dead end; a no-conflict dead end
  (empty cells with no legal digit) pauses immediately.
- Diagnostics: `python jev_bridge.py --selftest` (offline), `--print` (payload, no network),
  `--debug` (one real call, needs the key).

## Gotchas

- Windows-flavoured terminal: uses raw ANSI escapes for clearing/color, not `os.system`.
- UI text and prompts are in English.
- Board state lives in the module-level `numbers` list and is mutated in place; there is no persistence.
  `R` clears only non-fixed cells, `N` regenerates the board.
- `fixed` holds the original givens; the game and the bridge must never overwrite those cells.
  `available_cells(numbers, fixed)` is passed only the givens, so Jev's own played cells stay erasable.
- Rows/cols are 1-indexed, columns are letters `A`-`I`; `0` erases a cell.

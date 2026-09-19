"""Benchmark: how well and how fast does Jev 1.13 play Sudoku.

Plays full games with the model (one decision per move), then reports solve
rate, latency, tokens and cost. Needs an OPENROUTER_API_KEY (see .env.example).

Usage:
    python benchmark.py                      # 3 easy games
    python benchmark.py --games 5 --level hard
    python benchmark.py --games 2 --max-options 40
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

import jev_bridge
from sudoku import conflict_cells, generate_board


def play_game(level, max_options):
    board = generate_board(level)
    fixed = {(r, c) for r in range(9) for c in range(9) if board[r][c] != 0}

    moves = 0
    latencies = []
    confidences = []
    input_tokens = output_tokens = 0
    cost = 0.0
    forced = 0
    start = time.time()

    while any(board[r][c] == 0 for r in range(9) for c in range(9)):
        options = jev_bridge.available_cells(board, fixed)
        if not options:
            break
        result = jev_bridge.choose_move(
            board, fixed, session_id="sudoku-benchmark", max_options=max_options
        )
        if result is None:
            return {
                "level": level,
                "solved": False,
                "reason": "no valid move",
                "moves": moves,
                "seconds": time.time() - start,
                "latencies": latencies,
                "confidences": confidences,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": cost,
                "forced": forced,
            }
        moves += 1
        latencies.append(result.latency)
        if result.confidence is not None:
            confidences.append(result.confidence)
        input_tokens += result.input_tokens
        output_tokens += result.output_tokens
        if result.cost:
            cost += result.cost
        if len(options.get((result.row, result.col), [])) == 1:
            forced += 1
        board[result.row][result.col] = result.num
        fixed.add((result.row, result.col))

    solved = not conflict_cells(board) and all(
        board[r][c] != 0 for r in range(9) for c in range(9)
    )
    return {
        "level": level,
        "solved": solved,
        "reason": "solved" if solved else "dead end",
        "moves": moves,
        "seconds": time.time() - start,
        "latencies": latencies,
        "confidences": confidences,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": cost,
        "forced": forced,
    }


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=3)
    parser.add_argument("--level", choices=["easy", "medium", "hard"], default="easy")
    parser.add_argument("--max-options", type=int, default=None)
    args = parser.parse_args(argv)

    games = []
    for i in range(args.games):
        print(f"game {i + 1}/{args.games} ({args.level})...", flush=True)
        games.append(play_game(args.level, args.max_options))

    solved = sum(1 for g in games if g["solved"])
    all_latencies = [x for g in games for x in g["latencies"]]
    all_conf = [x for g in games for x in g["confidences"]]
    total_cost = sum(g["cost"] for g in games)
    total_in = sum(g["input_tokens"] for g in games)
    total_out = sum(g["output_tokens"] for g in games)
    total_forced = sum(g["forced"] for g in games)
    total_moves = sum(g["moves"] for g in games)

    print()
    print(f"games:        {len(games)}  ({args.level})")
    print(f"solved:       {solved}/{len(games)}")
    print(f"moves:        {total_moves}")
    print(f"forced moves: {total_forced}/{total_moves}"
          + (f" ({total_forced / total_moves:.0%})" if total_moves else ""))
    if all_latencies:
        print(f"latency:      avg {statistics.mean(all_latencies):.2f}s  "
              f"p50 {statistics.median(all_latencies):.2f}s  "
              f"max {max(all_latencies):.2f}s")
    if all_conf:
        print(f"confidence:   avg {statistics.mean(all_conf):.2%}")
    print(f"tokens:       {total_in} in / {total_out} out")
    print(f"cost:         ${total_cost:.5f}")
    return 0 if solved == len(games) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

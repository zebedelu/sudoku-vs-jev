import os
import sys
import time
import random

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import jev_bridge
except Exception:
    jev_bridge = None


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

FG_TITLE = "\033[1;38;5;213m"
FG_LABEL = "\033[38;5;245m"
FG_GIVEN = "\033[1;97m"
FG_USER = "\033[1;38;5;81m"
FG_ERR = "\033[1;97m"
FG_DIM = "\033[38;5;240m"
BG_ERR = "\033[48;5;160m"
BG_SEL = "\033[48;5;62m"
BG_SAME = "\033[48;5;236m"
FG_OK = "\033[1;38;5;84m"
FG_WARN = "\033[1;38;5;221m"
FG_JEV = "\033[1;38;5;214m"
FG_JEV_DIM = "\033[38;5;179m"
BG_JEV = "\033[48;5;214m\033[30m"
BG_JEV_DIM = "\033[48;5;94m\033[97m"


def enable_ansi():
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def clear_screen():
    sys.stdout.write("\033[2J\033[3J\033[H")
    sys.stdout.flush()


def hide_cursor():
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()


def show_cursor():
    sys.stdout.write("\033[?25h")
    sys.stdout.flush()


def is_valid(board, row, col, num):
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


def generate_solution():
    board = [[0] * 9 for _ in range(9)]

    def fill():
        for row in range(9):
            for col in range(9):
                if board[row][col] == 0:
                    candidates = list(range(1, 10))
                    random.shuffle(candidates)
                    for num in candidates:
                        if is_valid(board, row, col, num):
                            board[row][col] = num
                            if fill():
                                return True
                            board[row][col] = 0
                    return False
        return True

    fill()
    return board


def count_solutions(board, limit=2):
    chosen_row = chosen_col = -1
    chosen_candidates = None
    for row in range(9):
        for col in range(9):
            if board[row][col] == 0:
                candidates = [
                    num for num in range(1, 10) if is_valid(board, row, col, num)
                ]
                if not candidates:
                    return 0
                if chosen_candidates is None or len(candidates) < len(chosen_candidates):
                    chosen_candidates = candidates
                    chosen_row = row
                    chosen_col = col
        if chosen_candidates is not None and len(chosen_candidates) == 1:
            break

    if chosen_candidates is None:
        return 1

    total = 0
    for num in chosen_candidates:
        board[chosen_row][chosen_col] = num
        total += count_solutions(board, limit)
        board[chosen_row][chosen_col] = 0
        if total >= limit:
            return total
    return total


def generate_board(level):
    clues = {"easy": 45, "medium": 34, "hard": 26}
    target = clues[level]

    board = generate_solution()
    cells = [(row, col) for row in range(9) for col in range(9)]
    random.shuffle(cells)

    remaining = 81
    for row, col in cells:
        if remaining <= target:
            break
        value = board[row][col]
        board[row][col] = 0
        if count_solutions([row_copy[:] for row_copy in board]) != 1:
            board[row][col] = value
        else:
            remaining -= 1
    return board


def conflict_cells(board):
    conflicts = set()

    for r in range(9):
        seen = {}
        for c in range(9):
            v = board[r][c]
            if v:
                if v in seen:
                    conflicts.add((r, c))
                    conflicts.add((r, seen[v]))
                else:
                    seen[v] = c

    for c in range(9):
        seen = {}
        for r in range(9):
            v = board[r][c]
            if v:
                if v in seen:
                    conflicts.add((r, c))
                    conflicts.add((seen[v], c))
                else:
                    seen[v] = r

    for br in range(0, 9, 3):
        for bc in range(0, 9, 3):
            seen = {}
            for r in range(br, br + 3):
                for c in range(bc, bc + 3):
                    v = board[r][c]
                    if v:
                        if v in seen:
                            conflicts.add((r, c))
                            conflicts.add(seen[v])
                        else:
                            seen[v] = (r, c)

    return conflicts


numbers = [[0] * 9 for _ in range(9)]
fixed = set()
cursor = [0, 0]
conflicts = set()
start_time = time.time()
current_level = "easy"
message = ""
message_until = 0.0

highlight = None
jev_info = None
jev_cost = 0.0


def set_message(text, duration=2.0):
    global message, message_until
    message = text
    message_until = time.time() + duration


def format_cell(r, c):
    v = numbers[r][c]
    is_fixed = (r, c) in fixed
    is_selected = cursor[0] == r and cursor[1] == c
    is_error = (r, c) in conflicts
    current = numbers[cursor[0]][cursor[1]]

    ch = str(v) if v else " "

    if highlight is not None and highlight == (r, c):
        style = BG_JEV
    elif is_error:
        style = BG_ERR + FG_ERR
    elif is_selected:
        style = BG_SEL + BOLD + "\033[97m"
    elif is_fixed:
        style = FG_GIVEN
    else:
        style = FG_USER

    if highlight != (r, c) and not is_selected and not is_error and v and v == current:
        style = BG_SAME + style

    return f"{style} {ch} {RESET}"


def board_line(r):
    groups = []
    for g in range(3):
        groups.append("".join(format_cell(r, g * 3 + k) for k in range(3)))
    return f"{r + 1:2} {FG_DIM}│{RESET}" + f"{FG_DIM}│{RESET}".join(groups) + f"{FG_DIM}│{RESET}"


def draw(jev_mode=False):
    clear_screen()
    width = 45

    print(f"{FG_TITLE}{'═' * width}{RESET}")
    print(f"{FG_TITLE}{'S U D O K U'.center(width)}{RESET}")
    print(f"{FG_TITLE}{'═' * width}{RESET}")

    remaining = sum(1 for r in range(9) for c in range(9) if numbers[r][c] == 0)
    elapsed = int(time.time() - start_time)
    clock = f"{elapsed // 60:02}:{elapsed % 60:02}"

    names = {"easy": "Easy", "medium": "Medium", "hard": "Hard"}
    print(
        f"  {FG_LABEL}Level{RESET} {BOLD}{names.get(current_level, current_level):<8}{RESET}"
        f"  {FG_LABEL}Time{RESET} {BOLD}{clock}{RESET}"
        f"  {FG_LABEL}Left{RESET} {BOLD}{remaining}{RESET}"
    )
    print()

    cells = [f" {l} " for l in "ABCDEFGHI"]
    interior = " ".join("".join(cells[i:i + 3]) for i in range(0, 9, 3))
    print("      " + FG_LABEL + interior + RESET)

    print(f"     {FG_DIM}┌─────────┬─────────┬─────────┐{RESET}")
    for r in range(9):
        print("  " + board_line(r))
        if r in (2, 5):
            print(f"     {FG_DIM}├─────────┼─────────┼─────────┤{RESET}")
    print(f"     {FG_DIM}└─────────┴─────────┴─────────┘{RESET}")

    if jev_mode:
        if jev_info:
            draw_jev_panel(move=jev_info, cells=None)
        else:
            cells = jev_bridge.available_cells(numbers, fixed) if jev_bridge else {}
            draw_jev_panel(move=None, cells=cells)

    print()
    if message and time.time() < message_until:
        print(f"  {FG_WARN}{message}{RESET}")
    elif conflicts:
        print(f"  {BG_ERR}{FG_ERR} There is a conflict on the board! {RESET}")
    else:
        print("  " + " " * width)

    print()
    if jev_mode:
        print(
            f"  {FG_LABEL}SPACE{RESET} next move   {FG_LABEL}A{RESET} automatic   "
            f"{FG_LABEL}+/-{RESET} speed"
        )
        print(
            f"  {FG_LABEL}R{RESET} restart   {FG_LABEL}N{RESET} new game   "
            f"{FG_LABEL}H{RESET} help   {FG_LABEL}Q{RESET} quit"
        )
    else:
        print(
            f"  {FG_LABEL}arrows{RESET} move   {FG_LABEL}1-9{RESET} fill   "
            f"{FG_LABEL}0/BACKSPACE{RESET} erase"
        )
        print(
            f"  {FG_LABEL}N{RESET} new game   {FG_LABEL}R{RESET} restart   "
            f"{FG_LABEL}H{RESET} help   {FG_LABEL}Q{RESET} quit"
        )


def chance(c, p):
    return (c * 1103515245 + 12345) % 2147483648 < p * 2147483648


def confidence_bar(confidence, width=10):
    if confidence is None:
        return "─" * width
    filled = int(round(confidence * width))
    return "█" * filled + "░" * (width - filled)


def monospace(text, width):
    return f"{text:<{width}}"[:width]


def draw_jev_panel(move=None, cells=None):
    print()
    print(f"  {FG_JEV}┌─ JEV 1.13 ──────────────────────────────┐{RESET}")
    if move is None:
        lines = cells or {}
        waiting = monospace("waiting for the next move...", 39)
        print(f"  {FG_JEV_DIM}│{RESET} {FG_LABEL}{waiting}{RESET} {FG_JEV_DIM}│{RESET}")
        cells_txt = monospace(f"available cells: {len(lines)}", 39)
        print(f"  {FG_JEV_DIM}│{RESET} {BOLD}{cells_txt}{RESET} {FG_JEV_DIM}│{RESET}")
        sample = list(lines.items())[:6]
        for (r, c), cands in sample:
            txt = monospace(f"{'ABCDEFGHI'[c]}{r + 1}: {','.join(str(x) for x in cands)}", 39)
            print(f"  {FG_JEV_DIM}│{RESET} {FG_JEV_DIM}{txt}{RESET} {FG_JEV_DIM}│{RESET}")
        remaining = len(lines) - len(sample)
        if remaining > 0:
            print(f"  {FG_JEV_DIM}│{RESET} {FG_DIM}{monospace(f'... and {remaining} more', 39)}{RESET} {FG_JEV_DIM}│{RESET}")
    else:
        total = move.options or 0
        conf_text = f"{move.confidence:.0%}" if move.confidence is not None else "?"
        line1 = monospace(f"{move.raw}  conf {conf_text}", 39)
        print(f"  {FG_JEV_DIM}│{RESET} {BOLD}\033[97m{line1}{RESET} {FG_JEV_DIM}│{RESET}")
        bar = f"{FG_JEV}{confidence_bar(move.confidence, 16)}{RESET}"
        conf_text2 = f"({conf_text})"
        spaces = " " * max(0, 39 - 16 - 1 - len(conf_text2))
        print(f"  {FG_JEV_DIM}│{RESET} {bar} {FG_JEV_DIM}{conf_text2}{spaces}{RESET} {FG_JEV_DIM}│{RESET}")

        best = sorted(move.probabilities.items(), key=lambda kv: kv[1], reverse=True)[:3]
        for name, prob in best:
            prob_bar = f"{FG_JEV}{confidence_bar(prob, 8)}{RESET}"
            name_txt = monospace(name, 8)
            prob_txt = f"{prob:>4.0%}"
            pad = " " * (41 - (1 + 8 + 1 + 8 + 1 + 4))
            print(f"  {FG_JEV_DIM}│{RESET} {FG_JEV_DIM}{name_txt}{RESET} {prob_bar} "
                  f"{FG_JEV_DIM}{prob_txt}{RESET}{pad}{FG_JEV_DIM}│{RESET}")
        if not best:
            pad = " " * (41 - (1 + len("(no probabilities)")))
            print(f"  {FG_JEV_DIM}│{RESET} {FG_DIM}(no probabilities){RESET}{pad}{FG_JEV_DIM}│{RESET}")

        cost = move.cost if move.cost is not None else jev_cost
        info = monospace(f"tok {move.input_tokens}/{move.output_tokens}", 16)
        cost_txt = monospace(f"${cost:.5f}", 11)
        lat_txt = monospace(f"{move.latency:.2f}s", 6)
        pad = " " * (41 - (1 + 16 + 11 + 6 + 1))
        print(f"  {FG_JEV_DIM}│{RESET} {FG_LABEL}{info}{cost_txt}{lat_txt}{RESET}{pad} {FG_JEV_DIM}│{RESET}")
        opt_txt = f"options offered: {total}"
        print(f"  {FG_JEV_DIM}│{RESET} {FG_DIM}{monospace(opt_txt, 39)}{RESET} {FG_JEV_DIM}│{RESET}")
    print(f"  {FG_JEV}└─────────────────────────────────────────┘{RESET}")


def animate_cursor(row, col):
    while cursor[0] != row:
        cursor[0] += 1 if row > cursor[0] else -1
        draw(jev_mode=True)
        if not chance(cursor[0] + cursor[1], 0.3):
            time.sleep(0.035)
    while cursor[1] != col:
        cursor[1] += 1 if col > cursor[1] else -1
        draw(jev_mode=True)
        if not chance(cursor[1] + row, 0.7):
            time.sleep(0.035)


def blink_cell(row, col, times=3):
    global highlight
    for i in range(times):
        highlight = None if i % 2 else (row, col)
        draw(jev_mode=True)
        time.sleep(0.12)
    highlight = (row, col)


def animate_move(row, col, num):
    global highlight
    highlight = None
    animate_cursor(row, col)
    blink_cell(row, col)
    numbers[row][col] = num
    global conflicts
    conflicts = conflict_cells(numbers)
    draw(jev_mode=True)
    time.sleep(0.2)
    highlight = None


def animate_penalty(row, col):
    for _ in range(2):
        blink_cell(row, col, times=2)
        time.sleep(0.05)
    highlight = None


def help_screen():
    clear_screen()
    print(f"\n{FG_TITLE}{' HELP '.center(45, '═')}{RESET}\n")
    items = [
        ("Arrows / WASD", "move the cursor around the board"),
        ("1 to 9", "place a number in the current cell"),
        ("0 / Backspace", "erase the current cell"),
        ("N", "start a new game"),
        ("R", "clear your moves"),
        ("H", "open this help"),
        ("Q / Esc", "quit the game"),
    ]
    for key, desc in items:
        print(f"  {BOLD}{key:<14}{RESET}{FG_LABEL}{desc}{RESET}")
    print()
    print(f"  {FG_GIVEN}white numbers{RESET} {FG_LABEL}are fixed and cannot be changed{RESET}")
    print(f"  {FG_USER}blue numbers{RESET} {FG_LABEL}are your moves{RESET}")
    print(f"  {BG_ERR}{FG_ERR} red background {RESET} {FG_LABEL}marks an invalid move{RESET}")
    print()
    print(f"  {FG_JEV}Jev mode{RESET} {FG_LABEL}the model picks each move; you just watch{RESET}")
    print(f"  {BG_JEV} orange cell {RESET} {FG_LABEL}is the move chosen by Jev{RESET}")
    print()
    input(f"  {FG_LABEL}Press ENTER to go back...{RESET}")


def read_key():
    if msvcrt is None:
        return input().strip()[:1].lower()
    ch = msvcrt.getch()
    if ch in (b"\x00", b"\xe0"):
        ch2 = msvcrt.getch()
        return {
            b"H": "up",
            b"P": "down",
            b"K": "left",
            b"M": "right",
        }.get(ch2, "")
    try:
        return ch.decode("utf-8").lower()
    except UnicodeDecodeError:
        return ""


def move(direction):
    if direction == "up":
        cursor[0] = (cursor[0] - 1) % 9
    elif direction == "down":
        cursor[0] = (cursor[0] + 1) % 9
    elif direction == "left":
        cursor[1] = (cursor[1] - 1) % 9
    elif direction == "right":
        cursor[1] = (cursor[1] + 1) % 9


def won():
    if conflicts:
        return False
    return all(numbers[r][c] != 0 for r in range(9) for c in range(9))


def victory_screen():
    elapsed = int(time.time() - start_time)
    clock = f"{elapsed // 60:02}:{elapsed % 60:02}"
    print()
    print(f"  {FG_OK}CONGRATULATIONS! You completed the Sudoku!{RESET}")
    print(f"  {FG_LABEL}Level:{RESET} {BOLD}{current_level}{RESET}   "
          f"{FG_LABEL}Time:{RESET} {BOLD}{clock}{RESET}")
    print(f"  {FG_LABEL}Press any key for a new game...{RESET}")


def play(level):
    global numbers, fixed, start_time, current_level, conflicts, message

    numbers = generate_board(level)
    fixed = {(r, c) for r in range(9) for c in range(9) if numbers[r][c] != 0}
    cursor[0], cursor[1] = 0, 0
    current_level = level
    start_time = time.time()
    message = ""
    conflicts = conflict_cells(numbers)

    hide_cursor()
    try:
        while True:
            draw()
            key = read_key()

            if key in ("q", "\x1b"):
                return "quit"
            elif key in ("up", "w"):
                move("up")
            elif key in ("down", "s"):
                move("down")
            elif key in ("left", "a"):
                move("left")
            elif key in ("right", "d"):
                move("right")
            elif key == "n":
                return "new"
            elif key == "r":
                for r in range(9):
                    for c in range(9):
                        if (r, c) not in fixed:
                            numbers[r][c] = 0
                conflicts = conflict_cells(numbers)
                set_message("Moves cleared.", 1.5)
            elif key == "h":
                show_cursor()
                help_screen()
                hide_cursor()
            elif len(key) == 1 and key in "123456789":
                r, c = cursor[0], cursor[1]
                if (r, c) in fixed:
                    set_message("This cell is fixed and cannot be changed.")
                else:
                    numbers[r][c] = int(key)
                    conflicts = conflict_cells(numbers)
                    if won():
                        show_cursor()
                        draw()
                        victory_screen()
                        read_key()
                        return "new"
            elif key in ("0", "\x08", "\x7f"):
                r, c = cursor[0], cursor[1]
                if (r, c) in fixed:
                    set_message("This cell is fixed and cannot be changed.")
                else:
                    numbers[r][c] = 0
                    conflicts = conflict_cells(numbers)
    finally:
        show_cursor()


def choose_difficulty():
    options = ["easy", "medium", "hard"]
    names = ["Easy", "Medium", "Hard"]
    while True:
        clear_screen()
        print(f"\n{FG_TITLE}{'═' * 45}{RESET}")
        print(f"{FG_TITLE}{'S U D O K U'.center(45)}{RESET}")
        print(f"{FG_TITLE}{'═' * 45}{RESET}\n")
        print(f"  {FG_LABEL}Choose the difficulty:{RESET}\n")
        for i, name in enumerate(names, start=1):
            print(f"    {BOLD}{i}{RESET}  {name}")
        print()
        print(f"  {FG_LABEL}Q to quit{RESET}")
        print()
        choice = input(f"  {BOLD}> {RESET}").strip().lower()

        if choice in ("q", ""):
            return None
        if choice.isdigit() and 1 <= int(choice) <= 3:
            return options[int(choice) - 1]
        if choice in options:
            return choice


def play_jev(level):
    global numbers, fixed, start_time, current_level, conflicts, message
    global highlight, jev_info, jev_cost

    if jev_bridge is None:
        set_message("Module jev_bridge.py not found.", 3.0)
        return "menu"

    try:
        jev_bridge._get_key()
    except jev_bridge.JevMissingKey:
        no_key_screen()
        return "menu"

    numbers = generate_board(level)
    fixed = {(r, c) for r in range(9) for c in range(9) if numbers[r][c] != 0}
    cursor[0], cursor[1] = 0, 0
    current_level = level
    start_time = time.time()
    message = ""
    jev_info = None
    jev_cost = 0.0
    highlight = None
    conflicts = conflict_cells(numbers)

    def try_move():
        global jev_info, jev_cost, conflicts
        if not any(numbers[r][c] == 0 for r in range(9) for c in range(9)):
            return False
        prefix = 4
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        call_start = time.time()
        result = {}

        def call():
            result["move"] = jev_bridge.choose_move(
                numbers, fixed, session_id="sudoku-vs-jev"
            )

        import threading

        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        i = 0
        while thread.is_alive():
            cells = jev_bridge.available_cells(numbers, fixed)
            info = (
                f"now {prefix} {frames[i % len(frames)]}  thinking...   "
                f"cells: {len(cells)}   options: {sum(len(v) for v in cells.values())}   "
                f"{time.time() - call_start:4.1f}s"
            )
            draw(jev_mode=True)
            print(f"  {FG_JEV}{info}{RESET}")
            time.sleep(0.08)
            i += 1
        thread.join()
        move = result.get("move")

        if move is None:
            set_message("Jev did not return a valid move.", 3.0)
            return False

        jev_info = move
        if move.cost:
            jev_cost += move.cost
        animate_move(move.row, move.col, move.num)
        r, c = move.row, move.col
        fixed.add((r, c))
        if (r, c) in conflicts:
            set_message("Jev created a conflict! Undoing the move...", 3.0)
            global highlight
            animate_penalty(r, c)
            numbers[r][c] = 0
            fixed.discard((r, c))
            conflicts = conflict_cells(numbers)
            jev_info = None
            return False
        return True

    hide_cursor()
    delay_idx = 1
    delays = [0.0, 0.4, 1.0, 2.0]
    automatic = False
    try:
        draw(jev_mode=True)
        print()
        input(f"  {FG_LABEL}ENTER to start...{RESET}")
        while True:
            draw(jev_mode=True)
            if automatic:
                if won():
                    break
                try_move()
                if won():
                    break
                delay = delays[delay_idx]
                if delay > 0:
                    end = time.time() + delay
                    while time.time() < end:
                        draw(jev_mode=True)
                        time.sleep(0.05)
                continue

            key = read_key()
            if key in ("q", "\x1b"):
                return "quit"
            elif key in (" ", "\r", "\n"):
                if not try_move() and won():
                    break
            elif key == "a":
                automatic = not automatic
                set_message("Automatic enabled (A to pause)." if automatic
                            else "Automatic paused.", 1.5)
            elif key in ("+", "="):
                delay_idx = min(len(delays) - 1, delay_idx + 1)
                set_message(f"Delay: {delays[delay_idx]:.1f}s", 1.5)
            elif key == "-":
                delay_idx = max(0, delay_idx - 1)
                set_message(f"Delay: {delays[delay_idx]:.1f}s", 1.5)
            elif key == "n":
                return "new"
            elif key == "r":
                for r in range(9):
                    for c in range(9):
                        if (r, c) not in fixed:
                            numbers[r][c] = 0
                conflicts = conflict_cells(numbers)
                jev_info = None
                set_message("Moves cleared.", 1.5)
            elif key == "h":
                show_cursor()
                help_screen()
                hide_cursor()
        draw(jev_mode=True)
        jev_victory_screen()
        read_key()
        return "new"
    finally:
        show_cursor()


def jev_victory_screen():
    elapsed = int(time.time() - start_time)
    clock = f"{elapsed // 60:02}:{elapsed % 60:02}"
    print()
    print(f"  {FG_JEV}Jev completed the Sudoku!{RESET}")
    print(f"  {FG_LABEL}Level:{RESET} {BOLD}{current_level}{RESET}   "
          f"{FG_LABEL}Time:{RESET} {BOLD}{clock}{RESET}   "
          f"{FG_LABEL}Cost:{RESET} {BOLD}${jev_cost:.5f}{RESET}")
    print(f"  {FG_LABEL}Press any key for a new game...{RESET}")


def no_key_screen():
    clear_screen()
    print(f"\n{FG_WARN}{'═' * 45}{RESET}")
    print(f"{FG_WARN}{'OPENROUTER KEY NOT CONFIGURED'.center(45)}{RESET}")
    print(f"{FG_WARN}{'═' * 45}{RESET}\n")
    print(f"  {FG_LABEL}For Jev mode to work, do one of the following:{RESET}\n")
    print(f"  {BOLD}1){RESET} Create a {FG_JEV}.env{RESET} file in this folder with:")
    print(f"     {FG_JEV}OPENROUTER_API_KEY=sk-or-...{RESET}\n")
    print(f"  {BOLD}2){RESET} Or set the environment variable:")
    print(f"     {FG_DIM}set OPENROUTER_API_KEY=sk-or-...{RESET}\n")
    print(f"  {FG_LABEL}Get your key at:{RESET} https://openrouter.ai/keys\n")
    input(f"  {FG_LABEL}Press ENTER to go back...{RESET}")


def choose_mode():
    while True:
        clear_screen()
        print(f"\n{FG_TITLE}{'═' * 45}{RESET}")
        print(f"{FG_TITLE}{'S U D O K U  vs  J E V'.center(45)}{RESET}")
        print(f"{FG_TITLE}{'═' * 45}{RESET}\n")
        print(f"  {FG_LABEL}Choose the mode:{RESET}\n")
        print(f"    {BOLD}1{RESET}  Human  {FG_DIM}(you play){RESET}")
        print(f"    {BOLD}2{RESET}  {FG_JEV}Jev{RESET}    {FG_DIM}(the model plays){RESET}")
        print()
        print(f"  {FG_LABEL}Q to quit{RESET}")
        print()
        choice = input(f"  {BOLD}> {RESET}").strip().lower()
        if choice in ("q", ""):
            return None
        if choice in ("1", "h", "human"):
            return "human"
        if choice in ("2", "j", "jev"):
            return "jev"


def main():
    enable_ansi()
    mode = choose_mode()
    while mode is not None:
        level = choose_difficulty()
        if level is None:
            break
        clear_screen()
        print(f"\n  {FG_LABEL}Generating board...{RESET}")
        result = play(level) if mode == "human" else play_jev(level)
        if result == "quit":
            break
        if result == "menu":
            mode = choose_mode()
            continue
        level = None
    clear_screen()
    print(f"\n  {FG_TITLE}See you next time!{RESET}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        show_cursor()
        print(f"\n  {FG_TITLE}See you next time!{RESET}\n")

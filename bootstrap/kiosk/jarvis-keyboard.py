#!/usr/bin/env python3
"""Touch-first Jarvis keyboard that types directly into Chromium."""
import os
import signal
import subprocess
import tkinter as tk

BG = "#020911"
PANEL = "#071522"
KEY = "#0c2232"
KEY_ACTIVE = "#16445b"
CYAN = "#7fe9ff"
MUTED = "#7898aa"
PURPLE = "#9f83ff"
PID_FILE = "/tmp/jarvis-keyboard.pid"
TOGGLE_POLL_MS = 40

root = tk.Tk()
root.title("Jarvis Input")
root.overrideredirect(True)
root.attributes("-topmost", True)
root.configure(bg=BG, highlightbackground="#24c9ee", highlightthickness=2)
root.withdraw()

target_window = None
shifted = False
letter_buttons = []
pending_toggles = 0


def chromium_window():
    try:
        windows = subprocess.check_output(
            ["xdotool", "search", "--onlyvisible", "--class", "chromium"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).splitlines()
        return windows[-1] if windows else None
    except Exception:
        return None


def send_key(key):
    global shifted
    window = target_window or chromium_window()
    if not window:
        return
    if len(key) == 1:
        value = key.upper() if shifted and key.isalpha() else key
        subprocess.Popen([
            "xdotool", "type", "--clearmodifiers", "--delay", "0",
            "--window", str(window), value,
        ])
        if shifted:
            toggle_shift()
    else:
        subprocess.Popen([
            "xdotool", "key", "--clearmodifiers", "--window", str(window), key,
        ])


def type_text(value):
    window = target_window or chromium_window()
    if window:
        subprocess.Popen([
            "xdotool", "type", "--clearmodifiers", "--delay", "0",
            "--window", str(window), value,
        ])


def toggle_shift():
    global shifted
    shifted = not shifted
    for button_widget, letter in letter_buttons:
        button_widget.configure(text=letter.upper() if shifted else letter.lower())
    shift_button.configure(bg=PURPLE if shifted else KEY)


def hide():
    root.withdraw()


def toggle():
    global target_window
    if root.state() == "withdrawn":
        target_window = chromium_window()
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
    else:
        hide()


def button(parent, label, command, column, width=1, accent=None):
    widget = tk.Button(
        parent, text=label, command=command, bg=accent or KEY, fg=CYAN,
        activebackground=KEY_ACTIVE, activeforeground="#ffffff", bd=0,
        highlightthickness=0, font=("DejaVu Sans", 19, "bold"),
        takefocus=False,
    )
    widget.grid(
        row=0, column=column, columnspan=width,
        sticky="nsew", padx=4, pady=4,
    )
    return widget


header = tk.Frame(root, bg=PANEL, height=58)
header.pack(fill="x")
tk.Label(
    header, text="JARVIS // SECURE INPUT", bg=PANEL, fg=CYAN,
    font=("DejaVu Sans Mono", 14, "bold"), padx=18, pady=12,
).pack(side="left")
tk.Label(
    header, text="LOCAL KEYBOARD", bg=PANEL, fg=MUTED,
    font=("DejaVu Sans Mono", 10), padx=12,
).pack(side="left")
tk.Button(
    header, text="CLOSE  ×", command=hide, bg="#142536", fg=CYAN,
    activebackground="#28445a", activeforeground="white", bd=0,
    font=("DejaVu Sans Mono", 12, "bold"), padx=20, pady=12,
).pack(side="right")

body = tk.Frame(root, bg=BG, padx=12, pady=10)
body.pack(fill="both", expand=True)

rows = [
    [(char, char) for char in "1234567890"] + [("⌫", "BackSpace")],
    [(char, char) for char in "!@#$%^&*()+="],
    [(char, char) for char in "qwertyuiop"] + [("↵", "Return")],
    [(char, char) for char in "asdfghjkl"] + [("-", "-"), ("_", "_")],
    [(char, char) for char in "zxcvbnm"] + [
        ("@", "@"), (".", "."), ("/", "/"),
    ],
    [
        ("~", "~"), ("`", "`"), ("<", "<"), (">", ">"),
        ("[", "["), ("]", "]"), ("{", "{"), ("}", "}"),
        ("\\", "\\"), ("|", "|"), (":", ":"), (";", ";"),
        ("'", "'"), ('"', '"'), ("?", "?"),
    ],
]

for row_index, keys in enumerate(rows):
    row = tk.Frame(body, bg=BG)
    row.pack(fill="both", expand=True)
    for column in range(len(keys)):
        row.grid_columnconfigure(column, weight=1, uniform=f"row{row_index}")
    row.grid_rowconfigure(0, weight=1)
    for column, (label, key) in enumerate(keys):
        widget = button(row, label, lambda value=key: send_key(value), column)
        if label.isalpha() and len(label) == 1:
            letter_buttons.append((widget, label))

controls = tk.Frame(body, bg=BG)
controls.pack(fill="both", expand=True)
for column, weight in enumerate((2, 2, 7, 2, 2)):
    controls.grid_columnconfigure(column, weight=weight)
controls.grid_rowconfigure(0, weight=1)
shift_button = button(controls, "⇧ SHIFT", toggle_shift, 0)
button(controls, ".COM", lambda: type_text(".com"), 1, accent="#16233d")
button(controls, "SPACE", lambda: send_key("space"), 2, accent="#102c3d")
button(controls, "←", lambda: send_key("Left"), 3)
button(controls, "→", lambda: send_key("Right"), 4)

screen_width = root.winfo_screenwidth()
screen_height = root.winfo_screenheight()
width = int(screen_width * 0.92)
height = min(int(screen_height * 0.42), 760)
root.geometry(
    f"{width}x{height}+{(screen_width - width) // 2}"
    f"+{screen_height - height - 18}"
)

with open(PID_FILE, "w", encoding="utf-8") as pid_file:
    pid_file.write(str(os.getpid()))


def signal_toggle(_signum, _frame):
    global pending_toggles
    pending_toggles += 1


def service_toggle_requests():
    global pending_toggles
    if pending_toggles:
        pending_toggles -= 1
        toggle()
    root.after(TOGGLE_POLL_MS, service_toggle_requests)


signal.signal(signal.SIGUSR1, signal_toggle)
root.after(TOGGLE_POLL_MS, service_toggle_requests)
root.mainloop()

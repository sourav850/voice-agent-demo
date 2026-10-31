#!/usr/bin/env python3
"""
AppBot - a tiny chat bot that opens apps on your laptop.

Run:  python app_bot.py
Type things like:
    open chrome
    launch notepad and calculator
    close spotify
    go to youtube.com
    search google for best laptops 2026

No 3rd-party packages needed. Works best on Windows 10/11
(also runs on macOS/Linux with a smaller app set).
"""

from __future__ import annotations

import difflib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "AppBot"
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "custom_apps.json"
CACHE_PATH = BASE_DIR / "app_index_cache.json"

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"


# --------------------------------------------------------------------------
# App catalogue
# --------------------------------------------------------------------------
@dataclass
class App:
    """One launchable thing."""
    target: str                  # exe name, .lnk path, uri, or url
    kind: str = "path"           # path | uri | url
    aliases: list[str] = field(default_factory=list)
    process: str | None = None   # exe name used when closing it


BUILTIN_APPS: dict[str, App] = {
    # --- browsers -------------------------------------------------------
    "chrome": App("chrome.exe", aliases=["google chrome", "browser"], process="chrome.exe"),
    "edge": App("msedge.exe", aliases=["microsoft edge"], process="msedge.exe"),
    "firefox": App("firefox.exe", process="firefox.exe"),
    "brave": App("brave.exe", process="brave.exe"),
    # --- windows built-ins ---------------------------------------------
    "notepad": App("notepad.exe", aliases=["notes", "text editor"], process="notepad.exe"),
    "calculator": App("calc.exe", aliases=["calc"], process="CalculatorApp.exe"),
    "paint": App("mspaint.exe", aliases=["ms paint"], process="mspaint.exe"),
    "cmd": App("cmd.exe", aliases=["command prompt", "terminal"], process="cmd.exe"),
    "powershell": App("powershell.exe", process="powershell.exe"),
    "task manager": App("taskmgr.exe", aliases=["taskmgr"], process="Taskmgr.exe"),
    "file explorer": App("explorer.exe", aliases=["explorer", "files", "my files"], process="explorer.exe"),
    "control panel": App("control.exe"),
    "snipping tool": App("snippingtool.exe", aliases=["screenshot", "snip"]),
    "settings": App("ms-settings:", kind="uri", aliases=["windows settings"], process="SystemSettings.exe"),
    "store": App("ms-windows-store:", kind="uri", aliases=["microsoft store"]),
    "camera": App("microsoft.windows.camera:", kind="uri", aliases=["webcam"]),
    # --- common desktop apps -------------------------------------------
    "spotify": App("spotify.exe", process="Spotify.exe"),
    "vlc": App("vlc.exe", aliases=["media player"], process="vlc.exe"),
    "word": App("winword.exe", aliases=["ms word", "microsoft word"], process="WINWORD.EXE"),
    "excel": App("excel.exe", aliases=["ms excel"], process="EXCEL.EXE"),
    "powerpoint": App("powerpnt.exe", aliases=["ppt", "slides"], process="POWERPNT.EXE"),
    "outlook": App("outlook.exe", process="OUTLOOK.EXE"),
    "teams": App("ms-teams:", kind="uri", aliases=["microsoft teams"], process="ms-teams.exe"),
    "whatsapp": App("whatsapp:", kind="uri", process="WhatsApp.exe"),
    "telegram": App("telegram.exe", process="Telegram.exe"),
    "discord": App("discord:", kind="uri", process="Discord.exe"),
    "zoom": App("zoommtg:", kind="uri", process="Zoom.exe"),
    "steam": App("steam://open/main", kind="uri", process="steam.exe"),
    "vs code": App("code", aliases=["vscode", "visual studio code", "code"], process="Code.exe"),
    "git bash": App("git-bash.exe", process="git-bash.exe"),
    "obs": App("obs64.exe", aliases=["obs studio"], process="obs64.exe"),
}

# Spoken shortcuts that map to a ready-made URL
SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "maps": "https://maps.google.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
    "instagram": "https://www.instagram.com",
    "whatsapp web": "https://web.whatsapp.com",
    "linkedin": "https://www.linkedin.com",
    "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.in",
    "flipkart": "https://www.flipkart.com",
    "stackoverflow": "https://stackoverflow.com",
    "wikipedia": "https://www.wikipedia.org",
}

SEARCH_ENGINES = {
    "google": "https://www.google.com/search?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "amazon": "https://www.amazon.in/s?k={}",
    "flipkart": "https://www.flipkart.com/search?q={}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={}",
    "github": "https://github.com/search?q={}",
    "maps": "https://www.google.com/maps/search/{}",
    "stackoverflow": "https://stackoverflow.com/search?q={}",
    "web": "https://duckduckgo.com/?q={}",
}


# --------------------------------------------------------------------------
# Launching
# --------------------------------------------------------------------------
def _start_menu_dirs() -> list[Path]:
    """Folders that hold Start Menu shortcuts."""
    if IS_WINDOWS:
        roots = [
            os.environ.get("APPDATA", ""),
            os.environ.get("PROGRAMDATA", ""),
        ]
        dirs = []
        for root in filter(None, roots):
            dirs.append(Path(root) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
        return [d for d in dirs if d.is_dir()]
    if IS_MAC:
        return [Path("/Applications"), Path.home() / "Applications"]
    return [Path("/usr/share/applications"), Path.home() / ".local/share/applications"]


def build_app_index(force: bool = False) -> dict[str, str]:
    """Map lowercase app name -> file to launch, by scanning Start Menu shortcuts."""
    if not force and CACHE_PATH.is_file():
        age = time.time() - CACHE_PATH.stat().st_mtime
        if age < 3 * 24 * 3600:                      # cache for 3 days
            try:
                return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

    index: dict[str, str] = {}
    patterns = ("*.lnk", "*.app", "*.desktop") if not IS_WINDOWS else ("*.lnk", "*.url")
    for base in _start_menu_dirs():
        for pattern in patterns:
            for item in base.rglob(pattern):
                key = item.stem.strip().lower()
                # skip uninstallers / docs, keep first hit (user profile comes first)
                if any(bad in key for bad in ("uninstall", "readme", "help", "website", "license")):
                    continue
                index.setdefault(key, str(item))
    try:
        CACHE_PATH.write_text(json.dumps(index, indent=1), encoding="utf-8")
    except OSError:
        pass
    return index


def load_custom_apps() -> dict[str, App]:
    """User-defined shortcuts from custom_apps.json (created on first run)."""
    if not CONFIG_PATH.is_file():
        sample = {
            "_comment": "Add your own: \"my app\": {\"target\": \"C:/Path/To/App.exe\", \"process\": \"App.exe\"}",
            "my app": {"target": "C:/Path/To/App.exe"},
        }
        try:
            CONFIG_PATH.write_text(json.dumps(sample, indent=2), encoding="utf-8")
        except OSError:
            pass
        return {}

    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    custom: dict[str, App] = {}
    for name, spec in raw.items():
        if name.startswith("_") or not isinstance(spec, dict):
            continue
        custom[name.strip().lower()] = App(
            target=spec.get("target", ""),
            kind=spec.get("kind", "path"),
            aliases=[a.lower() for a in spec.get("aliases", [])],
            process=spec.get("process"),
        )
    return custom


def open_target(target: str, kind: str = "path") -> None:
    """Actually launch something."""
    if kind == "url":
        webbrowser.open(target)
        return

    if IS_WINDOWS:
        try:
            os.startfile(target)                     # handles .exe, .lnk, uri, paths
            return
        except OSError:
            pass
        subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
        return

    if IS_MAC:
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def close_process(process: str) -> bool:
    """Best-effort kill by image name."""
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ["taskkill", "/IM", process, "/F"],
                capture_output=True, text=True, timeout=15,
            )
            return result.returncode == 0
        subprocess.run(["pkill", "-f", Path(process).stem], timeout=15)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


# --------------------------------------------------------------------------
# The bot brain
# --------------------------------------------------------------------------
OPEN_WORDS = ("open", "launch", "start", "run", "fire up", "boot up", "load")
CLOSE_WORDS = ("close", "quit", "kill", "stop", "shut down", "exit")

HELP_TEXT = (
    "Here's what I understand:\n"
    "  • open chrome            – launch any app\n"
    "  • open notepad and paint – several at once\n"
    "  • close spotify          – shut an app down\n"
    "  • go to youtube.com      – open a website\n"
    "  • search google for X    – search the web\n"
    "  • play X on youtube      – youtube search\n"
    "  • list apps              – what I can find on this PC\n"
    "  • refresh apps           – re-scan the Start Menu\n"
    "  • help / bye"
)


class Bot:
    def __init__(self) -> None:
        self.app_index: dict[str, str] = {}
        self.custom: dict[str, App] = {}
        self._index_ready = threading.Event()

    # -- setup ---------------------------------------------------------
    def warm_up(self) -> None:
        """Scan the Start Menu in the background so the window opens instantly."""
        def work() -> None:
            self.custom = load_custom_apps()
            self.app_index = build_app_index()
            self._index_ready.set()

        threading.Thread(target=work, daemon=True).start()

    def ensure_index(self) -> None:
        if not self._index_ready.is_set():
            self._index_ready.wait(timeout=20)

    # -- lookups -------------------------------------------------------
    def _all_names(self) -> dict[str, App]:
        """Every known name -> App."""
        table: dict[str, App] = {}
        for name, app in BUILTIN_APPS.items():
            table[name] = app
            for alias in app.aliases:
                table[alias] = app
        for name, app in self.custom.items():
            table[name] = app
            for alias in app.aliases:
                table[alias] = app
        for name, path in self.app_index.items():
            table.setdefault(name, App(path, kind="path"))
        return table

    def resolve_app(self, query: str) -> tuple[App | None, list[str]]:
        """Find an App for a query. Returns (app, suggestions)."""
        self.ensure_index()
        query = normalize(query)
        if not query:
            return None, []

        table = self._all_names()

        # 1) exact name
        if query in table:
            return table[query], []

        # 2) query without filler words: "the google chrome browser"
        cleaned = re.sub(r"\b(app|application|the|please|program)\b", " ", query).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if cleaned in table:
            return table[cleaned], []

        # 3) name is inside the query or vice-versa ("open my chrome now")
        for name in sorted(table, key=len, reverse=True):
            if len(name) >= 4 and re.search(rf"\b{re.escape(name)}\b", query):
                return table[name], []

        # 4) fuzzy
        pool = list(table.keys())
        matches = difflib.get_close_matches(cleaned or query, pool, n=3, cutoff=0.72)
        if matches:
            return table[matches[0]], matches[1:]

        # 5) is it on PATH?
        found = shutil.which(query) or shutil.which(query.replace(" ", "-"))
        if found:
            return App(found), []

        # 6) give up, but offer hints
        hints = difflib.get_close_matches(cleaned or query, pool, n=3, cutoff=0.4)
        return None, hints

    # -- the main entry point ------------------------------------------
    def respond(self, message: str) -> str:
        text = (message or "").strip()
        if not text:
            return "Say something like: open chrome"

        low = normalize(text)

        if low in {"hi", "hello", "hey", "yo", "hii", "namaste", "hello bot", "hey bot"}:
            return f"Hey! I'm {APP_NAME}. Ask me to open an app — try \"open chrome\" or \"help\"."

        if low in {"help", "?", "what can you do", "commands"}:
            return HELP_TEXT

        if low in {"bye", "goodbye", "exit", "quit", "close yourself", "see you"}:
            return "__QUIT__"

        if low.startswith(("list apps", "show apps", "what apps", "which apps")):
            return self.list_apps()

        if low.startswith(("refresh apps", "rescan", "scan apps", "update apps")):
            self.app_index = build_app_index(force=True)
            return f"Done — I can see {len(self.app_index)} apps from your Start Menu."

        # search intents
        search = self._parse_search(low)
        if search:
            engine, query = search
            url = SEARCH_ENGINES.get(engine, SEARCH_ENGINES["web"]).format(quote_plus(query))
            open_target(url, "url")
            return f'Searching {engine} for "{query}"…'

        # close intents
        close_target = strip_prefix(low, CLOSE_WORDS)
        if close_target is not None and close_target not in ("", "the"):
            return self.close_apps(close_target)

        # open intents
        open_target_text = strip_prefix(low, OPEN_WORDS)
        if open_target_text is not None:
            if not open_target_text:
                return "Open what? Try: open chrome"
            return self.open_apps(open_target_text)

        # bare input: a site name, a url, or an app name
        site = self._site_lookup(low)
        if site:
            open_target(site, "url")
            return f"Opening {low} in your browser…"

        app, suggestions = self.resolve_app(low)
        if app:
            return self._launch_one(low, app)

        if suggestions:
            preview = ", ".join(f'"{s}"' for s in suggestions)
            return (f'I couldn\'t find "{low}". Did you mean {preview}?\n'
                    'You can also add it to custom_apps.json, or say "list apps".')
        return (f'I couldn\'t find "{low}" on this PC. Try "list apps" to see what I know, '
                'or add the full path to custom_apps.json.')

    # -- helpers -------------------------------------------------------
    def _parse_search(self, low: str):
        m = re.match(r"^(?:search|google|youtube|look up|find)\s+(?:on\s+)?(\w+)?\s*(?:for\s+)?(.+)$", low)
        if m:
            engine, query = m.group(1), m.group(2).strip()
            if engine in SEARCH_ENGINES and query:
                return engine, query
            if engine in {"for", "up"}:
                return "google", m.group(2).strip()
            if engine is None:
                return "google", m.group(2).strip()
        m = re.match(r"^(?:play|watch)\s+(.+?)\s+(?:on|in)\s+(youtube)$", low)
        if m:
            return "youtube", m.group(1).strip()
        m = re.match(r"^search\s+(?:for\s+)?(.+)$", low)
        if m:
            return "google", m.group(1).strip()
        return None

    def _site_lookup(self, low: str) -> str | None:
        bare = re.sub(r"^(?:go to|goto|visit|open|take me to)\s+", "", low).strip()
        if re.match(r"^[\w.-]+\.(com|in|org|net|io|dev|co|ai|gov|edu)(/\S*)?$", bare):
            return "https://" + bare
        if bare in SITES:
            return SITES[bare]
        for name, url in SITES.items():
            if bare.startswith(name):
                return url
        return None

    def _launch_one(self, spoken: str, app: App) -> str:
        try:
            open_target(app.target, app.kind)
            return f"Opening {pretty(spoken)}…"
        except Exception as exc:                     # noqa: BLE001 - show the user anything
            return f'Couldn\'t open "{spoken}": {exc}'

    def open_apps(self, spoken: str) -> str:
        # "notepad and paint, calculator" -> three things
        parts = [p.strip() for p in re.split(r"\s*(?:,|;|\band\b|\+|&)\s*", spoken) if p.strip()]
        if not parts:
            return "Open what?"

        opened, problems, hints = [], [], []
        for part in parts:
            site = self._site_lookup(part)
            if site:
                open_target(site, "url")
                opened.append(pretty(part))
                continue
            app, suggestions = self.resolve_app(part)
            if app:
                try:
                    open_target(app.target, app.kind)
                    opened.append(pretty(part))
                except Exception as exc:             # noqa: BLE001
                    problems.append(f"{part} ({exc})")
            else:
                problems.append(part)
                hints.extend(suggestions)

        lines = []
        if opened:
            lines.append("Opening " + ", ".join(opened) + "…")
        if problems:
            lines.append("Not found: " + ", ".join(problems))
            if hints:
                lines.append("Did you mean " + ", ".join(f'"{h}"' for h in dict.fromkeys(hints)) + "?")
        return "\n".join(lines) or "Nothing to open."

    def close_apps(self, spoken: str) -> str:
        parts = [p.strip() for p in re.split(r"\s*(?:,|;|\band\b|\+|&)\s*", spoken) if p.strip()]
        done, failed = [], []
        for part in parts:
            app, _ = self.resolve_app(part)
            process = None
            if app and app.process:
                process = app.process
            elif app and app.target.endswith(".exe"):
                process = Path(app.target).name
            elif part.endswith(".exe"):
                process = part
            else:
                process = f"{part.replace(' ', '')}.exe"

            if close_process(process):
                done.append(pretty(part))
            else:
                failed.append(pretty(part))

        lines = []
        if done:
            lines.append("Closed " + ", ".join(done) + ".")
        if failed:
            lines.append("Couldn't close " + ", ".join(failed) + " (maybe it wasn't running).")
        return "\n".join(lines)

    def list_apps(self) -> str:
        self.ensure_index()
        names = sorted(set(self._all_names()))
        if not names:
            return "I couldn't scan any apps — try adding yours to custom_apps.json."
        # keep the message readable: show everything but wrapped
        shown = [n for n in names if not n.endswith(".exe")]
        chunks, line = [], []
        for name in shown:
            line.append(name)
            if sum(len(x) + 2 for x in line) > 74:
                chunks.append(" • ".join(line))
                line = []
        if line:
            chunks.append(" • ".join(line))
        return f"I can open {len(shown)} apps:\n" + "\n".join(chunks)


# --------------------------------------------------------------------------
# Small text utilities
# --------------------------------------------------------------------------
def normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"^(?:hey|hi|ok|okay|please|can you|could you|would you)\s+", "", text)
    text = re.sub(r"[!?.,]+$", "", text).strip()
    text = re.sub(r"^(?:please\s+)", "", text)
    return text


def strip_prefix(text: str, verbs: tuple[str, ...]) -> str | None:
    """If text starts with one of the verbs, return the rest."""
    for verb in sorted(verbs, key=len, reverse=True):
        if text == verb:
            return ""
        if text.startswith(verb + " "):
            return text[len(verb) + 1:].strip()
    return None


def pretty(name: str) -> str:
    return " ".join(word.capitalize() if not word.isupper() else word for word in name.split())


def quote_plus(text: str) -> str:
    from urllib.parse import quote_plus as _qp
    return _qp(text)


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
def run_gui() -> None:
    import tkinter as tk
    from tkinter import font as tkfont

    BG = "#0f1117"
    PANEL = "#151926"
    BOT = "#1c2333"
    USER = "#2563eb"
    TEXT = "#e6e9f2"
    MUTED = "#8b93a7"

    bot = Bot()
    bot.warm_up()

    root = tk.Tk()
    root.title(f"{APP_NAME} — your laptop assistant")
    root.geometry("620x680")
    root.minsize(460, 480)
    root.configure(bg=BG)

    body = tkfont.Font(family="Segoe UI", size=10)
    head = tkfont.Font(family="Segoe UI Semibold", size=13)
    small = tkfont.Font(family="Segoe UI", size=9)

    # --- header ---
    header = tk.Frame(root, bg=PANEL, height=64)
    header.pack(fill="x", side="top")
    header.pack_propagate(False)
    tk.Label(header, text="🤖  AppBot", bg=PANEL, fg=TEXT, font=head).pack(side="left", padx=18)
    status = tk.Label(header, text="ready", bg=PANEL, fg=MUTED, font=small)
    status.pack(side="right", padx=18)

    # --- chat log ---
    chat = tk.Text(root, bg=BG, fg=TEXT, font=body, wrap="word", bd=0,
                   padx=18, pady=14, insertbackground=TEXT, spacing1=2, spacing3=6)
    chat.pack(fill="both", expand=True)
    chat.tag_configure("user", foreground="#ffffff", background=USER,
                       lmargin1=110, lmargin2=110, rmargin=8, justify="right",
                       spacing1=8, spacing3=10, borderwidth=0)
    chat.tag_configure("bot", foreground=TEXT, background=BOT,
                       lmargin1=8, lmargin2=8, rmargin=110,
                       spacing1=8, spacing3=10, borderwidth=0)
    chat.tag_configure("note", foreground=MUTED, font=small, justify="center", spacing1=6)
    chat.configure(state="disabled")

    def add(text: str, tag: str) -> None:
        chat.configure(state="normal")
        chat.insert("end", " " + text + " \n", tag)
        chat.configure(state="disabled")
        chat.see("end")

    # --- input row ---
    bar = tk.Frame(root, bg=PANEL)
    bar.pack(fill="x", side="bottom")
    entry = tk.Entry(bar, bg="#0b0e15", fg=TEXT, font=body, bd=0,
                     insertbackground=TEXT, relief="flat")
    entry.pack(side="left", fill="x", expand=True, padx=(16, 8), pady=14, ipady=8)
    entry.focus_set()

    send_btn = tk.Button(bar, text="Send", bg=USER, fg="white", font=body, bd=0,
                         activebackground="#1d4ed8", activeforeground="white",
                         relief="flat", padx=18, pady=7, cursor="hand2")
    send_btn.pack(side="right", padx=(0, 16))

    busy = {"value": False}

    def handle(_event=None) -> None:
        if busy["value"]:
            return
        text = entry.get().strip()
        if not text:
            return
        entry.delete(0, "end")
        add(text, "user")

        busy["value"] = True
        send_btn.configure(state="disabled")
        status.configure(text="working…")

        def work() -> None:
            try:
                reply = bot.respond(text)
            except Exception as exc:                 # noqa: BLE001
                reply = f"Something went wrong: {exc}"

            def finish() -> None:
                busy["value"] = False
                send_btn.configure(state="normal")
                status.configure(text="ready")
                if reply == "__QUIT__":
                    add("Bye! 👋", "bot")
                    root.after(500, root.destroy)
                    return
                add(reply, "bot")

            root.after(0, finish)

        threading.Thread(target=work, daemon=True).start()

    entry.bind("<Return>", handle)
    send_btn.configure(command=handle)

    add(f"Hi! I'm {APP_NAME}. Tell me what to open.", "bot")
    add('try:  open chrome   •   close spotify   •   search google for laptops   •   list apps', "note")

    def on_close() -> None:
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


# --------------------------------------------------------------------------
# CLI mode + self test
# --------------------------------------------------------------------------
def run_cli() -> None:
    bot = Bot()
    bot.custom = load_custom_apps()
    bot.app_index = build_app_index()
    bot._index_ready.set()
    print(f"{APP_NAME}: hi! type 'help' for commands, 'bye' to quit.")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye!")
            break
        if not line:
            continue
        reply = bot.respond(line)
        if reply == "__QUIT__":
            print("bot> bye! 👋")
            break
        print("bot> " + reply)


def selftest() -> None:
    """Headless check of the parsing/matching logic (no apps are launched)."""
    bot = Bot()
    bot.custom = {}
    bot.app_index = {
        "google chrome": "C:/fake/chrome.lnk",
        "notepad++": "C:/fake/npp.lnk",
        "whatsapp": "C:/fake/whatsapp.lnk",
        "zoom workplace": "C:/fake/zoom.lnk",
    }
    bot._index_ready.set()

    launched = []
    opened = []
    closed = []
    global open_target, close_process
    real_open, real_close = open_target, close_process
    open_target = lambda target, kind="path": launched.append((target, kind))   # noqa: E731
    close_process = lambda process: (closed.append(process), True)[1]           # noqa: E731

    cases = [
        ("open chrome", "Opening Chrome"),
        ("launch notepad and calculator", "Opening Notepad, Calculator"),
        ("open notepad++", "Opening Notepad++"),
        ("open the whatsapp app", "Opening The Whatsapp App"),
        ("go to youtube.com", "browser"),
        ("open youtube", "browser"),
        ("search google for best laptops 2026", "Searching google"),
        ("play lo-fi beats on youtube", "Searching youtube"),
        ("close spotify", "Closed Spotify"),
        ("list apps", "I can open"),
        ("help", "Here's what I understand"),
        ("open flibbertigibbet", "couldn"),
        ("hello", "Hey!"),
        ("bye", "__QUIT__"),
    ]

    failures = 0
    for user, expect in cases:
        reply = bot.respond(user)
        ok = expect in reply
        if not ok:
            failures += 1
        print(("PASS  " if ok else "FAIL  ") + f'{user!r:42} -> {reply.splitlines()[0][:70]!r}')

    # fuzzy match check
    app, hints = bot.resolve_app("notpad")
    ok = app is not None and app.target == "notepad.exe"
    print(("PASS  " if ok else "FAIL  ") + f"fuzzy 'notpad' -> {app.target if app else hints}")
    failures += 0 if ok else 1

    app, hints = bot.resolve_app("zoom")
    ok = app is not None and app.target == "C:/fake/zoom.lnk"
    print(("PASS  " if ok else "FAIL  ") + f"start-menu 'zoom' -> {app.target if app else hints}")
    failures += 0 if ok else 1

    open_target, close_process = real_open, real_close
    print(f"\n{len(cases) + 2 - failures}/{len(cases) + 2} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    if "--cli" in sys.argv or not sys.argv[1:]:
        # no args -> GUI if tkinter exists, else CLI
        if "--cli" in sys.argv:
            run_cli()
        else:
            try:
                run_gui()
            except Exception as exc:                 # noqa: BLE001
                print(f"(GUI unavailable: {exc}) — falling back to terminal mode.\n")
                run_cli()
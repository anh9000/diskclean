#!/usr/bin/env python3
r"""
diskclean

A portable, cross platform disk cache and temp cleaner for the terminal.
It scans known safe cache and temp locations, shows their sizes, lets you
pick what to remove, and cleans with a live progress bar that shows elapsed
time and ETA. Works on Windows, macOS, and Linux. No dependencies, no
installer. Just run it.

Safety: it only ever touches a built in list of cache and temp folders that
regenerate on their own. It never deletes system files, installed programs,
or personal data, and nothing is removed without your confirmation.
"""

import os
import sys
import time
import json
import shutil
import platform
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------------------------
# terminal setup: enable ANSI colors and UTF-8 output where possible
# ---------------------------------------------------------------------------
def _enable_ansi():
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            os.system("")


_enable_ansi()

try:
    sys.stdout.reconfigure(encoding="utf-8")
    BLOCK_FULL, BLOCK_EMPTY = "█", "░"
except Exception:
    BLOCK_FULL, BLOCK_EMPTY = "#", "."


# ---------------------------------------------------------------------------
# green retro terminal theme
# ---------------------------------------------------------------------------
GREEN = "\033[38;5;46m"
GREEN_HOT = "\033[38;5;83m"
GREEN_DIM = "\033[38;5;28m"
WHITE = "\033[97m"
GREY = "\033[90m"
RED = "\033[91m"
YELLOW = "\033[93m"
INVERT = "\033[7m"
RESET = "\033[0m"

WIDTH = 64

BANNER = r"""
     _ _      _        _
  __| (_)___ | | _____| | ___  __ _ _ __
 / _` | / __|| |/ / __| |/ _ \/ _` | '_ \
| (_| | \__ \|   < (__| |  __/ (_| | | | |
 \__,_|_|___/|_|\_\___|_|\___|\__,_|_| |_|
"""


def banner():
    for line in BANNER.strip("\n").splitlines():
        print(GREEN_HOT + line + RESET)
    print(GREEN_DIM + "  portable disk cache and temp cleaner" + RESET)


def rule():
    print(GREEN_DIM + ("=" * WIDTH) + RESET)


def bar_title(text):
    pad = WIDTH - len(text) - 2
    if pad < 0:
        pad = 0
    print(INVERT + GREEN_HOT + "  " + text + (" " * pad) + RESET)


def say(text, color=GREEN):
    print(color + "  " + text + RESET)


def dim(text):
    print(GREEN_DIM + "  " + text + RESET)


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------
def fmt_size(nbytes):
    n = float(nbytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}"
        n /= 1024


def fmt_time(secs):
    secs = int(max(0, secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# progress bar (tqdm style: bar, percent, count, elapsed, ETA, rate)
# ---------------------------------------------------------------------------
class Bar:
    def __init__(self, total, label="", width=26):
        self.total = max(1, int(total))
        self.label = label
        self.width = width
        self.start = time.monotonic()
        self.n = 0
        self._last = 0.0

    def update(self, step=1):
        self.n += step
        now = time.monotonic()
        if now - self._last >= 0.05 or self.n >= self.total:
            self._last = now
            self._draw()

    def _draw(self):
        frac = min(1.0, self.n / self.total)
        filled = int(round(self.width * frac))
        bar = BLOCK_FULL * filled + BLOCK_EMPTY * (self.width - filled)
        elapsed = time.monotonic() - self.start
        rate = self.n / elapsed if elapsed > 0 else 0
        eta = (self.total - self.n) / rate if rate > 0 else 0
        line = (f"{GREEN}  {self.label} {GREEN_HOT}[{bar}]{GREEN} "
                f"{int(frac * 100):3d}% | {self.n}/{self.total} "
                f"[{fmt_time(elapsed)}<{fmt_time(eta)}, {rate:.1f}/s]{RESET}")
        sys.stdout.write("\r" + line + "    ")
        sys.stdout.flush()

    def close(self):
        self._draw()
        sys.stdout.write("\r" + (" " * (WIDTH + 40)) + "\r")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# file system helpers
# ---------------------------------------------------------------------------
def iter_files(path):
    """Yield every file under a path, skipping anything unreadable."""
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        yield from iter_files(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        yield entry.path
                except OSError:
                    continue
    except OSError:
        return


def dir_size(paths):
    """Total size in bytes across one or more paths."""
    total = 0
    for p in paths:
        if not os.path.exists(p):
            continue
        for f in iter_files(p):
            try:
                total += os.path.getsize(f)
            except OSError:
                continue
    return total


def dir_size_live(paths, label):
    """Total size with a live progress line so a big folder is not silent."""
    total = 0.0
    count = 0
    start = time.monotonic()
    for p in paths:
        if not os.path.exists(p):
            continue
        for f in iter_files(p):
            try:
                total += os.path.getsize(f)
            except OSError:
                continue
            count += 1
            if count % 800 == 0:
                el = int(time.monotonic() - start)
                msg = f"  scanning {label}  {count:,} files  {fmt_size(total)}  {el}s"
                sys.stdout.write("\r" + GREEN_DIM + msg[:WIDTH + 30] + RESET + "    ")
                sys.stdout.flush()
    sys.stdout.write("\r" + (" " * (WIDTH + 40)) + "\r")
    sys.stdout.flush()
    return total


def clear_paths(paths, label):
    """Delete file contents under each path with a live progress bar.
    Returns (freed_bytes, failed_count). Folders are kept; only contents go."""
    files = []
    for p in paths:
        if os.path.exists(p):
            files.extend(iter_files(p))
    freed = 0
    failed = 0
    b = Bar(len(files) or 1, label=label)
    for f in files:
        try:
            sz = os.path.getsize(f)
        except OSError:
            sz = 0
        try:
            os.remove(f)
            freed += sz
        except OSError:
            failed += 1
        b.update(1)
    b.close()
    for p in paths:
        _remove_empty_dirs(p)
    return freed, failed


def _remove_empty_dirs(path):
    """Remove now empty subfolders, deepest first. Keep the top folder itself."""
    if not os.path.isdir(path):
        return
    for root, dirs, _files in os.walk(path, topdown=False):
        if os.path.abspath(root) == os.path.abspath(path):
            continue
        try:
            os.rmdir(root)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# cross platform target list (the safe allowlist)
# ---------------------------------------------------------------------------
class Target:
    def __init__(self, name, paths, desc, needs_priv=False):
        self.name = name
        self.paths = [str(p) for p in paths]
        self.desc = desc
        self.needs_priv = needs_priv
        self.size = 0


def _env_path(var, default):
    return Path(os.environ.get(var, str(default)))


def get_targets():
    sysname = platform.system()
    home = Path.home()
    t = []

    if sysname == "Windows":
        la = _env_path("LOCALAPPDATA", home / "AppData" / "Local")
        ra = _env_path("APPDATA", home / "AppData" / "Roaming")
        win = _env_path("WINDIR", Path("C:/Windows"))
        tmp = _env_path("TEMP", la / "Temp")
        t = [
            Target("User Temp", [tmp], "Your temporary files.", False),
            Target("Windows Temp", [win / "Temp"], "System temp files.", True),
            Target("Crash dumps", [la / "CrashDumps"], "Old app crash logs.", False),
            Target("pip cache", [la / "pip" / "cache"], "Python package cache.", False),
            Target("NVIDIA shader cache", [la / "NVIDIA" / "DXCache", la / "NVIDIA" / "GLCache"],
                   "GPU shader cache. Games rebuild it.", False),
            Target("Chrome cache", [la / "Google" / "Chrome" / "User Data" / "Default" / "Cache",
                                    la / "Google" / "Chrome" / "User Data" / "Default" / "Code Cache",
                                    la / "Google" / "Chrome" / "User Data" / "Default" / "GPUCache"],
                   "Browser cache. Logins are kept.", False),
            Target("Edge cache", [la / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache"],
                   "Browser cache. Logins are kept.", False),
            Target("Spotify cache", [la / "Spotify" / "Storage", la / "Spotify" / "Data"],
                   "Cached and offline song data.", False),
            Target("Discord cache", [ra / "discord" / "Cache", ra / "discord" / "Code Cache",
                                     ra / "discord" / "GPUCache"], "Discord media cache.", False),
        ]

    elif sysname == "Darwin":
        lib = home / "Library"
        t = [
            Target("User caches", [lib / "Caches"], "App caches in your Library.", False),
            Target("User logs", [lib / "Logs"], "App log files.", False),
            Target("Trash", [home / ".Trash"], "Your Trash.", False),
            Target("Chrome cache", [lib / "Caches" / "Google" / "Chrome"],
                   "Browser cache. Logins are kept.", False),
            Target("pip cache", [lib / "Caches" / "pip"], "Python package cache.", False),
        ]

    else:  # Linux and other Unix
        cache = _env_path("XDG_CACHE_HOME", home / ".cache")
        t = [
            Target("User cache", [cache], "App cache in ~/.cache.", False),
            Target("Thumbnail cache", [cache / "thumbnails"], "Image thumbnail cache.", False),
            Target("Trash", [home / ".local" / "share" / "Trash"], "Your Trash.", False),
            Target("pip cache", [cache / "pip"], "Python package cache.", False),
            Target("Chrome cache", [cache / "google-chrome", cache / "chromium"],
                   "Browser cache. Logins are kept.", False),
        ]

    return t


def is_admin():
    if os.name == "nt":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0 if hasattr(os, "geteuid") else False


# ---------------------------------------------------------------------------
# selection parser: "1,3,5" or "2-6" or "all"
# ---------------------------------------------------------------------------
def parse_selection(text, count):
    text = text.strip().lower()
    if text == "all":
        return list(range(1, count + 1))
    if text in ("", "q", "none", "quit"):
        return []
    picked = set()
    for tok in text.split(","):
        tok = tok.strip()
        if tok.isdigit():
            picked.add(int(tok))
        elif "-" in tok:
            a, _, b = tok.partition("-")
            if a.strip().isdigit() and b.strip().isdigit():
                lo, hi = sorted((int(a), int(b)))
                picked.update(range(lo, hi + 1))
    return sorted(i for i in picked if 1 <= i <= count)


def ask(prompt):
    sys.stdout.write(GREEN + "  " + prompt + " " + GREEN_DIM + "> " + RESET)
    sys.stdout.flush()
    try:
        return input()
    except (EOFError, KeyboardInterrupt):
        return "q"


def ask_yesno(prompt):
    while True:
        ans = ask(prompt + " [y/n]").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no", "", "q"):
            return False
        dim("Please type y or n.")


# ---------------------------------------------------------------------------
# history log
# ---------------------------------------------------------------------------
def log_path():
    here = Path(__file__).resolve().parent
    return here / "diskclean_history.log"


def write_log(entry):
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(stamp + "  |  " + entry + "\n")
    except OSError:
        pass


def show_history():
    p = log_path()
    if not p.exists():
        dim("No history yet. This looks like your first run.")
        return
    try:
        lines = [ln.rstrip("\n") for ln in open(p, encoding="utf-8") if ln.strip()]
    except OSError:
        return
    if not lines:
        dim("No history yet.")
        return
    say("Last run: " + lines[-1].split("|")[0].strip(), WHITE)
    say("Recent history:", WHITE)
    for ln in lines[-5:]:
        dim(ln)


# ---------------------------------------------------------------------------
# main flow
# ---------------------------------------------------------------------------
def disk_summary():
    try:
        usage = shutil.disk_usage(str(Path.home()))
        say(f"Disk: {fmt_size(usage.free)} free of {fmt_size(usage.total)}", WHITE)
    except Exception:
        pass


def run_once(admin):
    rule()
    bar_title("SCAN")
    dim("Measuring safe cache and temp locations, please wait.")
    print()

    targets = get_targets()
    for t in targets:
        t.size = dir_size_live(t.paths, t.name)
        flag = ""
        if t.needs_priv and not admin:
            flag = GREY + "  (needs admin)" + RESET
        print(f"  {GREEN}{t.name.ljust(22)}{GREEN_HOT}{fmt_size(t.size).rjust(10)}{RESET}{flag}")

    cleanable = [t for t in targets if t.size > 0]
    total = sum(t.size for t in cleanable)
    print()
    rule()
    say("Total reclaimable: " + fmt_size(total), WHITE)
    rule()

    if not cleanable:
        print()
        say("Nothing to clean. Everything is already empty.")
        return False, 0, []

    # selection menu
    print()
    bar_title("SELECT WHAT TO DELETE")
    dim("Type the numbers you want. Examples: 1,3,5 or 2-6 or all. q to skip.")
    print()
    for i, t in enumerate(cleanable, 1):
        flag = ""
        if t.needs_priv and not admin:
            flag = GREY + "  (needs admin)" + RESET
        print(f"  {GREEN_HOT}{str(i).rjust(2)}.{GREEN} {t.name.ljust(22)}"
              f"{fmt_size(t.size).rjust(10)}{RESET}{flag}")
    print()

    picks = parse_selection(ask("Your selection"), len(cleanable))
    if not picks:
        print()
        dim("Nothing selected. No changes made.")
        return False, 0, []

    chosen = [cleanable[i - 1] for i in picks]
    sel_total = sum(t.size for t in chosen)
    print()
    say("You selected:", WHITE)
    for t in chosen:
        print(f"  {GREEN}{t.name.ljust(22)}{GREEN_HOT}{fmt_size(t.size).rjust(10)}{RESET}")
    print()
    say("This permanently wipes the selected files from your computer.", YELLOW)
    say("They are deleted for good and do NOT go to a recycle bin or trash.", YELLOW)
    if not ask_yesno(f"Delete these {len(chosen)} items ({fmt_size(sel_total)})?"):
        print()
        dim("Cancelled. No changes made.")
        return False, 0, []

    # cleaning
    print()
    bar_title("CLEANING")
    freed_total = 0
    cleaned = []
    for t in chosen:
        if t.needs_priv and not admin:
            print(f"  {GREY}[ SKIP ]  {t.name}  needs admin{RESET}")
            continue
        freed, failed = clear_paths(t.paths, t.name)
        freed_total += freed
        cleaned.append(t.name)
        if failed == 0:
            print(f"  {GREEN_HOT}[ DONE ]  {GREEN}{t.name}  freed {fmt_size(freed)}{RESET}")
        else:
            print(f"  {YELLOW}[ PART ]  {t.name}  freed {fmt_size(freed)}, "
                  f"{failed} files in use skipped{RESET}")

    return True, freed_total, cleaned


def main():
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        banner()
        print()
        admin = is_admin()
        if not admin and platform.system() == "Windows":
            dim("Not running as administrator. Windows Temp will be skipped.")
            dim("Run as administrator to include it.")
            print()
        disk_summary()
        show_history()
        print()

        did, freed, cleaned = run_once(admin)

        print()
        rule()
        bar_title("DONE")
        if did:
            say("Total freed this run: " + fmt_size(freed), WHITE)
        try:
            usage = shutil.disk_usage(str(Path.home()))
            say("Disk free now: " + fmt_size(usage.free), WHITE)
            free_str = fmt_size(usage.free)
        except Exception:
            free_str = "unknown"
        rule()

        items = ", ".join(cleaned) if cleaned else "none"
        write_log(f"freed: {fmt_size(freed)}  |  free after: {free_str}  |  cleaned: {items}")
        print()
        dim("History log: " + str(log_path()))
        print()

        if not ask_yesno("Run another scan or cleanup? (n closes diskclean)"):
            print()
            say("Done. Run diskclean again any time.", WHITE)
            print()
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(RESET + "\n  Stopped.\n")

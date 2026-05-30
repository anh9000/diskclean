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
# monochrome white terminal theme
# ---------------------------------------------------------------------------
FG = "\033[37m"        # primary text (white)
FG_HOT = "\033[1;97m"  # bright white (headers, banner, highlights)
FG_DIM = "\033[90m"    # grey (secondary, dim)
WHITE = "\033[97m"     # bright white
GREY = "\033[90m"      # grey
INVERT = "\033[7m"     # inverted (black on white) for title bars
RESET = "\033[0m"

WIDTH = 64

# session options the user can change at the prompt (for example: age 30)
OPTS = {"age_days": None}

BANNER = r"""
    __ __         __          __
.--|  |__|.-----.|  |--.----.|  |.-----.---.-.-----.
|  _  |  ||__ --||    <|  __||  ||  -__|  _  |     |
|_____|__||_____||__|__|____||__||_____|___._|__|__|
"""


def banner():
    print()
    for line in BANNER.strip("\n").splitlines():
        print(FG_HOT + "  " + line + RESET)
    print()
    print(FG_DIM + "  portable disk cache and temp cleaner" + RESET)
    print()


def rule():
    print(FG_DIM + ("=" * WIDTH) + RESET)


def bar_title(text):
    pad = WIDTH - len(text) - 2
    if pad < 0:
        pad = 0
    print(INVERT + FG_HOT + "  " + text + (" " * pad) + RESET)


def say(text, color=FG):
    print(color + "  " + text + RESET)


def dim(text):
    print(FG_DIM + "  " + text + RESET)


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
        line = (f"{FG}  {self.label} {FG_HOT}[{bar}]{FG} "
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
def iter_files(path, cutoff=None):
    """Yield every file under a path, skipping anything unreadable. When cutoff
    is set (a unix timestamp), only files last modified before it are yielded."""
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        yield from iter_files(entry.path, cutoff)
                    elif entry.is_file(follow_symlinks=False):
                        if cutoff is not None:
                            try:
                                if entry.stat(follow_symlinks=False).st_mtime > cutoff:
                                    continue
                            except OSError:
                                continue
                        yield entry.path
                except OSError:
                    continue
    except OSError:
        return


def _cutoff(min_age_days):
    """Convert an age in days to a unix mtime cutoff, or None for no filter."""
    if not min_age_days:
        return None
    return time.time() - float(min_age_days) * 86400.0


def dir_size(paths, min_age_days=None):
    """Total size in bytes across one or more paths."""
    cutoff = _cutoff(min_age_days)
    total = 0
    for p in paths:
        if not os.path.exists(p):
            continue
        for f in iter_files(p, cutoff):
            try:
                total += os.path.getsize(f)
            except OSError:
                continue
    return total


def _clear_line():
    cols = shutil.get_terminal_size((80, 20)).columns
    sys.stdout.write("\r" + (" " * (cols - 1)) + "\r")
    sys.stdout.flush()


def dir_size_live(paths, label, idx=None, total=None, min_age_days=None):
    """Total size with a live display. When idx and total are given it shows a
    progress bar for the outer scan (item idx of total) with the current item's
    running size, so scanning looks like a loading bar. Otherwise it shows a
    plain status line (used for single item scans)."""
    cutoff = _cutoff(min_age_days)
    nbytes = 0.0
    count = 0
    start = time.monotonic()
    bar_w = 18

    def render():
        el = int(time.monotonic() - start)
        if total:
            frac = min(1.0, idx / total)
            filled = int(round(bar_w * frac))
            bar = BLOCK_FULL * filled + BLOCK_EMPTY * (bar_w - filled)
            name = label if len(label) <= 20 else label[:19] + "~"
            line = (f"  {FG}[{FG_HOT}{bar}{FG}] {int(frac * 100):3d}% | {idx}/{total} | "
                    f"{name} | {fmt_size(nbytes)} | {el}s{RESET}")
        else:
            line = f"  {FG_DIM}scanning {label}  {count:,} files  {fmt_size(nbytes)}  {el}s{RESET}"
        sys.stdout.write("\r" + line + "    ")
        sys.stdout.flush()

    render()
    for p in paths:
        if not os.path.exists(p):
            continue
        for f in iter_files(p, cutoff):
            try:
                nbytes += os.path.getsize(f)
            except OSError:
                continue
            count += 1
            if count % 600 == 0:
                render()
    render()
    _clear_line()
    return nbytes


def clear_paths(paths, label, min_age_days=None):
    """Delete file contents under each path with a live progress bar.
    Returns (freed_bytes, failed_count). Folders are kept; only contents go."""
    cutoff = _cutoff(min_age_days)
    files = []
    for p in paths:
        if os.path.exists(p):
            files.extend(iter_files(p, cutoff))
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


# ---------------------------------------------------------------------------
# browser cache discovery (covers every profile, not just Default)
# ---------------------------------------------------------------------------
def _chromium_cache_dirs(profile):
    p = Path(profile)
    return [p / "Cache", p / "Code Cache", p / "GPUCache",
            p / "Service Worker" / "CacheStorage"]


def _chromium_user_data_caches(user_data_root):
    """All cache dirs across every profile in a Chromium User Data folder."""
    root = Path(user_data_root)
    paths = []
    if not root.is_dir():
        return paths
    try:
        entries = list(os.scandir(root))
    except OSError:
        return paths
    for entry in entries:
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        n = entry.name
        if n == "Default" or n.startswith("Profile ") or n in ("Guest Profile", "System Profile"):
            paths.extend(_chromium_cache_dirs(entry.path))
    return paths


def _single_profile_caches(profile_root):
    """Cache dirs for a browser whose profile root is the folder itself (Opera)."""
    root = Path(profile_root)
    if not root.is_dir():
        return []
    return _chromium_cache_dirs(root)


def _firefox_caches(profiles_root):
    """cache2 folders across every Firefox profile."""
    root = Path(profiles_root)
    out = []
    if not root.is_dir():
        return out
    try:
        for entry in os.scandir(root):
            if entry.is_dir(follow_symlinks=False):
                out.append(Path(entry.path) / "cache2")
    except OSError:
        pass
    return out


def browser_caches():
    """Map a browser name to its cache paths across every profile for this OS.
    Covers Chrome, Edge, Brave, Vivaldi, Chromium, Opera, Opera GX, and Firefox.
    Only browsers that are actually present are returned."""
    home = Path.home()
    s = platform.system()
    out = {}
    if s == "Windows":
        la = _env_path("LOCALAPPDATA", home / "AppData" / "Local")
        for name, ud in (
            ("Chrome", la / "Google" / "Chrome" / "User Data"),
            ("Edge", la / "Microsoft" / "Edge" / "User Data"),
            ("Brave", la / "BraveSoftware" / "Brave-Browser" / "User Data"),
            ("Vivaldi", la / "Vivaldi" / "User Data"),
            ("Chromium", la / "Chromium" / "User Data"),
        ):
            paths = _chromium_user_data_caches(ud)
            if paths:
                out[name] = paths
        for name, root in (
            ("Opera", la / "Opera Software" / "Opera Stable"),
            ("Opera GX", la / "Opera Software" / "Opera GX Stable"),
        ):
            paths = _single_profile_caches(root)
            if paths:
                out[name] = paths
        ff = _firefox_caches(la / "Mozilla" / "Firefox" / "Profiles")
        if ff:
            out["Firefox"] = ff
    elif s == "Darwin":
        c = home / "Library" / "Caches"
        for name, cand in (
            ("Chrome", [c / "Google" / "Chrome"]),
            ("Edge", [c / "Microsoft Edge"]),
            ("Brave", [c / "BraveSoftware" / "Brave-Browser"]),
            ("Opera", [c / "com.operasoftware.Opera"]),
            ("Vivaldi", [c / "Vivaldi"]),
            ("Firefox", [c / "Firefox"]),
        ):
            existing = [p for p in cand if Path(p).is_dir()]
            if existing:
                out[name] = existing
    else:
        cache = _env_path("XDG_CACHE_HOME", home / ".cache")
        for name, cand in (
            ("Chrome", [cache / "google-chrome"]),
            ("Chromium", [cache / "chromium"]),
            ("Edge", [cache / "microsoft-edge"]),
            ("Brave", [cache / "BraveSoftware" / "Brave-Browser"]),
            ("Opera", [cache / "opera"]),
            ("Vivaldi", [cache / "vivaldi"]),
            ("Firefox", [cache / "mozilla" / "firefox"]),
        ):
            existing = [p for p in cand if Path(p).is_dir()]
            if existing:
                out[name] = existing
    return out


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
            Target("pip cache", [lib / "Caches" / "pip"], "Python package cache.", False),
        ]

    else:  # Linux and other Unix
        cache = _env_path("XDG_CACHE_HOME", home / ".cache")
        t = [
            Target("User cache", [cache], "App cache in ~/.cache.", False),
            Target("Thumbnail cache", [cache / "thumbnails"], "Image thumbnail cache.", False),
            Target("Trash", [home / ".local" / "share" / "Trash"], "Your Trash.", False),
            Target("pip cache", [cache / "pip"], "Python package cache.", False),
        ]

    if sysname == "Windows":
        for name, paths in browser_caches().items():
            t.append(Target(name + " cache", paths,
                            "Browser cache across all profiles. Logins are kept.", False))

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
# drive detection and read only overview
# ---------------------------------------------------------------------------
class Drive:
    def __init__(self, label, root, free, total, cloud):
        self.label = label
        self.root = root
        self.free = free
        self.total = total
        self.cloud = cloud


def get_drives():
    out = []
    cloud_limit = 100 * (1024 ** 4)  # over 100 TB means a cloud or virtual mount
    if os.name == "nt":
        import ctypes
        import string
        from ctypes import wintypes, create_unicode_buffer, byref
        try:
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        except Exception:
            bitmask = 0
        for i, letter in enumerate(string.ascii_uppercase):
            if not (bitmask & (1 << i)):
                continue
            root = f"{letter}:\\"
            try:
                u = shutil.disk_usage(root)
            except OSError:
                continue
            label, fs, dtype = "", "", 0
            try:
                volb = create_unicode_buffer(261)
                fsb = create_unicode_buffer(261)
                serial, maxlen, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
                ctypes.windll.kernel32.GetVolumeInformationW(
                    root, volb, 261, byref(serial), byref(maxlen), byref(flags), fsb, 261)
                label, fs = volb.value, fsb.value
                dtype = ctypes.windll.kernel32.GetDriveTypeW(root)
            except Exception:
                pass
            # cloud or virtual mount markers: fake huge size, network drive,
            # a FUSE or rclone or Dokan filesystem, or an account named volume
            # (Google Drive and similar label the drive with the account email).
            cloud = (u.total > cloud_limit or dtype == 4
                     or any(m in fs.lower() for m in ("fuse", "rclone", "dokan"))
                     or "@" in label)
            out.append(Drive(letter, root, u.free, u.total, cloud))
    else:
        roots = ["/"]
        for base in ("/Volumes", "/media", "/mnt"):
            if os.path.isdir(base):
                try:
                    for name in sorted(os.listdir(base)):
                        p = os.path.join(base, name)
                        if os.path.isdir(p):
                            roots.append(p)
                except OSError:
                    pass
        for root in roots:
            try:
                u = shutil.disk_usage(root)
            except OSError:
                continue
            out.append(Drive(root, root, u.free, u.total, u.total > cloud_limit))
    return out


def drive_overview(root):
    print()
    bar_title("DRIVE OVERVIEW  " + root)
    dim("Read only. Shows where space is used. Nothing here is deleted.")
    print()
    say("Measuring top level folders, this can take a few minutes.")
    print()
    try:
        entries = [e for e in os.scandir(root) if e.is_dir(follow_symlinks=False)]
    except OSError as e:
        dim(f"Cannot read {root}: {e}")
        return
    rows = []
    n = len(entries)
    for i, e in enumerate(entries, 1):
        sz = dir_size_live([e.path], e.name, i, n)
        rows.append((e.name, sz))
        print(f"  {FG_DIM}{e.name.ljust(28)}{fmt_size(sz).rjust(10)}{RESET}")
    print()
    say("Largest folders:", WHITE)
    for name, sz in sorted(rows, key=lambda r: r[1], reverse=True)[:12]:
        print(f"  {FG}{name.ljust(28)}{FG_HOT}{fmt_size(sz).rjust(10)}{RESET}")
    print()
    rule()
    dim("This is only a view of where space is. Nothing was deleted.")


def offer_drive_overview():
    drives = get_drives()
    if not drives:
        return
    bar_title("DRIVE SCAN")
    dim("Pick a drive to see where its space is used (read only).")
    print()
    selectable = [d for d in drives if not d.cloud]
    for i, d in enumerate(selectable, 1):
        info = f"{fmt_size(d.free)} free of {fmt_size(d.total)}"
        print(f"  {FG_HOT}{str(i).rjust(2)}.{FG} {d.label.ljust(14)}{info}{RESET}")
    for d in drives:
        if d.cloud:
            print(f"  {GREY}    {d.label.ljust(14)}cloud or network, not scannable here{RESET}")
    print()
    ans = ask("Drive to scan for an overview (number, or Enter to skip)").strip()
    if ans.isdigit():
        idx = int(ans)
        if 1 <= idx <= len(selectable):
            drive_overview(selectable[idx - 1].root)
    print()


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
    sys.stdout.write(FG + "  " + prompt + " " + FG_DIM + "> " + RESET)
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
# ---------------------------------------------------------------------------
# named app catalog: scan one app's cache by name, per OS
# ---------------------------------------------------------------------------
def app_catalog():
    """Map common app names to their cache paths for the current OS."""
    home = Path.home()
    s = platform.system()
    la = _env_path("LOCALAPPDATA", home / "AppData" / "Local")
    ra = _env_path("APPDATA", home / "AppData" / "Roaming")
    cache = _env_path("XDG_CACHE_HOME", home / ".cache")
    cfg = _env_path("XDG_CONFIG_HOME", home / ".config")
    lib = home / "Library"
    appsup = lib / "Application Support"

    def pick(w, m, x):
        return w if s == "Windows" else (m if s == "Darwin" else x)

    cat = {}

    def add(name, desc, w, m, x):
        paths = pick(w, m, x)
        if paths:
            cat[name] = (paths, desc)

    add("discord", "Discord media and code cache.",
        [ra / "discord" / "Cache", ra / "discord" / "Code Cache", ra / "discord" / "GPUCache"],
        [appsup / "discord" / "Cache", appsup / "discord" / "Code Cache"],
        [cfg / "discord" / "Cache", cfg / "discord" / "Code Cache"])
    # browsers are added from browser_caches() below so every profile is
    # covered, not just Default, across Chrome, Edge, Brave, Opera, and more.
    add("spotify", "Spotify cache.",
        [la / "Spotify" / "Storage", la / "Spotify" / "Data"],
        [lib / "Caches" / "com.spotify.client"],
        [cache / "spotify"])
    add("slack", "Slack cache.",
        [ra / "Slack" / "Cache", ra / "Slack" / "Service Worker" / "CacheStorage"],
        [appsup / "Slack" / "Cache"],
        [cfg / "Slack" / "Cache"])
    add("teams", "Microsoft Teams cache.",
        [ra / "Microsoft" / "Teams" / "Cache"],
        [appsup / "Microsoft" / "Teams" / "Cache"],
        [cfg / "Microsoft" / "Microsoft Teams" / "Cache"])
    add("vscode", "VS Code cache.",
        [ra / "Code" / "Cache", ra / "Code" / "CachedData", ra / "Code" / "GPUCache"],
        [appsup / "Code" / "Cache", appsup / "Code" / "CachedData"],
        [cfg / "Code" / "Cache", cfg / "Code" / "CachedData"])
    add("steam", "Steam shader and web cache.",
        [Path("C:/Program Files (x86)/Steam/steamapps/shadercache"), la / "Steam" / "htmlcache"],
        [appsup / "Steam" / "steamapps" / "shadercache"],
        [home / ".steam" / "steam" / "steamapps" / "shadercache"])
    add("nvidia", "NVIDIA shader cache.",
        [la / "NVIDIA" / "DXCache", la / "NVIDIA" / "GLCache"],
        [], [home / ".nv" / "GLCache"])
    add("pip", "Python pip cache.",
        [la / "pip" / "cache"], [lib / "Caches" / "pip"], [cache / "pip"])
    add("npm", "Node npm cache.",
        [ra / "npm-cache"], [home / ".npm" / "_cacache"], [home / ".npm" / "_cacache"])

    for name, paths in browser_caches().items():
        cat[name.lower()] = (paths, name + " cache across all profiles. Logins are kept.")

    return cat


def resolve_app(name):
    """Find a catalog app by exact name, then by substring. Returns (key, Target)
    or (None, None) if nothing matches uniquely."""
    name = name.strip().lower()
    cat = app_catalog()
    if name in cat:
        paths, desc = cat[name]
        return name, Target(name, paths, desc)
    matches = [k for k in cat if name and name in k]
    if len(matches) == 1:
        paths, desc = cat[matches[0]]
        return matches[0], Target(matches[0], paths, desc)
    return None, None


def list_apps():
    cat = sorted(app_catalog())
    say("Apps you can scan by name:", WHITE)
    line = "  "
    for a in cat:
        if len(line) + len(a) + 2 > WIDTH:
            print(FG + line + RESET)
            line = "  "
        line += a + "  "
    if line.strip():
        print(FG + line + RESET)


def scan_one(name, admin):
    """Scan one named app cache, show its size, and offer to clean it.
    Returns (freed_bytes, cleaned_name or None)."""
    key, target = resolve_app(name)
    if not target:
        cat = app_catalog()
        near = [k for k in cat if name.strip().lower() in k]
        if near:
            dim("Did you mean: " + ", ".join(near))
        else:
            dim("No app by that name in the catalog.")
            list_apps()
        return 0, None

    print()
    bar_title("SCAN  " + target.name)
    if OPTS["age_days"]:
        dim(f"Age filter on: only items older than {OPTS['age_days']} days are counted and cleaned.")
    size = dir_size_live(target.paths, target.name, min_age_days=OPTS["age_days"])
    if size == 0:
        say(f"{target.name}: nothing to clean. Not installed, or already empty.")
        for p in target.paths:
            dim(p)
        return 0, None

    say(f"{target.name}: {fmt_size(size)}", WHITE)
    dim(target.desc)
    print()
    say("This permanently wipes the files. They do NOT go to a recycle bin or trash.", FG_HOT)
    if not ask_yesno(f"Clean {target.name} ({fmt_size(size)})?"):
        dim("Skipped.")
        return 0, None

    freed, failed = clear_paths(target.paths, target.name, min_age_days=OPTS["age_days"])
    if failed == 0:
        print(f"  {FG_HOT}[ DONE ]  {FG}{target.name}  freed {fmt_size(freed)}{RESET}")
    else:
        print(f"  {WHITE}[ PART ]  {target.name}  freed {fmt_size(freed)}, "
              f"{failed} files in use skipped{RESET}")
    return freed, target.name


def run_once(admin):
    rule()
    bar_title("SCAN")
    dim("Measuring safe cache and temp locations, please wait.")
    if OPTS["age_days"]:
        dim(f"Age filter on: only items older than {OPTS['age_days']} days are counted and cleaned.")
    print()

    targets = get_targets()
    for i, t in enumerate(targets, 1):
        t.size = dir_size_live(t.paths, t.name, i, len(targets), min_age_days=OPTS["age_days"])
        flag = ""
        if t.needs_priv and not admin:
            flag = GREY + "  (needs admin)" + RESET
        print(f"  {FG}{t.name.ljust(22)}{FG_HOT}{fmt_size(t.size).rjust(10)}{RESET}{flag}")

    cleanable = sorted([t for t in targets if t.size > 0],
                       key=lambda t: t.size, reverse=True)
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
        print(f"  {FG_HOT}{str(i).rjust(2)}.{FG} {t.name.ljust(22)}"
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
        print(f"  {FG}{t.name.ljust(22)}{FG_HOT}{fmt_size(t.size).rjust(10)}{RESET}")
    print()
    say("This permanently wipes the selected files from your computer.", FG_HOT)
    say("They are deleted for good and do NOT go to a recycle bin or trash.", FG_HOT)
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
        freed, failed = clear_paths(t.paths, t.name, min_age_days=OPTS["age_days"])
        freed_total += freed
        cleaned.append(t.name)
        if failed == 0:
            print(f"  {FG_HOT}[ DONE ]  {FG}{t.name}  freed {fmt_size(freed)}{RESET}")
        else:
            print(f"  {WHITE}[ PART ]  {t.name}  freed {fmt_size(freed)}, "
                  f"{failed} files in use skipped{RESET}")

    return True, freed_total, cleaned


def _finish(freed, cleaned):
    try:
        free_str = fmt_size(shutil.disk_usage(str(Path.home())).free)
    except Exception:
        free_str = "unknown"
    rule()
    say("Freed: " + fmt_size(freed) + "   |   Disk free now: " + free_str, WHITE)
    rule()
    items = ", ".join(cleaned) if cleaned else "none"
    write_log(f"freed: {fmt_size(freed)}  |  free after: {free_str}  |  cleaned: {items}")
    dim("History log: " + str(log_path()))


def set_age(arg):
    """Set or clear the age filter from a command like 'age 30' or 'age off'."""
    arg = arg.strip().lower()
    if arg in ("off", "0", "none", "all", "any"):
        OPTS["age_days"] = None
        say("Age filter off. Scans and cleaning include files of any age.", WHITE)
        return
    if arg == "":
        if OPTS["age_days"]:
            say(f"Age filter on: only items older than {OPTS['age_days']} days.", WHITE)
        else:
            say("Age filter is off.", WHITE)
        dim("Set it with a number of days, for example: age 30   turn it off with: age off")
        return
    if arg.isdigit() and int(arg) > 0:
        OPTS["age_days"] = int(arg)
        say(f"Age filter set: only items older than {int(arg)} days will be scanned and cleaned.", WHITE)
    else:
        dim("Use a number of days, for example: age 30   or: age off")


def main():
    admin = is_admin()
    os.system("cls" if os.name == "nt" else "clear")
    banner()
    print()
    if not admin and platform.system() == "Windows":
        dim("Not running as administrator. Windows Temp is skipped in the full scan.")
        dim("Run as administrator to include it.")
        print()
    show_history()
    print()

    while True:
        bar_title("COMMAND")
        dim("scan <app>   clean one app cache by name (example: scan discord)")
        dim("all          scan all safe cache and temp locations")
        dim("age <days>   only clean items older than N days (age off to clear)")
        dim("drives       pick a drive to see where its space is used")
        dim("apps         list the apps you can scan by name")
        dim("q            quit")
        if OPTS["age_days"]:
            dim(f"age filter: on, older than {OPTS['age_days']} days")
        print()
        cmd = ask("diskclean").strip()
        low = cmd.lower()

        if low in ("q", "quit", "exit"):
            break
        elif low == "":
            pass
        elif low == "apps":
            print()
            list_apps()
        elif low == "drives":
            offer_drive_overview()
        elif low == "all":
            did, freed, cleaned = run_once(admin)
            if cleaned:
                _finish(freed, cleaned)
        elif low == "age" or low.startswith("age "):
            print()
            set_age(cmd[3:])
        elif low == "scan":
            dim("Type the app name too, for example: scan discord")
        elif low.startswith("scan "):
            freed, cleaned = scan_one(cmd[5:].strip(), admin)
            if cleaned:
                _finish(freed, [cleaned])
        else:
            dim("Unknown command. Try: scan <app>, all, age <days>, drives, apps, or q.")
        print()

    print()
    say("Done. Run diskclean again any time.", WHITE)
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(RESET + "\n  Stopped.\n")

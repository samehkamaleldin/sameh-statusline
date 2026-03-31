#!/usr/bin/env python3
"""
Claude Code Status Line
=======================
A powerline-style status bar for Claude Code with rounded pill badges.

Requirements: Python 3.10+, Nerd Font (Hack/FiraCode/etc.), optional `gh` CLI for PR status.

Data flow: stdin JSON → detection (git/stacks/tools) → segment groups → ANSI render → stdout

Stdin JSON schema (provided by Claude Code):
    workspace.project_dir    — original launch directory
    workspace.current_dir    — agent's current working directory
    context_window           — {context_window_size, used_percentage, remaining_percentage, current_usage}
    cost                     — {total_cost_usd, total_duration_ms}
    model                    — {display_name}
    vim                      — {mode}

Segment groups (separated by · ):
    project › branch+sync+health › stats · stacks · tools · context · cost · model · duration
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# =============================================================================
# ANSI helpers
# =============================================================================

def _ansi_fg(color_index: int) -> str:
    """256-colour foreground escape."""
    return f"\x1b[38;5;{color_index}m"


def _sanitize(s: str) -> str:
    """Strip non-printable and ESC characters to prevent terminal injection."""
    return re.sub(r'[\x00-\x1f\x7f]', '', s)

def _display_width(s: str) -> int:
    """Return terminal display width of a string, accounting for wide/emoji chars."""
    w = 0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ('W', 'F'):
            w += 2
        elif unicodedata.category(ch) in ('Mn', 'Me', 'Cf'):
            pass  # zero-width
        else:
            w += 1
    return w

RESET = "\x1b[0m"
BOLD  = "\x1b[1m"


# =============================================================================
# Colour palette  (256-colour, muted tones suited to dimmed terminal display)
# =============================================================================

COLOR_SEP_ESC = _ansi_fg(240)  # separator glyph (pre-built escape)

# Semantic color indices (raw 256-color ints for _pill / inline use)
COLOR_PILL_TEXT       = 250   # off-white text inside dark pills
COLOR_MUTED           = 243   # muted gray (commit age, secondary info)
COLOR_HOST_ICON       = 110   # project/folder icon
COLOR_BRANCH          = 109   # branch icon
COLOR_GIT_CLEAN       = 108   # green (sync ok, clean, insertions)
COLOR_GIT_BEHIND      = 167   # coral (behind, deletions)
COLOR_GIT_DIRTY       = 214   # amber (dirty indicator, modified)
COLOR_GIT_STASH       = 147   # lavender (stash, untracked)
COLOR_GIT_STAGED      = 78    # teal green
COLOR_GIT_INSERTIONS  = 108   # green
COLOR_GIT_DELETIONS   = 167   # coral
COLOR_WORKTREE        = 141   # purple
COLOR_COST            = 178   # gold
COLOR_MODEL           = 146   # soft gray-green
COLOR_SESSION         = 109   # slate
COLOR_VIM_INSERT      = 108   # green
COLOR_VIM_NORMAL      = 141   # purple
COLOR_SEPARATOR       = 240   # thin chevron separator


# =============================================================================
# Stack badge colours  — (fg, bg) pairs in 256-colour
# Brand-inspired, muted for dimmed terminal display.
# =============================================================================

# Stack badge scheme: (icon_fg, brand_bg). Text always uses COLOR_PILL_TEXT.
_STACK_BADGE: dict[str, tuple[int, int]] = {
    #                icon_fg   bg
    "Python":     (  220,      24),   # yellow icon on CPython blue
    "Next.js":    (  231,     233),   # white icon on near-black
    "Node":       (  117,      22),   # sky blue icon on dark green
    "TypeScript": (  231,      26),   # white icon on TS blue
    "React":      (   45,      23),   # cyan icon on dark teal
    "Vue":        (   83,      22),   # bright green on dark green
    "Nuxt":       (   83,      22),   # same green family
    "Angular":    (  203,      88),   # red icon on dark red
    "Svelte":     (  208,      52),   # orange icon on dark red
    "Go":         (   45,      23),   # cyan icon on dark teal
    "Rust":       (  208,      52),   # orange icon on dark brown
    "Ruby":       (  197,      88),   # bright red on dark red
}
_STACK_BADGE_FALLBACK = (231, 238)  # white on grey

# =============================================================================
# Context window — gradient anchors and mood progression
# =============================================================================

CTX_CRITICAL_THRESHOLD = 85

# Fill-bar gradient: list of (pct_used, fg_256color) anchors.
# Color transitions from green → yellow-green → yellow → amber → orange → red
# as context consumption rises.
CTX_FILL_GRADIENT: list[tuple[int, int]] = [
    (0,   28),   # dark green
    (15, 34),   # medium green
    (30, 40),   # bright green
    (45, 148),  # yellow-green
    (55, 184),  # yellow
    (65, 214),  # amber/orange
    (75, 208),  # orange
    (85, 202),  # deep orange
    (92, 196),  # bright red
    (100, 160), # dark red
]


_MOOD_STEPS: list[tuple[int, str]] = [
    (85, "☢️"), (80, "🤯"), (75, "😱"), (65, "😰"), (55, "😟"),
    (45, "😐"), (35, "🙂"), (25, "😊"), (15, "🤓"), (5, "😎"),
]
_MOOD_DEFAULT = "😎"

# =============================================================================
# Cost spending-journey icons (threshold_usd, emoji)
# =============================================================================

COST_MIN_DISPLAY_USD = 0.01
SESSION_MIN_DISPLAY_MS = 60_000

_COST_ICONS: list[tuple[float, str]] = [
    (0, "🆓"), (0.5, "🫧"), (1, "🪙"), (2, "💵"), (3, "☕"),
    (4, "🍩"), (5, "🌯"), (7, "🍕"), (10, "🍱"), (13, "🎫"),
    (15, "🧋"), (16, "📚"), (20, "👕"), (25, "🎮"), (30, "👟"),
    (35, "🍷"), (40, "💇"), (45, "💈"), (50, "🛒"), (60, "⛽"),
    (70, "💊"), (80, "🎭"), (90, "🧳"), (100, "💳"), (110, "📱"),
    (120, "🎸"), (130, "🎿"), (140, "✈️"), (150, "🏨"), (160, "🎰"),
    (170, "💎"), (180, "👔"), (190, "🎩"), (200, "🔥"), (220, "😰"),
    (240, "🚗"), (260, "💸"), (280, "🏠"), (300, "🤑"), (320, "😱"),
    (340, "🏦"), (350, "🚨"), (360, "📉"), (380, "🆘"), (400, "💀"),
    (420, "☠️"), (440, "🪦"), (460, "☢️"), (480, "🌋"), (500, "💥"),
]

# =============================================================================
# Git remote host mapping — (icon, color)
# =============================================================================

_GIT_HOSTS: list[tuple[str, str, int]] = [
    # (url_match, icon, color)
    ("github",       "\U000F02A4", COLOR_PILL_TEXT),   # 󰊤
    ("gitlab",       "\U000F0BA0", 208),               # 󰮠
    ("dev.azure",    "\U000F0805", 33),                # 󰠅
    ("visualstudio", "\U000F0805", 33),                # 󰠅
    ("bitbucket",    "\ue703",     33),                #
]

# =============================================================================
# Subprocess timeouts
# =============================================================================

TIMEOUT_DEFAULT = 0.5
TIMEOUT_FAST    = 0.3
TIMEOUT_GIT_STATUS = 0.8

# =============================================================================
# Nerd Font v3 / Powerline glyphs
# =============================================================================

class Icon:
    # Powerline
    CHEVRON_R  = "\ue0b1"   # thin right chevron ›
    # Nerd Font
    FOLDER     = "\uf07c"   #
    BRANCH     = "\ue0a0"   #
    AHEAD      = "\u21e1"   # ⇡
    BEHIND     = "\u21e3"   # ⇣
    STAGED     = "\uf067"   #
    MODIFIED   = "\uf069"   #
    UNTRACKED  = "\uf128"   #
    PYTHON     = "\ue606"   #
    NODE       = "\ue718"   #
    TYPESCRIPT = "\ue628"   #
    RUST       = "\ue7a8"   #
    GO         = "\ue626"   #
    RUBY       = "\ue791"   #
    REACT      = "\ue7ba"   #
    VUE        = "\ue6a0"   #
    NEXT       = "\ue76c"   #
    ANGULAR    = "\ue753"   #
    SVELTE     = "\ue697"   #
    MODEL      = "\U000F0626"  # 󰘦 (brain — model)
    CLOCK      = "\U000F0954"  # 󰥔 nf-md-clock-outline
    VIM        = "\ue62b"   #  (vim mode)
    # Git indicators
    GIT_SYNCED = "\U000F04E6"  # 󰓦 cloud-check
    GIT_LOCAL  = "\uf1d2"   # 󰇲 generic git (no remote)
    STASH      = "\U000F01A7"  # 󰆧 nf-md-archive
    GIT_DIRTY  = "\U000F02A0"  # 󰊠 shield-alert
    GIT_CLEAN  = "\U000F05E0"  # 󰗠 leaf
    NO_GIT     = "\U000F0193"  # 󰆓 cancel
    WORKTREE   = "\U000F0AB9"  # 󰪹 source-branch
    # PR status
    PR_MERGED  = "\U000F0450"  # 󰑐 merge
    PR_DRAFT   = "\U000F0B4C"  # 󰭌 draft
    PR_APPROVED = "\U000F05E0" # 󰗠 check
    PR_CHANGES = "\U000F0028"  # 󰀨 alert
    PR_REVIEW  = "\U000F0208"  # 󰈈 eye
    PR_OPEN    = "\U000F0041"  # 󰁁 open
    # Dev-tools segment
    DOCKER     = "\ue7b0"   # nf-dev-docker
    KUBERNETES = "\U000F10FE"  # nf-md-kubernetes
    HELM       = "\u2388"   # ⎈ ship wheel (Helm brand symbol)
    AWS        = "\U000F0E0F"  # nf-md-aws
    AZURE      = "\U000F0805"  # nf-md-microsoft_azure
    GCP        = "\U000F11F6"  # nf-md-google_cloud
    VERCEL     = "\u25b2"   # ▲ triangle (Vercel brand symbol)
    TERRAFORM  = "\U000F1062"  # nf-md-terraform
    ANSIBLE    = "\U000F108A"  # nf-md-ansible
    GITHUB_CI  = "\U000F02A4"  # nf-md-github
    GITLAB_CI  = "\U000F0BA0"  # nf-md-gitlab
    NPM        = "\ue71e"   # nf-dev-npm
    NGINX      = "\ue776"   # nf-dev-nginx
    SUPABASE   = "\u26a1"   # ⚡ lightning bolt (Supabase brand symbol)
    FIREBASE   = "\U000F0967"  # nf-md-firebase
    POSTGRESQL = "\ue76e"   # nf-dev-postgresql
    REDIS      = "\ue76d"   # nf-dev-redis
    MONGODB    = "\U000F0016"  # nf-dev-mongodb (leaf)


# =============================================================================
# Data model
# =============================================================================

@dataclass
class Segment:
    """One coloured token inside a group."""
    styled: str        # ANSI-escaped string to print
    width:  int        # visible character width (no escapes)


@dataclass
class GitInfo:
    branch:     str = ""
    ahead:      int = 0
    behind:     int = 0
    staged:     int = 0
    modified:   int = 0
    untracked:  int = 0
    insertions: int = 0
    deletions:  int = 0
    stash:      int = 0
    dirty:      bool = False
    has_upstream: bool = False
    is_worktree: bool = False
    is_bare:     bool = False
    worktree_name: str = ""
    last_commit_ts: int = 0


# =============================================================================
# Utilities
# =============================================================================

def fmt_tokens(n: int) -> str:
    """Format a token count as human-readable string (e.g. 1500 → '1.5K', 1200000 → '1.2M')."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    return str(n)


def _run(cmd: list[str], cwd: str, timeout: float = 0.5) -> str:
    """Run a subprocess and return stdout, or '' on any error (timeout, missing binary, etc.)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.stdout.strip()
    except Exception:
        return ""


_DEFAULT_BG = 237  # dark grey pill background for general badges

# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
_CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "claude-statusline"
_CACHE_TTL = 30       # seconds — stacks, tools, remote
_CACHE_TTL_PR = 300   # seconds — PR status (changes infrequently)
_CACHE_HASH_LEN = 16

def _cache_path(project_dir: str) -> Path:
    h = hashlib.sha256(project_dir.encode()).hexdigest()[:_CACHE_HASH_LEN]
    return _CACHE_DIR / f"{h}.json"

def _load_cache(project_dir: str) -> Optional[dict]:
    try:
        cp = _cache_path(project_dir)
        data = json.loads(cp.read_text())
        now = time.time()
        if now - data.get("ts", 0) > _CACHE_TTL:
            return None
        # PR has its own TTL — expire it independently
        if data.get("pr_info") and now - data.get("pr_ts", 0) > _CACHE_TTL_PR:
            data["pr_info"] = None
        return data
    except Exception:
        return None

def _save_cache(project_dir: str, payload: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload["ts"] = time.time()
        if "pr_info" in payload and "pr_ts" not in payload:
            payload["pr_ts"] = payload["ts"]
        cp = _cache_path(project_dir)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=_CACHE_DIR, delete=False, suffix=".tmp"
        ) as tf:
            json.dump(payload, tf)
            tmp = Path(tf.name)
        tmp.replace(cp)  # atomic on POSIX
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _relative_age(epoch: int) -> str:
    delta = int(time.time()) - epoch
    if delta < 60:
        return "now"
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < 86400:
        return f"{delta // 3600}h"
    if delta < 604800:
        return f"{delta // 86400}d"
    if delta < 2592000:
        return f"{delta // 604800}w"
    return f"{delta // 2592000}mo"

def _pill(bg: int, icon_fg: int, text_fg: int, icon: str, label: str) -> Segment:
    """Render a rounded pill badge with powerline half-circle caps.

    Args:
        bg: 256-color background index for the pill interior.
        icon_fg: 256-color foreground index for the icon character.
        text_fg: 256-color foreground index for the label text.
        icon: A single glyph (Nerd Font or emoji) displayed at the left.
        label: Text displayed after the icon inside the pill.
    """
    bg_esc     = f"\x1b[48;5;{bg}m"
    pill_left  = f"\x1b[38;5;{bg}m\ue0b6{RESET}"
    pill_right = f"\x1b[38;5;{bg}m\ue0b4{RESET}"
    styled  = f"{pill_left}{bg_esc} \x1b[38;5;{icon_fg}m{icon}\x1b[38;5;{text_fg}m {label} {RESET}{pill_right}"
    visible = f"\ue0b6 {icon} {label} \ue0b4"
    return Segment(styled, _display_width(visible))


# =============================================================================
# Git
# =============================================================================

_RE_INSERTIONS = re.compile(r"(\d+) insertion")
_RE_DELETIONS  = re.compile(r"(\d+) deletion")

def get_git_info(cwd: str) -> Optional[GitInfo]:
    """Parse git state from the working directory. Handles normal repos, worktrees, and bare repos."""
    # Check if we're inside a git repo at all
    git_dir = _run(["git", "--no-optional-locks", "rev-parse", "--git-dir"], cwd=cwd, timeout=TIMEOUT_FAST)
    if not git_dir:
        return None

    info = GitInfo()

    # Detect bare repo — git status doesn't work in bare repos
    is_bare = _run(["git", "--no-optional-locks", "rev-parse", "--is-bare-repository"], cwd=cwd, timeout=TIMEOUT_FAST) == "true"
    info.is_bare = is_bare

    if is_bare:
        # Bare repo: only branch + last commit available (no working tree)
        info.branch = _sanitize(
            _run(["git", "--no-optional-locks", "symbolic-ref", "--short", "HEAD"], cwd=cwd, timeout=TIMEOUT_FAST)
            or "HEAD"
        )
    else:
        # Normal/worktree repo: full porcelain v2 parse
        raw = _run(
            ["git", "--no-optional-locks", "status", "--branch", "--porcelain=v2",
             "--untracked-files=normal"],
            cwd=cwd, timeout=TIMEOUT_GIT_STATUS,
        )
        if not raw:
            return None

        for line in raw.splitlines():
            if line.startswith("# branch.head "):
                info.branch = _sanitize(line[14:])
                if info.branch == "(detached)":
                    info.branch = _run(
                        ["git", "--no-optional-locks", "rev-parse", "--short", "HEAD"],
                        cwd=cwd, timeout=TIMEOUT_FAST,
                    ) or "HEAD"
            elif line.startswith("# branch.ab "):
                info.has_upstream = True
                parts = line.split()
                try:
                    info.ahead = int(parts[2].lstrip("+"))
                    info.behind = abs(int(parts[3].lstrip("-")))
                except (ValueError, IndexError):
                    pass
            elif line.startswith("1 ") or line.startswith("2 "):
                xy = line.split()[1] if len(line.split()) > 1 else ""
                if len(xy) >= 2:
                    if xy[0] in "MADRCU":
                        info.staged += 1
                    if xy[1] in "MD":
                        info.modified += 1
            elif line.startswith("? "):
                info.untracked += 1

        # Worktree detection: git-dir vs common-dir
        git_common = _run(["git", "--no-optional-locks", "rev-parse", "--git-common-dir"], cwd=cwd, timeout=TIMEOUT_FAST)
        if git_dir and git_common and os.path.abspath(git_common) != os.path.abspath(git_dir):
            info.is_worktree = True
            info.worktree_name = Path(git_dir).name

        # Diff stats (insertions/deletions) — not available in porcelain v2
        stat = _run(["git", "--no-optional-locks", "diff", "--shortstat"], cwd=cwd)
        stat_staged = _run(["git", "--no-optional-locks", "diff", "--cached", "--shortstat"], cwd=cwd)
        for diff_output in (stat, stat_staged):
            match_ins = _RE_INSERTIONS.search(diff_output)
            match_del = _RE_DELETIONS.search(diff_output)
            if match_ins:
                info.insertions += int(match_ins.group(1))
            if match_del:
                info.deletions += int(match_del.group(1))

        info.dirty = bool(info.staged or info.modified or info.untracked)

    # Last commit timestamp (works in both bare and normal repos)
    ts_str = _run(["git", "--no-optional-locks", "log", "-1", "--format=%ct"], cwd=cwd, timeout=TIMEOUT_FAST)
    if ts_str:
        try:
            info.last_commit_ts = int(ts_str)
        except ValueError:
            pass

    # Stash count (works in both bare and normal repos)
    stash_out = _run(["git", "--no-optional-locks", "stash", "list", "--format=%H"], cwd=cwd, timeout=TIMEOUT_FAST)
    if stash_out:
        info.stash = len(stash_out.splitlines())
    return info


# =============================================================================
# PR detection
# =============================================================================

def _detect_pr(cwd: str) -> Optional[dict]:
    """Return PR info dict or None. Uses gh CLI."""
    if not shutil.which("gh"):
        return None
    try:
        r = subprocess.run(
            ["gh", "pr", "view", "--json", "state,reviewDecision,isDraft,number"],
            capture_output=True, text=True, timeout=0.5, cwd=cwd,
        )
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
        # Validate PR number is an int
        if "number" in data and not isinstance(data["number"], int):
            data["number"] = ""
        return data
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


_PR_STATES: dict[str, tuple[str, int]] = {
    "MERGED":            (Icon.PR_MERGED,  COLOR_WORKTREE),   # purple
    "DRAFT":             (Icon.PR_DRAFT,   COLOR_MUTED),      # gray
    "APPROVED":          (Icon.PR_APPROVED, COLOR_GIT_CLEAN),  # green
    "CHANGES_REQUESTED": (Icon.PR_CHANGES, COLOR_GIT_DELETIONS), # coral
    "REVIEW_REQUIRED":   (Icon.PR_REVIEW,  180),              # amber
    "OPEN":              (Icon.PR_OPEN,    COLOR_BRANCH),     # teal
}

def _pr_indicator(pr: Optional[dict]) -> tuple[str, str]:
    """Return (styled_fragment, visible_fragment) for PR status."""
    if not pr:
        return ("", "")
    state = pr.get("state", "")
    decision = pr.get("reviewDecision", "")
    is_draft = pr.get("isDraft", False)
    num = pr.get("number", "")

    if state == "MERGED":
        key = "MERGED"
    elif is_draft:
        key = "DRAFT"
    elif decision in _PR_STATES:
        key = decision
    else:
        key = "OPEN"

    icon, fg = _PR_STATES[key]
    label = f"{icon}#{num}" if isinstance(num, int) else icon
    return (f"\x1b[38;5;{fg}m {label}", f" {label}")


# =============================================================================
# Stack detection
# =============================================================================

# Ordered by precedence: checked against package.json contents
_FRAMEWORKS: list[tuple[str, str, str]] = [
    ('"next"',          "Next.js",    Icon.NEXT),
    ('"nuxt"',          "Nuxt",       Icon.VUE),
    ('"@angular/core"', "Angular",    Icon.ANGULAR),
    ('"svelte"',        "Svelte",     Icon.SVELTE),
    ('"react"',         "React",      Icon.REACT),
    ('"vue"',           "Vue",        Icon.VUE),
]

_RUNTIMES: list[tuple[list[str], str, str]] = [
    (["pyproject.toml", "requirements.txt", "setup.py", "uv.lock"], "Python",     Icon.PYTHON),
    (["go.mod"],                                                      "Go",         Icon.GO),
    (["Cargo.toml"],                                                  "Rust",       Icon.RUST),
    (["Gemfile"],                                                     "Ruby",       Icon.RUBY),
]


def _pyproject_name(d: str) -> str:
    """Extract project name from pyproject.toml at root or one level deep."""
    p = Path(d)
    candidates = [p / "pyproject.toml"]
    try:
        candidates.extend(
            c for c in p.glob("*/pyproject.toml")
            if not c.parent.name.startswith(".")
        )
    except OSError:
        pass
    for toml in candidates[:5]:
        try:
            for line in toml.read_bytes()[:4096].decode(errors="replace").splitlines():
                if line.strip().startswith("name") and "=" in line:
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
        except (OSError, UnicodeDecodeError):
            pass
    return ""


def _get_python_venv(project_dir: str = "") -> str:
    """Return a meaningful env label: project name from pyproject.toml, or conda env, or fallback."""
    virtual_env = os.environ.get("VIRTUAL_ENV", "")
    if virtual_env:
        name = _pyproject_name(project_dir) if project_dir else ""
        return name or Path(virtual_env).name

    conda_env = os.environ.get("CONDA_DEFAULT_ENV", "")
    if conda_env:
        return conda_env

    if project_dir:
        for vname in (".venv", "venv"):
            if (Path(project_dir) / vname / "pyvenv.cfg").exists():
                name = _pyproject_name(project_dir)
                return name or vname
    return ""


def _get_runtime_version(label: str, project_dir: str) -> str:
    """Get the runtime version string (e.g. '3.12', '20.11', '1.78')."""
    p = Path(project_dir)

    # Try file-based detection first (fast, no subprocess)
    if label == "Python":
        pv = p / ".python-version"
        if pv.exists():
            try:
                return pv.read_text(errors="replace").strip().split("\n")[0]
            except (OSError, UnicodeDecodeError):
                pass
        out = _run(["python3", "--version"], cwd=project_dir, timeout=0.3)
        m = re.search(r"(\d+\.\d+)", out)
        return m.group(1) if m else ""

    if label in ("Node", "TypeScript"):
        for vf in (".node-version", ".nvmrc"):
            f = p / vf
            if f.exists():
                try:
                    v = f.read_text(errors="replace").strip().lstrip("v")
                    if v:
                        return v
                except (OSError, UnicodeDecodeError):
                    pass
        out = _run(["node", "--version"], cwd=project_dir, timeout=0.3)
        m = re.search(r"v?(\d+\.\d+)", out)
        return m.group(1) if m else ""

    if label == "Go":
        gm = p / "go.mod"
        if gm.exists():
            try:
                for line in gm.read_text(errors="replace").splitlines():
                    if line.startswith("go "):
                        return line.split()[1]
            except (OSError, UnicodeDecodeError):
                pass
        out = _run(["go", "version"], cwd=project_dir, timeout=0.3)
        m = re.search(r"go(\d+\.\d+)", out)
        return m.group(1) if m else ""

    if label == "Rust":
        tc = p / "rust-toolchain.toml"
        if tc.exists():
            try:
                m = re.search(r'channel\s*=\s*"([^"]+)"', tc.read_text(errors="replace"))
                if m:
                    return m.group(1)
            except (OSError, UnicodeDecodeError):
                pass
        out = _run(["rustc", "--version"], cwd=project_dir, timeout=0.3)
        m = re.search(r"(\d+\.\d+)", out)
        return m.group(1) if m else ""

    if label == "Ruby":
        rv = p / ".ruby-version"
        if rv.exists():
            try:
                return rv.read_text(errors="replace").strip()
            except (OSError, UnicodeDecodeError):
                pass
        out = _run(["ruby", "--version"], cwd=project_dir, timeout=0.3)
        m = re.search(r"(\d+\.\d+)", out)
        return m.group(1) if m else ""

    return ""


def _get_framework_version(label: str, pkg_path: Path) -> str:
    """Get framework version from a specific package.json."""
    pkg_names = {
        "Next.js": "next", "Nuxt": "nuxt", "Angular": "@angular/core",
        "Svelte": "svelte", "React": "react", "Vue": "vue",
    }
    pkg_name = pkg_names.get(label, "")
    if not pkg_name:
        return ""
    try:
        pkg_data = json.loads(pkg_path.read_text(errors="replace"))
        deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
        if pkg_name in deps:
            v = deps[pkg_name].lstrip("^~>=<")
            m = re.match(r"(\d+\.\d+)", v)
            return m.group(1) if m else ""
    except (OSError, json.JSONDecodeError):
        pass
    return ""


def _detect_js_stack(pkg_path: Path) -> tuple[str, str]:
    """From a package.json, return (label, icon) for the best JS framework or runtime."""
    try:
        text = pkg_path.read_text(errors="replace")
    except OSError:
        text = ""
    for key, label, icon in _FRAMEWORKS:
        if key in text:
            return label, icon
    if (pkg_path.parent / "tsconfig.json").exists():
        return "TypeScript", Icon.TYPESCRIPT
    return "Node", Icon.NODE


def detect_stacks(project_dir: str) -> list[tuple[str, str, str, str]]:
    """Return list of (label, icon, version, extra) for all detected languages.

    For Python, extra holds the active virtual environment name (from VIRTUAL_ENV
    or CONDA_DEFAULT_ENV), or empty string when no venv is active.
    For all other stacks, extra is always an empty string.

    Scans root and up to 2 levels of subdirectories. Deduplicates by label,
    keeping the first match. Returns at most one JS-ecosystem entry.
    """
    p = Path(project_dir)
    seen_labels: set[str] = set()
    results: list[tuple[str, str, str, str]] = []
    js_labels = {"Node", "TypeScript", "Next.js", "Nuxt", "Angular", "Svelte", "React", "Vue"}
    has_js = False

    python_venv = _get_python_venv(project_dir)

    # Collect all directories to scan (root + up to depth 2)
    dirs = [p]
    for child in sorted(p.iterdir()) if p.is_dir() else []:
        if child.is_dir() and not child.name.startswith(".") and child.name != "node_modules":
            dirs.append(child)
            for grandchild in sorted(child.iterdir()) if child.is_dir() else []:
                if grandchild.is_dir() and not grandchild.name.startswith(".") and grandchild.name != "node_modules":
                    dirs.append(grandchild)

    for d in dirs:
        # Check JS/TS ecosystem (only keep best one)
        pkg = d / "package.json"
        if pkg.exists() and not has_js:
            label, icon = _detect_js_stack(pkg)
            if label in js_labels - {"Node", "TypeScript"}:
                ver = _get_framework_version(label, pkg)
            else:
                ver = _get_runtime_version(label, project_dir)
            results.append((label, icon, ver, ""))
            seen_labels.add(label)
            has_js = True

        # Check non-JS runtimes
        for markers, label, icon in _RUNTIMES:
            if label not in seen_labels and any((d / m).exists() for m in markers):
                ver = _get_runtime_version(label, project_dir)
                extra = python_venv if label == "Python" else ""
                results.append((label, icon, ver, extra))
                seen_labels.add(label)

    return results


# =============================================================================
# Dev-tools detection
# =============================================================================


# Each entry: (label, icon_or_letters, icon_fg_color, is_letters)
# is_letters=True  → render as bold coloured text (no icon character)
# is_letters=False → render as a Nerd Font / Unicode glyph character
_TOOL_DEFS: list[tuple[str, str, int, bool]] = [
    # Containers / Orchestration
    ("Docker",     Icon.DOCKER,     39,  False),
    ("Kubernetes", Icon.KUBERNETES, 33,  False),
    ("Helm",       Icon.HELM,       26,  False),
    # Cloud Providers
    ("AWS",        Icon.AWS,        214, False),
    ("Azure",      Icon.AZURE,      33,  False),
    ("GCP",        Icon.GCP,        69,  False),
    ("Vercel",     Icon.VERCEL,     231, False),
    # IaC / DevOps
    ("Terraform",  Icon.TERRAFORM,  134, False),
    ("Ansible",    Icon.ANSIBLE,    196, False),
    # CI/CD
    ("GitHub CI",  Icon.GITHUB_CI,  231, False),
    ("GitLab CI",  Icon.GITLAB_CI,  208, False),
    # Data / ML
    ("DVC",        "DV",            134, True),
    # Databases
    ("PostgreSQL", Icon.POSTGRESQL, 33,  False),
    ("Redis",      Icon.REDIS,      160, False),
    ("MongoDB",    Icon.MONGODB,    35,  False),
    # Package managers
    ("pnpm",       "pn",            214, True),
    ("yarn",       "yn",            75,  True),
    ("npm",        Icon.NPM,        160, False),
    ("uv",         "uv",            171, True),
    ("poetry",     "po",            111, True),
    # Other
    ("Nginx",      Icon.NGINX,      34,  False),
    ("Supabase",   Icon.SUPABASE,   42,  False),
    ("Firebase",   Icon.FIREBASE,   220, False),
]


_TOOL_LABEL_MAP: dict[str, tuple[str, int, bool]] = {
    label: (glyph, fg, is_letters) for label, glyph, fg, is_letters in _TOOL_DEFS
}


def _has_cli(cmd: str) -> bool:
    """Return True if the given CLI command is available on PATH."""
    return shutil.which(cmd) is not None


_env_file_cache: dict[str, str] = {}

def _env_or_compose_mentions(project_dir: str, *keywords: str) -> bool:
    """Return True if any keyword appears in .env* files or docker-compose*.yml."""
    if project_dir not in _env_file_cache:
        p = Path(project_dir)
        texts: list[str] = []
        for pattern in ("*.env", ".env", ".env.*", "docker-compose*.yml", "docker-compose*.yaml"):
            for t in p.glob(pattern):
                try:
                    texts.append(t.read_bytes()[:65536].decode("utf-8", errors="replace").lower())
                except OSError:
                    pass
        _env_file_cache[project_dir] = "\n".join(texts)
    combined = _env_file_cache[project_dir]
    return any(kw.lower() in combined for kw in keywords)


def detect_tools(project_dir: str) -> list[str]:
    """Return a list of detected tool labels (ordered as _TOOL_DEFS)."""
    p = Path(project_dir)
    detected: list[str] = []

    def _exists(*rel_paths: str) -> bool:
        return any((p / rp).exists() for rp in rel_paths)

    def _glob_any(*patterns: str) -> bool:
        return any(next(p.glob(pat), None) is not None for pat in patterns)

    # --- Containers / Orchestration ---
    if _exists("Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".dockerignore"):
        detected.append("Docker")

    if _exists("k8s", "kustomization.yaml") or _glob_any("*.kube"):
        detected.append("Kubernetes")

    if _exists("Chart.yaml"):
        detected.append("Helm")

    # --- Cloud Providers ---
    if _exists("samconfig.toml", ".aws", "cdk.json") or _has_cli("aws"):
        detected.append("AWS")

    if _exists(".azure", "azure-pipelines.yml"):
        detected.append("Azure")

    if _exists("app.yaml", ".gcloud"):
        detected.append("GCP")

    if _exists("vercel.json", ".vercel"):
        detected.append("Vercel")

    # --- IaC / DevOps ---
    if _exists(".terraform") or _glob_any("*.tf"):
        detected.append("Terraform")

    if _exists("ansible.cfg", "playbook.yml", "ansible"):
        detected.append("Ansible")

    # --- CI/CD ---
    if _exists(".github/workflows"):
        detected.append("GitHub CI")

    if _exists(".gitlab-ci.yml"):
        detected.append("GitLab CI")

    # --- Data / ML ---
    if _exists(".dvc", "dvc.yaml"):
        detected.append("DVC")

    # --- Databases (check .env / docker-compose mentions) ---
    if _env_or_compose_mentions(project_dir, "postgres", "postgresql"):
        detected.append("PostgreSQL")

    if _env_or_compose_mentions(project_dir, "redis"):
        detected.append("Redis")

    if _env_or_compose_mentions(project_dir, "mongo", "mongodb"):
        detected.append("MongoDB")

    # --- Package Managers (mutually exclusive lock-file checks) ---
    if _exists("pnpm-lock.yaml"):
        detected.append("pnpm")
    elif _exists("yarn.lock"):
        detected.append("yarn")
    elif _exists("package-lock.json"):
        detected.append("npm")

    if _exists("uv.lock"):
        detected.append("uv")
    elif _exists("poetry.lock"):
        detected.append("poetry")

    # --- Other ---
    if _exists("nginx.conf", "nginx"):
        detected.append("Nginx")

    if _exists("supabase"):
        detected.append("Supabase")

    if _exists("firebase.json", ".firebaserc"):
        detected.append("Firebase")

    return detected


def build_tools_group(detected_labels: list[str]) -> list[Segment]:
    """Render detected dev tools as icons-only on a shared dark charcoal background."""
    if not detected_labels:
        return []

    label_to_def = _TOOL_LABEL_MAP

    bg_esc   = f"\x1b[48;5;{_DEFAULT_BG}m"
    parts_styled:  list[str] = []
    parts_visible: list[str] = []

    for label in detected_labels:
        if label not in label_to_def:
            continue
        glyph, fg, is_letters = label_to_def[label]
        fg_esc = f"\x1b[38;5;{fg}m"
        if is_letters:
            # Bold coloured letters on charcoal bg
            parts_styled.append(f"{bg_esc}{fg_esc}{BOLD}{glyph}{RESET}")
        else:
            parts_styled.append(f"{bg_esc}{fg_esc}{glyph}{RESET}")
        parts_visible.append(glyph)

    if not parts_styled:
        return []

    # Wrap entire tools group in half-circle pill
    lhc = f"\x1b[38;5;{_DEFAULT_BG}m\ue0b6{RESET}"
    rhc = f"\x1b[38;5;{_DEFAULT_BG}m\ue0b4{RESET}"
    inner_styled  = f"{bg_esc} {RESET}".join(parts_styled)
    inner_visible = " ".join(parts_visible)
    styled  = f"{lhc}{bg_esc} {RESET}{inner_styled}{bg_esc} {RESET}{rhc}"
    visible = f"\ue0b6 {inner_visible} \ue0b4"
    return [Segment(styled, _display_width(visible))]


# =============================================================================
# Segment-group builders
# =============================================================================

def _git_host_info(git: Optional[GitInfo], cwd: str) -> tuple[str, int, str]:
    """Return (icon, color, repo_name) based on git remote."""
    if not git:
        return (Icon.FOLDER, COLOR_HOST_ICON, "")
    remote = _run(["git", "--no-optional-locks", "remote", "get-url", "origin"], cwd=cwd, timeout=TIMEOUT_FAST)
    if not remote:
        return (Icon.GIT_LOCAL, COLOR_BRANCH, "")
    repo_name = ""
    r = remote.rstrip("/")
    if r.endswith(".git"):
        r = r[:-4]
    if ":" in r and "@" in r:
        repo_name = r.split(":")[-1]
    else:
        parts = r.split("/")
        if len(parts) >= 2:
            repo_name = "/".join(parts[-2:])
    rl = remote.lower()
    for match, icon, color in _GIT_HOSTS:
        if match in rl:
            return (icon, color, repo_name)
    return (Icon.GIT_LOCAL, COLOR_BRANCH, repo_name)


def build_project_group(name: str, git: Optional[GitInfo], cwd: str = "",
                        remote_info: Optional[tuple[str, int, str]] = None, pr_info: Optional[dict] = None,
                        truncate_repo: bool = False) -> list[Segment]:
    """Render the unified project + git segment.

    Renders: host icon, repo name, branch, sync arrows, stash count,
    dirty/clean indicator, PR badge, and diff stats — all in one pill.

    Args:
        name: Fallback project name (directory basename) when no git remote.
        git: Parsed git state; None renders a no-git indicator.
        cwd: Working directory for subprocess calls (used when remote_info is None).
        remote_info: Cached (icon, color, repo_name) tuple to avoid subprocess.
        pr_info: Cached PR state dict from _detect_pr(); or None.
        truncate_repo: Drop org/owner prefix for narrow terminals.
    """
    # Two parallel string builders: `_styled` carries ANSI escapes for output;
    # `_visible` carries bare characters for display-width measurement.
    bg = _DEFAULT_BG
    bg_esc = f"\x1b[48;5;{bg}m"
    pill_left  = f"\x1b[38;5;{bg}m\ue0b6{RESET}"
    pill_right = f"\x1b[38;5;{bg}m\ue0b4{RESET}"
    sep = f"\x1b[38;5;{COLOR_SEPARATOR}m\ue0b1"

    # Resolve remote host icon and repo name
    if remote_info:
        host_icon, host_fg, repo_name = remote_info
    else:
        host_icon, host_fg, repo_name = _git_host_info(git, cwd)
    display_name = repo_name or name
    if truncate_repo and "/" in display_name:
        display_name = display_name.split("/")[-1]

    # Project name fragment
    styled  = f"\x1b[38;5;{host_fg}m{host_icon} \x1b[38;5;{COLOR_PILL_TEXT}m{display_name}"
    visible = f"{host_icon} {display_name}"

    if git and git.branch:
        # Repo type prefix (bare or worktree)
        prefix_styled = prefix_visible = ""
        if git.is_bare:
            prefix_styled  = f"\x1b[38;5;{COLOR_MUTED}m\U0001F333 BARE "
            prefix_visible = "\U0001F333 BARE "
        elif git.is_worktree:
            prefix_styled  = f"\x1b[38;5;{COLOR_WORKTREE}m{Icon.WORKTREE} "
            prefix_visible = f"{Icon.WORKTREE} "

        # Commit age
        age_styled = age_visible = ""
        if git.last_commit_ts:
            age = _relative_age(git.last_commit_ts)
            age_styled  = f" \x1b[38;5;{COLOR_MUTED}m{age}"
            age_visible = f" {age}"

        # Branch
        styled  += f" {sep} {prefix_styled}\x1b[38;5;{COLOR_BRANCH}m{Icon.BRANCH} \x1b[38;5;{COLOR_PILL_TEXT}m{_sanitize(git.branch)}{age_styled}"
        visible += f" \ue0b1 {prefix_visible}{Icon.BRANCH} {git.branch}{age_visible}"

        # Sync: ⇡n ⇣n / cloud-check (synced) / git-local (no upstream)
        if git.ahead or git.behind:
            if git.ahead:
                styled  += f" \x1b[38;5;{COLOR_GIT_CLEAN}m{Icon.AHEAD}{git.ahead}"
                visible += f" {Icon.AHEAD}{git.ahead}"
            if git.behind:
                styled  += f" \x1b[38;5;{COLOR_GIT_BEHIND}m{Icon.BEHIND}{git.behind}"
                visible += f" {Icon.BEHIND}{git.behind}"
        elif git.has_upstream:
            styled  += f" \x1b[38;5;{COLOR_GIT_CLEAN}m{Icon.GIT_SYNCED}"
            visible += f" {Icon.GIT_SYNCED}"
        else:
            # No upstream tracking
            styled  += f" \x1b[38;5;{COLOR_GIT_DELETIONS}m\U000F0164"
            visible += " \U000F0164"

        # Stash count
        if git.stash:
            styled  += f" \x1b[38;5;{COLOR_GIT_STASH}m{Icon.STASH} {git.stash}"
            visible += f" {Icon.STASH} {git.stash}"

        # Health: dirty = staged | modified | untracked (see GitInfo.dirty)
        if git.dirty:
            styled  += f" \x1b[38;5;{COLOR_GIT_DIRTY}m{Icon.GIT_DIRTY}"
            visible += f" {Icon.GIT_DIRTY}"
        else:
            styled  += f" \x1b[38;5;{COLOR_GIT_CLEAN}m{Icon.GIT_CLEAN}"
            visible += f" {Icon.GIT_CLEAN}"

        # PR status
        if pr_info:
            pr_styled, pr_visible = _pr_indicator(pr_info)
            styled  += pr_styled
            visible += pr_visible

        # File stats (staged, modified, untracked, +/-)
        stat_items: list[tuple[int, str]] = []
        if git.staged:
            stat_items.append((COLOR_GIT_STAGED,     f"{Icon.STAGED}{git.staged}"))
        if git.modified:
            stat_items.append((COLOR_GIT_DIRTY,      f"{Icon.MODIFIED}{git.modified}"))
        if git.untracked:
            stat_items.append((COLOR_GIT_STASH,      f"{Icon.UNTRACKED}{git.untracked}"))
        if git.insertions:
            stat_items.append((COLOR_GIT_INSERTIONS,  f"+{git.insertions}"))
        if git.deletions:
            stat_items.append((COLOR_GIT_DELETIONS,   f"-{git.deletions}"))
        if stat_items:
            stats_styled  = " ".join(f"\x1b[38;5;{fg}m{txt}" for fg, txt in stat_items)
            stats_visible = " ".join(txt for _, txt in stat_items)
            styled  += f" {sep} {stats_styled}"
            visible += f" \ue0b1 {stats_visible}"
    else:
        # No git — fallback indicator
        styled  += f" {sep} \x1b[38;5;{COLOR_SEPARATOR}m{Icon.NO_GIT}"
        visible += f" \ue0b1 {Icon.NO_GIT}"

    styled  = f"{pill_left}{bg_esc} {styled} {RESET}{pill_right}"
    visible = f"\ue0b6 {visible} \ue0b4"
    return [Segment(styled, _display_width(visible))]


def build_stack_group(stacks: list[tuple[str, str, str, str]], icons_only: bool = False) -> list[Segment]:
    """Render stack badges (Python, Node, etc.) with brand-colored icon backgrounds.

    When icons_only=True, renders compact icon-only pills (used for narrow terminals).
    """
    if not stacks:
        return []
    badge_segs: list[Segment] = []
    badge_bg = _DEFAULT_BG
    badge_bg_esc = f"\x1b[48;5;{badge_bg}m"
    for label, icon, version, extra in stacks:
        display = version if version else label
        icon_fg, brand_bg = _STACK_BADGE.get(label, _STACK_BADGE_FALLBACK)
        if icons_only:
            pill_left  = f"\x1b[38;5;{brand_bg}m\ue0b6{RESET}"
            pill_right = f"\x1b[38;5;{brand_bg}m\ue0b4{RESET}"
            pill_visible = f"\ue0b6{icon}\ue0b4"
            styled = f"{pill_left}\x1b[48;5;{brand_bg}m\x1b[38;5;{icon_fg}m{icon}{RESET}{pill_right}"
        else:
            pill_left  = f"\x1b[38;5;{brand_bg}m\ue0b6{RESET}"
            pill_right = f"\x1b[38;5;{badge_bg}m\ue0b4{RESET}"
            pill_visible = f"\ue0b6{icon}  {display}\ue0b4"
            styled = (
                f"{pill_left}"
                f"\x1b[48;5;{brand_bg}m\x1b[38;5;{icon_fg}m{icon} {RESET}"
                f"{badge_bg_esc}\x1b[38;5;{COLOR_PILL_TEXT}m {display}{RESET}"
                f"{pill_right}"
            )
        badge_segs.append(Segment(styled, _display_width(pill_visible)))

    combined_styled  = " ".join(s.styled  for s in badge_segs)
    combined_visible = sum(s.width for s in badge_segs) + max(0, len(badge_segs) - 1)
    return [Segment(combined_styled, combined_visible)]



def _ctx_fill_color(pct_used: float) -> int:
    """Interpolate a 256-color index from CTX_FILL_GRADIENT for the given usage percentage.

    Linearly interpolates between the two nearest anchor points.  The 256-color
    palette is not perceptually uniform, so we pick the closest anchor rather
    than blending raw indices (blending indices across palette regions produces
    unrelated colours).
    """
    pct = max(0.0, min(100.0, float(pct_used)))
    anchors = CTX_FILL_GRADIENT

    if pct <= anchors[0][0]:
        return anchors[0][1]
    if pct >= anchors[-1][0]:
        return anchors[-1][1]

    for i in range(len(anchors) - 1):
        lo_pct, lo_col = anchors[i]
        hi_pct, hi_col = anchors[i + 1]
        if lo_pct <= pct <= hi_pct:
            span = hi_pct - lo_pct
            if span == 0:
                return lo_col
            # Pick the nearer anchor to preserve brand colours
            t = (pct - lo_pct) / span
            return lo_col if t < 0.5 else hi_col
    return anchors[-1][1]



def _ctx_text_fill(label: str, pct_used: float, fill_color: int, rest_bg: int) -> str:
    """Return an ANSI-styled string split into two solid background regions.

    Left region  (remaining context): background = rest_bg    (normal pill dark grey).
    Right region (used context):      background = fill_color (green→red gradient).

    The fill grows from RIGHT to LEFT as pct_used increases — at low usage only
    the rightmost characters are highlighted; at 100% the entire label is covered.

    Both regions are always solid — there is never a transparent area.
    The split boundary is character-aligned (never splits a wide character).

    Wide characters (emoji, CJK) count as 2 columns; zero-width combining
    characters count as 0 — consistent with _display_width().
    """
    total_cols = _display_width(label)
    if total_cols == 0:
        return label

    # Number of columns covered by the fill (right-hand portion).
    fill_cols = round(pct_used / 100.0 * total_cols)
    fill_cols = max(0, min(total_cols, fill_cols))
    # The split starts at this column from the left.
    split_col = total_cols - fill_cols

    fill_bg_esc = f"\x1b[48;5;{fill_color}m"
    rest_bg_esc = f"\x1b[48;5;{rest_bg}m"
    text_fg_esc = f"\x1b[38;5;{COLOR_PILL_TEXT}m"

    result  = rest_bg_esc + text_fg_esc  # open in rest zone (left side)
    col     = 0
    in_rest = True

    for ch in label:
        eaw = unicodedata.east_asian_width(ch)
        cat = unicodedata.category(ch)
        if eaw in ('W', 'F'):
            ch_width = 2
        elif cat in ('Mn', 'Me', 'Cf'):
            ch_width = 0
        else:
            ch_width = 1

        # Switch to fill background once we reach the split point.
        # Snapping to col >= split_col keeps the boundary on a whole character.
        if in_rest and col >= split_col:
            result  += fill_bg_esc + text_fg_esc
            in_rest  = False

        result += ch
        col    += ch_width

    result += RESET
    return result


def _mood_emoji(pct_used: int) -> str:
    for threshold, emoji in _MOOD_STEPS:
        if pct_used >= threshold:
            return emoji
    return _MOOD_DEFAULT


def build_context_group(data: dict) -> list[Segment]:
    """Render context window as a two-zone solid progress-fill pill.

    The segment text (e.g. '😎 20K/1M 98%') IS the progress bar.  The pill is
    always fully opaque — split into two solid background zones:

        LEFT  (remaining context): normal pill background (_DEFAULT_BG dark grey)
        RIGHT (used context):      gradient fill colour (green → red)

    The fill grows from RIGHT to LEFT as context is consumed.  At low usage only
    the rightmost characters are highlighted; at 100% the whole pill is filled.
    There is never a transparent or clear area — the pill always looks solid.

    Cap colours:
      Right cap fg = fill_color always      → always matches the filled right zone.
      Left cap  fg = fill_color at 100%     → whole pill is filled.
      Left cap  fg = _DEFAULT_BG otherwise  → matches the rest left zone.
    """
    ctx_window = data.get("context_window") or {}
    remaining  = ctx_window.get("remaining_percentage")
    win_size   = int(ctx_window.get("context_window_size") or 0)

    if remaining is None:
        if win_size:
            return [_pill(_DEFAULT_BG, COLOR_GIT_CLEAN, COLOR_PILL_TEXT, "🙂", fmt_tokens(win_size))]
        return []

    used_pct_raw = ctx_window.get("used_percentage")
    pct_used_raw = float(used_pct_raw) if used_pct_raw is not None else (100 - float(remaining))
    pct_used     = round(pct_used_raw)

    # Best available exact token count
    current = ctx_window.get("current_usage") or {}
    current_token_total = (int(current.get("input_tokens") or 0)
                           + int(current.get("cache_creation_input_tokens") or 0)
                           + int(current.get("cache_read_input_tokens") or 0))
    used_tokens = current_token_total if current_token_total > 0 else (
        int(win_size * pct_used_raw / 100) if win_size else 0
    )

    mood       = _mood_emoji(pct_used)
    fill_color = _ctx_fill_color(pct_used_raw)
    is_crit    = pct_used >= CTX_CRITICAL_THRESHOLD

    if win_size and used_tokens:
        token_text = f"{fmt_tokens(used_tokens)}/{fmt_tokens(win_size)}"
    else:
        token_text = ""

    pct_remaining = 100 - pct_used
    pct_label = f"{pct_remaining}%"

    # Build the inner label — the text that becomes the progress bar surface.
    # A single leading space and trailing space are included so the fill colour
    # visually bleeds to the pill caps rather than cutting off abruptly.
    if token_text:
        inner = f" {mood} {token_text} {pct_label} "
    else:
        inner = f" {mood} {pct_label} "

    # The pill is always a solid rectangle split into two background zones:
    #   LEFT  (remaining): _DEFAULT_BG background  (same dark grey as all other pills)
    #   RIGHT (used):      fill_color background    (green→red gradient)
    #
    # Fill grows from right to left — at low usage only the rightmost chars are
    # highlighted; at 100% the entire pill is the fill colour.
    #
    # Powerline caps:
    #   Right cap (): fg=fill_color always   → the cap IS the fill colour, matching
    #                  the right (filled) zone at all usage levels.
    #   Left cap  (): fg=fill_color only at 100% (whole pill is filled);
    #                  otherwise fg=_DEFAULT_BG to match the left (rest) zone.

    rest_bg     = _DEFAULT_BG
    fill_styled = _ctx_text_fill(inner, pct_used_raw, fill_color, rest_bg)
    if is_crit:
        fill_styled = BOLD + fill_styled + RESET

    # Right cap always matches the fill zone.
    rhc = f"\x1b[38;5;{fill_color}m\ue0b4{RESET}"

    if pct_used_raw >= 99.5:
        # Fully consumed: entire pill is fill_color — left cap also uses fill_color.
        lhc = f"\x1b[38;5;{fill_color}m\ue0b6{RESET}"
    else:
        # Partially consumed: left zone is rest_bg — left cap fg matches rest_bg.
        lhc = f"\x1b[38;5;{rest_bg}m\ue0b6{RESET}"

    visible = f"\ue0b6{inner}\ue0b4"
    styled  = f"{lhc}{fill_styled}{rhc}"
    return [Segment(styled, _display_width(visible))]


def build_cost_group(data: dict) -> list[Segment]:
    """Render cost badge with spending-journey emoji. Hidden below COST_MIN_DISPLAY_USD."""
    cost_data = data.get("cost") or {}
    cost = float(cost_data.get("total_cost_usd") or 0)

    if cost < COST_MIN_DISPLAY_USD:
        return []

    icon = "🆓"
    for threshold, emoji in _COST_ICONS:
        if cost >= threshold:
            icon = emoji
        else:
            break

    label = f"${cost:.2f}"
    return [_pill(_DEFAULT_BG, COLOR_COST, COLOR_PILL_TEXT, icon, label)]


def build_cwd_group(cwd: str, repo_root: str) -> list[Segment]:
    """Show relative path when agent has navigated away from the repo/project root.

    Hidden when cwd IS the root. Shows the relative subpath or directory name otherwise.
    """
    if not cwd or not repo_root:
        return []
    try:
        cwd_resolved = Path(cwd).resolve()
        root_resolved = Path(repo_root).resolve()
        if cwd_resolved == root_resolved:
            return []
        # Try to show relative path from root
        try:
            rel = cwd_resolved.relative_to(root_resolved)
            label = str(rel)
        except ValueError:
            # cwd is outside the repo — show parent/dirname
            parts = cwd_resolved.parts
            label = str(Path(*parts[-2:])) if len(parts) >= 2 else cwd_resolved.name
    except OSError:
        return []
    return [_pill(_DEFAULT_BG, COLOR_MUTED, COLOR_PILL_TEXT, "\uf07c", _sanitize(label))]


# Model icons — assigned per model name via hash
_MODEL_ICONS = ["\U000F02D8", "\U000F1853", "\U000F06F8", "\U000F0AE2"]  # 󰋘 󱡓 󰛸 󰫢

# Vibrant color palette — randomly picked per render for the model icon
_MODEL_COLORS = [196, 201, 208, 51, 46, 226, 129]  # red, magenta, orange, cyan, green, yellow, purple


def build_model_group(model_name: str) -> list[Segment]:
    """Render model name badge. Icon is fixed per model; icon color changes each render."""
    if not model_name:
        return []
    model_name = re.sub(r'\((\d+[KMB]?)\s+context\)', r'\1', model_name)
    # Fixed icon per model name (stable hash — Python's hash() is randomized per process)
    name_hash = int(hashlib.md5(model_name.encode()).hexdigest(), 16)
    icon = _MODEL_ICONS[name_hash % len(_MODEL_ICONS)]
    # Random vibrant color each render
    icon_color = random.choice(_MODEL_COLORS)
    return [_pill(_DEFAULT_BG, icon_color, COLOR_PILL_TEXT, icon, model_name)]


def build_session_group(data: dict) -> list[Segment]:
    """Render session duration badge. Hidden below SESSION_MIN_DISPLAY_MS."""
    cost_data = data.get("cost") or {}
    duration_ms = int(cost_data.get("total_duration_ms") or 0)
    if duration_ms < SESSION_MIN_DISPLAY_MS:
        return []
    total_mins = duration_ms / SESSION_MIN_DISPLAY_MS
    hours = int(total_mins // 60)
    mins = int(total_mins % 60)
    if hours > 0:
        label = f"{hours}h{mins:02d}m"
    else:
        label = f"{mins}m"
    return [_pill(_DEFAULT_BG, COLOR_SESSION, COLOR_PILL_TEXT, Icon.CLOCK, label)]



def build_vim_group(vim_mode: str) -> list[Segment]:
    if not vim_mode:
        return []
    icon_fg = COLOR_VIM_INSERT if vim_mode == "INSERT" else COLOR_VIM_NORMAL
    return [_pill(_DEFAULT_BG, icon_fg, COLOR_PILL_TEXT, Icon.VIM, vim_mode)]


# =============================================================================
# Renderer
# =============================================================================
#
# Within a group  →  items joined by thin chevron  ›
# Between groups  →  separator  │
#
# Final output:  [ grp1-item  › grp1-item  │  grp2-item  │  grp3-item ]

_THIN_SEP  = f" {COLOR_SEP_ESC}{Icon.CHEVRON_R}{RESET} "
_GROUP_SEP = f" {COLOR_SEP_ESC}\u00b7{RESET} "


def render(groups: list[list[Segment]]) -> str:
    non_empty = [g for g in groups if g]
    if not non_empty:
        return ""
    rendered_groups = [_THIN_SEP.join(s.styled for s in g) for g in non_empty]
    body = _GROUP_SEP.join(rendered_groups)
    return body


# =============================================================================
# Entry point
# =============================================================================

DEFAULT_TERM_WIDTH = 120

def _total_width(gs: list[list[Segment]]) -> int:
    non_empty = [g for g in gs if g]
    if not non_empty:
        return 0
    gw = [sum(s.width for s in g) + max(0, len(g) - 1) * 3 for g in non_empty]
    return sum(gw) + (len(non_empty) - 1) * 3


def main() -> None:
    try:
        data: dict = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, EOFError):
        sys.exit(0)

    try:
        workspace    = data.get("workspace") or {}
        project_dir  = workspace.get("project_dir") or data.get("cwd", "")
        current_dir  = workspace.get("current_dir") or data.get("cwd", "")
        model_name   = _sanitize((data.get("model") or {}).get("display_name", ""))
        vim_mode     = _sanitize((data.get("vim") or {}).get("mode", ""))

        cwd          = current_dir or project_dir or os.getcwd()
        project_name = Path(cwd).name if cwd else Path(project_dir).name

        # Terminal width for responsive truncation
        try:
            term_width = os.get_terminal_size().columns
        except OSError:
            term_width = int(os.environ.get("COLUMNS", DEFAULT_TERM_WIDTH))

        # Always-live data (changes between prompts)
        git = get_git_info(cwd)

        # Repo root for cwd indicator (git toplevel or project_dir fallback)
        repo_root = _run(
            ["git", "--no-optional-locks", "rev-parse", "--show-toplevel"],
            cwd=cwd, timeout=TIMEOUT_FAST,
        ) if git else project_dir

        # Cacheable data (stacks, tools, remote, PR — stable between prompts)
        cache = _load_cache(cwd)
        if cache:
            stacks = [tuple(s) for s in cache.get("stacks", [])]
            detected_tools = cache.get("tools", [])
            remote_info = tuple(cache.get("remote_info")) if cache.get("remote_info") else None
            pr_info = cache.get("pr_info")
        else:
            stacks = detect_stacks(cwd) if cwd else []
            detected_tools = detect_tools(cwd) if cwd else []
            remote_info = _git_host_info(git, cwd) if git else None
            pr_info = _detect_pr(cwd) if git else None
            _save_cache(cwd, {
                "stacks": stacks,
                "tools": detected_tools,
                "remote_info": list(remote_info) if remote_info else None,
                "pr_info": pr_info,
            })

        # Build groups
        groups = [
            build_project_group(project_name, git, cwd, remote_info=remote_info, pr_info=pr_info),
            build_cwd_group(cwd, repo_root),
            build_stack_group(stacks),
            build_tools_group(detected_tools),
            build_context_group(data),
            build_cost_group(data),
            build_model_group(model_name),
            build_session_group(data),
            build_vim_group(vim_mode),
        ]

        # Progressive truncation when output exceeds terminal width
        if _total_width(groups) > term_width:
            groups[0] = build_project_group(project_name, git, cwd,
                                            remote_info=remote_info, pr_info=pr_info, truncate_repo=True)
        if _total_width(groups) > term_width:
            groups[1] = []  # hide cwd indicator
        if _total_width(groups) > term_width:
            groups[3] = []  # hide tools
        if _total_width(groups) > term_width:
            groups[2] = build_stack_group(stacks, icons_only=True)  # icons-only stacks

        print(render(groups))

    except BrokenPipeError:
        pass
    except Exception:
        pass  # never crash — silent failure is better than traceback on every prompt


if __name__ == "__main__":
    main()

# sameh-statusline

A powerline-style status bar for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) that displays rich project context at a glance — git state, tech stacks, dev tools, context window usage, session cost, and more.

<!-- TODO: Add screenshot -->
<!-- ![Claude Statusline](screenshots/statusline.png) -->

## Features

| Segment | What it shows |
|---------|---------------|
| **Project** | Git host icon (GitHub/GitLab/Azure/Bitbucket), repo name |
| **Branch** | Current branch, commit age, sync status (ahead/behind/synced) |
| **Git Health** | Dirty/clean indicator, staged/modified/untracked counts, insertions/deletions |
| **Stash** | Stash count when stashes exist |
| **Worktree** | Worktree indicator when working in a git worktree |
| **PR Status** | PR state via `gh` CLI — draft, open, approved, changes requested, merged |
| **Stacks** | Auto-detected language/framework with version — Python, Node, TypeScript, React, Next.js, Vue, Angular, Svelte, Go, Rust, Ruby |
| **Dev Tools** | Auto-detected tooling — Docker, Kubernetes, AWS, Terraform, Vercel, GitHub CI, and more |
| **Context Window** | Visual progress-fill bar with gradient (green to red), token count, remaining %, mood emoji |
| **Cost** | Session spending with journey emoji (free -> coffee -> pizza -> ... -> skull) |
| **Model** | Current model name with randomized vibrant icon color |
| **Session Duration** | How long the session has been running |
| **Vim Mode** | Current vim mode when vim keybindings are active |
| **CWD** | Relative path when the agent navigates away from the project root |

### Design

- **Rounded pill badges** with powerline half-circle caps
- **Brand-colored stack badges** (Python blue, React teal, Rust orange, etc.)
- **Muted 256-color palette** suited for dimmed terminal displays
- **Progressive truncation** — segments drop gracefully as the terminal narrows
- **File-based caching** — stacks, tools, and PR status are cached to keep renders fast (30s TTL)

## Requirements

- **Python 3.10+** (uses stdlib only — zero external dependencies)
- **Nerd Font** in your terminal — [Hack](https://github.com/ryanoasis/nerd-fonts/tree/master/patched-fonts/Hack), [FiraCode](https://github.com/ryanoasis/nerd-fonts/tree/master/patched-fonts/FiraCode), [JetBrains Mono](https://github.com/ryanoasis/nerd-fonts/tree/master/patched-fonts/JetBrainsMono), or any [Nerd Font](https://www.nerdfonts.com/)
- **`gh` CLI** (optional) — enables PR status detection

## Installation

### Quick Install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/samehkamaleldin/sameh-statusline/main/install.sh | bash
```

This downloads `statusline.py` to `~/.claude/` and configures `settings.json` automatically.

### pip Install

```bash
pip install sameh-statusline
```

Then add to your `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "sameh-statusline"
  }
}
```

### Manual Install

1. Download the script:

```bash
curl -fsSL https://raw.githubusercontent.com/samehkamaleldin/sameh-statusline/main/statusline.py \
  -o ~/.claude/statusline.py
chmod +x ~/.claude/statusline.py
```

2. Add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.claude/statusline.py"
  }
}
```

3. Restart Claude Code.

### Let Claude Code Install It For You

Copy the prompt from [PROMPT.md](PROMPT.md) and paste it into Claude Code — it will clone, install, and configure everything for you.

## How It Works

Claude Code pipes a JSON object to the statusline command on every render. The JSON includes workspace info, context window state, cost data, and model info.

The statusline script:

1. **Parses** the stdin JSON for workspace, context, cost, and model data
2. **Detects** git state via `git status --porcelain=v2` and related commands
3. **Detects** tech stacks by scanning for marker files (`package.json`, `pyproject.toml`, `Cargo.toml`, etc.)
4. **Detects** dev tools by checking for config files and CLI availability
5. **Caches** slow-changing data (stacks, tools, PR status) to `~/.cache/sameh-statusline/`
6. **Renders** ANSI-escaped powerline segments with 256-color styling
7. **Truncates** progressively if the output exceeds terminal width

### Stdin JSON Schema

Claude Code provides this JSON on stdin:

```json
{
  "workspace": {
    "project_dir": "/path/to/project",
    "current_dir": "/path/to/current"
  },
  "context_window": {
    "context_window_size": 200000,
    "used_percentage": 25.0,
    "remaining_percentage": 75.0,
    "current_usage": {
      "input_tokens": 50000,
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 0
    }
  },
  "cost": {
    "total_cost_usd": 1.50,
    "total_duration_ms": 120000
  },
  "model": {
    "display_name": "Opus 4.6 (1M context)"
  },
  "vim": {
    "mode": "NORMAL"
  }
}
```

## Segment Reference

### Context Window

The context pill is a visual progress bar — the text IS the bar. As context fills up:

| Usage | Color | Mood |
|-------|-------|------|
| 0-15% | Dark green | `😎` |
| 15-25% | Medium green | `🤓` |
| 25-35% | Bright green | `😊` |
| 35-45% | Yellow-green | `🙂` |
| 45-55% | Yellow | `😐` |
| 55-65% | Amber | `😟` |
| 65-75% | Orange | `😰` |
| 75-85% | Deep orange | `😱` |
| 85-92% | Bright red | `🤯` |
| 92%+ | Dark red | `☢️` |

### Cost Journey

The cost emoji tells a story as your session spending grows:

| Range | Emoji | Meaning |
|-------|-------|---------|
| $0 | `🆓` | Free |
| $0.50 | `🫧` | Bubble |
| $1 | `🪙` | A coin |
| $2 | `💵` | A bill |
| $3-5 | `☕🍩` | Coffee & donuts |
| $5-10 | `🌯🍕` | Lunch |
| $10-20 | `🍱🎫📚` | Bento, tickets, books |
| $20-50 | `👕🎮👟💇🛒` | Shopping |
| $50-100 | `⛽💊🎭🧳` | Gas, meds, shows |
| $100-200 | `💳📱🎸🎿✈️🏨` | Big purchases |
| $200-300 | `🔥😰🚗💸` | Getting serious |
| $300-400 | `🤑😱🏦🚨` | Panic territory |
| $400+ | `📉🆘💀☠️🪦☢️🌋💥` | RIP |

### Git Host Icons

| Host | Icon |
|------|------|
| GitHub | `󰊤` |
| GitLab | `󰮠` |
| Azure DevOps | `󰠅` |
| Bitbucket | `` |
| Local (no remote) | `` |

### Stack Badges

Auto-detected with brand colors:

| Stack | Icon | Badge Color |
|-------|------|-------------|
| Python | `` | Yellow on blue |
| Next.js | `` | White on black |
| React | `` | Cyan on dark teal |
| TypeScript | `` | White on blue |
| Node.js | `` | Sky blue on green |
| Vue | `` | Green on dark green |
| Angular | `` | Red on dark red |
| Svelte | `` | Orange on dark red |
| Go | `` | Cyan on dark teal |
| Rust | `` | Orange on dark brown |
| Ruby | `` | Red on dark red |

### Dev Tools

Auto-detected from project files:

Docker, Kubernetes, Helm, AWS, Azure, GCP, Vercel, Terraform, Ansible, GitHub CI, GitLab CI, DVC, PostgreSQL, Redis, MongoDB, pnpm, yarn, npm, uv, poetry, Nginx, Supabase, Firebase

## Configuration

The statusline is configured via Claude Code's `settings.json` at `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.claude/statusline.py"
  }
}
```

The `type: "command"` setting tells Claude Code to pipe its JSON state to the command's stdin and display the stdout as the status line.

## Contributing

Contributions welcome! The entire statusline is a single Python file (`statusline.py`) with zero dependencies. Keep it that way.

1. Fork the repo
2. Make your changes to `statusline.py`
3. Test with: `echo '{"workspace":{"project_dir":"'$(pwd)'"},"context_window":{"context_window_size":200000,"used_percentage":30,"remaining_percentage":70},"cost":{"total_cost_usd":2.5,"total_duration_ms":300000},"model":{"display_name":"Opus 4.6"}}' | python3 statusline.py`
4. Open a PR

## License

[MIT](LICENSE)

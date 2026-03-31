# Claude Code Self-Install Prompt

Copy and paste the following prompt into Claude Code to automatically install the statusline:

---

```
Install the sameh-statusline for me. Here's what to do:

1. Clone https://github.com/samehkamaleldin/sameh-statusline into a temp location
2. Copy `statusline.py` to ~/.claude/statusline.py
3. Make it executable: chmod +x ~/.claude/statusline.py
4. Update my ~/.claude/settings.json to add (or update) this key, preserving all existing settings:
   "statusLine": {
     "type": "command",
     "command": "python3 ~/.claude/statusline.py"
   }
5. Clean up the cloned repo from the temp location
6. Tell me to restart Claude Code to see the new status bar

Requirements: Python 3.10+ and a Nerd Font (Hack, FiraCode, JetBrains Mono, etc.) in my terminal.
```

---

Alternatively, if you just want to point Claude Code at the raw file without cloning:

```
Download https://raw.githubusercontent.com/samehkamaleldin/sameh-statusline/main/statusline.py
to ~/.claude/statusline.py, make it executable, and add this to my ~/.claude/settings.json
(preserving existing settings):
  "statusLine": {"type": "command", "command": "python3 ~/.claude/statusline.py"}
Then tell me to restart Claude Code.
```

#!/usr/bin/env bash
# Claude Statusline — Installer
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/samehkamaleldin/sameh-statusline/main/install.sh | bash
#
# What it does:
#   1. Downloads statusline.py to ~/.claude/
#   2. Adds the statusLine entry to ~/.claude/settings.json (preserves existing settings)

set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/samehkamaleldin/sameh-statusline/main"
INSTALL_DIR="${HOME}/.claude"
SCRIPT_NAME="statusline.py"
SETTINGS_FILE="${INSTALL_DIR}/settings.json"

info()  { printf '\033[1;34m[info]\033[0m  %s\n' "$1"; }
ok()    { printf '\033[1;32m[ok]\033[0m    %s\n' "$1"; }
err()   { printf '\033[1;31m[error]\033[0m %s\n' "$1" >&2; }

# ── Preflight ────────────────────────────────────────────────────────────────

command -v python3 >/dev/null 2>&1 || { err "Python 3 is required but not found."; exit 1; }

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]; }; then
    err "Python 3.10+ is required (found $PYTHON_VERSION)."
    exit 1
fi

mkdir -p "$INSTALL_DIR"

# ── Download ─────────────────────────────────────────────────────────────────

info "Downloading statusline.py ..."
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "${REPO_RAW}/${SCRIPT_NAME}" -o "${INSTALL_DIR}/${SCRIPT_NAME}"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "${INSTALL_DIR}/${SCRIPT_NAME}" "${REPO_RAW}/${SCRIPT_NAME}"
else
    err "curl or wget is required."
    exit 1
fi
chmod +x "${INSTALL_DIR}/${SCRIPT_NAME}"
ok "Installed ${INSTALL_DIR}/${SCRIPT_NAME}"

# ── Configure settings.json ──────────────────────────────────────────────────

STATUSLINE_CMD="python3 ${INSTALL_DIR}/${SCRIPT_NAME}"

if [ -f "$SETTINGS_FILE" ]; then
    # Check if statusLine is already configured
    if python3 -c "
import json, sys
with open('${SETTINGS_FILE}') as f:
    d = json.load(f)
if 'statusLine' in d:
    sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
        info "statusLine already configured in settings.json — updating command."
        python3 -c "
import json
with open('${SETTINGS_FILE}') as f:
    d = json.load(f)
d['statusLine'] = {'type': 'command', 'command': '${STATUSLINE_CMD}'}
with open('${SETTINGS_FILE}', 'w') as f:
    json.dump(d, f, indent=4)
    f.write('\n')
"
    else
        info "Adding statusLine to existing settings.json ..."
        python3 -c "
import json
with open('${SETTINGS_FILE}') as f:
    d = json.load(f)
d['statusLine'] = {'type': 'command', 'command': '${STATUSLINE_CMD}'}
with open('${SETTINGS_FILE}', 'w') as f:
    json.dump(d, f, indent=4)
    f.write('\n')
"
    fi
else
    info "Creating settings.json ..."
    python3 -c "
import json
d = {
    '\$schema': 'https://json.schemastore.org/claude-code-settings.json',
    'statusLine': {'type': 'command', 'command': '${STATUSLINE_CMD}'}
}
with open('${SETTINGS_FILE}', 'w') as f:
    json.dump(d, f, indent=4)
    f.write('\n')
"
fi
ok "Configured ${SETTINGS_FILE}"

# ── Done ─────────────────────────────────────────────────────────────────────

echo ""
ok "Claude Statusline installed!"
info "Restart Claude Code to see your new status bar."
info "Requires a Nerd Font (Hack, FiraCode, JetBrains Mono, etc.)"
echo ""

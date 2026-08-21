#!/usr/bin/env bash
# Install HermesMenuBar as a per-user launchd agent (starts at login).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PYTHON="$HERMES_HOME/hermes-agent/venv/bin/python"
LABEL="dev.trapias.hermes-menubar"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGDIR="$HOME/Library/Logs"

[ -x "$PYTHON" ] || { echo "Python del venv Hermes non trovato: $PYTHON" >&2; exit 1; }
"$PYTHON" -c "import rumps" 2>/dev/null || { echo "Installo rumps..."; "$PYTHON" -m pip install -q rumps; }

mkdir -p "$HOME/Library/LaunchAgents" "$LOGDIR"

sed -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__SCRIPT__|$REPO/hermes_menubar.py|g" \
    -e "s|__HERMES_HOME__|$HERMES_HOME|g" \
    -e "s|__WORKDIR__|$REPO|g" \
    -e "s|__LOG__|$LOGDIR/hermes-menubar.log|g" \
    -e "s|__ERRLOG__|$LOGDIR/hermes-menubar.error.log|g" \
    "$REPO/$LABEL.plist" > "$PLIST"

# Replace any previous instance, launchd-owned or hand-started.
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
pkill -f hermes_menubar.py 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl enable "gui/$UID/$LABEL"

sleep 2
if launchctl list "$LABEL" >/dev/null 2>&1; then
    echo "Installato: $LABEL"
    echo "Plist:      $PLIST"
    echo "Log:        $LOGDIR/hermes-menubar.error.log"
    echo "HermesMenuBar e' ora nella menu bar e riparte a ogni login."
else
    echo "Bootstrap fallito. Controlla $LOGDIR/hermes-menubar.error.log" >&2
    exit 1
fi

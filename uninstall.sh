#!/usr/bin/env bash
set -euo pipefail
LABEL="dev.trapias.hermes-menubar"
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
pkill -f hermes_menubar.py 2>/dev/null || true
echo "Rimosso $LABEL."

#!/bin/bash
# Launcher for the telegram-bridge MCP server.
#
# TELEGRAM_BOT_TOKEN (Infisical name: TELEGRAM_BOT_TOKEN_CLAUDIA) and TELEGRAM_CHAT_ID come from
# Infisical, the canonical credential store,
# through the fleet's infisical-run.sh wrapper. There is no env-file fallback: a missing
# wrapper or secret is a hard error, so a stale local value can never be used silently.
#
# Inside the wrapper, start_server.py drops every other injected secret before the bridge
# (and uv) start; see that file for why this is not done in bash.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

WRAPPER=""
for candidate in "$HOME/.claude/scripts/infisical-run.sh" "$HOME/infisical-run.sh"; do
    if [ -x "$candidate" ]; then
        WRAPPER="$candidate"
        break
    fi
done
if [ -z "$WRAPPER" ]; then
    echo "telegram-bridge: infisical-run.sh not found in ~/.claude/scripts or ~; cannot load its secrets." >&2
    exit 1
fi

# Values inherited from the parent must not stand in for Infisical's.
unset TELEGRAM_BOT_TOKEN TELEGRAM_BOT_TOKEN_CLAUDIA TELEGRAM_CHAT_ID
exec "$WRAPPER" python3 -I -S "$DIR/start_server.py"

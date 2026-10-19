# Claude Telegram Bridge

MCP server that bridges Claude Code to Telegram for async communication. When the user is away from the terminal, Claude can send questions and summaries via Telegram and receive responses. Supports concurrent Claude sessions via reply-to-message threading.

## Project Structure

```
claude-telegram-bridge/
├── .github/workflows/ci.yml               # CI pipeline (lint, format, typecheck)
├── pyproject.toml                          # uv project config
├── uv.lock                                # Locked dependencies
├── setup_check.py                         # Standalone setup helper (no MCP needed)
├── launch.sh                              # MCP entry point: runs start_server.py inside infisical-run.sh
├── start_server.py                        # Keeps only the bridge's two secrets, then starts it
├── test_launch.py                         # Launcher tests (uv run --with pytest pytest -q)
├── CLAUDE.md                              # This file
├── README.md                              # User-facing documentation
└── src/claude_telegram_bridge/
    ├── __init__.py
    └── server.py                          # MCP server — all tools and logic
```

## Tech Stack

- **Python 3.11+** with `uv` as package manager
- **MCP SDK** (`mcp` package with `FastMCP`) for Claude Code integration
- **httpx** for async Telegram Bot API calls
- **Telegram Bot API** (long polling via `getUpdates`)
- **Dev tools**: ruff (lint + format), pyright (type checking)

## MCP Tools (5 total)

| Tool | Purpose | Away mode required |
|---|---|---|
| `setup_check` | Verify bot config, discover chat IDs | No |
| `set_away_mode` | Toggle away mode on/off | No (it sets the mode) |
| `send_question` | Send threaded question, poll for reply | Yes |
| `send_summary` | Send threaded notification, polls ~30s for reply | Yes |
| `check_messages` | Read unthreaded + buffered messages; always processes commands | No (commands always processed) |

## Telegram Commands (from phone)

- `/away` — activate away mode remotely
- `/back` — deactivate away mode
- `/status` — check current state

Commands are processed by `check_messages` regardless of away mode state, enabling remote activation from Telegram.

## Concurrent Session Support

Multiple Claude Code sessions can share the same bot without message routing conflicts:

- Each outgoing question/summary gets a unique Telegram `message_id`
- The user **swipe-replies** to the specific message on Telegram
- Only the session that sent the original message picks up the reply
- Replies are stored in `pending_replies` (keyed by `message_id`) in the shared state file, so if session B polls and gets a reply meant for session A, it stores it for session A to find
- Unthreaded messages (not replies to anything) go to the shared buffer and are picked up by whichever session calls `check_messages` first

## Message Handling

- **Reply-to threading**: `send_question` and `send_summary` track their outgoing `message_id`. Only replies to that specific message are returned to the calling session.
- **Pending replies store**: When one session picks up a reply meant for another, it's stored in `pending_replies` in the shared state file.
- **Message buffer**: Unthreaded messages are buffered in state. `check_messages` drains the buffer.
- **Multi-message replies**: After the first reply arrives, a 3s follow-up poll collects additional messages.
- **Post-send polling**: `send_summary` polls for up to 30s after sending, catching immediate user replies.

## State Management

State persisted at `~/.claude/telegram-bridge-state.json`:
```json
{
  "away": false,
  "project": null,
  "last_update_id": 0,
  "buffered_messages": [],
  "pending_replies": {}
}
```

- `pending_replies` is keyed by outgoing `message_id` (as string). Values are lists of reply texts.
- Shared between concurrent MCP server instances.

## MCP Server Config

Registered in `~/.claude.json` under `mcpServers.telegram-bridge`:
```
command: bash /path/to/claude-telegram-bridge/launch.sh
env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (from Infisical via infisical-run.sh; start_server.py
     drops every other injected secret before uv starts; no env file, missing secret = hard error)
```

## Development

```bash
# Install deps (includes dev tools)
uv sync

# Run all checks (same as CI)
uv run ruff check src/
uv run ruff format --check src/
uv run pyright src/
uv run python -c "from claude_telegram_bridge.server import mcp; print('OK')"

# Run standalone setup check (needs TELEGRAM_BOT_TOKEN env var)
TELEGRAM_BOT_TOKEN=xxx uv run python setup_check.py
```

## CI/CD

GitHub Actions workflow at `.github/workflows/ci.yml`:
- Runs on push to `main` and on PRs
- Tests against Python 3.11, 3.12, 3.13
- Steps: lint (ruff check), format (ruff format), typecheck (pyright), import verification
- No live Telegram tests (requires real credentials)

## Key Design Decisions

- **Reply-to threading**: Uses Telegram's native swipe-reply to route messages to the correct session. No session IDs or prefixes needed — just native Telegram UX.
- **Shared state with pending_replies**: When one session polls and gets a reply for another, it stores it in the shared state file. The target session checks `pending_replies` before polling Telegram.
- **Explicit activation**: Away mode must be toggled — no surprise notifications when user is at the terminal
- **Remote toggle**: `/away` and `/back` commands from Telegram are always processed by `check_messages`, even when away mode is off
- **Post-send polling**: `send_summary` and `send_question` poll after sending to catch immediate replies
- **Single chat**: All projects share one Telegram chat, tagged with `[project-name]` prefix
- **Long polling**: Uses Telegram's `getUpdates` with server-side timeout (efficient, no webhook infrastructure needed)
- **State file**: Simple JSON file shared between concurrent MCP instances
- **Plain text messages**: No Markdown in Telegram messages to avoid parsing issues with special characters

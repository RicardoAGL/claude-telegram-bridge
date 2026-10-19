# Claude Telegram Bridge

An MCP server that lets Claude Code communicate with you via Telegram when you're away from the terminal. During autonomous work sessions, Claude sends questions and progress updates to your phone and receives your replies.

Supports multiple concurrent Claude sessions — each message is threaded, so your replies are routed to the right session automatically.

## How It Works

```
You (Telegram)  <-->  Telegram Bot API  <-->  MCP Server  <-->  Claude Code
```

1. You tell Claude you're stepping away (or send `/away` from Telegram)
2. Claude activates away mode and routes communication through Telegram
3. Claude sends task summaries and questions to your Telegram chat
4. You **swipe-reply** to the specific message — the reply routes to the correct session
5. When you're back, say so in the terminal or send `/back` from Telegram

## Concurrent Sessions

When running multiple Claude Code instances (e.g., one working on frontend, another on backend), each session's messages get unique IDs. To reply to the right session, **swipe-reply to the specific message** on Telegram. This is native Telegram UX — no prefixes or session IDs needed.

Unthreaded messages (sent without replying to a specific message) go to whichever session checks next.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- Your Telegram chat ID

## Setup

### 1. Create a Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the bot token you receive

### 2. Clone and Install

```bash
git clone https://github.com/RicardoAGL/claude-telegram-bridge.git
cd claude-telegram-bridge
uv sync
```

### 3. Configure Environment

Store the bot token as `TELEGRAM_BOT_TOKEN_CLAUDIA` and the chat id as `TELEGRAM_CHAT_ID` in Infisical (project "Homelab Secrets", env `prod`); the launcher hands the token to the bridge as `TELEGRAM_BOT_TOKEN`. There is no env file: `launch.sh` loads both through the fleet's `infisical-run.sh` wrapper and fails loudly if either is missing.

To find your chat ID, send any message to your bot on Telegram, then run (`setup_check.py` reads `TELEGRAM_BOT_TOKEN`, so the Infisical name is mapped):

```bash
~/infisical-run.sh bash -c 'export TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN_CLAUDIA"; python3 -I -S setup_check.py'
```

This prints your bot info and recent chat IDs.

### 4. Register the MCP Server

Add to your Claude Code MCP config (`~/.claude.json`):

```json
{
  "mcpServers": {
    "telegram-bridge": {
      "command": "bash",
      "args": ["/path/to/claude-telegram-bridge/launch.sh"]
    }
  }
}
```

### 5. Allow the Tools (Optional)

To auto-approve the tools without prompting, add them to your `~/.claude/settings.json` allow list:

```json
{
  "permissions": {
    "allow": [
      "mcp__telegram-bridge__setup_check",
      "mcp__telegram-bridge__set_away_mode",
      "mcp__telegram-bridge__send_question",
      "mcp__telegram-bridge__send_summary",
      "mcp__telegram-bridge__check_messages"
    ]
  }
}
```

### 6. Verify

Start a new Claude Code session and ask Claude to run `setup_check`. It should display your bot name and chat ID.

## Usage

### From the Terminal (Claude Code)

Tell Claude you're stepping away:

> "I'm going AFK, activate away mode"

Claude will activate away mode and start routing through Telegram. When you return:

> "I'm back"

Claude deactivates away mode and switches back to terminal communication.

### From Telegram (Remote Control)

Send these commands directly to your bot:

| Command | Effect |
|---|---|
| `/away` | Activate away mode |
| `/back` | Deactivate away mode |
| `/status` | Show current mode and project |

This lets you toggle away mode from your phone without touching the terminal. The next time Claude calls `check_messages`, it picks up the command.

## MCP Tools

| Tool | Description | Requires Away Mode |
|---|---|---|
| `setup_check` | Verify bot config and discover chat IDs | No |
| `set_away_mode` | Toggle away mode on/off with optional project name | No |
| `send_question` | Send a threaded question, wait for swipe-reply | Yes |
| `send_summary` | Send a threaded notification, poll ~30s for reply | Yes |
| `check_messages` | Check for unthreaded + buffered messages, process commands | No (commands always work) |

## Message Handling

The bridge is designed so you don't lose messages, even with multiple sessions:

- **Reply-to threading** — Each question/summary gets a unique Telegram message ID. Swipe-reply to the specific message to route your answer to the correct Claude session.
- **Pending replies** — If one session picks up a reply meant for another, it's stored in the shared state file. The target session finds it on its next poll.
- **Multi-message replies** — After the first reply arrives, `send_question` polls for 3 extra seconds to collect follow-up messages sent in quick succession.
- **Post-send polling** — `send_summary` polls for up to 30 seconds after sending to catch your immediate reply.
- **Message buffer** — Unthreaded messages are buffered in state. The next `check_messages` call drains the buffer.

## Architecture

- **Reply-to threading** — Uses Telegram's native `reply_to_message` field to route replies to the correct session. No session IDs or user-facing prefixes.
- **Shared state file** — `~/.claude/telegram-bridge-state.json` is shared between concurrent MCP instances. Contains away mode, update offset, message buffer, and pending replies.
- **Long polling** — Uses Telegram's `getUpdates` with server-side timeouts. No webhook infrastructure needed.
- **Command processing** — `check_messages` always processes `/away`, `/back`, `/status` commands regardless of away mode state, enabling remote activation.
- **Plain text** — Messages use plain text (no Markdown) to avoid parsing issues with special characters in project names or summaries.

## Development

```bash
# Install all dependencies (including dev tools)
uv sync

# Run checks (same as CI)
uv run ruff check src/        # Lint
uv run ruff format --check src/ # Format check
uv run pyright src/            # Type check
```

## CI/CD

GitHub Actions runs on every push to `main` and on PRs:
- Lint and format check (ruff)
- Type checking (pyright)
- Import verification
- Tested against Python 3.11, 3.12, and 3.13

## License

MIT

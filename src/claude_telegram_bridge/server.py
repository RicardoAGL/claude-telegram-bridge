"""
Claude Code <-> Telegram bridge MCP server.

Provides tools for Claude Code to communicate with the user via Telegram
when they are away from the terminal.  Activated/deactivated explicitly
so notifications only fire when the user has opted in.

Supports concurrent Claude sessions via Telegram reply-to-message threading.
Each session's questions/summaries get unique message IDs; the user
swipe-replies to the specific message, and only the originating session
picks up the reply.

Environment variables required:
  TELEGRAM_BOT_TOKEN  - Bot token from @BotFather
  TELEGRAM_CHAT_ID    - Chat ID for the conversation with the user
"""

import json
import os
import time
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

STATE_FILE = Path.home() / ".claude" / "telegram-bridge-state.json"

mcp = FastMCP("claude-telegram-bridge")


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def _get_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN environment variable is not set. "
            "Start the bridge through launch.sh, which loads it from Infisical "
            "(TELEGRAM_BOT_TOKEN_CLAUDIA)."
        )
    return token


def _get_chat_id() -> str:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        raise ValueError(
            "TELEGRAM_CHAT_ID environment variable is not set. "
            "Run the setup_check tool to discover your chat ID."
        )
    return chat_id


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "away": False,
        "project": None,
        "last_update_id": 0,
        "buffered_messages": [],
        "pending_replies": {},
    }


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _drain_buffer(state: dict) -> list[str]:
    """Return and clear all buffered messages."""
    messages = state.get("buffered_messages", [])
    state["buffered_messages"] = []
    return messages


def _buffer_messages(state: dict, texts: list[str]) -> None:
    """Append message texts to the shared buffer."""
    buf = state.setdefault("buffered_messages", [])
    buf.extend(texts)


def _store_pending_reply(state: dict, reply_to_id: int, text: str) -> None:
    """Store a threaded reply keyed by the outgoing message it replies to."""
    pending = state.setdefault("pending_replies", {})
    key = str(reply_to_id)
    pending.setdefault(key, []).append(text)


def _collect_pending_replies(state: dict, message_id: int) -> list[str]:
    """Collect and clear pending replies for a specific outgoing message_id."""
    pending = state.get("pending_replies", {})
    key = str(message_id)
    return pending.pop(key, [])


# ---------------------------------------------------------------------------
# Telegram Bot API helpers
# ---------------------------------------------------------------------------


async def _send_message(text: str) -> int:
    """Send a message via Telegram.  Returns the outgoing message_id."""
    token = _get_token()
    chat_id = _get_chat_id()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
        data = resp.json()
    return data.get("result", {}).get("message_id", 0)


async def _poll_updates(state: dict, timeout: int = 10) -> list:
    """Long-poll Telegram for new updates.

    Advances ``state["last_update_id"]`` past *all* fetched updates
    (including those from other chats) to avoid re-reading them,
    but returns only messages from the configured chat.
    """
    token = _get_token()
    chat_id_str = str(_get_chat_id())

    async with httpx.AsyncClient(timeout=timeout + 10) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/getUpdates",
            json={
                "offset": state.get("last_update_id", 0),
                "timeout": timeout,
                "allowed_updates": ["message"],
            },
        )
        data = resp.json()

    all_updates = data.get("result", [])

    # Advance offset past everything so we never re-process
    if all_updates:
        state["last_update_id"] = all_updates[-1]["update_id"] + 1

    # Return only messages that belong to our chat
    return [
        u
        for u in all_updates
        if str(u.get("message", {}).get("chat", {}).get("id", "")) == chat_id_str
    ]


def _msg_header(state: dict, label: str) -> str:
    project = state.get("project") or "general"
    return f"--- {project} | {label} ---"


def _is_command(text: str) -> bool:
    return text.startswith("/")


def _get_reply_to_id(update: dict) -> int | None:
    """Extract the message_id this update is replying to, if any."""
    reply_to = update.get("message", {}).get("reply_to_message")
    if reply_to:
        return reply_to.get("message_id")
    return None


async def _process_updates(updates: list, state: dict) -> list[dict]:
    """Classify and route incoming updates.

    - Commands (``/away``, ``/back``, ``/status``) are handled immediately.
    - Threaded replies are stored in ``state["pending_replies"]`` keyed by
      the message_id they reply to, so the originating session can find them.
    - Returns the remaining *unthreaded* non-command updates.
    """
    unthreaded: list[dict] = []
    for update in updates:
        text = (update.get("message", {}).get("text", "") or "").strip()
        if not text:
            continue

        # Commands are always processed, regardless of threading
        if text == "/away":
            state["away"] = True
            _save_state(state)
            await _send_message("Away mode activated from Telegram.")
        elif text == "/back":
            state["away"] = False
            _save_state(state)
            await _send_message("Away mode deactivated. Welcome back!")
        elif text == "/status":
            status = "Active" if state.get("away") else "Inactive"
            project = state.get("project") or "none"
            await _send_message(f"Status: {status}\nProject: {project}")
        elif not _is_command(text):
            reply_to_id = _get_reply_to_id(update)
            if reply_to_id is not None:
                _store_pending_reply(state, reply_to_id, text)
            else:
                unthreaded.append(update)
    return unthreaded


def _texts_from_updates(updates: list[dict]) -> list[str]:
    """Extract non-empty message texts from a list of updates."""
    return [
        u.get("message", {}).get("text", "")
        for u in updates
        if u.get("message", {}).get("text", "")
    ]


# ---------------------------------------------------------------------------
# Polling helpers for send_question / send_summary
# ---------------------------------------------------------------------------


async def _poll_for_replies(
    state: dict,
    my_msg_id: int,
    timeout: int,
    *,
    follow_up_window: int = 3,
) -> tuple[list[str], bool]:
    """Poll until replies to ``my_msg_id`` arrive or timeout expires.

    Returns ``(collected_replies, got_reply)``.  Unthreaded messages
    encountered during polling are buffered in state.
    """
    collected: list[str] = []
    start = time.monotonic()

    while time.monotonic() - start < timeout:
        # Check pending_replies first (another session may have stored ours)
        state_fresh = _load_state()
        # Merge the fresh pending_replies into our working state
        state["pending_replies"] = state_fresh.get("pending_replies", {})
        found = _collect_pending_replies(state, my_msg_id)
        if found:
            collected.extend(found)

        if collected:
            # Got replies — do a brief follow-up poll for multi-message
            try:
                extras = await _poll_updates(state, timeout=follow_up_window)
                unthreaded = await _process_updates(extras, state)
                _buffer_messages(state, _texts_from_updates(unthreaded))
                # Check if more replies to our message arrived
                more = _collect_pending_replies(state, my_msg_id)
                collected.extend(more)
            except Exception:
                pass
            _save_state(state)
            return collected, True

        # Poll Telegram for new updates
        remaining = timeout - (time.monotonic() - start)
        wait = min(10, int(remaining))
        if wait <= 0:
            break

        try:
            updates = await _poll_updates(state, timeout=wait)
        except Exception:
            continue

        unthreaded = await _process_updates(updates, state)
        _buffer_messages(state, _texts_from_updates(unthreaded))
        _save_state(state)

        # Check if _process_updates routed a reply to our message
        found = _collect_pending_replies(state, my_msg_id)
        if found:
            collected.extend(found)
            # Continue to top of loop — will hit the follow-up poll

    _save_state(state)
    return collected, bool(collected)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def setup_check() -> str:
    """Verify the Telegram bot is configured correctly.

    Shows bot info and recent chat IDs so the user can find the right
    TELEGRAM_CHAT_ID to put in their config.  Run this during first-time
    setup.
    """
    token = _get_token()

    async with httpx.AsyncClient() as client:
        # Bot identity
        me_resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        me_data = me_resp.json()

        # Recent updates (to discover chat IDs)
        updates_resp = await client.post(
            f"https://api.telegram.org/bot{token}/getUpdates",
            json={"limit": 10, "timeout": 1},
        )
        updates_data = updates_resp.json()

    lines = ["Bot configuration check:\n"]

    if me_data.get("ok"):
        bot = me_data["result"]
        lines.append(f"  Bot name : {bot.get('first_name', '?')}")
        lines.append(f"  Username : @{bot.get('username', '?')}")
        lines.append(f"  Bot ID   : {bot.get('id', '?')}")
    else:
        lines.append(f"  ERROR: {me_data}")
        return "\n".join(lines)

    chats_seen: dict[str, str] = {}
    for u in updates_data.get("result", []):
        chat = u.get("message", {}).get("chat", {})
        cid = str(chat.get("id", ""))
        if cid:
            title = chat.get("title") or chat.get("first_name", "") + (
                " " + chat.get("last_name", "") if chat.get("last_name") else ""
            )
            chats_seen[cid] = title.strip()

    if chats_seen:
        lines.append("\nRecent chats (use one of these as TELEGRAM_CHAT_ID):\n")
        for cid, title in chats_seen.items():
            lines.append(f"  {cid}  ->  {title}")
    else:
        lines.append(
            "\nNo recent chats found. Send a message to the bot on Telegram "
            "first, then run this tool again."
        )

    return "\n".join(lines)


@mcp.tool()
async def set_away_mode(active: bool, project: str = "") -> str:
    """Activate or deactivate away mode.

    Call with active=true when the user says they are stepping away,
    leaving, going AFK, etc.  Call with active=false when they return.

    While active, send_question / send_summary / check_messages route
    through Telegram.  While inactive they return immediately, so Claude
    should use the normal terminal interaction instead.
    """
    state = _load_state()
    state["away"] = active
    if project:
        state["project"] = project

    if active:
        # Flush stale updates so old messages don't confuse polling
        try:
            await _poll_updates(state, timeout=1)
        except Exception:
            pass

        project_name = state.get("project") or "general"
        await _send_message(
            f"{_msg_header(state, 'Away mode ON')}\n\n"
            f"I will send questions and updates here.\n"
            f"Reply to a specific message to route your answer to the right session.\n"
            f"Commands: /back  /status"
        )
        _save_state(state)
        return f"Away mode activated. Project: {project_name}. Telegram notifications enabled."
    else:
        await _send_message(f"{_msg_header(state, 'Away mode OFF')}\n\nBack at the terminal.")
        _save_state(state)
        return "Away mode deactivated. Telegram notifications disabled."


@mcp.tool()
async def send_question(question: str, timeout: int = 300) -> str:
    """Send a question via Telegram and wait for the user's reply.

    Only works when away mode is active.  Use this *instead of* asking
    in the terminal when the user is away.  The tool blocks (polls) until
    a reply arrives or the timeout expires.

    The user should swipe-reply to this specific message on Telegram.
    This ensures the reply is routed to the correct session when multiple
    Claude instances are running concurrently.
    """
    state = _load_state()
    if not state.get("away"):
        return "Away mode is not active. Ask the user directly in the terminal instead."

    my_msg_id = await _send_message(f"{_msg_header(state, 'Question')}\n\n{question}")

    collected, got_reply = await _poll_for_replies(state, my_msg_id, timeout)

    if got_reply:
        return "User responded: " + "\n".join(collected)

    return (
        f"No response received within {timeout} seconds. "
        "Consider moving to the next task or trying again later."
    )


@mcp.tool()
async def send_summary(summary: str, poll_reply: int = 30) -> str:
    """Send a task-completion summary via Telegram.

    Only works when away mode is active.  Use after finishing a task or
    reaching a milestone so the user knows progress was made.

    After sending, polls for up to ``poll_reply`` seconds to catch any
    immediate response.  The user should swipe-reply to this specific
    message so the reply is routed to the correct session.
    """
    state = _load_state()
    if not state.get("away"):
        return "Away mode is not active. Communicate with the user directly in the terminal."

    my_msg_id = await _send_message(f"{_msg_header(state, 'Task complete')}\n\n{summary}")

    collected, got_reply = await _poll_for_replies(state, my_msg_id, poll_reply)

    if got_reply:
        return "Summary sent. User replied: " + "\n".join(collected)

    return "Summary sent via Telegram. No immediate reply."


@mcp.tool()
async def check_messages(timeout: int = 10) -> str:
    """Check for new messages / instructions from the user on Telegram.

    Always processes Telegram commands (/away, /back, /status) so the
    user can remotely activate away mode.  Regular unthreaded messages
    are only returned when away mode is active.

    Threaded replies (swipe-replies to a specific message) are routed
    to the session that sent the original message, not returned here.

    Use after sending a summary to see if the user replied with follow-up
    instructions, or periodically during long autonomous work.
    """
    state = _load_state()

    try:
        updates = await _poll_updates(state, timeout=timeout)
    except Exception as exc:
        _save_state(state)
        return f"Error checking messages: {exc}"

    unthreaded = await _process_updates(updates, state)
    was_away = state.get("away", False)

    if not was_away and state.get("away"):
        _save_state(state)
        return "Away mode was activated remotely from Telegram."

    if not was_away:
        _save_state(state)
        return "Away mode is not active. Ask the user directly in the terminal."

    # Collect unthreaded messages from buffer + current poll
    buffered = _drain_buffer(state)
    live = _texts_from_updates(unthreaded)
    all_messages = buffered + live

    _save_state(state)

    if all_messages:
        return "New messages:\n" + "\n".join(f"- {m}" for m in all_messages)

    return "No new messages."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    mcp.run()


if __name__ == "__main__":
    main()

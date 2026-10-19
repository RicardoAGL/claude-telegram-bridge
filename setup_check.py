"""Quick setup check — reads TELEGRAM_BOT_TOKEN from environment, calls getMe and getUpdates."""

import os
import json
import urllib.request

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not token:
    print("ERROR: TELEGRAM_BOT_TOKEN is not set. It comes from Infisical; see README step 3.")
    raise SystemExit(1)

base = f"https://api.telegram.org/bot{token}"

# Bot identity
me = json.loads(urllib.request.urlopen(f"{base}/getMe").read())
if me.get("ok"):
    bot = me["result"]
    print(f"Bot name : {bot.get('first_name', '?')}")
    print(f"Username : @{bot.get('username', '?')}")
    print(f"Bot ID   : {bot.get('id', '?')}")
else:
    print(f"ERROR: {me}")
    raise SystemExit(1)

# Recent chats
req = urllib.request.Request(
    f"{base}/getUpdates",
    data=json.dumps({"limit": 10, "timeout": 1}).encode(),
    headers={"Content-Type": "application/json"},
)
updates = json.loads(urllib.request.urlopen(req).read())
chats = {}
for u in updates.get("result", []):
    chat = u.get("message", {}).get("chat", {})
    cid = str(chat.get("id", ""))
    if cid:
        title = chat.get("title") or (
            chat.get("first_name", "") + (" " + chat.get("last_name", "") if chat.get("last_name") else "")
        )
        chats[cid] = title.strip()

if chats:
    print("\nRecent chats (use one of these as TELEGRAM_CHAT_ID):\n")
    for cid, title in chats.items():
        print(f"  {cid}  ->  {title}")
else:
    print("\nNo recent chats found. Send a message to the bot on Telegram first.")

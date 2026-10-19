"""Stage 2 of launch.sh: runs inside `infisical run`, keeps only what the bridge needs, starts it.

`infisical run` injects every project secret. Bash cannot drop them all: names that are not
valid shell identifiers (e.g. ATLASSIAN-SW-API) are invisible to it but still inherited by
whatever it execs. So the environment is rebuilt here from an allowlist, before `uv` starts,
and the secrets are passed in the environment, never in argv.

Run with `python3 -I -S` and the standard library only. -I alone still runs site-packages
.pth files (third-party startup code); -S skips site, so that code never runs while every
project secret is still in the environment. python3 and uv are found via PATH.
"""

import os
import sys

# Name the bridge reads -> name of the Infisical secret it comes from. The bridge's bot is
# @claude_bride_ricardo_bot ("Claudia"); Infisical also holds another bot's token.
SECRETS = {
    "TELEGRAM_BOT_TOKEN": "TELEGRAM_BOT_TOKEN_CLAUDIA",
    "TELEGRAM_CHAT_ID": "TELEGRAM_CHAT_ID",
}
ALLOWED = {"HOME", "PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "TERM"}
ALLOWED_PREFIXES = ("LC_", "XDG_")


def filtered_env(environ: dict[str, str]) -> dict[str, str]:
    kept = {
        k: v for k, v in environ.items()
        if k in ALLOWED or k.startswith(ALLOWED_PREFIXES)
    }
    for target, source in SECRETS.items():
        kept[target] = environ[source]
    return kept


def main() -> None:
    missing = [source for source in SECRETS.values() if not os.environ.get(source)]
    if missing:
        sys.stderr.write(f"telegram-bridge: not injected by Infisical: {', '.join(missing)}\n")
        sys.exit(1)
    here = os.path.dirname(os.path.abspath(__file__))
    os.execvpe(
        "uv",
        ["uv", "--directory", here, "run", "claude-telegram-bridge"],
        filtered_env(dict(os.environ)),
    )


if __name__ == "__main__":
    main()

# Why is the bot getting 429 at login?
#
# A 429 on connect has two very different causes and bot.py's handler prints the same
# message for both:
#   1. Gateway IDENTIFY quota exhausted - the bot restarted too many times today.
#      Self-inflicted, fixed by not restarting (no spin-down host / no crash loop).
#   2. Cloudflare 1015 - the HOST'S IP is banned, usually earned by a neighbouring
#      service on the same shared PaaS address. Nothing this bot does can fix it;
#      only a dedicated IP does.
#
# GET /gateway/bot reports the session-start budget, which separates them:
#   remaining is low/zero  -> cause 1
#   remaining is healthy   -> cause 2 (or a plain REST-route limit)
#
# Run it from the repo root:
#     python3 tools/gateway_check.py
# The token comes from DISCORD_TOKEN in the environment, or from a local .env file
# (which is gitignored). It is never printed, logged, or sent anywhere but Discord.

import os
import sys
import datetime

import requests

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _token():
    """Environment first, then .env - so this works on a host with real env vars AND
    on a laptop where nothing is exported."""
    tok = os.environ.get("DISCORD_TOKEN")
    if tok:
        return tok.strip()
    path = os.path.join(_REPO_ROOT, ".env")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == "DISCORD_TOKEN":
                return val.strip().strip('"').strip("'")
    return None


TOKEN = _token()
if not TOKEN:
    sys.exit(
        "No DISCORD_TOKEN found.\n"
        "Either export it:      export DISCORD_TOKEN='...'\n"
        "or put it in .env:     echo \"DISCORD_TOKEN=...\" >> .env\n"
        "(.env is gitignored, so it will not be committed.)"
    )

try:
    r = requests.get(
        "https://discord.com/api/v10/gateway/bot",
        headers={"Authorization": f"Bot {TOKEN}"},
        timeout=15,
    )
except Exception as e:
    sys.exit(f"Request failed: {e}")

if r.status_code == 401:
    sys.exit("401 Unauthorized - the token in this environment is wrong or was reset.")

if r.status_code == 429:
    # A 429 on THIS call is itself the answer: the IP is being limited, because a
    # single /gateway/bot request cannot possibly exhaust anything on its own.
    body = r.text[:200].replace("\n", " ")
    cf = "1015" in r.text or "cloudflare" in r.text.lower()
    print("429 on /gateway/bot itself.")
    print("  -> CLOUDFLARE / IP-LEVEL BAN." if cf else "  -> Rate limited at the IP level.")
    print(f"  retry-after header: {r.headers.get('retry-after')}")
    print(f"  body: {body}")
    sys.exit(0)

if r.status_code != 200:
    sys.exit(f"Unexpected {r.status_code}: {r.text[:200]}")

d = r.json()
lim = d.get("session_start_limit", {})
total = lim.get("total", 0)
remaining = lim.get("remaining", 0)
reset_ms = lim.get("reset_after", 0)
used = total - remaining
reset_at = datetime.datetime.now() + datetime.timedelta(milliseconds=reset_ms)

print(f"recommended shards      : {d.get('shards')}")
print(f"session starts (IDENTIFY): {used} used / {total} total  ->  {remaining} remaining")
print(f"budget resets            : in {reset_ms / 3600000:.1f}h  (~{reset_at:%H:%M})")
print(f"max concurrency          : {lim.get('max_concurrency')}")
print()

# --- IDENTIFY budget verdict -------------------------------------------------
# Only meaningful once the window has been running a while: reset_after counts DOWN
# to the next reset, so a value near 24h means the window just started and a low
# `used` proves nothing yet.
hours_elapsed = 24 - (reset_ms / 3600000)

if hours_elapsed < 1:
    print(f"IDENTIFY: INCONCLUSIVE - this 24h window is only {hours_elapsed:.1f}h old, so")
    print(f"          '{used} used' is expected either way. Re-run a few hours from now.")
elif total and remaining < total * 0.25:
    print(f"IDENTIFY: RESTART LOOP - {used} restarts in {hours_elapsed:.0f}h has burned most")
    print("          of the budget. Fix the restarts (a host that does not spin down); a")
    print("          dedicated IP alone would NOT fix this.")
elif used > 50:
    print(f"IDENTIFY: HIGH - {used} restarts in {hours_elapsed:.0f}h is more than a healthy")
    print("          bot needs, though not yet exhausting. Worth fixing.")
else:
    print(f"IDENTIFY: HEALTHY - {used} restarts in {hours_elapsed:.0f}h. Restart count is not")
    print("          your problem.")

print()
print("IP / CLOUDFLARE: NOT TESTED BY THIS RUN.")
print("  A 1015 ban belongs to the IP making the request, so this only tells you about")
print("  the machine you just ran it on. To test the host, run this script FROM there")
print("  (Render dashboard -> your service -> Shell). A 429 there = the host IP is banned;")
print("  a clean 200 there = the IP is fine and the 429s came from something else.")

"""Restore the recalibrated CCPL S1 tournament into MongoDB.

Reuses subscription_manager's EXACT connection + save path (same code the bot uses),
so it works even when MONGO_URI contains special characters in the password.

Run on the host (where MONGO_URI is set), from the repo root:
    python tools/restore_tournament.py
Then in Discord IMMEDIATELY:
    cv force_load
"""
import json
import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)   # import root modules + find the backup regardless of working directory
from core import subscription_manager as sm
ccpl = json.load(open(os.path.join(_ROOT, "data", "ccpl_backup.json")))["tournaments"][0]
sid, name = ccpl["server_id"], ccpl["name"]

# 1. Pull whatever tournaments currently exist (using the bot's own connection)
sm.load_tournament_data_from_bin()
tours = sm.DB_CACHE.get("tournaments", []) or []
print(f"Existing tournaments in DB before restore: {len(tours)}")

# 2. Drop any stale copy of this tournament, then add the recalibrated one
tours = [x for x in tours if not (x.get("server_id") == sid and x.get("name") == name)]
tours.append(ccpl)
sm.DB_CACHE["tournaments"] = tours

# 3. Save using the bot's exact write path
ok = sm.save_tournament_data_to_bin()
if not ok:
    print("Save returned falsy — check MONGO_URI / connection (see error above).")
    raise SystemExit(1)

# 4. Read it straight back to PROVE it persisted
sm.DB_CACHE["tournaments"] = []
sm.load_tournament_data_from_bin()
got = next((t for t in sm.DB_CACHE["tournaments"]
            if t.get("server_id") == sid and t.get("name") == name), None)
if not got:
    print("Wrote but could not read back. server_ids now in DB:",
          [t.get("server_id") for t in sm.DB_CACHE["tournaments"]])
    raise SystemExit(1)

done = sum(1 for m in got["schedule"] if m["status"] == "completed")
print(f"VERIFIED in MongoDB: '{name}'  server_id={sid}  — {done}/45 matches completed.")
print("   Now run `cv force_load` in Discord, then `cv tournament status`.")

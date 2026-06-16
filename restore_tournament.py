"""Restore the recalibrated CCPL S1 tournament into MongoDB.
Run on the host where MONGO_URI is set:  python restore_tournament.py
Then in Discord:  cv force_load"""
import os, json
from pymongo import MongoClient

MONGO_URI = os.environ["MONGO_URI"]
MONGO_DB  = os.environ.get("MONGO_DB", "cricket_bot")

ccpl = json.load(open("ccpl_backup.json"))["tournaments"][0]
db = MongoClient(MONGO_URI, tlsAllowInvalidCertificates=True, tlsAllowInvalidHostnames=True,
                 serverSelectionTimeoutMS=30000)[MONGO_DB]

doc  = db["tournaments"].find_one({"_id": "tournament_data"}) or {"tournaments": []}
tours = doc.get("tournaments", [])
sid, name = ccpl["server_id"], ccpl["name"]
tours = [x for x in tours if not (x.get("server_id") == sid and x.get("name") == name)]  # drop stale copy
tours.append(ccpl)
db["tournaments"].replace_one({"_id": "tournament_data"},
                              {"_id": "tournament_data", "tournaments": tours}, upsert=True)
done = sum(1 for m in ccpl["schedule"] if m["status"] == "completed")
print(f"✅ Restored '{name}' — {done}/45 matches completed. Now run `cv force_load` in Discord.")

"""
One-time migration script: JSONBin → MongoDB Atlas
Run ONCE before switching the bot over. Requires both old JSONBin
and new MongoDB credentials to be set in environment variables.

Usage:
    python migrate_to_mongo.py
"""
import os
import json
import requests
import certifi
from pymongo import MongoClient

# --- Old JSONBin credentials ---
JSONBIN_KEY             = os.environ.get("JSONBIN_KEY")
JSONBIN_BIN_ID          = os.environ.get("JSONBIN_BIN_ID")
JSONBIN_TOURNAMENT_BIN_ID = os.environ.get("JSONBIN_TOURNAMENT_BIN_ID")

# --- New MongoDB credentials ---
MONGO_URI = os.environ.get("MONGO_URI")
MONGO_DB  = os.environ.get("MONGO_DB", "cricket_bot")

def fetch_jsonbin(bin_id):
    url = f"https://api.jsonbin.io/v3/b/{bin_id}"
    res = requests.get(url, headers={"X-Master-Key": JSONBIN_KEY})
    res.raise_for_status()
    return res.json().get("record", {})

def main():
    if not JSONBIN_KEY:
        print("❌ JSONBIN_KEY not set in environment.")
        return
    if not MONGO_URI:
        print("❌ MONGO_URI not set in environment.")
        return

    print("Connecting to MongoDB Atlas...")
    client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
    db = client[MONGO_DB]

    # --- Migrate main data ---
    if JSONBIN_BIN_ID:
        print("Fetching main data from JSONBin...")
        main_data = fetch_jsonbin(JSONBIN_BIN_ID)
        players   = main_data.get("players", [])
        user_subs = main_data.get("user_subs", [])
        server_subs = main_data.get("server_subs", [])
        auth_admins = main_data.get("auth_admins", [])
        restricted  = main_data.get("restricted_channels", [])

        db["main"].replace_one(
            {"_id": "cricket_bot_data"},
            {
                "_id": "cricket_bot_data",
                "players": players,
                "user_subs": user_subs,
                "server_subs": server_subs,
                "auth_admins": auth_admins,
                "restricted_channels": restricted,
            },
            upsert=True
        )
        print(f"✅ Main data migrated — {len(players)} players, {len(user_subs)} user subs, {len(server_subs)} server subs")
    else:
        print("⚠️  JSONBIN_BIN_ID not set — skipping main data migration.")

    # --- Migrate tournament data ---
    if JSONBIN_TOURNAMENT_BIN_ID:
        print("Fetching tournament data from JSONBin...")
        t_data    = fetch_jsonbin(JSONBIN_TOURNAMENT_BIN_ID)
        tournaments = t_data.get("tournaments", [])

        db["tournaments"].replace_one(
            {"_id": "tournament_data"},
            {"_id": "tournament_data", "tournaments": tournaments},
            upsert=True
        )
        print(f"✅ Tournament data migrated — {len(tournaments)} tournament(s)")
    else:
        print("⚠️  JSONBIN_TOURNAMENT_BIN_ID not set — skipping tournament migration.")

    print("\n✅ Migration complete. Verify data in MongoDB Atlas, then update your .env.")

if __name__ == "__main__":
    main()

import os
import requests
import datetime
from threading import Thread

JSONBIN_KEY = os.environ.get("JSONBIN_KEY")
JSONBIN_BIN_ID = os.environ.get("JSONBIN_BIN_ID")
JSONBIN_TOURNAMENT_BIN_ID = os.environ.get("JSONBIN_TOURNAMENT_BIN_ID")

BIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}" if JSONBIN_BIN_ID else ""
TOURNAMENT_BIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_TOURNAMENT_BIN_ID}" if JSONBIN_TOURNAMENT_BIN_ID else ""
HEADERS = {
    "X-Master-Key": JSONBIN_KEY,
    "Content-Type": "application/json"
}

DB_CACHE = {
    "players": [],
    "user_subs": [],
    "server_subs": [],
    "auth_admins": [],
    "restricted_channels": [],
    "tournaments": []
}

def load_data_from_bin():
    if not JSONBIN_KEY or not JSONBIN_BIN_ID:
        print("⚠️ JSONBIN credentials missing! Cache will be empty.")
        return
    try:
        res = requests.get(BIN_URL, headers={"X-Master-Key": JSONBIN_KEY})
        if res.status_code == 200:
            data = res.json().get("record", {})
            DB_CACHE["players"] = data.get("players", [])
            DB_CACHE["user_subs"] = data.get("user_subs", [])
            DB_CACHE["server_subs"] = data.get("server_subs", [])
            DB_CACHE["auth_admins"] = data.get("auth_admins", [])
            DB_CACHE["restricted_channels"] = data.get("restricted_channels", [])
            print(f"✅ Loaded {len(DB_CACHE['players'])} players & subscriptions from JSONBin!")
        else:
            print(f"❌ Failed to load from JSONBin: {res.text}")
    except Exception as e:
        print(f"❌ JSONBin Load Error: {e}")

def load_tournament_data_from_bin():
    if not JSONBIN_KEY or not JSONBIN_TOURNAMENT_BIN_ID:
        print("⚠️ JSONBIN_TOURNAMENT_BIN_ID missing! Tournament data will be empty.")
        return
    try:
        res = requests.get(TOURNAMENT_BIN_URL, headers={"X-Master-Key": JSONBIN_KEY})
        if res.status_code == 200:
            data = res.json().get("record", {})
            DB_CACHE["tournaments"] = data.get("tournaments", [])
            print(f"✅ Loaded {len(DB_CACHE['tournaments'])} tournament(s) from Tournament JSONBin!")
        else:
            print(f"❌ Failed to load tournament data from JSONBin: {res.text}")
    except Exception as e:
        print(f"❌ Tournament JSONBin Load Error: {e}")

def save_data_to_bin():
    if not JSONBIN_KEY or not JSONBIN_BIN_ID: return None
    try:
        # Save only players/subscriptions — tournaments go to their own bin
        payload = {k: v for k, v in DB_CACHE.items() if k != "tournaments"}
        res = requests.put(BIN_URL, json=payload, headers=HEADERS)
        if res.status_code not in (200, 201, 204):
            print(f"❌ JSONBin Save Failed (HTTP {res.status_code}): {res.text[:300]}")
        else:
            print(f"✅ JSONBin Save OK (HTTP {res.status_code})")
        return res
    except Exception as e:
        print(f"❌ JSONBin Save Error: {e}")
        return None

def save_tournament_data_to_bin():
    if not JSONBIN_KEY or not JSONBIN_TOURNAMENT_BIN_ID: return None
    try:
        res = requests.put(TOURNAMENT_BIN_URL, json={"tournaments": DB_CACHE["tournaments"]}, headers=HEADERS)
        if res.status_code not in (200, 201, 204):
            print(f"❌ Tournament JSONBin Save Failed (HTTP {res.status_code}): {res.text[:300]}")
        else:
            print(f"✅ Tournament JSONBin Save OK (HTTP {res.status_code})")
        return res
    except Exception as e:
        print(f"❌ Tournament JSONBin Save Error: {e}")
        return None

def async_save_to_bin():
    Thread(target=save_data_to_bin).start()

def async_save_tournament_to_bin():
    Thread(target=save_tournament_data_to_bin).start()

def get_today_str():
    return datetime.date.today().isoformat()

def reset_daily_quotas():
    today = get_today_str()
    updated = False
    for u in DB_CACHE["user_subs"]:
        if u.get("last_reset") != today:
            u["sims_used"] = 0
            u["server_daily_used"] = 0
            u["last_reset"] = today
            updated = True
    for s in DB_CACHE["server_subs"]:
        if s.get("last_reset") != today:
            s["sims_used"] = 0
            s["last_reset"] = today
            updated = True
    if updated:
        async_save_to_bin()

def check_potential_quota(user_id: str, server_id: str, admin_discord_id: str):
    reset_daily_quotas()
    
    u_row = next((u for u in DB_CACHE["user_subs"] if u["user_id"] == user_id), None)
    u_tier = u_row["tier"] if u_row else "Free"
    u_used = u_row["sims_used"] if u_row else 0
    u_server_used = u_row.get("server_daily_used", 0) if u_row else 0

    if server_id:
        s_row = next((s for s in DB_CACHE["server_subs"] if s["server_id"] == server_id), None)
        if s_row:
            s_tier, s_used = s_row["tier"], s_row["sims_used"]
            if s_tier in ["Silver", "Diamond"]: 
                if u_tier in ["Server Pro", "Standard"]: return True, ""
                if u_server_used < 7: return True, ""
                return False, "❌ **Access Denied:** You have hit your 7 matches/day limit on Premium Servers. Contact **frenzy_guy** to upgrade to **Server Pro**!"
            if s_tier == "Bronze" and s_used < 10: return True, ""
            
    if u_tier in ["Standard", "Basic"] and u_used < 1: return True, ""
    if u_tier == "Single": return True, ""
        
    return False, "❌ **Access Denied:** You have exhausted your daily limit, or you do not have an active subscription tier. Please contact **frenzy_guy** to gain access or upgrade."

def consume_quota(user_id: str, server_id: str, format_val: str, admin_discord_id: str):
    reset_daily_quotas()
    
    u_row = next((u for u in DB_CACHE["user_subs"] if u["user_id"] == user_id), None)
    if not u_row:
        u_row = {"user_id": user_id, "tier": "Free", "sims_used": 0, "server_daily_used": 0, "last_reset": get_today_str()}
        DB_CACHE["user_subs"].append(u_row)
        
    if "server_daily_used" not in u_row:
        u_row["server_daily_used"] = 0
        
    u_tier = u_row["tier"]
    u_used = u_row["sims_used"]
    u_server_used = u_row["server_daily_used"]
    
    if server_id:
        s_row = next((s for s in DB_CACHE["server_subs"] if s["server_id"] == server_id), None)
        if s_row:
            s_tier, s_used = s_row["tier"], s_row["sims_used"]
            if s_tier in ["Silver", "Diamond"]:
                if u_tier in ["Server Pro", "Standard"] or u_server_used < 7:
                    s_row["sims_used"] += 1
                    if u_tier not in ["Server Pro", "Standard"]:
                        u_row["server_daily_used"] += 1
                    async_save_to_bin()
                    return True, ""
            elif s_tier == "Bronze" and s_used < 10:
                s_row["sims_used"] += 1 # Bronze doesn't drain the 7/day premium cap
                async_save_to_bin()
                return True, ""
                
    if u_tier == "Standard" and u_used < 1:
        u_row["sims_used"] += 1
        async_save_to_bin()
        return True, ""
    if u_tier == "Basic" and u_used < 1:
        if format_val not in ["20", "50"]: return False, "❌ **Basic Tier Restriction:** You can only simulate T20 or ODI formats."
        u_row["sims_used"] += 1
        async_save_to_bin()
        return True, ""
    if u_tier == "Single":
        u_row["tier"] = "Free" # Instantly downgrades them back to Free!
        u_row["sims_used"] += 1
        async_save_to_bin()
        return True, ""
            
    return False, "❌ **Access Denied:** You have exhausted your daily limit, or your tier restricts this format. Please contact **frenzy_guy** to upgrade."

def update_user_tier(user_id: str, tier_value: str, tier_name: str, mention: str):
    global DB_CACHE
    DB_CACHE["user_subs"] = [u for u in DB_CACHE["user_subs"] if u["user_id"] != user_id]
    if tier_value == "None":
        msg = f"🚫 Removed subscription from {mention}."
    else:
        DB_CACHE["user_subs"].append({
            "user_id": user_id,
            "tier": tier_value,
            "sims_used": 0,
            "server_daily_used": 0,
            "last_reset": get_today_str()
        })
        msg = f"✅ Assigned **{tier_name}** tier to {mention}."
    async_save_to_bin()
    return msg

def update_server_tier(server_id: str, tier_value: str, tier_name: str):
    global DB_CACHE
    DB_CACHE["server_subs"] = [s for s in DB_CACHE["server_subs"] if s["server_id"] != server_id]
    if tier_value == "None":
        msg = f"🚫 Removed subscription from Server ID `{server_id}`."
    else:
        DB_CACHE["server_subs"].append({
            "server_id": server_id,
            "tier": tier_value,
            "sims_used": 0,
            "last_reset": get_today_str()
        })
        msg = f"✅ Assigned **{tier_name}** tier to Server `{server_id}`."
    async_save_to_bin()
    return msg

def get_auth_admins():
    return [a["admin_id"] for a in DB_CACHE["auth_admins"]]

def toggle_auth_admin(admin_id: str):
    global DB_CACHE
    admins = get_auth_admins()
    if admin_id in admins:
        DB_CACHE["auth_admins"] = [a for a in DB_CACHE["auth_admins"] if a["admin_id"] != admin_id]
        added = False
    else:
        DB_CACHE["auth_admins"].append({"admin_id": admin_id})
        added = True
    async_save_to_bin()
    return added

def get_all_players():
    return DB_CACHE["players"]

def add_player(player_dict):
    global DB_CACHE
    if any(p["name"].lower() == player_dict["name"].lower() for p in DB_CACHE["players"]):
        return False
    DB_CACHE["players"].append(player_dict)
    async_save_to_bin()
    return True

def add_players_bulk(players_list):
    global DB_CACHE
    existing_names = {p["name"].lower() for p in DB_CACHE["players"]}
    added = 0
    for p in players_list:
        if p["name"].lower() not in existing_names:
            DB_CACHE["players"].append(p)
            existing_names.add(p["name"].lower())
            added += 1
    if added > 0:
        async_save_to_bin()
    return added

def update_player(old_name, player_dict):
    global DB_CACHE
    DB_CACHE["players"] = [p for p in DB_CACHE["players"] if p["name"].lower() != old_name.lower()]
    DB_CACHE["players"].append(player_dict)
    async_save_to_bin()

def delete_players(names_list):
    global DB_CACHE
    lower_names = [n.lower().strip() for n in names_list]
    initial_len = len(DB_CACHE["players"])
    DB_CACHE["players"] = [p for p in DB_CACHE["players"] if p["name"].lower().strip() not in lower_names]
    deleted_count = initial_len - len(DB_CACHE["players"])
    if deleted_count > 0:
        async_save_to_bin()
    return deleted_count

def clean_duplicate_players():
    global DB_CACHE
    seen = set()
    cleaned = []
    removed_names = []
    for p in DB_CACHE["players"]:
        n = p["name"].lower().strip()
        if n not in seen:
            seen.add(n)
            cleaned.append(p)
        else:
            removed_names.append(p["name"])
    
    if removed_names:
        DB_CACHE["players"] = cleaned
        async_save_to_bin()
    return removed_names

def get_tier_status(user_id: str, server_id: str):
    reset_daily_quotas()
    
    u_tier, u_used, u_server_used = "Free", 0, 0
    u_row = next((u for u in DB_CACHE["user_subs"] if u["user_id"] == user_id), None)
    if u_row:
        u_tier = u_row["tier"]
        u_used = u_row["sims_used"]
        u_server_used = u_row.get("server_daily_used", 0)
        
    s_tier, s_used = "None", 0
    if server_id:
        s_row = next((s for s in DB_CACHE["server_subs"] if s["server_id"] == server_id), None)
        if s_row:
            s_tier = s_row["tier"]
            s_used = s_row["sims_used"]
            
    return u_tier, u_used, u_server_used, s_tier, s_used

def is_channel_restricted(channel_id: str):
    return channel_id in DB_CACHE.get("restricted_channels", [])

def toggle_restricted_channel(channel_id: str):
    global DB_CACHE
    if "restricted_channels" not in DB_CACHE:
        DB_CACHE["restricted_channels"] = []
    if channel_id in DB_CACHE["restricted_channels"]:
        DB_CACHE["restricted_channels"].remove(channel_id)
        added = False
    else:
        DB_CACHE["restricted_channels"].append(channel_id)
        added = True
    async_save_to_bin()
    return added

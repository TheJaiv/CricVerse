import os
import psycopg2
from psycopg2.extras import DictCursor

DB_URL = os.environ.get("DATABASE_URL")

def get_db():
    return psycopg2.connect(DB_URL, sslmode='require')

def init_subs_db():
    if not DB_URL: return
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''CREATE TABLE IF NOT EXISTS user_subs (user_id TEXT PRIMARY KEY, tier TEXT, sims_used INTEGER DEFAULT 0, last_reset DATE DEFAULT CURRENT_DATE)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS server_subs (server_id TEXT PRIMARY KEY, tier TEXT, sims_used INTEGER DEFAULT 0, last_reset DATE DEFAULT CURRENT_DATE)''')
        conn.commit()

def reset_daily_quotas():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE user_subs SET sims_used = 0, last_reset = CURRENT_DATE WHERE last_reset < CURRENT_DATE")
                cur.execute("UPDATE server_subs SET sims_used = 0, last_reset = CURRENT_DATE WHERE last_reset < CURRENT_DATE")
            conn.commit()
    except Exception as e: print(f"Quota Reset Error: {e}")

def check_potential_quota(user_id: str, server_id: str, admin_discord_id: str):
    reset_daily_quotas()
    if user_id == admin_discord_id: return True, ""
    
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                if server_id:
                    cur.execute("SELECT tier, sims_used FROM server_subs WHERE server_id = %s", (server_id,))
                    s_row = cur.fetchone()
                    if s_row:
                        s_tier, s_used = s_row["tier"], s_row["sims_used"]
                        if s_tier in ["Silver", "Diamond"]: return True, ""
                        if s_tier == "Bronze" and s_used < 10: return True, ""
                
                cur.execute("SELECT tier, sims_used FROM user_subs WHERE user_id = %s", (user_id,))
                u_row = cur.fetchone()
                if u_row:
                    u_tier, u_used = u_row["tier"], u_row["sims_used"]
                    if u_tier in ["Basic", "Standard"] and u_used < 1: return True, ""
                
                return False, "❌ **Access Denied:** You have exhausted your daily limit, or you do not have an active subscription tier."
    except: return False, "Database error."

def consume_quota(user_id: str, server_id: str, format_val: str, admin_discord_id: str):
    reset_daily_quotas()
    if user_id == admin_discord_id: return True, ""
    
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                if server_id:
                    cur.execute("SELECT tier, sims_used FROM server_subs WHERE server_id = %s", (server_id,))
                    s_row = cur.fetchone()
                    if s_row:
                        s_tier, s_used = s_row["tier"], s_row["sims_used"]
                        if s_tier in ["Silver", "Diamond"] or (s_tier == "Bronze" and s_used < 10):
                            cur.execute("UPDATE server_subs SET sims_used = sims_used + 1 WHERE server_id = %s", (server_id,))
                            conn.commit()
                            return True, ""
                
                cur.execute("SELECT tier, sims_used FROM user_subs WHERE user_id = %s", (user_id,))
                u_row = cur.fetchone()
                if u_row:
                    u_tier, u_used = u_row["tier"], u_row["sims_used"]
                    if u_tier == "Standard" and u_used < 1:
                        cur.execute("UPDATE user_subs SET sims_used = sims_used + 1 WHERE user_id = %s", (user_id,))
                        conn.commit()
                        return True, ""
                    if u_tier == "Basic" and u_used < 1:
                        if format_val not in ["20", "50"]: return False, "❌ **Basic Tier Restriction:** You can only simulate T20 or ODI formats."
                        cur.execute("UPDATE user_subs SET sims_used = sims_used + 1 WHERE user_id = %s", (user_id,))
                        conn.commit()
                        return True, ""
                        
                return False, "❌ **Access Denied:** You have exhausted your daily limit, or your tier restricts this format."
    except Exception as e: return False, f"Database error: {e}"

def update_user_tier(user_id: str, tier_value: str, tier_name: str, mention: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            if tier_value == "None":
                cur.execute("DELETE FROM user_subs WHERE user_id = %s", (user_id,))
                msg = f"🚫 Removed subscription from {mention}."
            else:
                cur.execute('''
                    INSERT INTO user_subs (user_id, tier, sims_used, last_reset)
                    VALUES (%s, %s, 0, CURRENT_DATE)
                    ON CONFLICT (user_id) DO UPDATE SET tier = EXCLUDED.tier, sims_used = 0, last_reset = CURRENT_DATE
                ''', (user_id, tier_value))
                msg = f"✅ Assigned **{tier_name}** tier to {mention}."
        conn.commit()
    return msg

def update_server_tier(server_id: str, tier_value: str, tier_name: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            if tier_value == "None":
                cur.execute("DELETE FROM server_subs WHERE server_id = %s", (server_id,))
                msg = f"🚫 Removed subscription from Server ID `{server_id}`."
            else:
                cur.execute('''
                    INSERT INTO server_subs (server_id, tier, sims_used, last_reset)
                    VALUES (%s, %s, 0, CURRENT_DATE)
                    ON CONFLICT (server_id) DO UPDATE SET tier = EXCLUDED.tier, sims_used = 0, last_reset = CURRENT_DATE
                ''', (server_id, tier_value))
                msg = f"✅ Assigned **{tier_name}** tier to Server `{server_id}`."
        conn.commit()
    return msg
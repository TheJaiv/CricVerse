# ── TBECS (The Big Event Series) ───────────────────────────────────────────────
# A large 50-team event. Because a full 50-team schedule is 1000+ matches — each
# carrying a full 40-player scorecard — its match data would blow Mongo's 16MB
# per-document cap. So TBECS gets special storage: the tournament skeleton lives in
# its OWN document ("tbecs_tournament_data") and every match's heavy scorecard is
# sharded into a per-match document. All of that lives in subscription_manager
# (the Mongo leaf); this module just marks TBECS tournaments and owns the ad feature.
#
# The schedule/format is set up elsewhere (added later). Everything here works the
# moment a tournament carries tournament_type == "tbecs".
#
# Import direction: this module may import from subscription_manager only. Other
# managers must import tbecs_manager LAZILY (inside functions) to avoid import cycles.

import discord

from subscription_manager import DB_CACHE, async_save_to_bin

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════
TBECS_CONFIG = {
    "type_key": "tbecs",                 # tournament_type value (drives Mongo sharding)
    "display_name": "The Big Event Championship Series",   # guess for the acronym — edit freely
    "short_name": "TBECS",
    "team_count": 50,
    "squad_size": 20,                   # 20 players per team (per-match stats stored for all)
}

# Discord embed limits we respect when rendering the (unbounded) ad list.
_EMBED_DESC_LIMIT = 4096
_MAX_EMBEDS = 10


def is_tbecs_tournament(tourney):
    return bool(tourney) and tourney.get("tournament_type") == TBECS_CONFIG["type_key"]


def is_tbecs_match(match):
    """True for a live match object belonging to a TBECS tournament."""
    return getattr(match, "tournament_type", None) == TBECS_CONFIG["type_key"]


# ══════════════════════════════════════════════════════════════════════════════
# ADS — innings-break sponsor messages, managed per server, shown at every innings
# end of a TBECS match. Stored in DB_CACHE["tbecs_ads"] (main doc), keyed by server_id.
# ══════════════════════════════════════════════════════════════════════════════
def _ads_store():
    if "tbecs_ads" not in DB_CACHE or not isinstance(DB_CACHE["tbecs_ads"], dict):
        DB_CACHE["tbecs_ads"] = {}
    return DB_CACHE["tbecs_ads"]


def get_tbecs_ads(server_id):
    """Return the list of ad strings for a server (a copy; never the live list)."""
    return list(_ads_store().get(str(server_id), []))


def add_tbecs_ad(server_id, text):
    """Append an ad. Returns (ok, message, new_total). Persists to Mongo."""
    text = (text or "").strip()
    if not text:
        return False, "❌ Ad text is empty — give me something to show.", 0
    # Cap comfortably under the 4096 embed-description limit so one ad (plus its
    # "Ad #N" header) always fits a single embed even when multi-line with links.
    if len(text) > 3800:
        return False, f"❌ That ad is too long ({len(text)} chars, max 3800).", 0
    store = _ads_store()
    ads = store.setdefault(str(server_id), [])
    ads.append(text)
    async_save_to_bin()
    return True, f"✅ Ad #{len(ads)} added. It'll show at every innings end in TBECS matches.", len(ads)


def remove_tbecs_ad(server_id, index):
    """Remove the 1-based ad at `index`. Returns (ok, message). Persists to Mongo."""
    ads = _ads_store().get(str(server_id), [])
    if not ads:
        return False, "❌ No ads to remove."
    if index < 1 or index > len(ads):
        return False, f"❌ Ad #{index} doesn't exist — there are {len(ads)} ad(s) (1–{len(ads)})."
    removed = ads.pop(index - 1)
    async_save_to_bin()
    flat = " ⏎ ".join(x.strip() for x in removed.splitlines() if x.strip())
    preview = flat if len(flat) <= 80 else flat[:77] + "…"
    return True, f"🗑️ Removed ad #{index}: “{preview}”. {len(ads)} ad(s) left."


def clear_tbecs_ads(server_id):
    """Remove all ads for a server. Returns (count_cleared, message). Persists to Mongo."""
    ads = _ads_store().get(str(server_id), [])
    n = len(ads)
    if not n:
        return 0, "ℹ️ There are no ads to clear."
    _ads_store()[str(server_id)] = []
    async_save_to_bin()
    return n, f"🧹 Cleared all {n} ad(s) for this server."


def build_tbecs_ad_embeds(server_id):
    """Render every ad for a server as a list of discord.Embeds (usually one).
    Returns [] when the server has no ads. Splits across embeds if the combined
    text exceeds Discord's per-embed description limit, so ANY number of ads fits."""
    ads = get_tbecs_ads(server_id)
    if not ads:
        return []

    # Each ad as its own block, numbered, separated by a divider. Pack blocks into
    # embeds without exceeding the description limit.
    blocks = [f"**📢 Ad #{i}**\n{ad}" for i, ad in enumerate(ads, 1)]
    embeds, buf = [], ""
    for block in blocks:
        candidate = block if not buf else f"{buf}\n\n{block}"
        if len(candidate) > _EMBED_DESC_LIMIT:
            if buf:
                embeds.append(buf)
            # A single block longer than the limit is hard-truncated.
            buf = block[:_EMBED_DESC_LIMIT]
        else:
            buf = candidate
    if buf:
        embeds.append(buf)

    out = []
    for idx, desc in enumerate(embeds[:_MAX_EMBEDS]):
        e = discord.Embed(
            description=desc,
            color=0xF5A623,
        )
        if idx == 0:
            e.title = "📣 A word from our sponsors"
        out.append(e)
    return out

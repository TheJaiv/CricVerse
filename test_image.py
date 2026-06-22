"""
Test-match result graphics (PIL only — no discord import) so they can be rendered
and previewed locally without running the bot:

    python3 test_image.py          # simulates a Test and writes summary.png + scorecard.png

bot.py imports generate_test_summary_image / generate_test_scorecard_image from here
and passes the match-number label + POTM name.
"""
import io
import os
from PIL import Image, ImageDraw, ImageFont


def _font(size, bold=True):
    paths = ([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",      # Linux server
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",        # macOS
        "/Library/Fonts/Arial Bold.ttf",
    ] if bold else [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ])
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _hex(h):
    try:
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return (29, 78, 216)


# ─────────────────────────────────────────────────────────────────────────────
# COMPACT SUMMARY — one horizontal band per innings (score + top 2 bat/bowl)
# ─────────────────────────────────────────────────────────────────────────────
def generate_test_summary_image(match, match_no_label="", potm="") -> io.BytesIO:
    played = match.innings_list or []
    n = max(1, len(played))
    W = 1200
    HEAD_H, FOOT_H, BAND_H = 142, 58, 150
    H = HEAD_H + n * BAND_H + FOOT_H

    img = Image.new("RGB", (W, H), "#FFFFFF")
    d = ImageDraw.Draw(img)
    f_team = _font(42); f_score = _font(34); f_bold = _font(23)
    f_med = _font(20); f_small = _font(16, bold=False); f_lbl = _font(14); f_micro = _font(13, bold=False)

    def tw(t, f):
        try: return d.textlength(t, font=f)
        except Exception: return len(t) * 11

    c_navy = "#0A0F24"; c_grey = "#7A7F8A"; c_div = "#E4E6EA"
    c_accent = "#BCC4CD"            # Test whites — greyish silver
    c_band_a = "#FFFFFF"; c_band_b = "#F3F4F7"
    c_star = "#C9A227"

    def inn_score(i):
        dec = "d" if getattr(i, "declared", False) else ""
        return f"{i.total_runs}" if (i.wickets >= 10 and not dec) else f"{i.total_runs}/{i.wickets}{dec}"

    def top_bat(i, k=2):
        rows = [(p["name"], i.batting_stats[p["name"]]) for p in i.batting_team["players"]
                if i.batting_stats[p["name"]].balls_faced > 0 or i.batting_stats[p["name"]].dismissal != "not out"]
        return sorted(rows, key=lambda x: x[1].runs_scored, reverse=True)[:k]

    def top_bowl(i, k=2):
        rows = [(p["name"], i.bowling_stats[p["name"]]) for p in i.bowling_team["players"]
                if i.bowling_stats[p["name"]].balls_bowled > 0]
        return sorted(rows, key=lambda x: (x[1].wickets_taken, -x[1].runs_conceded), reverse=True)[:k]

    # ── Header ──
    t1n = match.team1["name"][:18].upper(); t2n = match.team2["name"][:18].upper()
    d.text((300 - tw(t1n, f_team)//2, 32), t1n, fill=c_navy, font=f_team)
    d.text((900 - tw(t2n, f_team)//2, 32), t2n, fill=c_navy, font=f_team)
    if match_no_label:
        d.text((W - 12 - tw(match_no_label, f_micro), 8), match_no_label, fill=c_grey, font=f_micro)
    try:
        logo_path = "assets/logo.png" if os.path.exists("assets/logo.png") else "assets/logo.jpg"
        logo = Image.open(logo_path).convert("RGBA").resize((84, 84), Image.Resampling.LANCZOS)
        mask = Image.new("L", (84, 84), 0); ImageDraw.Draw(mask).ellipse((0, 0, 84, 84), fill=255)
        img.paste(logo, (558, 14), mask)
        d.ellipse([(558, 14), (642, 98)], outline=c_div, width=2)
    except Exception:
        d.ellipse([(558, 14), (642, 98)], fill="#FFFFFF", outline=c_div, width=3)

    d.rectangle([(0, HEAD_H - 30), (W, HEAD_H)], fill=c_accent)
    _pink = getattr(match, "pink_ball", False)
    bar = "SIMULATION MATCH    •    DAY-NIGHT TEST    •    PINK BALL" if _pink else "SIMULATION MATCH    •    TEST MATCH"
    d.text((W // 2 - tw(bar, f_med) // 2, HEAD_H - 27), bar, fill=("#C2185B" if _pink else c_navy), font=f_med)

    # ── Innings bands ──
    BAT_X, BOWL_X = 392, 800
    for idx, inn in enumerate(played):
        y0 = HEAD_H + idx * BAND_H
        d.rectangle([(0, y0), (W, y0 + BAND_H)], fill=c_band_a if idx % 2 == 0 else c_band_b)
        d.rectangle([(0, y0), (7, y0 + BAND_H)], fill=_hex(inn.batting_team.get("color", "#1D4ED8")))
        d.line([(0, y0), (W, y0)], fill=c_div, width=1)
        d.line([(BAT_X - 22, y0 + 18), (BAT_X - 22, y0 + BAND_H - 18)], fill=c_div, width=2)
        d.line([(BOWL_X - 22, y0 + 18), (BOWL_X - 22, y0 + BAND_H - 18)], fill=c_div, width=2)

        lbl = ["1ST", "2ND", "3RD", "4TH"][idx] + " INNINGS"
        if getattr(inn, "declared", False): lbl += "  (DEC)"
        d.text((30, y0 + 20), lbl, fill=c_grey, font=f_lbl)
        d.text((30, y0 + 42), inn.batting_team["name"][:16].upper(), fill=c_navy, font=f_bold)
        d.text((30, y0 + 74), inn_score(inn), fill=c_navy, font=f_score)
        d.text((30, y0 + 116), f"{inn.overs_str} ov  ·  RR {inn.run_rate}", fill=c_grey, font=f_small)

        d.text((BAT_X, y0 + 20), "TOP BATTING", fill=c_grey, font=f_lbl)
        for j, (nm, st) in enumerate(top_bat(inn)):
            yy = y0 + 50 + j * 42
            disp = nm[:18]
            d.text((BAT_X, yy), disp, fill=c_navy, font=f_med)
            if potm and potm == nm:
                d.text((BAT_X + tw(disp, f_med) + 6, yy - 1), "★", fill=c_star, font=f_med)
            no = "*" if st.dismissal == "not out" else ""
            fig = f"{st.runs_scored}{no} ({st.balls_faced})"
            d.text((BOWL_X - 30 - tw(fig, f_med), yy), fig, fill=c_navy, font=f_med)

        d.text((BOWL_X, y0 + 20), "TOP BOWLING", fill=c_grey, font=f_lbl)
        for j, (nm, st) in enumerate(top_bowl(inn)):
            yy = y0 + 50 + j * 42
            disp = nm[:18]
            d.text((BOWL_X, yy), disp, fill=c_navy, font=f_med)
            if potm and potm == nm:
                d.text((BOWL_X + tw(disp, f_med) + 6, yy - 1), "★", fill=c_star, font=f_med)
            fig = f"{st.wickets_taken}-{st.runs_conceded} ({st.overs_str})"
            d.text((W - 30 - tw(fig, f_med), yy), fig, fill=c_navy, font=f_med)

    # ── Footer ──
    fy = HEAD_H + n * BAND_H
    d.rectangle([(0, fy), (W, fy + FOOT_H)], fill=c_accent)
    res = (getattr(match, "result", None) or "MATCH DRAWN").upper()
    if potm:
        res += f"    •    POTM: {potm.upper()}"
    rf = f_bold if tw(res, f_bold) <= W - 40 else f_med
    d.text((W // 2 - tw(res, rf) // 2, fy + (FOOT_H - 24) // 2), res, fill=c_navy, font=rf)

    buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    return buf


# ─────────────────────────────────────────────────────────────────────────────
# FULL SCORECARD — every batter (dismissal/R/B/4s/6s/SR), extras+total, FoW, bowling
# ─────────────────────────────────────────────────────────────────────────────
def generate_test_scorecard_image(match, match_no_label="", potm="") -> io.BytesIO:
    W = 1080; M = 36; ROW = 30; HDR_H, RES_H = 104, 46
    C_BG = (17, 21, 32); C_GRAD_L = (11, 17, 44); C_GRAD_R = (21, 92, 140)
    C_RESBAR = (13, 14, 19); C_ROW_A = (26, 31, 44); C_ROW_B = (21, 26, 38)
    C_HEAD = (255, 255, 255); C_SUB = (176, 184, 200); C_COL = (132, 142, 160)
    C_NAME = (236, 239, 246); C_DISM = (150, 158, 174); C_NUM = (224, 230, 240)
    C_SCORE = (0, 212, 255); C_GOLD = (255, 209, 64); C_DIV = (44, 51, 68); C_EXTRA = (200, 206, 218)

    f_huge = _font(40); f_sub = _font(16, bold=False); f_res = _font(20); f_potm = _font(15)
    f_team = _font(21); f_sc = _font(24); f_meta = _font(14, bold=False); f_col = _font(13)
    f_nm = _font(16); f_dm = _font(14, bold=False); f_num = _font(16); f_sm = _font(13, bold=False)

    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    def tw(t, f):
        try: return measure.textlength(t, font=f)
        except Exception: return len(t) * 8

    def _bat_rows(inn):
        return [p for p in inn.batting_team["players"]
                if inn.batting_stats[p["name"]].balls_faced > 0 or inn.batting_stats[p["name"]].dismissal != "not out"]
    def _bowl_rows(inn):
        return [p for p in inn.bowling_team["players"] if inn.bowling_stats[p["name"]].balls_bowled > 0]

    played = match.innings_list
    INN_HEAD, GAP = 46, 16
    y = HDR_H + RES_H + 8
    for inn in played:
        nb, nbo = len(_bat_rows(inn)), len(_bowl_rows(inn))
        y += INN_HEAD + 6 + 24 + nb * ROW + ROW + 6 + 22 + 10 + 24 + nbo * ROW + GAP
    H = max(560, y + 18)

    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)
    def rt(x, yy, t, f, fill): d.text((x - tw(t, f), yy), t, font=f, fill=fill)
    def lerp(a, b, t): return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

    for x in range(W):
        d.line([(x, 0), (x, HDR_H)], fill=lerp(C_GRAD_L, C_GRAD_R, x / (W - 1)))
    d.text((M, 20), "DAY-NIGHT TEST" if getattr(match, "pink_ball", False) else "TEST MATCH", font=f_huge, fill=C_HEAD)
    d.text((M + 2, 70), f"{match.team1['name']}  vs  {match.team2['name']}", font=f_sub, fill=C_SUB)
    meta = f"{match.pitch} · {match.weather}" + (" · PINK BALL" if getattr(match, "pink_ball", False) else "") + (f" · {match_no_label}" if match_no_label else "")
    rt(W - M, 24, meta, f_meta, (200, 210, 224))

    d.rectangle([(0, HDR_H), (W, HDR_H + RES_H)], fill=C_RESBAR)
    d.text((M, HDR_H + (RES_H - 20) // 2), (getattr(match, "result", None) or "IN PROGRESS").upper(), font=f_res, fill=C_GOLD)
    if potm:
        rt(W - M, HDR_H + (RES_H - 15) // 2, f"★ POTM  {potm.upper()}", f_potm, C_GOLD)

    BAT_DISM = 300; BAT_R, BAT_B, BAT_4, BAT_6, BAT_SR = 690, 760, 826, 892, W - M
    BOW_O, BOW_M, BOW_R, BOW_W, BOW_E = 560, 648, 740, 832, W - M

    y = HDR_H + RES_H + 8
    for idx, inn in enumerate(played):
        tc = _hex(inn.batting_team.get("color", "#1D4ED8"))
        d.rectangle([(0, y), (W, y + INN_HEAD)], fill=tc)
        d.rectangle([(0, y), (6, y + INN_HEAD)], fill=C_GOLD)
        d.text((M, y + 6), ["1ST", "2ND", "3RD", "4TH"][idx] + " INNINGS", font=f_sm, fill=(255, 255, 255))
        d.text((M, y + 21), inn.batting_team["name"].upper()[:26], font=f_team, fill=C_HEAD)
        dec = " dec" if getattr(inn, "declared", False) else ""
        rt(W - M, y + 8, f"{inn.total_runs}/{inn.wickets}{dec}", f_sc, C_HEAD)
        rt(W - M, y + 26, f"{inn.overs_str} ov · RR {inn.run_rate}", f_sm, (235, 240, 248))
        y += INN_HEAD + 6

        d.text((M, y), "BATTER", font=f_col, fill=C_COL)
        for x, t in ((BAT_R, "R"), (BAT_B, "B"), (BAT_4, "4s"), (BAT_6, "6s"), (BAT_SR, "SR")):
            rt(x, y, t, f_col, C_COL)
        y += 22; d.line([(M, y - 4), (W - M, y - 4)], fill=C_DIV, width=1)
        for i, p in enumerate(_bat_rows(inn)):
            st = inn.batting_stats[p["name"]]
            d.rectangle([(0, y), (W, y + ROW)], fill=C_ROW_A if i % 2 == 0 else C_ROW_B)
            no = "*" if st.dismissal == "not out" else ""
            sr = round(st.runs_scored / st.balls_faced * 100, 1) if st.balls_faced else 0.0
            d.text((M, y + 6), p["name"][:20], font=f_nm, fill=C_NAME)
            d.text((BAT_DISM, y + 7), (st.dismissal or "")[:34], font=f_dm, fill=C_DISM)
            rt(BAT_R, y + 6, f"{st.runs_scored}{no}", f_num, C_SCORE if st.runs_scored >= 50 else C_NUM)
            rt(BAT_B, y + 6, str(st.balls_faced), f_num, C_NUM)
            rt(BAT_4, y + 6, str(st.fours), f_sm, C_SUB)
            rt(BAT_6, y + 6, str(st.sixes), f_sm, C_SUB)
            rt(BAT_SR, y + 6, f"{sr:.1f}", f_sm, C_SUB)
            y += ROW
        d.line([(M, y + 2), (W - M, y + 2)], fill=C_DIV, width=1); y += 8
        d.text((M, y), "Extras", font=f_dm, fill=C_EXTRA)
        d.text((M + 92, y), str(inn.extras), font=f_num, fill=C_EXTRA)
        d.text((620, y), "TOTAL", font=f_num, fill=C_HEAD)
        rt(W - M, y, f"{inn.total_runs}/{inn.wickets}{dec}  ({inn.overs_str} ov)", f_num, C_GOLD)
        y += ROW
        if inn.fow:
            fow = "FoW: " + "  ".join(inn.fow)
            while tw(fow, f_sm) > (W - 2 * M) and len(fow) > 10:
                fow = fow[:-4].rstrip() + "…"
            d.text((M, y), fow, font=f_sm, fill=(126, 134, 150))
        y += 22 + 10
        d.text((M, y), "BOWLING", font=f_col, fill=C_COL)
        for x, t in ((BOW_O, "O"), (BOW_M, "M"), (BOW_R, "R"), (BOW_W, "W"), (BOW_E, "ECON")):
            rt(x, y, t, f_col, C_COL)
        y += 22; d.line([(M, y - 4), (W - M, y - 4)], fill=C_DIV, width=1)
        for i, p in enumerate(_bowl_rows(inn)):
            st = inn.bowling_stats[p["name"]]
            d.rectangle([(0, y), (W, y + ROW)], fill=C_ROW_A if i % 2 == 0 else C_ROW_B)
            d.text((M, y + 6), p["name"][:24], font=f_nm, fill=C_NAME)
            rt(BOW_O, y + 6, st.overs_str, f_num, C_NUM)
            rt(BOW_M, y + 6, str(st.maidens), f_sm, C_SUB)
            rt(BOW_R, y + 6, str(st.runs_conceded), f_num, C_NUM)
            rt(BOW_W, y + 6, str(st.wickets_taken), f_num, C_SCORE if st.wickets_taken >= 3 else C_NUM)
            rt(BOW_E, y + 6, f"{st.economy:.2f}", f_sm, C_SUB)
            y += ROW
        y += INN_HEAD - 30

    buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    return buf


if __name__ == "__main__":
    import random
    import test_simulation as T
    random.seed()
    m = T.TestMatch(T.TEAM_ALPHA, T.TEAM_BETA,
                    random.choice(["Flat", "Green", "Dusty", "Hard"]),
                    random.choice(["Clear", "Overcast", "Dry Heat"]))
    T.simulate_match(m)
    potm = (T._player_of_match(m) or "").split(" (")[0]
    open("summary.png", "wb").write(generate_test_summary_image(m, "TEST-Match No 1", potm).read())
    open("scorecard.png", "wb").write(generate_test_scorecard_image(m, "TEST-Match No 1", potm).read())
    print(f"Result: {m.result}")
    print(f"POTM: {potm}")
    print(f"Innings: {[ (i.batting_team['name'], i.total_runs, i.wickets) for i in m.innings_list ]}")
    print("Wrote summary.png and scorecard.png")

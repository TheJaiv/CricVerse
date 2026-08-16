"""
Career Mode broadcast renderers.

Every function takes the plain state dict from career/snapshot.py (or a career
document) and returns a PNG BytesIO. No discord, no bot, no Mongo - so the whole
GUI can be rendered and eyeballed offline via tools/career_render_preview.py.

Layout is a TV lower-third: header strip, score hero, crease panel, over ticker,
stat rail. Deliberately unlike the tournament scorecards, which are bright panels
composited onto template PNGs.
"""
from PIL import Image, ImageDraw

from career.ui import theme as T

W, H = 1280, 720
M = 48                      # safe margin


def _header(img, d, state):
    y = 40
    f_badge = T.font(20, "bold")
    live_on = state.get("kind") != "break"
    x = T.live_badge(d, M, y, f_badge, "LIVE", live_on)

    f_chip = T.font(18, "cond")
    for label in _condition_chips(state):
        w = int(T.tw(d, label, f_chip)) + 30
        T.chip(d, [x + 14, y, x + 14 + w, y + f_badge.size + 12], label, f_chip, T.PANEL_2, T.INK_SOFT)
        x += 14 + w

    fmt = f"{state['format_overs']} OVERS"
    kind = {"club": "CLUB MATCH", "debut": "ACADEMY TRIAL",
            "scenario": "SCENARIO", "casual": "MATCH"}.get(state.get("kind"), "MATCH")
    f_r = T.font(20, "cond")
    T.rtext(d, W - M, y + 8, f"{kind}  ·  {fmt}", f_r, T.INK_SOFT)
    d.line([(M, y + 52), (W - M, y + 52)], fill=T.LINE, width=2)


def _condition_chips(state):
    out = []
    if state.get("pitch"):
        out.append(state["pitch"].upper())
    if state.get("weather"):
        out.append(state["weather"].upper())
    if state.get("free_hit"):
        out.append("FREE HIT")
    return out


def _score_hero(img, d, state):
    """Team band + the big score. The one thing readable on a phone at a glance."""
    bat = state["batting"]
    color = T.hex_to_rgb(bat["color"]) if isinstance(bat["color"], str) else tuple(bat["color"])
    top, bot = 116, 268

    T.rrect(d, [M, top, W - M, bot], 18, fill=T.PANEL)
    # team-colour spine down the left edge
    T.vgrad(img, [M, top, M + 12, bot], color, T.mix(color, T.BG, 0.5))
    d.rectangle([M, top, M + 12, top + 18], fill=color)

    f_team = T.fit_text(d, bat["name"].upper(), lambda s: T.font(s, "cond"), 520, 34, 18)
    d.text((M + 38, top + 26), bat["name"].upper(), font=f_team, fill=T.INK)

    score = f"{bat['runs']}/{bat['wickets']}"
    if bat["wickets"] >= state.get("max_wickets", 10):
        score = f"{bat['runs']} all out"
    f_score = T.font(76, "bold")
    d.text((M + 36, top + 62), score, font=f_score, fill=T.INK)

    f_ov = T.font(26, "cond")
    ov_x = M + 44 + int(T.tw(d, score, f_score))
    d.text((ov_x, top + 100), f"({bat['overs']}/{state['format_overs']}.0)", font=f_ov, fill=T.INK_SOFT)

    # Right column: chase equation in innings 2, projection in innings 1
    rx = W - M - 28
    if state.get("target"):
        fi = state.get("first_innings") or {}
        f_small = T.font(19, "cond")
        T.rtext(d, rx, top + 24, f"{fi.get('name','')}  {fi.get('runs',0)}/{fi.get('wickets',0)}", f_small, T.INK_SOFT)
        need, bl = state.get("need") or 0, state.get("balls_left") or 0
        if need > 0 and bl > 0:
            f_eq = T.font(40, "bold")
            T.rtext(d, rx, top + 54, f"NEED {need} OFF {bl}", f_eq, T.ACCENT)
            f_rrr = T.font(22, "cond")
            T.rtext(d, rx, top + 106, f"RRR {state.get('rrr', 0):.2f}"
                    + ("   DLS" if state.get("dls") else ""), f_rrr, T.INK_SOFT)
        else:
            f_eq = T.font(34, "bold")
            T.rtext(d, rx, top + 70, "TARGET REACHED", f_eq, T.GOOD)
    else:
        f_lab = T.font(19, "cond")
        T.rtext(d, rx, top + 30, "PROJECTED", f_lab, T.INK_SOFT)
        f_proj = T.font(56, "bold")
        T.rtext(d, rx, top + 54, str(state.get("proj") or 0), f_proj, T.ACCENT)
        T.rtext(d, rx, top + 118, f"CRR {state.get('crr', 0):.2f}", f_lab, T.INK_SOFT)


def _crease(img, d, state):
    """Batters at the crease and the bowler in his spell."""
    top, bot = 292, 452
    mid = 792
    T.rrect(d, [M, top, mid - 10, bot], 16, fill=T.PANEL)
    T.rrect(d, [mid + 10, top, W - M, bot], 16, fill=T.PANEL)

    f_h = T.font(17, "cond")
    d.text((M + 24, top + 16), "AT THE CREASE", font=f_h, fill=T.INK_DIM)
    d.text((mid + 34, top + 16), "BOWLING", font=f_h, fill=T.INK_DIM)

    f_name = T.font(25, "bold")
    f_num = T.font(27, "bold")
    f_sub = T.font(17, "cond")
    y = top + 48
    for b in state.get("batters", [])[:2]:
        if b["striker"]:
            T.rrect(d, [M + 14, y - 6, mid - 24, y + 52], 10, fill=T.PANEL_2)
            d.rectangle([M + 14, y - 6, M + 19, y + 52], fill=T.ACCENT)
        nm = T.fit_text(d, b["name"], lambda s: T.font(s, "bold"), 330, 25, 15)
        d.text((M + 32, y), b["name"], font=nm, fill=T.INK if b["striker"] else T.INK_SOFT)
        d.text((M + 32, y + 30), f"SR {b['sr']:.1f}   4s {b['fours']}   6s {b['sixes']}",
               font=f_sub, fill=T.INK_DIM)
        runs = f"{b['runs']}"
        T.rtext(d, mid - 92, y + 4, runs, f_num, T.INK if b["striker"] else T.INK_SOFT)
        T.rtext(d, mid - 40, y + 10, f"({b['balls']})", f_sub, T.INK_DIM)
        y += 62

    bw = state.get("bowler")
    if bw:
        nm = T.fit_text(d, bw["name"], lambda s: T.font(s, "bold"), 300, 25, 15)
        d.text((mid + 34, top + 48), bw["name"], font=nm, fill=T.INK)
        f_fig = T.font(40, "bold")
        figs = f"{bw['wickets']}-{bw['runs']}"
        d.text((mid + 34, top + 84), figs, font=f_fig, fill=T.ACCENT_2)
        f_ov = T.font(19, "cond")
        d.text((mid + 40 + int(T.tw(d, figs, f_fig)), top + 100),
               f"({bw['overs']} ov)", font=f_ov, fill=T.INK_SOFT)
        T.rtext(d, W - M - 24, top + 100, f"ECON {bw['econ']:.2f}", f_ov, T.INK_SOFT)


def _last_ball_banner(img, d, state, y):
    """What just happened, drawn ON the card.

    This used to be a chat line per ball. Players wanted the feed to read as a
    run of images with nothing between them, so the result lives on the graphic
    instead - the card is then self-contained and each ball's image is a complete
    record of that ball.
    """
    rec = state.get("last_ball")
    over_no = state.get("over_no", 0)
    if not rec:
        d.text((M, y), f"OVER {over_no + 1}  ·  THIS OVER", font=T.font(17, "cond"), fill=T.INK_DIM)
        return

    if rec.get("dismissal"):
        tone, word = "wicket", "WICKET"
    elif rec.get("is_wide"):
        tone, word = "wide", "WIDE"
    else:
        runs = rec.get("runs_off_bat", 0)
        tone = {0: "dot", 4: "four", 6: "six"}.get(runs, "single")
        word = {0: "DOT", 1: "1 RUN", 4: "FOUR", 6: "SIX"}.get(runs, f"{runs} RUNS")
        if rec.get("is_no_ball"):
            tone, word = "noball", f"NO BALL · {word}"
        elif rec.get("is_bye"):
            word = f"{rec.get('extras', 0)} LEG BYES"
    col = T.OUTCOME_COLOR.get(tone, T.INK_SOFT)

    f_w = T.font(21, "bold")
    pill_w = int(T.tw(d, word, f_w)) + 30
    T.chip(d, [M, y - 4, M + pill_w, y + 28], word, f_w, col)

    detail = f"{rec.get('over', 0)}.{rec.get('ball', 1)}  {rec.get('bowler','')} to {rec.get('striker','')}"
    extra = " · ".join(x for x in (rec.get("delivery"), rec.get("shot")) if x)
    if extra:
        detail += f"  ·  {extra}"
    if rec.get("dismissal_desc"):
        detail += f"  ·  {rec['dismissal_desc']}"
    f_d = T.fit_text(d, detail, lambda s: T.font(s, "cond"), W - 2 * M - pill_w - 220, 18, 12)
    d.text((M + pill_w + 16, y + 2), detail, font=f_d, fill=T.INK_SOFT)


def _ball_pills(d, balls, x, y, size, dim=False):
    """Row of coloured ball pills. Returns the x it ended at."""
    f_b = T.font(int(size * 0.42), "bold")
    for b in balls[:12]:
        col = T.OUTCOME_COLOR.get(b["tone"], T.INK_DIM)
        if dim:
            col = T.mix(col, T.BG, 0.42)
        w = size + (int(size * 0.42) if len(b["label"]) > 2 else 0)
        T.rrect(d, [x, y, x + w, y + size], size // 2, fill=T.mix(col, T.BG, 0.62))
        T.rrect(d, [x + 2, y + 2, x + w - 2, y + size - 2], (size - 4) // 2,
                fill=T.mix(col, T.PANEL, 0.25) if b["tone"] == "dot" else col)
        ink = T.text_on(col if b["tone"] != "dot" else T.PANEL)
        T.ctext(d, x + w / 2, y + (size - f_b.size) / 2 - 2, b["label"], f_b, ink)
        x += w + (12 if not dim else 8)
    return x


def _over_strip(img, d, state):
    """Ball-by-ball ticker: this over, the over before it, and the recent-overs run.

    The previous over matters more than it looks. The card is a single image that
    keeps being replaced, so anything not drawn on it is gone - players reported
    losing the thread of the match because only six balls were ever visible.
    """
    top = 466
    over_no = state.get("over_no", 0)
    _last_ball_banner(img, d, state, top)

    balls = state.get("this_over", [])
    y = top + 34
    x_end = M
    if balls:
        x_end = _ball_pills(d, balls, M, y, 46)
    else:
        d.text((M, y + 12), "over starting…", font=T.font(19, "cond"), fill=T.INK_DIM)
        x_end = M + 160

    # Previous over as ONE short trailing label, on the pills row so it cannot
    # collide with the DRS block above it. Drawing every over onto the card was
    # tried and rejected in playtest - it crowds the graphic and gets hard to read.
    recent = state.get("recent_overs", [])
    if recent:
        o, runs, wkts = recent[-1]
        summ = f"last over {runs}" + (f" · {wkts}w" if wkts else "")
        d.text((x_end + 10, y + 14), summ, font=T.font(16, "cond"), fill=T.INK_DIM)

    # Review pips, right-aligned on the top row. Laid out right-to-left but each
    # side's pips fill left-to-right, so remaining reviews are always the left ones.
    rev = state.get("reviews") or {}
    if rev:
        f_r = T.font(16, "cond")
        T.rtext(d, W - M, top, "DRS REVIEWS", f_r, T.INK_DIM)
        edge = W - M
        for key, label in (("bowling", "BOWL"), ("batting", "BAT")):
            left = rev.get(key)
            if left is None:
                continue
            x0 = edge - 2 * 24
            for i in range(2):
                col = T.GOOD if i < left else T.mix(T.BAD, T.BG, 0.55)
                cx = x0 + i * 24
                d.ellipse([cx, y + 12, cx + 16, y + 28], fill=col)
            T.rtext(d, x0 - 10, y + 10, label, f_r, T.INK_DIM)
            edge = x0 - 10 - int(T.tw(d, label, f_r)) - 26


def _rail(img, d, state):
    """Bottom stat rail plus the career objective chip."""
    top, bot = 596, 672
    T.rrect(d, [M, top, W - M, bot], 14, fill=T.PANEL)

    # CRR/RRR/projection already headline the hero panel - repeating them here just
    # burns the rail, so it carries the secondary numbers instead.
    bat = state["batting"]
    wkts_left = max(0, state.get("max_wickets", 10) - bat["wickets"])
    cells = [
        ("OVERS", f"{bat['overs']}/{state['format_overs']}"),
        ("P'SHIP", str(state.get("partnership", 0))),
        ("EXTRAS", str(bat.get("extras", 0))),
        ("WKTS LEFT", str(wkts_left)),
        ("BALLS LEFT", str(state.get("balls_left", 0))),
    ]

    f_lab = T.font(15, "cond")
    f_val = T.font(28, "bold")
    cw = (W - 2 * M) / (len(cells) + 2)
    for i, (lab, val) in enumerate(cells):
        cx = M + cw * (i + 0.5)
        T.ctext(d, cx, top + 12, lab, f_lab, T.INK_DIM)
        T.ctext(d, cx, top + 32, val, f_val, T.INK)
        if i:
            d.line([(M + cw * i, top + 16), (M + cw * i, bot - 16)], fill=T.LINE, width=1)

    obj = state.get("objective")
    if obj:
        ox = M + cw * len(cells)
        col = T.GOOD if obj.get("done") else T.ACCENT
        T.rrect(d, [ox, top + 10, W - M - 14, bot - 10], 12, fill=T.mix(col, T.BG, 0.72))
        f_o = T.font(21, "cond")
        d.text((ox + 20, top + 18), obj["text"], font=f_o, fill=col)
        d.text((ox + 20, top + 44), obj["detail"], font=T.font(17, "cond"), fill=T.INK_SOFT)

    toss = state.get("toss")
    if toss:
        T.ctext(d, W / 2, bot + 16, toss, T.font(15, "cond"), T.INK_DIM)


def render_live_card(state):
    """The card that gets edited into the pinned broadcast message every ball."""
    img, d = T.canvas(W, H)
    _header(img, d, state)
    _score_hero(img, d, state)
    _crease(img, d, state)
    _over_strip(img, d, state)
    _rail(img, d, state)
    return T.save_png(img)


# Innings break / result / full scorecard
def _summary_head(img, d, state, title, subtitle=None):
    T.vgrad(img, [0, 0, W, 128], T.mix(T.PANEL_2, T.BG, 0.25), T.BG)
    T.ctext(d, W / 2, 30, title, T.font(44, "bold"), T.INK)
    if subtitle:
        T.ctext(d, W / 2, 84, subtitle, T.font(20, "cond"), T.ACCENT)
    chips = [c for c in (state.get("pitch"), state.get("weather"),
                         f"{state.get('format_overs')} OVERS") if c]
    f = T.font(16, "cond")
    x = M
    for c in chips:
        cw = int(T.tw(d, str(c).upper(), f)) + 28
        T.chip(d, [x, 26, x + cw, 56], str(c).upper(), f, T.PANEL_2, T.INK_SOFT)
        x += cw + 10


def _innings_block(img, d, inn, box, compact=False):
    """One innings: coloured team header, top batters, top bowlers."""
    x1, y1, x2, y2 = box
    color = T.hex_to_rgb(inn["color"]) if isinstance(inn["color"], str) else tuple(inn["color"])
    T.rrect(d, box, 16, fill=T.PANEL)
    T.hgrad(img, [x1, y1, x2, y1 + 54], T.mix(color, T.BG, 0.15), T.mix(color, T.BG, 0.72))

    f_team = T.fit_text(d, inn["team"].upper(), lambda s: T.font(s, "cond"), (x2 - x1) * 0.55, 26, 15)
    d.text((x1 + 20, y1 + 14), inn["team"].upper(), font=f_team, fill=T.text_on(color))
    T.rtext(d, x2 - 20, y1 + 10, f"{inn['runs']}/{inn['wickets']}", T.font(30, "bold"), T.INK)
    T.rtext(d, x2 - 20, y1 + 60, f"({inn['overs']} ov)", T.font(17, "cond"), T.INK_SOFT)

    f_k = T.font(14, "cond")
    f_n = T.font(18, "bold")
    f_v = T.font(18, "mono")
    y = y1 + 88
    d.text((x1 + 20, y), "BATTER", font=f_k, fill=T.INK_DIM)
    T.rtext(d, x2 - 20, y, "R (B)   4s 6s   SR", f_k, T.INK_DIM)
    y += 24
    bats = sorted(inn["batting"], key=lambda b: -b["runs"])[:5 if compact else 7]
    for b in bats:
        nm = T.fit_text(d, b["name"], lambda s: T.font(s, "bold"), (x2 - x1) * 0.42, 18, 12)
        d.text((x1 + 20, y), b["name"], font=nm, fill=T.INK if not b["out"] else T.INK_SOFT)
        star = "" if b["out"] else "*"
        T.rtext(d, x2 - 20, y,
                f"{b['runs']}{star} ({b['balls']})   {b['fours']} {b['sixes']}   {b['sr']:.0f}",
                f_v, T.INK)
        y += 26
    y += 12
    d.text((x1 + 20, y), "BOWLER", font=f_k, fill=T.INK_DIM)
    T.rtext(d, x2 - 20, y, "O-R-W   ECON", f_k, T.INK_DIM)
    y += 24
    bowls = sorted(inn["bowling"], key=lambda b: (-b["wickets"], b["econ"]))[:4 if compact else 5]
    for b in bowls:
        nm = T.fit_text(d, b["name"], lambda s: T.font(s, "bold"), (x2 - x1) * 0.42, 18, 12)
        d.text((x1 + 20, y), b["name"], font=nm, fill=T.INK)
        T.rtext(d, x2 - 20, y, f"{b['overs']}-{b['runs']}-{b['wickets']}   {b['econ']:.2f}",
                f_v, T.INK_SOFT)
        y += 26
    d.text((x1 + 20, y2 - 30), f"Extras {inn['extras']}", font=f_k, fill=T.INK_DIM)


def render_innings_break(state):
    """Posted at the innings-1 break, with the target the chase now needs."""
    img, d = T.canvas(W, H)
    inn = state["innings"][0]
    target = (state.get("target") or inn["runs"] + 1)
    _summary_head(img, d, state, "INNINGS BREAK",
                  f"{inn['bowling_team']} need {target} to win")
    _innings_block(img, d, inn, [M, 148, W - M, H - 108])
    rr = (inn["runs"] / max(1, state["format_overs"]))
    T.ctext(d, W / 2, H - 88, f"TARGET {target}   ·   {rr:.2f} RUNS PER OVER REQUIRED",
            T.font(22, "cond"), T.ACCENT)
    return T.save_png(img)


def render_result(state):
    """Final graphic: both innings side by side, result line and player of the match."""
    img, d = T.canvas(W, H)
    _summary_head(img, d, state, "FULL TIME", state.get("result"))
    inns = state["innings"][:2]
    if len(inns) == 2:
        mid = W / 2
        _innings_block(img, d, inns[0], [M, 148, mid - 12, H - 96], compact=True)
        _innings_block(img, d, inns[1], [mid + 12, 148, W - M, H - 96], compact=True)
    elif inns:
        _innings_block(img, d, inns[0], [M, 148, W - M, H - 96])
    if state.get("potm"):
        T.ctext(d, W / 2, H - 78, "PLAYER OF THE MATCH", T.font(16, "cond"), T.INK_DIM)
        T.ctext(d, W / 2, H - 56, state["potm"].upper(), T.font(30, "bold"), T.ACCENT)
    return T.save_png(img)


def render_scorecard(state):
    """Full scorecard - every batter who faced a ball, every bowler who bowled."""
    inns = state["innings"]
    rows = sum(len(i["batting"]) + len(i["bowling"]) for i in inns)
    height = max(720, 260 + rows * 30 + len(inns) * 120)
    img, d = T.canvas(W, height)
    _summary_head(img, d, state, "SCORECARD", state.get("result"))

    y = 150
    f_k = T.font(14, "cond")
    f_n = T.font(19, "bold")
    f_v = T.font(19, "mono")
    for inn in inns:
        color = T.hex_to_rgb(inn["color"]) if isinstance(inn["color"], str) else tuple(inn["color"])
        T.hgrad(img, [M, y, W - M, y + 46], T.mix(color, T.BG, 0.2), T.mix(color, T.BG, 0.8))
        d.text((M + 18, y + 11), inn["team"].upper(), font=T.font(23, "cond"), fill=T.text_on(color))
        T.rtext(d, W - M - 18, y + 8, f"{inn['runs']}/{inn['wickets']}  ({inn['overs']})",
                T.font(24, "bold"), T.INK)
        y += 58
        for b in inn["batting"]:
            d.text((M + 18, y), b["name"], font=f_n, fill=T.INK)
            d.text((M + 250, y + 2), b["how"], font=T.font(15, "cond"),
                   fill=T.INK_DIM if b["out"] else T.GOOD)
            T.rtext(d, W - M - 18, y,
                    f"{b['runs']} ({b['balls']})   {b['fours']}x4 {b['sixes']}x6   {b['sr']:.1f}",
                    f_v, T.INK_SOFT)
            y += 28
        d.text((M + 18, y), f"EXTRAS {inn['extras']}", font=f_k, fill=T.INK_DIM)
        y += 30
        for b in inn["bowling"]:
            d.text((M + 18, y), b["name"], font=f_n, fill=T.INK_SOFT)
            T.rtext(d, W - M - 18, y,
                    f"{b['overs']}-{b['runs']}-{b['wickets']}   ECON {b['econ']:.2f}", f_v, T.INK_SOFT)
            y += 28
        y += 26

    if state.get("potm"):
        T.ctext(d, W / 2, height - 46, f"PLAYER OF THE MATCH · {state['potm'].upper()}",
                T.font(20, "cond"), T.ACCENT)
    return T.save_png(img)


# Charts
def render_manhattan(history, title="RUNS PER OVER"):
    """Over-by-over bar chart, wickets marked - reads the ball history the engine
    now keeps, which is why this chart could not exist before."""
    from career import ballfeed as BF
    overs = BF.over_runs(history)
    cw, ch = 1120, 460
    img, d = T.canvas(cw, ch)
    d.text((40, 28), title, font=T.font(26, "cond"), fill=T.INK)
    if not overs:
        return T.save_png(img)

    top, bottom = 90, ch - 70
    peak = max(4, max(r for _, r, _ in overs))
    bw = (cw - 96) / len(overs)
    f_s = T.font(14, "cond")
    for i, (o, runs, wkts) in enumerate(overs):
        x = 48 + i * bw
        h = (bottom - top) * (runs / peak)
        # Bar colour encodes runs only; wickets are the red dots above it. Tinting
        # the bar too made an expensive over with a wicket read as a quiet one.
        col = T.GOOD if runs >= 12 else (T.ACCENT_2 if runs >= 7 else T.mix(T.ACCENT_2, T.BG, 0.55))
        T.rrect(d, [x + 3, bottom - h, x + bw - 5, bottom], 5, fill=col)
        T.ctext(d, x + bw / 2, bottom - h - 20, str(runs), f_s, T.INK_SOFT)
        T.ctext(d, x + bw / 2, bottom + 8, str(o + 1), f_s, T.INK_DIM)
        for w in range(wkts):
            d.ellipse([x + bw / 2 - 5, bottom - h - 40 - w * 14,
                       x + bw / 2 + 5, bottom - h - 30 - w * 14], fill=T.BAD)
    d.line([(44, bottom), (cw - 44, bottom)], fill=T.LINE, width=2)
    return T.save_png(img)


def render_wagon_wheel(history, batter):
    """Where a batter scored: angle and carry per scoring shot."""
    from career import ballfeed as BF
    pts = BF.scoring_shots(history, batter)
    size = 640
    img, d = T.canvas(size, size + 60)
    cx, cy, R = size / 2, size / 2 + 30, size * 0.42

    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(16, 46, 34), outline=T.LINE, width=3)
    d.ellipse([cx - R * 0.55, cy - R * 0.55, cx + R * 0.55, cy + R * 0.55], outline=T.LINE, width=1)
    d.rectangle([cx - 8, cy - 34, cx + 8, cy + 34], fill=(196, 178, 138))

    for angle, carry, runs in pts:
        dx, dy = BF.polar(angle, R * max(0.2, carry))
        col = T.OUTCOME_COLOR["six"] if runs == 6 else (
            T.OUTCOME_COLOR["four"] if runs == 4 else T.INK_SOFT)
        d.line([(cx, cy), (cx + dx, cy + dy)], fill=col, width=3 if runs >= 4 else 2)
        if runs >= 4:
            d.ellipse([cx + dx - 5, cy + dy - 5, cx + dx + 5, cy + dy + 5], fill=col)

    d.text((28, 20), f"WAGON WHEEL · {batter.upper()}", font=T.font(22, "cond"), fill=T.INK)
    fours = sum(1 for _, _, r in pts if r == 4)
    sixes = sum(1 for _, _, r in pts if r == 6)
    T.rtext(d, size - 28, 24, f"{fours}x4   {sixes}x6", T.font(20, "cond"), T.ACCENT)
    return T.save_png(img)


# Player card
PC_W, PC_H = 1020, 600
_PC_PANEL = 372          # width of the left identity panel


def render_player_card(state):
    """`cv profile` - the career identity card, in the same broadcast language as
    the live graphic rather than the old standalone 760x420 card."""
    tier = state.get("tier", "Bronze")
    accent = T.TIER_COLOR.get(tier, T.TIER_COLOR["Bronze"])
    img = Image.new("RGB", (PC_W, PC_H), T.BG)
    d = ImageDraw.Draw(img)

    # right side: broadcast navy; left: tier-coloured identity slab
    T.vgrad(img, [_PC_PANEL, 0, PC_W, PC_H], T.mix(T.BG, T.PANEL, 0.55), T.BG_DEEP)
    T.vgrad(img, [0, 0, _PC_PANEL, PC_H],
            T.mix(accent, (255, 255, 255), 0.12), T.mix(accent, (0, 0, 0), 0.55))
    ink = T.text_on(accent)
    ink_soft = T.mix(ink, accent, 0.32)
    d.line([(_PC_PANEL, 0), (_PC_PANEL, PC_H)], fill=T.mix(accent, T.BG, 0.4), width=3)

    cx = _PC_PANEL // 2
    T.ctext(d, cx, 34, str(state.get("ovr", 0)), T.font(112, "bold"), ink)
    T.ctext(d, cx, 168, "OVERALL", T.font(17, "cond"), ink_soft)
    T.ctext(d, cx, 206, tier.upper(), T.font(36, "cond"), ink)
    T.ctext(d, cx, 250, T.TIER_BLURB.get(tier, ""), T.font(16, "cond"), ink_soft)
    d.line([(52, 288), (_PC_PANEL - 52, 288)], fill=ink_soft, width=2)
    T.ctext(d, cx, 302, state.get("role", "ALL-ROUNDER"), T.font(21, "cond"), ink)

    f_chip = T.font(16, "cond")
    y = 356
    for txt in state.get("chips", []):
        T.chip(d, [46, y, _PC_PANEL - 46, y + 34], txt, f_chip,
               T.mix(accent, (0, 0, 0), 0.34), ink)
        y += 44

    if not state.get("debut_done"):
        T.chip(d, [46, PC_H - 66, _PC_PANEL - 46, PC_H - 32], "DEBUT PENDING",
               f_chip, (86, 30, 30), (244, 150, 150))

    # Right: name, attributes, career numbers
    nx = _PC_PANEL + 36
    name = str(state.get("name", "Rookie"))
    f_name = T.fit_text(d, name, lambda s: T.font(s, "bold"), PC_W - nx - 190, 46, 22)
    d.text((nx, 34), name, font=f_name, fill=T.INK)
    sub = " · ".join([s for s in (state.get("title"), state.get("club"),
                                  f"Season {state['season']}" if state.get("season") else None) if s])
    if sub:
        d.text((nx, 84), sub, font=T.font(18, "cond"), fill=accent)

    _pc_condition(d, state, PC_W - 36)

    f_lab = T.font(17, "cond")
    f_val = T.font(26, "bold")
    y = 128
    bar_x2 = PC_W - 110
    for label, v in state.get("attributes", []):
        col = T.rating_color(v)
        d.text((nx, y), label, font=f_lab, fill=T.INK_SOFT)
        T.rtext(d, PC_W - 40, y - 6, str(v), f_val, col)
        T.bar(d, [nx, y + 26, bar_x2, y + 36], max(2, v) / 99.0, col, T.PANEL_2)
        y += 54

    _pc_stats(d, state, nx, y + 16)

    T.rrect(d, [nx, PC_H - 62, nx + 234, PC_H - 22], 20, fill=(44, 36, 14))
    d.text((nx + 20, PC_H - 54), f"COINS  {state.get('coins', 0):,}",
           font=T.font(19, "bold"), fill=T.ACCENT)
    if state.get("premium"):
        T.chip(d, [nx + 252, PC_H - 62, nx + 372, PC_H - 22], "PREMIUM",
               T.font(16, "cond"), T.mix(T.ACCENT_2, T.BG, 0.55), T.ACCENT_2)
    return T.save_png(img)


def _pc_condition(d, state, rx):
    """Form / fitness gauges. Only drawn once Phase 3 has put them on the document,
    so an old career shows the card exactly as before."""
    form, fit = state.get("form"), state.get("fitness")
    if form is None and fit is None:
        return
    f_l = T.font(14, "cond")
    y = 40
    for label, val, good in (("FORM", form, T.ACCENT_2), ("FITNESS", fit, T.GOOD)):
        if val is None:
            continue
        col = good if val >= 60 else (T.WARN if val >= 35 else T.BAD)
        T.rtext(d, rx, y, label, f_l, T.INK_DIM)
        T.bar(d, [rx - 120, y + 18, rx, y + 26], val / 100.0, col, T.PANEL_2)
        y += 44
    if state.get("injury"):
        T.rtext(d, rx, y, state["injury"].upper(), T.font(15, "cond"), T.BAD)


def _pc_stats(d, state, x, y):
    """Two compact stat rows - batting then bowling."""
    bat, bowl = state.get("batting", {}), state.get("bowling", {})
    avg = bat.get("avg")
    rows = [
        ("BATTING", [("M", bat.get("matches", 0)), ("RUNS", bat.get("runs", 0)),
                     ("HS", bat.get("hs", 0)),
                     ("AVG", f"{avg:.1f}" if avg else "-"),
                     ("SR", f"{bat.get('sr', 0):.1f}"),
                     ("50/100", f"{bat.get('fifties', 0)}/{bat.get('hundreds', 0)}")]),
        ("BOWLING", [("OV", bowl.get("overs", "0.0")), ("WKTS", bowl.get("wickets", 0)),
                     ("BEST", bowl.get("best", "-")),
                     ("ECON", f"{bowl.get('econ', 0):.2f}")]),
    ]
    f_h = T.font(14, "cond")
    f_k = T.font(13, "cond")
    f_v = T.font(21, "bold")
    for title, cells in rows:
        d.text((x, y), title, font=f_h, fill=T.INK_DIM)
        cw = (PC_W - 40 - x) / max(1, len(cells))
        for i, (k, v) in enumerate(cells):
            cxx = x + cw * i
            d.text((cxx, y + 20), k, font=f_k, fill=T.INK_DIM)
            d.text((cxx, y + 36), str(v), font=f_v, fill=T.INK)
        y += 84

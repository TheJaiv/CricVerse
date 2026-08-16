"""
Animated replays - the "live video" layer of Career Mode.

Discord cannot embed real video, but it plays GIFs inline, so a short generated
clip is the closest honest thing. Pillow writes animated GIFs natively, so this
adds no dependency (no ffmpeg, no moviepy).

Clips are small on purpose: 480x270, 14-20 frames, adaptive palette. A replay is
posted only on the moments listed in career/ballfeed.is_highlight plus DRS, so
the feed stays special instead of turning into a flipbook of dot balls.
"""
import io

from PIL import Image, ImageDraw

from career.ui import theme as T
from career import ballfeed as BF

W, H = 480, 270
FRAME_MS = 80

# Pitch drawn in perspective: narrow at the bowler's end, wide at the camera end.
_TOP_Y, _BOT_Y = 62, 232
_TOP_HW, _BOT_HW = 26, 96
_CX = W // 2


def _pitch_x(frac, line=0.0):
    """Screen x for a point `frac` down the pitch on a -1..1 line."""
    hw = _TOP_HW + (_BOT_HW - _TOP_HW) * frac
    return _CX + line * hw


def _pitch_y(frac):
    return _TOP_Y + (_BOT_Y - _TOP_Y) * frac


def _scene(state=None, tint=None):
    """Base frame: sky, outfield, pitch strip, stumps, crease lines."""
    img = Image.new("RGB", (W, H), T.BG)
    T.vgrad(img, [0, 0, W, 118], T.mix(T.BG, T.PANEL_2, 0.85), T.mix(T.BG, (16, 46, 34), 0.9))
    T.vgrad(img, [0, 108, W, H], (18, 58, 40), (11, 34, 24))
    d = ImageDraw.Draw(img)

    strip = (196, 178, 138)
    if tint:
        strip = T.mix(strip, tint, 0.25)
    d.polygon([(_pitch_x(0, -1), _TOP_Y), (_pitch_x(0, 1), _TOP_Y),
               (_pitch_x(1, 1), _BOT_Y), (_pitch_x(1, -1), _BOT_Y)], fill=strip)
    for frac in (0.08, 0.94):
        d.line([(_pitch_x(frac, -1), _pitch_y(frac)), (_pitch_x(frac, 1), _pitch_y(frac))],
               fill=(238, 232, 214), width=2)

    # stumps at both ends
    _stumps(d, 0.02, 12, (232, 228, 216))
    _stumps(d, 1.0, 30, (238, 234, 222))

    # bowler at the far end, batter at the near end (camera sits behind the bowler
    # end looking down the pitch, so the batter is the big one)
    _figure(d, _pitch_x(0.06, 1.9), _pitch_y(0.06) + 10, 30, (74, 88, 116), "bowl")
    _figure(d, _pitch_x(0.98, -0.62), _pitch_y(0.98), 54, (26, 33, 50), "bat")
    return img, d


def _stumps(d, frac, height, color, knocked=False):
    x = _pitch_x(frac)
    y = _pitch_y(frac)
    hw = (_TOP_HW + (_BOT_HW - _TOP_HW) * frac) * 0.22
    for i, off in enumerate((-hw, 0, hw)):
        lean = (off * 0.7) if knocked else 0
        d.line([(x + off, y), (x + off + lean, y - height)], fill=color, width=3)
    if not knocked:
        d.line([(x - hw - 2, y - height), (x + hw + 2, y - height)], fill=color, width=2)


def _banner(img, d, text, color, sub=None):
    """Full-width outcome slab across the bottom third."""
    T.vgrad(img, [0, H - 74, W, H], T.mix(color, T.BG_DEEP, 0.45), T.BG_DEEP)
    d.rectangle([0, H - 74, W, H - 70], fill=color)
    f = T.font(38, "bold")
    T.ctext(d, W / 2, H - 66, text, f, T.INK)
    if sub:
        T.ctext(d, W / 2, H - 24, sub, T.font(16, "cond"), T.INK_SOFT)


def _ball(d, x, y, r, color=(242, 242, 246), trail=None):
    # Trail blends toward the ball colour, never toward the page background - the
    # flight crosses the pale pitch, where a background-mixed trail reads as dirt.
    if trail:
        for i, (tx, ty, tr) in enumerate(trail):
            fade = (i + 1) / (len(trail) + 1)
            rr = tr * 0.55 * fade
            d.ellipse([tx - rr, ty - rr, tx + rr, ty + rr],
                      fill=T.mix((168, 176, 192), color, fade))
    d.ellipse([x - r, y - r, x + r, y + r], fill=color)


def _figure(d, x, y, h, color, stance="bat"):
    """Blocky player silhouette - enough to sell the scene at 480x270."""
    hw = h * 0.16
    d.ellipse([x - hw, y - h, x + hw, y - h + hw * 2], fill=color)          # head
    d.polygon([(x - hw, y - h + hw * 1.8), (x + hw, y - h + hw * 1.8),
               (x + hw * 1.2, y - h * 0.38), (x - hw * 1.2, y - h * 0.38)], fill=color)
    d.line([(x - hw * 0.7, y - h * 0.38), (x - hw * 1.1, y)], fill=color, width=max(2, int(h * 0.07)))
    d.line([(x + hw * 0.7, y - h * 0.38), (x + hw * 1.1, y)], fill=color, width=max(2, int(h * 0.07)))
    if stance == "bat":
        # bat angled down toward the crease
        d.line([(x + hw, y - h * 0.62), (x + hw * 2.4, y - h * 0.05)],
               fill=(226, 206, 168), width=max(3, int(h * 0.09)))
    else:
        # bowling arm up in the delivery stride
        d.line([(x - hw, y - h * 0.62), (x - hw * 1.6, y - h * 1.12)],
               fill=color, width=max(2, int(h * 0.07)))


def _flight(g, steps):
    """Sample the delivery's flight: [(x, y, radius)] from release to the batter."""
    pts = []
    for i in range(steps):
        t = i / max(1, steps - 1)
        frac = t
        # a small hop after the pitch point so the bounce reads
        lift = 0.0
        if frac > g["pitch_frac"]:
            after = (frac - g["pitch_frac"]) / max(0.05, 1 - g["pitch_frac"])
            lift = g["bounce"] * 26 * (1 - abs(2 * after - 1))
        x = _pitch_x(frac, g["line"] * frac)
        y = _pitch_y(frac) - lift
        pts.append((x, y, 3 + 5 * frac))
    return pts


def _save_gif(frames, duration=FRAME_MS):
    """Quantise and pack. Adaptive palette per clip keeps colour banding low while
    holding a wicket replay near 200 KB."""
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=96) for f in frames]
    buf = io.BytesIO()
    pal[0].save(buf, format="GIF", save_all=True, append_images=pal[1:],
                duration=duration, loop=0, optimize=True, disposal=2)
    buf.seek(0)
    return buf


def build_replay_gif(rec, state=None):
    """Short replay of one delivery: run-in, flight, bounce, outcome."""
    g = BF.geometry(rec)
    dismissal = rec.get("dismissal")
    runs = rec.get("runs_off_bat", 0)

    if dismissal:
        color, label = T.OUTCOME_COLOR["wicket"], "OUT"
        sub = rec.get("dismissal_desc") or dismissal
    elif runs == 6:
        color, label, sub = T.OUTCOME_COLOR["six"], "SIX", f"{rec.get('striker','')} goes big"
    elif runs == 4:
        color, label, sub = T.OUTCOME_COLOR["four"], "FOUR", f"{rec.get('striker','')} finds the rope"
    else:
        color, label = T.OUTCOME_COLOR.get("dot" if not runs else "single"), f"{runs}"
        sub = rec.get("outcome_text") or ""

    flight = _flight(g, 9)
    frames = []

    # flight
    for i in range(len(flight)):
        img, d = _scene(state)
        x, y, r = flight[i]
        _ball(d, x, y, r, trail=flight[max(0, i - 3):i])
        _caption(d, rec)
        frames.append(img)

    # outcome
    for i in range(7):
        img, d = _scene(state, tint=color if dismissal else None)
        for px, py, pr in flight[-3:]:
            _ball(d, px, py, pr * 0.7)
        if dismissal in ("Bowled", "Hit Wicket"):
            _stumps(d, 1.0, 30, (250, 120, 120), knocked=True)
        elif g["angle"] is not None:
            # ball leaving the bat toward its shot angle
            dx, dy = BF.polar(g["angle"], 30 + i * (16 + 10 * g["carry"]))
            bx, by = _pitch_x(1.0), _pitch_y(1.0) - 18
            _ball(d, bx + dx, by + dy, max(2.5, 7 - i * 0.5), color)
        if i >= 2:
            _banner(img, d, label, color, sub)
        frames.append(img)

    return _save_gif(frames)


def _caption(d, rec):
    f = T.font(15, "cond")
    txt = f"{rec.get('bowler','')}  ·  {rec.get('delivery','')}"
    d.text((14, 12), txt, font=f, fill=T.INK_SOFT)
    ov = f"{rec.get('over', 0)}.{rec.get('ball', 1)}"
    T.rtext(d, W - 14, 12, ov, f, T.INK_SOFT)


def build_drs_gif(rec, verdict):
    """Ball-tracking sequence: replay, then pitching / impact / wickets stages.

    `verdict` is the dict from career/drs.py: it owns the decision, this only
    animates it, so the graphic can never disagree with the result applied.
    """
    g = BF.geometry(rec)
    z = verdict.get("zones") or BF.wicket_zone(rec)
    flight = _flight(g, 8)
    frames = []

    for i in range(len(flight)):
        img, d = _scene()
        x, y, r = flight[i]
        _ball(d, x, y, r, color=(255, 232, 120), trail=flight[max(0, i - 3):i])
        _drs_header(d, rec)
        frames.append(img)

    stages = [
        ("PITCHING", z["pitching"], verdict.get("pitching_call", "IN LINE")),
        ("IMPACT", z["impact"], verdict.get("impact_call", "IN LINE")),
        ("WICKETS", z["hitting"], verdict.get("hitting_call", "HITTING")),
    ]
    for name, conf, call in stages:
        good = call in ("IN LINE", "HITTING")
        col = T.GOOD if good else (T.WARN if "UMPIRE" in call else T.BAD)
        for f_i in range(3):
            img, d = _scene()
            for px, py, pr in flight[-3:]:
                _ball(d, px, py, pr * 0.8, color=(255, 232, 120))
            _zone_box(d, col, conf, reveal=(f_i + 1) / 3)
            _drs_header(d, rec)
            _stage_label(d, name, call, col, show=f_i >= 1)
            frames.append(img)

    final = verdict.get("decision", "OUT")
    col = T.BAD if final == "OUT" else (T.WARN if final == "UMPIRE'S CALL" else T.GOOD)
    for _ in range(4):
        img, d = _scene(tint=col)
        _drs_header(d, rec)
        _banner(img, d, final, col, verdict.get("summary", ""))
        frames.append(img)

    return _save_gif(frames, duration=110)


def _drs_header(d, rec):
    f = T.font(15, "cond")
    d.text((14, 12), "DRS REVIEW", font=f, fill=T.ACCENT)
    T.rtext(d, W - 14, 12, f"{rec.get('bowler','')} to {rec.get('striker','')}", f, T.INK_SOFT)


def _zone_box(d, color, conf, reveal=1.0):
    """Stump-zone rectangle at the batter's end, filled in proportion to how much
    of the ball is inside it."""
    x, y = _pitch_x(1.0), _pitch_y(1.0)
    hw = _BOT_HW * 0.24
    top = y - 34
    d.rectangle([x - hw, top, x + hw, y], outline=T.INK_SOFT, width=2)
    fill_h = (y - top) * max(0.0, min(1.0, conf)) * reveal
    if fill_h > 1:
        d.rectangle([x - hw + 2, y - fill_h, x + hw - 2, y - 2], fill=T.mix(color, T.BG, 0.45))


def _stage_label(d, name, call, color, show=True):
    if not show:
        return
    f = T.font(20, "cond")
    d.text((14, H - 58), name, font=f, fill=T.INK_SOFT)
    d.text((14, H - 34), call, font=T.font(24, "bold"), fill=color)


def build_milestone_gif(kind, player, value):
    """Fifty / hundred / five-for celebration slab."""
    label = {"fifty": "FIFTY", "hundred": "HUNDRED", "five_for": "FIVE-FOR"}.get(kind, kind.upper())
    color = T.ACCENT if kind == "fifty" else T.OUTCOME_COLOR["six"]
    frames = []
    for i in range(14):
        img, d = _scene(tint=color if i > 3 else None)
        grow = min(1.0, (i + 1) / 8)
        f = T.font(int(20 + 34 * grow), "bold")
        T.ctext(d, W / 2, H / 2 - f.size * 0.7, label, f, color)
        if i >= 5:
            T.ctext(d, W / 2, H / 2 + 18, f"{player}  {value}", T.font(22, "cond"), T.INK)
        frames.append(img)
    return _save_gif(frames, duration=90)

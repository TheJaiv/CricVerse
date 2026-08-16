"""
Render every Career Mode graphic offline into test_previews/.

No Discord, no Mongo, no bot import - the renderers only ever see the plain state
dict from career/snapshot.py, so this runs anywhere Pillow does. Use it to eyeball
the GUI while iterating instead of starting the bot.

Run:  python3 tools/career_render_preview.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from career.ui import theme as T
from career.ui import broadcast as BC

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_previews")


def over(labels):
    tones = {"W": "wicket", "WD": "wide", "NB": "noball", "4": "four", "6": "six",
             "0": "dot", "1": "single", "2": "two", "3": "three"}
    return [{"label": l, "tone": tones.get(l, "single"), "legal": l not in ("WD", "NB")}
            for l in labels]


def state_innings1():
    return {
        "kind": "club", "format_overs": 20, "innings_num": 1,
        "pitch": "Hard", "weather": "Clear", "free_hit": False,
        "batting": {"name": "Cobras XI", "color": "#2E6BE6", "runs": 148, "wickets": 3,
                    "balls": 88, "overs": "14.4", "extras": 7},
        "bowling": {"name": "Titans XI", "color": "#E2603A"},
        "max_wickets": 10, "crr": 10.09, "partnership": 62, "balls_left": 32,
        "batters": [
            {"name": "J. Patel", "runs": 71, "balls": 41, "fours": 6, "sixes": 3, "sr": 173.2, "striker": True},
            {"name": "R. Mehra", "runs": 24, "balls": 19, "fours": 2, "sixes": 0, "sr": 126.3, "striker": False},
        ],
        "bowler": {"name": "A. Khan", "balls": 16, "overs": "2.4", "runs": 31, "wickets": 2, "econ": 11.62},
        "this_over": over(["1", "4", "0", "W", "6"]),
        "reviews": {"batting": 2, "bowling": 1},
        "proj": 201, "target": None, "need": None, "rrr": None,
        "first_innings": None, "objective": None,
        "toss": "Cobras XI won the toss · chose to BAT",
    }


def state_chase():
    s = state_innings1()
    s.update({
        "innings_num": 2, "kind": "club", "weather": "Overcast",
        "batting": {"name": "Titans XI", "color": "#E2603A", "runs": 162, "wickets": 6,
                    "balls": 102, "overs": "17.0", "extras": 11},
        "bowling": {"name": "Cobras XI", "color": "#2E6BE6"},
        "target": 202, "need": 40, "rrr": 13.33, "balls_left": 18, "proj": None,
        "crr": 9.53, "partnership": 18,
        "first_innings": {"name": "Cobras XI", "runs": 201, "wickets": 5, "overs": "20.0"},
        "this_over": over(["0", "1", "WD", "4", "1"]),
        "reviews": {"batting": 0, "bowling": 2},
        "free_hit": True,
        "objective": None,
    })
    return s


def state_scenario():
    s = state_innings1()
    s.update({
        "kind": "scenario", "format_overs": 5, "pitch": "Dusty", "weather": "Clear",
        "batting": {"name": "You", "color": "#8B5CF6", "runs": 38, "wickets": 1,
                    "balls": 21, "overs": "3.3", "extras": 2},
        "bowling": {"name": "Challenge XI", "color": "#37A06B"},
        "crr": 10.85, "partnership": 21, "balls_left": 9, "proj": 54,
        "this_over": over(["6", "0", "1"]),
        "reviews": {},
        "objective": {"kind": "chase", "text": "TARGET 55", "detail": "need 17 off 9", "done": False},
        "toss": None,
    })
    return s


def main():
    os.makedirs(OUT, exist_ok=True)
    if not T.fonts_are_vendored():
        print("WARNING: assets/fonts is missing - output will use fallback faces")

    jobs = [
        ("live_innings1.png", state_innings1()),
        ("live_chase.png", state_chase()),
        ("live_scenario.png", state_scenario()),
    ]
    for name, st in jobs:
        buf = BC.render_live_card(st)
        path = os.path.join(OUT, name)
        with open(path, "wb") as fh:
            fh.write(buf.getvalue())
        print(f"  {path}  ({len(buf.getvalue()) // 1024} KB)")


if __name__ == "__main__":
    main()

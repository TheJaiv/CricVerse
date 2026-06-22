<h1 align="center">🏏 CricVerse</h1>

<p align="center">
  <b>A full-featured cricket simulation bot for Discord</b><br>
  Ball-by-ball match engine · multi-format tournaments · draft mode · player career system
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white">
  <img alt="discord.py" src="https://img.shields.io/badge/discord.py-2.x-5865F2?logo=discord&logoColor=white">
  <img alt="MongoDB" src="https://img.shields.io/badge/MongoDB-Atlas-47A248?logo=mongodb&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-yellow">
</p>

---

## Overview

CricVerse simulates cricket at the **per-ball** level — every delivery is a contest between a batter and a bowler, shaped by pitch, weather, ball age, match situation, and player ratings. It runs **T20, ODI, and Test** matches (including **day-night pink-ball Tests**), full **tournaments**, a knowledge-based **draft mode**, and a persistent **player career** layer — all inside Discord, with broadcast-style scorecards rendered as images.

## ✨ Features

### 🎮 Match engine
- **Three formats** — T20 (20), ODI (50), and 5-day **Test** matches with sessions, declarations, follow-ons, and the new/old ball.
- **Day-night (pink-ball) Tests** — the ball swings and seams under lights; the twilight session is a genuine danger period for pace.
- **Interactive play** — pick your delivery and shot ball-by-ball, or instantly **simulate** the whole match.
- **Conditions matter** — 15+ pitch types, ~10 weather states, pitch wear/deterioration, ball-age swing & reverse, and a Super Over for ties.
- **Calibrated, not random** — outcome weights are Monte-Carlo-tuned with variance compressors so *skill* decides matches (no freak 250s or 30-all-out cascades between equal sides).

### 🏆 Tournaments
- **Round Robin**, **T20 World Cup** (groups → Super 8 → knockouts), and the bespoke **Akatsuki Cricket League (ACL)** — a 14-team league → IPL-style Top-6 playoffs → Super Cup.
- Auto-generated fixtures, live points tables & **NRR**, knockout brackets, per-player leaderboards, and PIL-rendered standings/scorecards.

### 🎲 Draft mode
- A **blind, knowledge-based draft** — ratings are hidden; each round poses a question (*"name a leg-spinner", "name a finisher keeper"*) and captains build a **balanced XI** from memory before the match is played out.
- 1v1 or vs **AI**, tiered player pools (Legends / Greats / Youngsters), and a wins leaderboard.

### 🧑‍💼 Player system
- A database of **1,200+ players** with batting/bowling ratings, roles, and playing-style archetypes (Aggressor / Anchor / Finisher / Standard).
- **Per-server rating overrides** — tweak ratings for one server without touching the global database.
- Career mode (WIP): create a player, earn coins, upgrade attributes, play club matches.

## 🛠️ Tech stack

| Area | Tech |
|------|------|
| Language | Python 3.11+ |
| Discord | `discord.py` (slash + prefix commands) |
| Persistence | MongoDB (`pymongo`) |
| Image rendering | `Pillow` (PIL) |
| Hosting / keep-alive | `Flask` |

## 🚀 Getting started

```bash
# 1. Clone
git clone https://github.com/JaivTheCoder/CricVerse.git
cd CricVerse

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env        # then fill in DISCORD_TOKEN and MONGO_URI

# 4. Run
python bot.py
```

Set the variables from [`.env.example`](.env.example) — at minimum `DISCORD_TOKEN` and `MONGO_URI`. (On a host like Render, set them as environment variables instead of a file.)

## 💬 Command sampler

> Commands work as both slash (`/command`) and the `cv` prefix. `cvt` = tournament, `cvd` = draft.

| Command | What it does |
|---------|--------------|
| `cv match [@opponent]` | Start an interactive match (vs a user or the AI) |
| `cv simulatematch` | Instantly simulate a full match |
| `cvd [@opponent]` | Start a blind draft → match · `cvd lb` for the leaderboard |
| `cvt create` / `cvt start` | Create & launch a tournament |
| `cvt fixtures` / `cvt standings` / `cvt bracket` | Track an ongoing tournament |
| `cv searchplayer <name>` | Look up a player |

## 📁 Project structure

```
bot.py                  # Discord client, commands, interactive flows, image rendering
t20_simulation.py       # T20 ball-by-ball engine
odi_simulation.py       # ODI ball-by-ball engine
test_simulation.py      # Test-match engine (sessions, day-night, declarations)
tournament_manager.py   # Tournament formats, standings, brackets, ACL
draft_mode.py           # Blind draft questions, validation, AI picks
career_manager.py       # Career/club system (WIP) + career_match.py / career_ui.py
subscription_manager.py # MongoDB persistence, tiers, server overrides, draft stats
test_image.py           # Test-match scorecard/summary image rendering
prefix_handler.py       # Prefix-command helpers
keep_alive.py           # Tiny Flask server for uptime pings
players_master.csv      # Player database (name, bat, bowl, role, archetype)
assets/                 # PNG templates for the rendered scorecards/standings/banners
tools/                  # Standalone scripts: sim_harness.py (calibration), restore_tournament.py
archive/                # Superseded experiments (old engine versions, one-off calibration scripts)
```

## 🧪 Engine calibration

The match engines are tuned against a standalone Monte-Carlo harness (`sim_harness.py`) to hit realistic targets — neutral T20 par ~165–175, sensible wicket distributions, decisive separation between strong and weak sides, and format-appropriate Test draw rates — rather than relying on raw randomness.

## 📜 License

[MIT](LICENSE) © Jaiv Patel

---

<p align="center"><i>Built with Python, a lot of cricket, and a healthy obsession with realistic match simulation.</i></p>

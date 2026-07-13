# Setting up your own CricVerse

Everything you need to go from a fresh clone to a running bot in your own
server: the Discord application, the database, configuration, and hosting.

## Prerequisites

- Python 3.11 or newer (`python3 --version`)
- A Discord account and a server where you have Manage Server permission
- A free MongoDB Atlas account (or any MongoDB you can reach)

## 1. Clone and install

```bash
git clone https://github.com/TheJaiv/CricVerse.git
cd CricVerse
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
```

## 2. Create the Discord application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
   and click **New Application**. Name it whatever you like.
2. Open the **Bot** tab and click **Reset Token**. Copy the token - this is
   your `DISCORD_TOKEN`. Treat it like a password; anyone with it controls
   your bot.
3. Still on the Bot tab, scroll to **Privileged Gateway Intents** and enable:
   - **Message Content Intent** (required - the `cv` prefix commands read
     message text)
   - **Server Members Intent** (used for career tier roles and lobbies)
4. Invite the bot to your server: open **OAuth2 > URL Generator**, tick the
   `bot` and `applications.commands` scopes, then under Bot Permissions tick:
   - Send Messages, Embed Links, Attach Files, Read Message History,
     Add Reactions, Use External Emojis, Manage Roles (for career tier roles)

   Open the generated URL in your browser and add the bot to your server.

## 3. Create the database (MongoDB Atlas, free tier)

1. Sign up at [mongodb.com/cloud/atlas](https://www.mongodb.com/cloud/atlas)
   and create a free **M0** cluster (any region).
2. Under **Database Access**, add a database user with a username and password
   (role: *Read and write to any database*).
3. Under **Network Access**, add an IP access entry. For a bot hosted on a
   platform whose IPs change, use `0.0.0.0/0` (allow from anywhere) - the
   user/password still protects the cluster.
4. Click **Connect > Drivers** on your cluster and copy the connection string.
   It looks like:

   ```
   mongodb+srv://<user>:<password>@cluster0.abcde.mongodb.net/?retryWrites=true&w=majority
   ```

   Fill in your user and password - this is your `MONGO_URI`.

You don't need to create any collections by hand. The bot creates its database
(`cricket_bot` by default) and collections on first write.

## 4. Configure the environment

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Required | What it is |
|----------|----------|------------|
| `DISCORD_TOKEN` | yes | Bot token from step 2 |
| `MONGO_URI` | yes | Connection string from step 3 |
| `ADMIN_DISCORD_ID` | yes | Your Discord user ID - enables owner-only admin commands. (In Discord: Settings > Advanced > Developer Mode, then right-click your name > Copy User ID.) |
| `MONGO_DB` | no | Database name, default `cricket_bot` |
| `LOG_CHANNEL_ID` | no | Channel ID for DB/update log messages |
| `CAREER_MODE` | no | `1` to enable career mode (default on) |
| `PORT` | no | Keep-alive web server port, default 8080 |

## 5. Run it

```bash
python bot.py
```

You should see the login message and "Slash commands synchronized globally"
(the first global sync can take a few minutes to show up in Discord).

## 6. Load the player database

The repo ships with `data/players_master.csv` (1,200+ rated players). Load it
into your MongoDB from Discord with the owner-only command:

```
cv sync_csv
```

It shows the players about to be added and asks you to confirm. After that,
`cv searchplayer kohli` should work, and matches/drafts/tournaments have a
full player pool.

## 7. Hosting (optional)

Any host that runs a Python process works. On [Render](https://render.com)
(or Railway/Fly/a VPS, same idea):

1. Create a **Web Service** from your GitHub fork.
2. Build command: `pip install -r requirements.txt`
3. Start command: `python bot.py`
4. Add the environment variables from step 4 in the dashboard - don't upload
   your `.env`.

The bot starts a tiny Flask server (`core/keep_alive.py`) on `PORT`, so
free-tier hosts that require an open HTTP port are happy, and you can point an
uptime pinger (e.g. UptimeRobot) at it to keep the instance awake.

## Troubleshooting

- **Bot is online but slash commands don't appear** - global sync can take up
  to an hour the very first time; the `cv` prefix commands work immediately.
- **`ServerSelectionTimeoutError` from pymongo** - your `MONGO_URI` is wrong
  or Network Access in Atlas doesn't allow your IP.
- **Career/admin commands say you're not allowed** - set `ADMIN_DISCORD_ID`
  to your own user ID and restart.
- **Images fail to render** - make sure the `assets/` folder is present next
  to `bot.py`; the scorecards are drawn onto those templates.

## Running the offline test suites

None of these need Discord or a database:

```bash
python3 tools/career_flow_test.py
python3 tools/ipl_flow_test.py
python3 tools/dsl_flow_test.py
python3 tools/rating_league_test.py
python3 tools/sim_harness.py        # engine calibration report (slow)
```

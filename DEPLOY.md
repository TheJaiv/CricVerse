# Deploying CricVerse — zero to running

Complete path from nothing to a bot running 24/7 on your own server. Written for
macOS on your side and Ubuntu 24.04 on the server.

Work through the phases in order. Each one ends with something you can check.

---

## Why move off a PaaS free tier

A Discord bot is a long-lived **outbound** connection. It receives no HTTP traffic, so
it is a *worker*, not a web service. Free web-service tiers kill anything with no
inbound requests — which is why `core/keep_alive.py` exists, faking traffic with a Flask
server and an uptime pinger so the platform doesn't reap the process.

That costs three things:

1. **Cold boots.** Every spin-down/wake burns a gateway IDENTIFY. The cap is 1000 per
   24h per bot; a restart loop exhausts it and you get 429s at login.
2. **A shared outbound IP.** Cloudflare `1015` bans are per-IP. On shared hosting you
   inherit whatever neighbouring services do to Discord's API. No code can prevent
   this — only your own IP can. *This is what took your bot down.*
3. **512 MB RAM**, while the bot renders scorecards with Pillow.

On your own server the bot runs as a plain process under systemd. `bot.py` starts the
keep-alive server **only when `PORT` is set** — PaaS sets it, a VPS doesn't — so the
same code works on both with no edits.

---

## Phase 0 — Before you touch a server

### 0.1 Rotate the Discord token

Your current token has been exposed. Do this first:

Discord Developer Portal → your app → **Bot** → **Reset Token**. Copy the new one
somewhere temporary; you'll paste it into the server in Phase 4 and nowhere else.

### 0.2 Get the GitHub Student Developer Pack

<https://education.github.com/pack> → **Sign up for Student Developer Pack**.

Verify with your college email address. Approval is usually minutes, occasionally a
day or two.

### 0.3 Activate Azure for Students

From the pack, find **Microsoft Azure** → activate **Azure for Students**.

- **$100 credit for 12 months**, plus 12 months of free-tier services
- **No credit card required** — this is the whole reason we're using Azure
- Verification is via the same college email

> If your college email is not recognised, skip to **Appendix A** for the
> no-cloud fallback.

---

## Phase 1 — Create the server

### 1.1 Make an SSH key (on your Mac)

An SSH key is a keypair: a private half that stays on your Mac and a public half you
give the server. The server then lets you in without a password — safer, and it can't
be brute-forced.

```bash
ssh-keygen -t ed25519 -C "cricverse" -f ~/.ssh/cricverse
```

Press Enter twice to skip the passphrase (or set one — you'll be asked for it on each
connect). This creates:

- `~/.ssh/cricverse` — **private**, never share or upload this
- `~/.ssh/cricverse.pub` — public, this goes to Azure

Print the public half; you'll paste it in the portal:

```bash
cat ~/.ssh/cricverse.pub
```

### 1.2 Create the VM

Azure Portal → **Virtual machines** → **Create** → **Azure virtual machine**.

| Field | Value |
|---|---|
| Resource group | Create new → `cricverse-rg` |
| VM name | `cricverse` |
| Region | Closest to you (e.g. Central India) |
| Image | **Ubuntu Server 24.04 LTS — x64 Gen2** |
| Size | **B1s** (1 vCPU, 1 GB) — look for the free-services tag |
| Authentication type | **SSH public key** |
| Username | `jaiv` |
| SSH public key source | **Use existing public key** → paste `cricverse.pub` |
| Public inbound ports | **Allow selected** → **SSH (22)** only |

Everything else can stay default. **Review + create** → **Create**.

Size note: B1s is the free-tier-eligible size — confirm the portal shows it as free
before creating. It's 1 GB RAM, double what Render gave you, and Phase 3.4 adds swap so
Pillow rendering can't OOM-kill the bot.

Only port 22 is open. The bot makes **outbound** connections only; nothing needs to
reach it from the internet.

### 1.3 Note the public IP

The VM's overview page shows a **Public IP address**. Write it down — you need it twice
(SSH, and the Atlas allowlist).

---

## Phase 2 — Connect

```bash
ssh -i ~/.ssh/cricverse jaiv@YOUR_VM_IP
```

First connect asks to trust the host fingerprint — type `yes`.

To avoid retyping the key path, add this to `~/.ssh/config` on your Mac:

```
Host cricverse
    HostName YOUR_VM_IP
    User jaiv
    IdentityFile ~/.ssh/cricverse
```

Then it's just `ssh cricverse`.

**Everything from here runs on the server**, unless it says otherwise.

---

## Phase 3 — Prepare the machine

### 3.1 Update and install packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip git
```

### 3.2 Get the code

```bash
sudo mkdir -p /opt/cricverse && sudo chown $USER:$USER /opt/cricverse
git clone https://github.com/TheJaiv/CricVerse.git /opt/cricverse
cd /opt/cricverse
```

Private repo? Either make it public, or use a deploy key / personal access token.

### 3.3 Virtual environment

A venv keeps this project's packages separate from the system Python, so an `apt`
upgrade can't break your dependencies.

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Pillow ships prebuilt wheels for x86 and ARM, so this is quick either way.

### 3.4 Add swap

1 GB is enough for normal running but leaves little headroom when Pillow renders a
scorecard. Without swap, a spike gets the process **OOM-killed** — which looks exactly
like a mystery crash. 2 GB of swap makes that essentially impossible.

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Check it:

```bash
free -h
```

The Swap row should show 2.0Gi. The `/etc/fstab` line makes it survive reboots.

### 3.5 Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw --force enable
sudo ufw status
```

Inbound SSH only. Outbound (Discord, MongoDB) is unrestricted by default.

---

## Phase 4 — Configuration

### 4.1 Secrets

systemd reads these from a root-owned file, so they never touch the repo:

```bash
sudo tee /etc/cricverse.env >/dev/null <<'EOF'
DISCORD_TOKEN=paste-your-NEW-token-here
MONGO_URI=paste-your-atlas-uri-here
MONGO_DB=cricket_bot
EOF
sudo chmod 600 /etc/cricverse.env
```

`chmod 600` means only root can read it.

**Do not set `PORT`.** Its absence is what tells `bot.py` to skip the keep-alive web
server. Setting it would start a pointless Flask server.

Check for typos without printing the token:

```bash
sudo grep -c '=' /etc/cricverse.env    # should print 3
```

### 4.2 Allow the server through MongoDB Atlas

Atlas rejects unknown IPs. Get the server's public IP:

```bash
curl -s ifconfig.me
```

Then: Atlas → **Network Access** → **Add IP Address** → paste it → Confirm.

Skip this and you get `ServerSelectionTimeoutError` on first boot.

---

## Phase 5 — Install the service

```bash
sudo cp /opt/cricverse/deploy/cricverse.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cricverse
```

`enable` starts it on every boot; `--now` also starts it immediately.

### Why the restart policy matters

`RestartSec=60` plus the `StartLimit*` cap in the unit are not boilerplate. If the bot
crash-loops, an instant-restart supervisor hammers Discord's login endpoint and turns a
recoverable problem into an IDENTIFY ban or a Cloudflare 1015. The unit allows at most
**5 starts per hour, 60s apart**, then stops and waits for you. That's the protection
Render's free tier never gave you.

---

## Phase 6 — Verify

### 6.1 Is it running

```bash
systemctl status cricverse
```

Want: `Active: active (running)`.

### 6.2 Watch the logs

```bash
journalctl -u cricverse -f
```

A healthy first boot shows roughly:

```
No PORT set - running as a plain worker process (no keep-alive server).
Loaded N players & subscriptions from MongoDB!
global_stats: loaded N players from MongoDB
Slash commands synchronized globally (first run).
Logged in successfully as CricVerse
Global stats ready — N players.
Memory Cache Loaded and Ready.
```

`Ctrl-C` stops following (it does not stop the bot).

On **later** restarts you should see `Slash commands unchanged - skipping global sync.`
That's the fix that stops hammering Discord's most rate-limited route on every boot.

### 6.3 Check from Discord

```
cv syncstats status
```

Should report your real player count. If MongoDB is empty because you never seeded it
before leaving Render, restore from a DM'd backup:

```
cv importstats      (attach global_stats.json)
cv syncstats
```

Then try any normal command to confirm the bot responds.

---

## Phase 7 — Day-to-day

### Deploy a change

```bash
cd /opt/cricverse
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart cricverse
```

### Common commands

```bash
systemctl status cricverse            # is it up
journalctl -u cricverse -f            # live logs
journalctl -u cricverse --since "1 hour ago"
sudo systemctl restart cricverse
sudo systemctl stop cricverse
free -h                               # memory + swap
```

### Slash commands look stale

```
cv sync
```

Forces a global sync. Normally automatic and only when the command list actually
changed.

---

## Keeping it up long-term

The VM itself never idles out — that's the point of moving. `systemctl enable` restarts
the bot after host maintenance or reboots, and discord.py reconnects through transient
gateway drops on its own. Nothing needs pinging.

The thing that actually ends your uptime is the **credit clock**.

### What consumes credit

- **B1s VM** — free tier covers 750 hours/month for 12 months. A full month of 24/7 is
  744 hours, so exactly one VM fits. A second would be billed.
- **Public IP** — usually *not* free. A static Standard IP is roughly $3–4/month and
  draws down the $100. This is the line item that quietly eats the credit.
- **Outbound bandwidth** — 100 GB/month free; a Discord bot uses a tiny fraction.

Confirm the actual prices in the portal when you create the VM. These offers change,
and the cost summary at the Review step is authoritative.

### Set a budget alert (do this on day one)

Azure Portal → **Cost Management + Billing** → **Budgets** → **Add**.

- Scope: your subscription
- Amount: `100`
- Alerts at **50%**, **80%** and **95%** → your email

Without this, the first sign of trouble is the bot going offline.

### When the credit runs out

The subscription is **disabled** and the VM stops. You gave no card, so there is no
surprise bill — but the bot goes down. Options at that point:

1. **Renew Azure for Students** — available annually while you're still a student.
   Re-verify with your college email.
2. **Move to the hardware fallback** (Appendix A) — free and permanent.
3. **Pay for a small VPS** — Hetzner CX22 is ~€4/month for 4 GB, and by then you'll
   have done this whole process once and it'll take twenty minutes.

Because the state lives in MongoDB Atlas, not on the server's disk, moving hosts is
just Phases 3–6 again against a new machine. Nothing is trapped on the VM.

---

## Troubleshooting

**`Permission denied (publickey)` on SSH** — wrong key path or username. Use
`ssh -i ~/.ssh/cricverse jaiv@IP` and check the username matches the VM's.

**`ServerSelectionTimeoutError` from pymongo** — Phase 4.2 not done, or `MONGO_URI` is
wrong. Atlas must allowlist this server's IP.

**Service won't start** — read the actual error:
```bash
journalctl -u cricverse -n 50 --no-pager
```

**`start request repeated too quickly`** — the crash-loop cap did its job. Fix the
underlying error, then:
```bash
sudo systemctl reset-failed cricverse && sudo systemctl start cricverse
```

**429 at login again** — diagnose **on the server**, since a Cloudflare ban belongs to
the IP making the request:
```bash
cd /opt/cricverse && .venv/bin/python tools/gateway_check.py
```
It reports the IDENTIFY budget (restart-loop evidence) and whether this host's IP is
blocked. On a dedicated IP this should never recur — if it does, something here is
generating far too many requests.

**Bot online but silent** — check Message Content Intent is still enabled in the
Developer Portal, and that the bot has permissions in the channel.

---

## Appendix A — No cloud account

If the student verification doesn't work, run it on hardware you already have: an old
laptop or a Raspberry Pi with Ubuntu Server.

Skip Phases 0.2–2, then follow Phases 3–7 unchanged. A residential IP is essentially
never Cloudflare-banned, so this actually solves the original problem well.

Two caveats: the machine must stay powered on and awake (`sudo systemctl mask
sleep.target suspend.target` on a laptop, and set it to not sleep when the lid closes),
and if your home IP is dynamic you'll re-add it to Atlas when it changes — or allowlist
`0.0.0.0/0` and rely on the database password, which is weaker but workable.

---

## Appendix B — What changed for this migration

- `bot.py` starts `keep_alive()` **only when `PORT` is set**, so the same code is
  correct on a PaaS and on a VPS.
- `deploy/cricverse.service` — the systemd unit, with the crash-loop cap in `[Unit]`
  (systemd moved `StartLimit*` there in v230 and silently ignores it in `[Service]`).
- `tools/gateway_check.py` — tells a restart loop apart from an IP ban.
- Global stats live in MongoDB, so nothing on disk needs migrating.

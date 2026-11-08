# Hosting aikeytake-zog on a VPS (IONOS or any Ubuntu server)

This guide moves your library to a rented Linux server so you can reach it from any of your
machines. It assumes **Ubuntu 22.04 / 24.04** and a root or sudo login by SSH. IONOS VPS images
are normally Ubuntu; the steps are the same on any provider. Replace `VPS_IP` with your server's
IP address.

For running on your own machine first, see [running.md](running.md). Reference details
(backups, Docker, HTTPS) are in [aikeytake-zog.md](aikeytake-zog.md).

**Contents**
1. [Which way is more secure?](#1-which-way-is-more-secure)
2. [Prepare the VPS](#2-prepare-the-vps)
3. [Install the app](#3-install-the-app)
4. [Copy your library to the VPS](#4-copy-your-library-to-the-vps)
5. [Run it as a service](#5-run-it-as-a-service)
6. [Firewall](#6-firewall)
7. [Option A — Tailscale (private)](#7-option-a--tailscale-private)
8. [Option B — your own domain with HTTPS (public)](#8-option-b--your-own-domain-with-https-public)
9. [Point the extension and tools at the VPS](#9-point-the-extension-and-tools-at-the-vps)
10. [Hardening the VPS (both options)](#10-hardening-the-vps-both-options)
11. [Updating and backups](#11-updating-and-backups)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Which way is more secure?

**Option A, Tailscale, is the more secure one.** Prefer it unless you need to open the library from
a computer where you cannot install Tailscale.

| | **A. Tailscale (private)** | **B. Domain + HTTPS (public)** |
|---|---|---|
| Who can reach the login page | Only your own devices that are signed in to your Tailscale account | Anyone on the internet |
| Public ports open on the VPS | None except SSH | 80 and 443 (plus SSH) |
| What protects the data | Encrypted private network **and** your password **and** tokens | **Only** your password and tokens |
| Brute-force password guessing | Not possible from outside your network | Possible: bots find public sites within hours. The server allows only 5 wrong passwords per 5 minutes per IP address, but behind Caddy every visitor appears as the same address, so that limit is shared and weak |
| Traffic encryption | WireGuard (built into Tailscale), even over `http://` | HTTPS certificate from Caddy |
| Needs a domain name | No | Yes |
| Works on a PC where you cannot install software | No (needs the Tailscale app) | Yes, any browser |
| Setup effort | Small | A little more (domain, DNS, Caddy) |

Why the public option is riskier: the whole library sits behind **one password**. A public login
page is found and probed by automated scanners. The server does limit logins (5 wrong passwords per
5 minutes per IP address, kept in memory and reset when the server restarts), but behind a reverse
proxy such as Caddy the server sees every visitor as the proxy's own address. One bot can then lock
you out, and the limit does nothing per attacker. If you choose Option B anyway, use a long random
password (20+ characters from a password manager), keep the server updated, and ask for the limit
to use the real client address (`X-Forwarded-For`) first. The API tokens also work from anywhere in
Option B, so revoke any you no longer use.

A good middle path is Option A for daily use, and adding Option B later only if you really need it.

## 2. Prepare the VPS

```bash
ssh root@VPS_IP
apt update && apt -y upgrade
apt -y install git rsync sqlite3 ufw curl
adduser --disabled-password --gecos "" zog
usermod -aG sudo zog
```

Give the `zog` user your SSH key so you can log in as it (run this **on your own computer**):

```bash
ssh-copy-id zog@VPS_IP        # if this asks for a password, first run: ssh root@VPS_IP 'passwd zog'
```

Then work as `zog` from here on:

```bash
ssh zog@VPS_IP
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
df -h /                      # your data is about 4-5 GB; keep at least 10 GB free
```

## 3. Install the app

```bash
git clone https://github.com/vankhoa21991/zotero_rag.git
cd zotero_rag
uv sync --extra server --extra pdf
```

This creates `/home/zog/zotero_rag/.venv/bin/aikeytake-zog`, which the service uses below.

## 4. Copy your library to the VPS

The database and the `files/` folder belong together. Do this from **your own computer**.

1. In the web app on your computer, open **Settings** and **revoke every token**. Delete the file
   `~/.local/share/aikeytake-zog/import-token.txt` if it exists (it holds an old token).
2. Stop your local server (`Ctrl+C`). Copying a running database can give an inconsistent copy.
3. Copy the folder (resumable if the connection drops; run it again to continue):

```bash
rsync -avP ~/.local/share/aikeytake-zog/ zog@VPS_IP:/home/zog/aikeytake-zog-data/
```

Your existing web password is stored in the database, so it works on the VPS unchanged. If you
would rather start empty, skip this step and run this on the VPS later:

```bash
AIKEYTAKE_ZOG_DATA_DIR=/home/zog/aikeytake-zog-data uv run --extra server --extra pdf aikeytake-zog set-password
```

## 5. Run it as a service

On the VPS, create `/etc/systemd/system/aikeytake-zog.service` (`sudo nano` it):

```ini
[Unit]
Description=aikeytake-zog
After=network.target

[Service]
User=zog
WorkingDirectory=/home/zog/zotero_rag
Environment=AIKEYTAKE_ZOG_DATA_DIR=/home/zog/aikeytake-zog-data
ExecStart=/home/zog/zotero_rag/.venv/bin/aikeytake-zog serve --host 127.0.0.1 --port 8321
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Start it and check:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now aikeytake-zog
systemctl status aikeytake-zog              # "active (running)"
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8321/login   # prints 200
journalctl -u aikeytake-zog -f              # live log (Ctrl+C to leave)
```

At this point the server listens only on the VPS itself (`127.0.0.1`). Options A and B below
decide how you reach it.

## 6. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw enable            # default policy is: deny all incoming, allow outgoing
sudo ufw status
```

Also check the firewall of your hosting provider. In the IONOS Cloud Panel a firewall policy may
be attached to the server (Network → Firewall Policies). Whatever it is, it must allow **SSH
(22)**, and for Option B also **80 and 443**. The exact menu names change; look for the firewall
section of your server.

## 7. Option A — Tailscale (private)

1. Install Tailscale on the VPS and sign in:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up           # prints a login URL: open it and approve this machine
   tailscale ip -4             # the VPS's Tailscale address, e.g. 100.x.y.z
   ```
2. Install Tailscale on each of your own computers and sign in to the **same account**.
3. Let the app listen on the Tailscale network and open only that interface:
   ```bash
   sudo sed -i 's/--host 127.0.0.1/--host 0.0.0.0/' /etc/systemd/system/aikeytake-zog.service
   sudo systemctl daemon-reload && sudo systemctl restart aikeytake-zog
   sudo ufw allow in on tailscale0 to any port 8321
   ```
   Port 8321 stays closed on the public network because `ufw` denies incoming traffic by default.
   Verify from a computer that is **not** on your Tailscale: `http://VPS_IP:8321` must **not**
   load.
4. Your server URL is **`http://<VPS tailscale name or IP>:8321`**, for example
   `http://100.101.102.103:8321`. Plain `http` is fine here because Tailscale encrypts the link.
   Do not set `AIKEYTAKE_ZOG_COOKIE_SECURE` for this option, or you cannot log in over `http`.

## 8. Option B — your own domain with HTTPS (public)

Read [section 1](#1-which-way-is-more-secure) first.

1. At your domain registrar, create an **A record**, e.g. `library.example.com` → `VPS_IP`. Wait a
   few minutes (`dig +short library.example.com` should print the IP).
2. Install Caddy; it fetches and renews the HTTPS certificate automatically:
   ```bash
   sudo apt -y install debian-keyring debian-archive-keyring apt-transport-https
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
   sudo apt update && sudo apt -y install caddy
   ```
3. Put this in `/etc/caddy/Caddyfile` (replace the domain):
   ```
   library.example.com {
       reverse_proxy 127.0.0.1:8321
   }
   ```
4. Turn on secure cookies, restart both, open the web ports:
   ```bash
   sudo sed -i '/^\[Service\]/a Environment=AIKEYTAKE_ZOG_COOKIE_SECURE=1' /etc/systemd/system/aikeytake-zog.service
   sudo systemctl daemon-reload && sudo systemctl restart aikeytake-zog
   sudo systemctl restart caddy
   sudo ufw allow 80,443/tcp
   ```
   Open **80 and 443** in the provider's firewall policy as well.
5. Your server URL is **`https://library.example.com`** (no port).

Caddy has no upload size limit by default. If you use nginx instead, add `client_max_body_size 0;`
or PDF uploads over 1 MB fail with HTTP 413.

## 9. Point the extension and tools at the VPS

1. Create a **new write token** on the VPS (your old ones were revoked in step 4). Either log in at
   your server URL and use **Settings → Create token**, or on the VPS:
   ```bash
   cd ~/zotero_rag
   AIKEYTAKE_ZOG_DATA_DIR=/home/zog/aikeytake-zog-data uv run --extra server --extra pdf aikeytake-zog create-token browser --scope write
   ```
   Give each computer its own token so you can revoke one without breaking the others.
2. In the extension **Options**, set **Server URL** to your URL from step 7 or 8 and paste the
   token. **Save**, then **Test connection**.
3. **Fix "date added" on your old items (once).** Zotero lives on your computer, so run the import
   there against the VPS:
   ```bash
   uv run --extra server --extra pdf aikeytake-zog import-zotero --server <your server URL>
   ```
   Paste a write token when it asks, then revoke that token afterwards.

## 10. Hardening the VPS (both options)

```bash
# keys only for SSH, no root login (make sure your key login works FIRST)
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/;s/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart ssh

# automatic security updates and blocking of repeated failed SSH logins
sudo apt -y install unattended-upgrades fail2ban
sudo dpkg-reconfigure -plow unattended-upgrades
```

Keep your SSH session open while you test a **second** SSH login, so a typo cannot lock you out.
Use a long random web password, and revoke tokens you no longer need under **Settings**.

## 11. Updating and backups

**Update the app** (on the VPS):

```bash
cd ~/zotero_rag && git pull && uv sync --extra server --extra pdf && sudo systemctl restart aikeytake-zog
```

Then press `Ctrl+Shift+R` in the browser. The database upgrades itself on start.

**Back up** from your own computer. The database snapshot and the `files/` folder are one pair:

```bash
mkdir -p ~/zog-backup/files
ssh zog@VPS_IP 'sqlite3 /home/zog/aikeytake-zog-data/aikeytake-zog.db ".backup /home/zog/db-backup.db"'
rsync -avP zog@VPS_IP:/home/zog/db-backup.db ~/zog-backup/aikeytake-zog.db
rsync -avP zog@VPS_IP:/home/zog/aikeytake-zog-data/files/ ~/zog-backup/files/
```

To restore, put `aikeytake-zog.db` and `files/` back into the data folder together. Your original
Zotero folder stays untouched by all of this; keep it as another copy.

## 12. Troubleshooting

| Problem | Fix |
|---|---|
| `systemctl status` shows `failed` | `journalctl -u aikeytake-zog -n 50`. Usually a wrong path in the service file or `uv sync` was not run. |
| Page does not load from your computer (Option A) | Is Tailscale on and signed in on both machines? Did you run the `ufw allow in on tailscale0 ...` line and use `--host 0.0.0.0`? Use `http://`, not `https://`. |
| Login page says "too many attempts" | 5 wrong passwords in 5 minutes block your IP. Wait 5 minutes, or run `sudo systemctl restart aikeytake-zog` to clear it at once. |
| Forgot the web password | On the VPS: `AIKEYTAKE_ZOG_DATA_DIR=/home/zog/aikeytake-zog-data ~/zotero_rag/.venv/bin/aikeytake-zog set-password` |
| Cannot log in (Option A) | You set `AIKEYTAKE_ZOG_COOKIE_SECURE=1` but use plain `http://`. Remove that line, reload, restart. |
| Cannot log in (Option B) | Use the `https://` address exactly. Cookie-secure sites do not log in over `http://`. |
| Caddy gets no certificate | The A record is wrong, ports 80/443 are closed (also in the provider firewall), or another service already uses them. `journalctl -u caddy -n 50`. |
| Upload fails with 413 (nginx) | Add `client_max_body_size 0;`. |
| `Permission denied` on the data folder | `sudo chown -R zog:zog /home/zog/aikeytake-zog-data`. |
| Extension says "Can't reach" | Check the URL in a normal browser tab first; the server, VPN, DNS and firewall must all work there. |
| Disk full | `df -h /`; PDFs and snapshots are in `files/`. Resize the VPS disk or clean old backups. |

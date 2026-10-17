# Running Benchtop for a group

There are three ways to share one Benchtop between several people. Start with the simplest one that
works for you; you can move up later (back up, then restore the backup on the new setup).

| | Who can reach it | Needs | Effort |
|---|---|---|---|
| **1. A lab Mac** (`./benchtop lab`) | People on the same Wi-Fi / network | A Mac that stays on | 2 minutes |
| **2. A lab Mac + Tailscale** | Group members anywhere | Free Tailscale accounts | 15 minutes |
| **3. A server with HTTPS** | Anyone you give an account, anywhere | A small Linux server or VM, a domain name | An afternoon |

Whatever you choose, everyone gets their **own account** (Users & audit → Add user, or single sign-on),
and you organise people into **groups** (Groups page). A notebook can then be open to everyone, to one
group, or private; group leads see all of their group's notebooks. Signatures only mean something if
accounts aren't shared.

---

## 1. A lab Mac on the local network

On the Mac that will hold the notebook (one that stays on, e.g. a lab desktop):

```bash
cd ~/benchtop
./benchtop lab
```

It prints an address such as `http://lab-imac.local:8000`. Labmates open that in their browser.
Leave the Terminal window open while people work. Backups work as usual; point
`BENCHTOP_BACKUP_DIR` at an iCloud Drive or Dropbox folder to keep copies off the Mac.

This is plain HTTP inside your network, which is fine on a trusted lab network but not on public
Wi-Fi. For anything wider, use option 2 or 3.

**Settings file.** Instead of typing environment variables, put `KEY=value` lines in
`backend/data/settings.env` (it lives with your data, so upgrades never touch it), for example:

```
BENCHTOP_BACKUP_DIR=/Users/you/Library/Mobile Documents/com~apple~CloudDocs/Benchtop backups
```

## 2. Reaching the lab Mac from anywhere with Tailscale

[Tailscale](https://tailscale.com) makes a private network between your own devices; nothing is
exposed to the internet.

1. Install Tailscale on the lab Mac and sign in.
2. Each group member installs Tailscale and is invited to your tailnet (Tailscale admin console → Users → Invite).
3. Run `./benchtop lab` on the lab Mac. People open `http://<mac-name>:8000` (the Mac's Tailscale name),
   from home or the bench.

For HTTPS on top (recommended), turn on HTTPS certificates in the Tailscale admin console and run
`tailscale serve --bg 8000` on the lab Mac; the address becomes `https://<mac-name>.<tailnet>.ts.net`.

## 3. A server with HTTPS (and optionally single sign-on)

You need a Linux machine with [Docker](https://docs.docker.com/engine/install/) (a small cloud VM
with 2 GB of RAM is plenty for a group), and a domain name pointed at it (ask your department's IT
for something like `eln.yourlab.ca`). Ports 80 and 443 must be reachable; Caddy fetches and renews
the HTTPS certificate by itself.

```bash
git clone <your Benchtop repository> benchtop && cd benchtop
cp deploy/server.env.example deploy/.env
nano deploy/.env          # domain, passwords (openssl rand -hex 32), single sign-on if you want it
docker compose -f deploy/docker-compose.server.yml --env-file deploy/.env up -d --build
```

Open `https://<your domain>`. The first visit asks you to create your own administrator account.
Data lives in Postgres and Docker volumes; backups are written nightly to `deploy/backups` (copy
that folder somewhere else as well).

To upgrade: `git pull` (or unzip the new version over the folder, keeping `deploy/.env`), then run the
same `docker compose … up -d --build` command.

### Single sign-on

People sign in with the account they already have (university Microsoft 365, Google Workspace…),
and nobody has another password to forget. Benchtop speaks standard OpenID Connect, so most
providers work.

**Microsoft 365 / Entra ID** (most universities, including U of T's UTORid-backed Microsoft accounts;
you may need IT to do this for you):

1. Entra admin centre → App registrations → New registration. Name: Benchtop.
   Supported accounts: this organisation only. Redirect URI (Web): `https://<your domain>/api/auth/sso/callback`.
2. Certificates & secrets → New client secret; copy its **value**.
3. In `deploy/.env`:
   ```
   BENCHTOP_OIDC_ISSUER=https://login.microsoftonline.com/<Directory (tenant) ID>/v2.0
   BENCHTOP_OIDC_CLIENT_ID=<Application (client) ID>
   BENCHTOP_OIDC_CLIENT_SECRET=<the secret value>
   BENCHTOP_OIDC_NAME=Microsoft
   ```

**Google Workspace:** Google Cloud console → APIs & Services → Credentials → Create OAuth client ID
(Web application) with the same redirect URI; issuer `https://accounts.google.com`. Use
`BENCHTOP_OIDC_ALLOWED_DOMAINS=yourlab.org` so only your domain's accounts get in.

How accounts are matched: the first time someone signs in, Benchtop links them to an existing account
with the same email address, or (with `BENCHTOP_OIDC_AUTO_CREATE=on`, the default) creates a
scientist account. With it `off`, an admin adds people first and their email decides who is who.
Admins can always still sign in with a password, so a provider outage never locks you out.

Signing records still asks for a password (the e-signature rules require re-authentication at signing),
so people who sign in with single sign-on set a **signing password** once under Account settings;
Benchtop reminds them.

Single sign-on also works without a server, e.g. on a lab Mac: put the same `BENCHTOP_OIDC_*` lines,
plus `BENCHTOP_PUBLIC_URL=https://<address people use>`, in `backend/data/settings.env`.

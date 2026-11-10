# Rememberry

A small, self-hosted personal app with two things in it:

- **People**: a dossier per person with a photo, birthday, children, when/where you met, job/work and a big free-text notes field. List, search across every field, and see birthdays coming up in the next 30 days.
- **Journal**: one short entry per day. Today's entry is on the home page; browse, search and edit past days.
- **@mentions**: type `@` in a journal entry to pick a person (`@Sarah Chen`, or just `@Sarah` if no one else is called Sarah). Their page lists every day they were mentioned.

Single user, one container, one port, everything stored in `/data`.

![Today](docs/today-desktop.png)

## Stack

Python 3.12, FastAPI, Jinja2 server-rendered pages, SQLite (stdlib `sqlite3`), plain CSS. No JavaScript build, no external services.

## Data

Everything lives in the `/data` volume:

| Path | Contents |
| --- | --- |
| `/data/rememberry.db` | SQLite database (people + journal) |
| `/data/uploads/` | Uploaded photos |

Back up that folder and you have backed up everything.

### Upgrades

The database schema is versioned. On startup the app applies any pending migrations; before it touches an existing database it writes a copy next to it as `rememberry.db.v<old-version>-<timestamp>.bak`. Once you're happy the new version works you can delete those `.bak` files.

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `APP_PASSWORD` | empty | If set, the app shows a login screen asking for this password. Empty means no login. |
| `TZ` | `UTC` | Timezone that decides what "today" is, e.g. `Australia/Sydney`. |
| `SECRET_KEY` | derived from password | Optional random string used to sign the login cookie. |
| `PORT` | `8080` | Port inside the container. |

Birthdays are free text. Write them as `YYYY-MM-DD` (or `MM-DD` if you don't know the year) and they show up in "Upcoming birthdays" with the age they're turning; anything else is stored as-is.

## Run with Docker Compose

```bash
docker compose up -d --build
# open http://localhost:8080
```

Data goes to `./data`. Edit `TZ` and `APP_PASSWORD` in `docker-compose.yml`.

## Run locally without Docker

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
DATA_DIR=./data uvicorn app.main:app --reload --port 8080
```

## Unraid setup

### 1. Get the image onto the server

The image isn't on Docker Hub, so pick one:

- **Build on the Unraid box** (simplest). In the Unraid terminal:

  ```bash
  cd /mnt/user/appdata
  git clone https://github.com/matda59/rememberry.git rememberry-src
  cd rememberry-src && docker build -t rememberry:latest .
  ```

  To update later: `cd /mnt/user/appdata/rememberry-src && git pull && docker build -t rememberry:latest .`, then restart the container.

- **Use GitHub Container Registry.** The included workflow (`.github/workflows/docker.yml`) builds and pushes `ghcr.io/matda59/rememberry:latest` on every push to `main`. Because the repo is private the package is private too: either make the package public in GitHub's package settings, or run `docker login ghcr.io` on Unraid with a personal access token that has `read:packages`. Then use `ghcr.io/matda59/rememberry:latest` as the repository below.

- **Build elsewhere and copy it:** `docker build -t rememberry:latest . && docker save rememberry:latest | ssh root@tower docker load`.

### 2. Add the container

**Option A: template.** Copy `unraid/rememberry.xml` to `/boot/config/plugins/dockerMan/templates-user/my-rememberry.xml` on the flash drive, then go to **Docker → Add Container** and pick `rememberry` from the Template dropdown.

```bash
cp /mnt/user/appdata/rememberry-src/unraid/rememberry.xml /boot/config/plugins/dockerMan/templates-user/my-rememberry.xml
```

**Option B: by hand.** **Docker → Add Container**, Advanced View off, and fill in:

| Field | Value |
| --- | --- |
| Name | `rememberry` |
| Repository | `rememberry:latest` (or `ghcr.io/matda59/rememberry:latest`) |
| Network Type | `bridge` |
| WebUI | `http://[IP]:[PORT:8080]/` |
| **Add Port**: Container Port `8080`, Host Port | `8080` (or any free port) |
| **Add Path**: Container Path `/data`, Host Path | `/mnt/user/appdata/rememberry` |
| **Add Variable**: Key `TZ` | e.g. `Australia/Sydney` |
| **Add Variable**: Key `APP_PASSWORD` | your password, or leave empty |

Click **Apply**, then open `http://<unraid-ip>:8080`.

### 3. Back up

Include `/mnt/user/appdata/rememberry` in your appdata backup (e.g. the Appdata Backup plugin). It holds the database and all photos.

## Security notes

This is built for a home LAN. The optional password is a single shared secret with a long-lived cookie; if you expose the app to the internet, put it behind a reverse proxy with HTTPS (and ideally your existing SSO/VPN) rather than relying on it alone.

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, notify

BASE = Path(__file__).parent
PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET = os.environ.get("SECRET_KEY") or hashlib.sha256(("rememberry:" + PASSWORD).encode()).hexdigest()
TZ = ZoneInfo(os.environ.get("TZ", "UTC"))
COOKIE = "rememberry_auth"
MAX_PHOTO_BYTES = 10 * 1024 * 1024
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/heic": ".heic"}
PERSON_FIELDS = ("name", "birthday", "children", "met", "work", "notes")
REMINDER_DAYS_AHEAD = int(os.environ.get("REMINDER_DAYS_AHEAD") or 3)
REMINDER_HOUR = int(os.environ.get("REMINDER_HOUR") or 9)
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")
log = logging.getLogger("rememberry")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


@app.on_event("startup")
def startup():
    db.init()
    with db.connect() as conn:
        reindex_mentions(conn)
    threading.Thread(target=reminder_loop, daemon=True).start()


def today() -> date:
    return datetime.now(TZ).date()


def auth_token() -> str:
    return hmac.new(SECRET.encode(), b"ok", hashlib.sha256).hexdigest()


@app.middleware("http")
async def require_password(request: Request, call_next):
    open_paths = ("/login", "/static/", "/healthz")
    if PASSWORD and not request.url.path.startswith(open_paths):
        if not hmac.compare_digest(request.cookies.get(COOKIE, ""), auth_token()):
            return RedirectResponse("/login", status_code=303)
    return await call_next(request)


def render(request: Request, name: str, **ctx):
    ctx.setdefault("nav", "")
    return templates.TemplateResponse(request, name, {"auth_enabled": bool(PASSWORD), **ctx})


def redirect(url: str):
    return RedirectResponse(url, status_code=303)


# ---------- auth ----------

@app.get("/login")
def login_form(request: Request):
    if not PASSWORD:
        return redirect("/")
    return render(request, "login.html", nav="login", error=False)


@app.post("/login")
def login(request: Request, password: str = Form("")):
    if PASSWORD and secrets.compare_digest(password.encode(), PASSWORD.encode()):
        resp = redirect("/")
        resp.set_cookie(COOKIE, auth_token(), max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
        return resp
    return render(request, "login.html", nav="login", error=True)


@app.post("/logout")
def logout():
    resp = redirect("/login")
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------- birthdays ----------

BIRTHDAY_RE = re.compile(r"^\s*(?:(\d{4})-)?(\d{1,2})-(\d{1,2})\s*$")


def parse_birthday(text: str):
    """Returns (year or None, month, day) for 'YYYY-MM-DD' or 'MM-DD', else None."""
    m = BIRTHDAY_RE.match(text or "")
    if not m:
        return None
    year = int(m.group(1)) if m.group(1) else None
    month, day = int(m.group(2)), int(m.group(3))
    try:
        date(2000, month, day)
    except ValueError:
        return None
    return year, month, day


def next_occurrence(month: int, day: int, ref: date) -> date:
    for year in (ref.year, ref.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            d = date(year, 2, 28)
        if d >= ref:
            return d
    raise AssertionError


def upcoming_birthdays(days: int = 30):
    ref = today()
    with db.connect() as conn:
        rows = conn.execute("SELECT id, name, birthday, photo FROM people WHERE birthday != ''").fetchall()
    result = []
    for row in rows:
        parsed = parse_birthday(row["birthday"])
        if not parsed:
            continue
        year, month, day = parsed
        nxt = next_occurrence(month, day, ref)
        delta = (nxt - ref).days
        if delta <= days:
            result.append({
                "id": row["id"], "name": row["name"], "photo": row["photo"], "date": nxt,
                "in_days": delta, "turning": (nxt.year - year) if year else None,
            })
    return sorted(result, key=lambda r: r["in_days"])


# ---------- reminders ----------

def birthday_message(b) -> str:
    who = f"{b['name']} turns {b['turning']}" if b["turning"] else f"{b['name']}'s birthday is"
    when = "today" if b["in_days"] == 0 else "tomorrow" if b["in_days"] == 1 else f"in {b['in_days']} days"
    return f"{who} {when} ({b['date']:%a %d %b})"


def send_birthday_reminders():
    """Once a day after REMINDER_HOUR: one message covering birthdays today and REMINDER_DAYS_AHEAD days out."""
    now = datetime.now(TZ)
    if not notify.any_configured() or now.hour < REMINDER_HOUR:
        return
    if db.get_setting("reminders_sent_for") == now.date().isoformat():
        return
    db.set_setting("reminders_sent_for", now.date().isoformat())
    due = [b for b in upcoming_birthdays(REMINDER_DAYS_AHEAD) if b["in_days"] in (0, REMINDER_DAYS_AHEAD)]
    if not due:
        return
    title = "Birthday reminder" if len(due) == 1 else f"{len(due)} birthday reminders"
    for name, error in notify.send_all(title, "\n".join(birthday_message(b) for b in due)):
        if error:
            log.warning("Birthday reminder via %s failed: %s", name, error)
        else:
            log.info("Birthday reminder sent via %s", name)


def reminder_loop():
    while True:
        try:
            send_birthday_reminders()
        except Exception:
            log.exception("Birthday reminder check failed")
        time.sleep(600)


@app.get("/settings")
def settings_page(request: Request, results=None):
    return render(request, "settings.html", nav="settings", channels=notify.channels(), results=results,
                  days_ahead=REMINDER_DAYS_AHEAD, hour=REMINDER_HOUR, tz=str(TZ),
                  last_check=db.get_setting("reminders_sent_for"))


@app.post("/settings/test-notification")
def settings_test(request: Request):
    results = notify.send_all("Rememberry test", "Test notification from Rememberry. If you can read this, "
                              "birthday reminders will reach you here.")
    return settings_page(request, results=results or [("No channel configured", "Set one of the env vars below.")])


# ---------- mentions ----------

MENTION_AT = re.compile(r"(?<![\w@])@")
MENTION_WORD = re.compile(r"\w[\w-]*")


def find_mentions(body: str, people) -> set[int]:
    """People referenced as @Full Name, or @FirstName when that first name is unique."""
    names = sorted(((p["name"].strip(), p["id"]) for p in people if p["name"].strip()), key=lambda n: -len(n[0]))
    by_first: dict[str, set[int]] = {}
    for name, pid in names:
        by_first.setdefault(name.split()[0].lower(), set()).add(pid)
    found = set()
    for m in MENTION_AT.finditer(body or ""):
        rest = body[m.end():]
        for name, pid in names:
            if rest[:len(name)].lower() == name.lower() and not rest[len(name):len(name) + 1].isalnum():
                found.add(pid)
                break
        else:
            word = MENTION_WORD.match(rest)
            ids = by_first.get(word.group(0).lower(), set()) if word else set()
            if len(ids) == 1:
                found |= ids
    return found


def index_day_mentions(conn, day: str, body: str, people=None):
    people = people if people is not None else conn.execute("SELECT id, name FROM people").fetchall()
    conn.execute("DELETE FROM mentions WHERE day = ?", (day,))
    conn.executemany("INSERT INTO mentions (day, person_id) VALUES (?, ?)",
                     [(day, pid) for pid in find_mentions(body, people)])


def reindex_mentions(conn):
    people = conn.execute("SELECT id, name FROM people").fetchall()
    conn.execute("DELETE FROM mentions")
    for row in conn.execute("SELECT day, body FROM journal WHERE body LIKE '%@%'").fetchall():
        index_day_mentions(conn, row["day"], row["body"], people)


def mentioned_people(day: date):
    with db.connect() as conn:
        return conn.execute(
            "SELECT p.id, p.name, p.photo FROM mentions m JOIN people p ON p.id = m.person_id "
            "WHERE m.day = ? ORDER BY p.name COLLATE NOCASE", (day.isoformat(),)
        ).fetchall()


@app.get("/api/people")
def api_people():
    with db.connect() as conn:
        rows = conn.execute("SELECT id, name FROM people ORDER BY name COLLATE NOCASE").fetchall()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


# ---------- journal ----------

def parse_day(day: str) -> date:
    try:
        return date.fromisoformat(day)
    except ValueError:
        raise HTTPException(404)


def get_entry(day: date):
    with db.connect() as conn:
        return conn.execute("SELECT * FROM journal WHERE day = ?", (day.isoformat(),)).fetchone()


@app.get("/")
def home(request: Request):
    t = today()
    with db.connect() as conn:
        recent = conn.execute(
            "SELECT day, body FROM journal WHERE day < ? AND body != '' ORDER BY day DESC LIMIT 5", (t.isoformat(),)
        ).fetchall()
        on_this_day = conn.execute(
            "SELECT day, body FROM journal WHERE substr(day, 6) = ? AND day < ? AND body != '' ORDER BY day DESC",
            (t.isoformat()[5:], t.isoformat()),
        ).fetchall()
    return render(request, "home.html", nav="today", day=t, entry=get_entry(t), recent=recent, on_this_day=on_this_day,
                  birthdays=upcoming_birthdays(), mentioned=mentioned_people(t),
                  saved=request.query_params.get("saved"))


@app.get("/journal")
def journal_list(request: Request, q: str = "", page: int = 1):
    per_page = 30
    page = max(page, 1)
    sql = "SELECT day, body FROM journal WHERE body != ''"
    args: list = []
    if q:
        sql += " AND body LIKE ?"
        args.append(f"%{q}%")
    sql += " ORDER BY day DESC LIMIT ? OFFSET ?"
    args += [per_page + 1, (page - 1) * per_page]
    with db.connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return render(request, "journal_list.html", nav="journal", entries=rows[:per_page], q=q, page=page,
                  has_more=len(rows) > per_page, today=today())


@app.get("/journal/go")
def journal_go(day: str = ""):
    return redirect(f"/journal/{parse_day(day).isoformat()}")


@app.get("/journal/{day}")
def journal_day(request: Request, day: str):
    d = parse_day(day)
    t = today()
    if d == t:
        return redirect("/")
    return render(request, "journal_day.html", nav="journal", day=d, entry=get_entry(d), today=t,
                  mentioned=mentioned_people(d),
                  prev_day=d - timedelta(days=1), next_day=d + timedelta(days=1),
                  saved=request.query_params.get("saved"))


@app.post("/journal/{day}")
def journal_save(day: str, body: str = Form("")):
    d = parse_day(day)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO journal (day, body, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(day) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at",
            (d.isoformat(), body.strip()),
        )
        index_day_mentions(conn, d.isoformat(), body)
    return redirect("/?saved=1" if d == today() else f"/journal/{d.isoformat()}?saved=1")


# ---------- people ----------

def get_person(person_id: int):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    return row


async def save_photo(photo: UploadFile | None) -> str | None:
    if not photo or not photo.filename:
        return None
    ext = PHOTO_TYPES.get(photo.content_type or "")
    if not ext:
        raise HTTPException(400, "Photo must be a JPEG, PNG, WebP, GIF or HEIC image.")
    data = await photo.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(400, "Photo is larger than 10 MB.")
    name = uuid.uuid4().hex + ext
    (db.UPLOAD_DIR / name).write_bytes(data)
    return name


def delete_photo(name: str):
    if name:
        (db.UPLOAD_DIR / Path(name).name).unlink(missing_ok=True)


@app.get("/photos/{name}")
def photo(name: str):
    path = db.UPLOAD_DIR / Path(name).name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=31536000, immutable"})


@app.get("/people")
def people_list(request: Request, q: str = ""):
    sql = "SELECT id, name, work, birthday, photo FROM people"
    args: list = []
    if q:
        like = f"%{q}%"
        sql += " WHERE " + " OR ".join(f"{f} LIKE ?" for f in PERSON_FIELDS)
        args = [like] * len(PERSON_FIELDS)
    sql += " ORDER BY name COLLATE NOCASE"
    with db.connect() as conn:
        people = conn.execute(sql, args).fetchall()
    return render(request, "people_list.html", nav="people", people=people, q=q, birthdays=upcoming_birthdays())


@app.get("/people/new")
def person_new(request: Request):
    return render(request, "person_form.html", nav="people", person=None)


@app.post("/people")
async def person_create(
    name: str = Form(...), birthday: str = Form(""), children: str = Form(""), met: str = Form(""),
    work: str = Form(""), notes: str = Form(""), photo: UploadFile | None = File(None),
):
    if not name.strip():
        raise HTTPException(400, "Name is required.")
    photo_name = await save_photo(photo) or ""
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO people (name, birthday, children, met, work, notes, photo) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name.strip(), birthday.strip(), children.strip(), met.strip(), work.strip(), notes.strip(), photo_name),
        )
        new_id = cur.lastrowid
        reindex_mentions(conn)
    return redirect(f"/people/{new_id}")


@app.get("/people/{person_id}")
def person_view(request: Request, person_id: int):
    person = get_person(person_id)
    parsed = parse_birthday(person["birthday"])
    birthday_info = None
    if parsed:
        year, month, day = parsed
        nxt = next_occurrence(month, day, today())
        birthday_info = {"next": nxt, "in_days": (nxt - today()).days, "turning": (nxt.year - year) if year else None}
    with db.connect() as conn:
        mentions = conn.execute(
            "SELECT j.day, j.body FROM mentions m JOIN journal j ON j.day = m.day "
            "WHERE m.person_id = ? ORDER BY j.day DESC", (person_id,)
        ).fetchall()
        notes = conn.execute(
            "SELECT id, day, body FROM person_notes WHERE person_id = ? ORDER BY day DESC, id DESC", (person_id,)
        ).fetchall()
    return render(request, "person_view.html", nav="people", person=person, birthday_info=birthday_info,
                  mentions=mentions, quick_notes=notes, today=today())


@app.get("/people/{person_id}/edit")
def person_edit(request: Request, person_id: int):
    return render(request, "person_form.html", nav="people", person=get_person(person_id))


@app.post("/people/{person_id}")
async def person_update(
    person_id: int, name: str = Form(...), birthday: str = Form(""), children: str = Form(""), met: str = Form(""),
    work: str = Form(""), notes: str = Form(""), photo: UploadFile | None = File(None), remove_photo: str = Form(""),
):
    person = get_person(person_id)
    if not name.strip():
        raise HTTPException(400, "Name is required.")
    photo_name = person["photo"]
    new_photo = await save_photo(photo)
    if new_photo or remove_photo:
        delete_photo(photo_name)
        photo_name = new_photo or ""
    with db.connect() as conn:
        conn.execute(
            "UPDATE people SET name=?, birthday=?, children=?, met=?, work=?, notes=?, photo=?, "
            "updated_at=datetime('now') WHERE id=?",
            (name.strip(), birthday.strip(), children.strip(), met.strip(), work.strip(), notes.strip(),
             photo_name, person_id),
        )
        if name.strip() != person["name"]:
            reindex_mentions(conn)
    return redirect(f"/people/{person_id}")


@app.post("/people/{person_id}/delete")
def person_delete(person_id: int):
    person = get_person(person_id)
    delete_photo(person["photo"])
    with db.connect() as conn:
        conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
        conn.execute("DELETE FROM person_notes WHERE person_id = ?", (person_id,))
        reindex_mentions(conn)
    return redirect("/people")


@app.post("/people/{person_id}/notes")
def person_note_add(person_id: int, body: str = Form(""), day: str = Form("")):
    get_person(person_id)
    if body.strip():
        d = parse_day(day) if day else today()
        with db.connect() as conn:
            conn.execute("INSERT INTO person_notes (person_id, day, body) VALUES (?, ?, ?)",
                         (person_id, d.isoformat(), body.strip()))
    return redirect(f"/people/{person_id}#quick-notes")


@app.post("/people/{person_id}/notes/{note_id}/delete")
def person_note_delete(person_id: int, note_id: int):
    with db.connect() as conn:
        conn.execute("DELETE FROM person_notes WHERE id = ? AND person_id = ?", (note_id, person_id))
    return redirect(f"/people/{person_id}#quick-notes")

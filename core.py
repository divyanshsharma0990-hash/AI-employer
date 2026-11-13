"""AI Employer core: admin login, AI employees, chat history, attachments, and calling the AIs."""
import os, re, json, time, sqlite3, secrets, hashlib, asyncio, base64, io
from contextlib import closing
import httpx
from cryptography.fernet import Fernet

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("DB_PATH", os.path.join(BASE, "ai_employer.db"))
KEYFILE = os.path.join(BASE, ".secret.key")


def _fernet():
    key = os.environ.get("FERNET_KEY")  # set this in production so saved API keys survive restarts
    if not key:
        if not os.path.exists(KEYFILE):
            with open(KEYFILE, "wb") as f:
                f.write(Fernet.generate_key())
            try:
                os.chmod(KEYFILE, 0o600)
            except OSError:
                pass
        with open(KEYFILE, "rb") as f:
            key = f.read().decode()
    return Fernet(key.encode())


fernet = _fernet()

with closing(sqlite3.connect(DB)) as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, salt TEXT, hash TEXT);
    CREATE TABLE IF NOT EXISTS agents(id INTEGER PRIMARY KEY, name TEXT UNIQUE, provider TEXT, model TEXT,
        base_url TEXT, api_key TEXT, role TEXT);
    CREATE TABLE IF NOT EXISTS convs(id INTEGER PRIMARY KEY, title TEXT, created REAL);
    CREATE TABLE IF NOT EXISTS msgs(id INTEGER PRIMARY KEY, conv_id INTEGER, sender TEXT, kind TEXT,
        content TEXT, created REAL);
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, msg_id INTEGER, name TEXT, mime TEXT,
        kind TEXT, text TEXT, data TEXT);
    """)
    _c.commit()


def q(sql, args=(), write=False):
    with closing(sqlite3.connect(DB)) as c:
        c.row_factory = sqlite3.Row
        cur = c.execute(sql, args)
        if write:
            c.commit()
            return cur.lastrowid
        return [dict(r) for r in cur.fetchall()]


# ---------- admin login ----------
def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()


def has_users():
    return bool(q("SELECT 1 FROM users"))


def create_admin(username, password):
    username = username.strip()
    if has_users():
        raise ValueError("An admin already exists")
    if not username or len(password) < 8:
        raise ValueError("Use a username and a password of at least 8 characters")
    salt = secrets.token_hex(16)
    q("INSERT INTO users(username,salt,hash) VALUES(?,?,?)", (username, salt, _hash(password, salt)), write=True)


def check_login(username, password):
    rows = q("SELECT * FROM users WHERE username=?", (username.strip(),))
    if not rows or not secrets.compare_digest(rows[0]["hash"], _hash(password, rows[0]["salt"])):
        time.sleep(1)  # slows down password guessing
        raise ValueError("Wrong username or password")


# On a public deployment, set ADMIN_USERNAME and ADMIN_PASSWORD so nobody else can claim the admin account.
if os.environ.get("ADMIN_USERNAME") and os.environ.get("ADMIN_PASSWORD") and not has_users():
    create_admin(os.environ["ADMIN_USERNAME"], os.environ["ADMIN_PASSWORD"])


# ---------- AI employees ----------
def list_agents():
    return q("SELECT id,name,provider,model,base_url,role FROM agents")


def get_key(aid):
    rows = q("SELECT api_key FROM agents WHERE id=?", (aid,))
    return fernet.decrypt(rows[0]["api_key"].encode()).decode() if rows else ""


def add_agent(name, provider, model, base_url, api_key, role):
    """provider 'manual' = web-login employee: you copy the prompt into the site and paste the reply back."""
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", name):
        raise ValueError("Name: letters, numbers, - or _ only (max 24)")
    if provider != "manual":
        if provider != "anthropic" and not base_url.strip():
            raise ValueError("Base URL is required for this provider")
        if not api_key.strip() or not model.strip():
            raise ValueError("Model and API key are required")
    enc = fernet.encrypt(api_key.strip().encode()).decode()
    try:
        q("INSERT INTO agents(name,provider,model,base_url,api_key,role) VALUES(?,?,?,?,?,?)",
          (name, provider, model.strip(), base_url.strip(), enc, role.strip()), write=True)
    except sqlite3.IntegrityError:
        raise ValueError("An employee with this name already exists")


def update_agent(aid, model, base_url, role, api_key=""):
    q("UPDATE agents SET model=?, base_url=?, role=? WHERE id=?", (model.strip(), base_url.strip(), role.strip(), aid), write=True)
    if api_key.strip():
        q("UPDATE agents SET api_key=? WHERE id=?", (fernet.encrypt(api_key.strip().encode()).decode(), aid), write=True)


def del_agent(aid):
    q("DELETE FROM agents WHERE id=?", (aid,), write=True)


def fetch_models(provider, base_url, key):
    """Ask the provider which models this key can use."""
    if not key.strip():
        raise ValueError("Enter the API key first")
    try:
        if provider == "anthropic":
            r = httpx.get((base_url or "https://api.anthropic.com").rstrip("/") + "/v1/models?limit=100",
                          headers={"x-api-key": key.strip(), "anthropic-version": "2023-06-01"}, timeout=20)
        else:
            r = httpx.get(base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key.strip()}"}, timeout=20)
        r.raise_for_status()
        ids = sorted({m["id"].removeprefix("models/") for m in r.json()["data"]})
    except httpx.HTTPStatusError as e:
        raise ValueError(f"Provider said {e.response.status_code}. Check the key and Base URL.")
    except Exception as e:
        raise ValueError(f"Could not load models ({type(e).__name__}). Check the key and Base URL.")
    if not ids:
        raise ValueError("The provider returned no models")
    return ids


async def test_agent(aid):
    rows = q("SELECT * FROM agents WHERE id=?", (aid,))
    if not rows:
        return False, "Employee not found"
    if rows[0]["provider"] == "manual":
        return True, "Web-login employees need no connection test."
    ok, text = await _ask(rows[0], "You are a connection test.", "Reply with the single word: ready")
    return ok, text


# ---------- attachments ----------
IMG = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}


def read_attachment(name, raw):
    ext = name.rsplit(".", 1)[-1].lower()
    if len(raw) > 5_000_000:
        raise ValueError(f"{name} is larger than 5 MB")
    if ext in IMG:
        return {"name": name, "mime": IMG[ext], "kind": "image", "text": "", "data": base64.b64encode(raw).decode()}
    if ext == "pdf":
        from pypdf import PdfReader
        text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(raw)).pages)
    elif ext == "docx":
        from docx import Document
        text = "\n".join(p.text for p in Document(io.BytesIO(raw)).paragraphs)
    else:
        text = raw.decode("utf-8", "replace")
    return {"name": name, "mime": "text/plain", "kind": "doc", "text": text[:20000], "data": ""}


# ---------- chat history ----------
def list_convs():
    return q("SELECT id,title FROM convs ORDER BY id DESC")


def new_conv():
    return q("INSERT INTO convs(title,created) VALUES('New chat',?)", (time.time(),), write=True)


def del_conv(cid):
    q("DELETE FROM files WHERE msg_id IN (SELECT id FROM msgs WHERE conv_id=?)", (cid,), write=True)
    q("DELETE FROM msgs WHERE conv_id=?", (cid,), write=True)
    q("DELETE FROM convs WHERE id=?", (cid,), write=True)


def del_all_convs():
    q("DELETE FROM files", write=True)
    q("DELETE FROM msgs", write=True)
    q("DELETE FROM convs", write=True)


def get_msgs(cid):
    rows = q("SELECT id,sender,kind,content FROM msgs WHERE conv_id=? ORDER BY id", (cid,))
    for r in rows:
        r["files"] = q("SELECT name,mime,kind,data FROM files WHERE msg_id=?", (r["id"],)) if r["kind"] == "user" else []
    return rows


def _add(cid, sender, kind, content):
    return q("INSERT INTO msgs(conv_id,sender,kind,content,created) VALUES(?,?,?,?,?)",
             (cid, sender, kind, content, time.time()), write=True)


def resolve_pending(mid, text):
    if text.strip():
        q("UPDATE msgs SET kind='ai', content=? WHERE id=? AND kind='pending'", (text.strip(), mid), write=True)


def skip_pending(mid):
    q("DELETE FROM msgs WHERE id=? AND kind='pending'", (mid,), write=True)


def _transcript(cid):
    rows = q("SELECT id,sender,kind,content FROM msgs WHERE conv_id=? AND kind NOT IN ('error','pending') "
             "ORDER BY id DESC LIMIT 30", (cid,))[::-1]
    out = []
    for r in rows:
        line = f"{'Boss' if r['kind'] == 'user' else r['sender']}: {r['content']}"
        for f in q("SELECT name,kind,text FROM files WHERE msg_id=?", (r["id"],)):
            line += f"\n[Attached file {f['name']}]" + (f":\n{f['text']}" if f["kind"] == "doc" else " (image)")
        out.append(line)
    return "\n".join(out)[-60000:]


def _system(a, team):
    others = ", ".join(x["name"] for x in team if x["id"] != a["id"]) or "none"
    return (f"You are {a['name']}, an AI employee working for a human Boss in a group chat. Colleagues: {others}. "
            f"{a['role']} Write only your own reply, short and useful. Never write lines for other people.")


async def _ask(a, system, prompt, images=None):
    """Call one AI. Returns (ok, text). Retries when the provider is overloaded."""
    images = images or []
    try:
        key = fernet.decrypt(a["api_key"].encode()).decode()
        if a["provider"] == "anthropic":
            url = (a["base_url"] or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
            content = prompt if not images else (
                [{"type": "image", "source": {"type": "base64", "media_type": i["mime"], "data": i["data"]}} for i in images]
                + [{"type": "text", "text": prompt}])
            body = {"model": a["model"], "max_tokens": 1000, "system": system,
                    "messages": [{"role": "user", "content": content}]}
        else:
            url = a["base_url"].rstrip("/") + "/chat/completions"
            headers = {"Authorization": f"Bearer {key}"}
            content = prompt if not images else (
                [{"type": "text", "text": prompt}]
                + [{"type": "image_url", "image_url": {"url": f"data:{i['mime']};base64,{i['data']}"}} for i in images])
            body = {"model": a["model"], "messages": [{"role": "system", "content": system},
                                                       {"role": "user", "content": content}]}
        async with httpx.AsyncClient(timeout=120) as h:
            for attempt in range(3):
                try:
                    r = await h.post(url, headers=headers, json=body)
                except httpx.TimeoutException:
                    if attempt < 2:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    raise
                busy = r.status_code in (500, 502, 503, 504) or (
                    r.status_code == 429 and not re.search("credit|quota|insufficient", r.text, re.I))
                if busy and attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                break
        r.raise_for_status()
        d = r.json()
        if a["provider"] == "anthropic":
            text = "".join(b.get("text", "") for b in d["content"])
        else:
            text = d["choices"][0]["message"]["content"]
        return True, (text or "").strip()
    except httpx.HTTPStatusError as e:
        return False, f"{e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"


async def _run(cid, team, jobs, images=None):
    """jobs = [(agent, extra_instruction)]. API agents answer in parallel; web-login agents get a pending prompt."""
    ts = _transcript(cid)
    prompts = [f"Conversation so far:\n{ts}\n\n{extra}Reply now as {a['name']}." for a, extra in jobs]
    api = [(i, a) for i, (a, _) in enumerate(jobs) if a["provider"] != "manual"]
    outs = await asyncio.gather(*[_ask(a, _system(a, team), prompts[i], images) for i, a in api])
    for (i, a), (ok, text) in zip(api, outs):
        if ok and text.upper().startswith("PASS"):
            continue
        _add(cid, a["name"], "ai" if ok else "error", text or "(empty reply)")
    for i, (a, _) in enumerate(jobs):
        if a["provider"] == "manual":
            _add(cid, a["name"], "pending", f"{_system(a, team)}\n\n{prompts[i]}")


async def send(cid, text, mode="all", manager_id=None, files=None):
    """mode 'all': everyone (or the @mentioned) answers. mode 'team': a manager AI splits the task.
    Empty text with mode 'all' lets the API employees discuss what was already said.
    files = [(filename, bytes)] attached to this message."""
    team = [dict(r) for r in q("SELECT * FROM agents")]
    if not team:
        raise ValueError("Add at least one AI employee first")
    text = text.strip()
    atts = [read_attachment(n, b) for n, b in (files or [])][:4]
    if text or atts:
        mid = _add(cid, "Boss", "user", text or "(attachment)")
        for f in atts:
            q("INSERT INTO files(msg_id,name,mime,kind,text,data) VALUES(?,?,?,?,?,?)",
              (mid, f["name"], f["mime"], f["kind"], f["text"], f["data"]), write=True)
        if q("SELECT COUNT(*) n FROM msgs WHERE conv_id=?", (cid,))[0]["n"] == 1:
            q("UPDATE convs SET title=? WHERE id=?", ((text or atts[0]["name"])[:40], cid), write=True)
    images = [f for f in atts if f["kind"] == "image"]
    task = text or "Please look at the attached file(s)."

    if mode == "team" and (text or atts):
        api_team = [a for a in team if a["provider"] != "manual"]
        if not api_team:
            raise ValueError("Team task needs at least one API-based employee to act as manager")
        mgr = next((a for a in api_team if a["id"] == manager_id), api_team[0])
        staff = [a for a in team if a["id"] != mgr["id"]] or [mgr]
        roster = "\n".join(f"- {a['name']}: {a['role'] or a['model'] or 'web chat'}" for a in staff)
        ok, plan = await _ask(mgr, _system(mgr, team),
                              f"The Boss gave this task: {task}\nSplit it among these team members:\n{roster}\n"
                              'Reply ONLY with JSON: {"assignments":[{"to":"Name","task":"what they should do"}]}\n'
                              f"Recent chat:\n{_transcript(cid)}")
        jobs = []
        try:
            if not ok:
                raise ValueError(plan)
            items = json.loads(re.search(r"\{.*\}", plan, re.S).group(0))["assignments"]
            by_name = {a["name"].lower(): a for a in staff}
            jobs = [(by_name[i["to"].lower()], f"Your task from {mgr['name']}: {i['task']}\n\n")
                    for i in items if str(i.get("to", "")).lower() in by_name]
        except Exception:
            _add(cid, mgr["name"], "error", "Could not make a plan, so everyone gets the full task.")
        if not jobs:
            jobs = [(a, f"Your task: {task}\n\n") for a in staff]
        _add(cid, mgr["name"], "system",
             "Plan: " + "; ".join(f"{a['name']} → {e.split(': ', 1)[-1].strip()}" for a, e in jobs))
        await _run(cid, team, jobs, images)
        if q("SELECT 1 FROM msgs WHERE conv_id=? AND kind='pending'", (cid,)):
            _add(cid, mgr["name"], "system", "Waiting for pasted replies. After pasting them, press Let them discuss.")
        else:
            await _run(cid, team, [(mgr, "All assigned work is done. Write the final combined answer for the Boss.\n\n")])
    else:
        mentioned = {m.lower() for m in re.findall(r"@([\w-]+)", text)}
        targets = [a for a in team if a["name"].lower() in mentioned] or team
        extra = ""
        if not (text or atts):
            targets = [a for a in targets if a["provider"] != "manual"]
            extra = "Colleagues replied above. Add to the discussion only if useful, otherwise reply exactly PASS.\n\n"
        await _run(cid, team, [(a, extra) for a in targets], images)

"""AI Employer core: admin login, AI employees, chat history, and calling the AIs."""
import os, re, json, time, sqlite3, secrets, hashlib, asyncio
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


def add_agent(name, provider, model, base_url, api_key, role):
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", name):
        raise ValueError("Name: letters, numbers, - or _ only (max 24)")
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


def del_agent(aid):
    q("DELETE FROM agents WHERE id=?", (aid,), write=True)


# ---------- chat history ----------
def list_convs():
    return q("SELECT id,title FROM convs ORDER BY id DESC")


def new_conv():
    return q("INSERT INTO convs(title,created) VALUES('New chat',?)", (time.time(),), write=True)


def del_conv(cid):
    q("DELETE FROM msgs WHERE conv_id=?", (cid,), write=True)
    q("DELETE FROM convs WHERE id=?", (cid,), write=True)


def get_msgs(cid):
    return q("SELECT id,sender,kind,content FROM msgs WHERE conv_id=? ORDER BY id", (cid,))


def _add(cid, sender, kind, content):
    q("INSERT INTO msgs(conv_id,sender,kind,content,created) VALUES(?,?,?,?,?)",
      (cid, sender, kind, content, time.time()), write=True)


def _transcript(cid):
    rows = q("SELECT sender,kind,content FROM msgs WHERE conv_id=? AND kind!='error' ORDER BY id DESC LIMIT 30", (cid,))[::-1]
    return "\n".join(f"{'Boss' if r['kind'] == 'user' else r['sender']}: {r['content']}" for r in rows)


def _system(a, team):
    others = ", ".join(x["name"] for x in team if x["id"] != a["id"]) or "none"
    return (f"You are {a['name']}, an AI employee working for a human Boss in a group chat. Colleagues: {others}. "
            f"{a['role']} Write only your own reply, short and useful. Never write lines for other people.")


async def _ask(a, system, prompt):
    """Call one AI. Returns (ok, text)."""
    try:
        key = fernet.decrypt(a["api_key"].encode()).decode()
        async with httpx.AsyncClient(timeout=90) as h:
            if a["provider"] == "anthropic":
                url = (a["base_url"] or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
                r = await h.post(url, headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                                 json={"model": a["model"], "max_tokens": 1000, "system": system,
                                       "messages": [{"role": "user", "content": prompt}]})
            else:
                r = await h.post(a["base_url"].rstrip("/") + "/chat/completions",
                                 headers={"Authorization": f"Bearer {key}"},
                                 json={"model": a["model"],
                                       "messages": [{"role": "system", "content": system},
                                                    {"role": "user", "content": prompt}]})
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


async def _run(cid, team, jobs):
    """jobs = [(agent, extra_instruction)]. All agents answer in parallel and see the same chat."""
    ts = _transcript(cid)
    outs = await asyncio.gather(*[
        _ask(a, _system(a, team), f"Conversation so far:\n{ts}\n\n{extra}Reply now as {a['name']}.")
        for a, extra in jobs])
    for (a, _), (ok, text) in zip(jobs, outs):
        if ok and text.upper().startswith("PASS"):
            continue
        _add(cid, a["name"], "ai" if ok else "error", text or "(empty reply)")


async def send(cid, text, mode="all", manager_id=None):
    """mode 'all': everyone (or the @mentioned) answers. mode 'team': manager splits the task.
    Empty text with mode 'all' lets the team discuss what was already said."""
    team = [dict(r) for r in q("SELECT * FROM agents")]
    if not team:
        raise ValueError("Add at least one AI employee first")
    text = text.strip()
    if text:
        _add(cid, "Boss", "user", text)
        if q("SELECT COUNT(*) n FROM msgs WHERE conv_id=?", (cid,))[0]["n"] == 1:
            q("UPDATE convs SET title=? WHERE id=?", (text[:40], cid), write=True)

    if mode == "team" and text:
        mgr = next((a for a in team if a["id"] == manager_id), team[0])
        staff = [a for a in team if a["id"] != mgr["id"]] or [mgr]
        roster = "\n".join(f"- {a['name']}: {a['role'] or a['model']}" for a in staff)
        ok, plan = await _ask(mgr, _system(mgr, team),
                              f"The Boss gave this task: {text}\nSplit it among these team members:\n{roster}\n"
                              'Reply ONLY with JSON: {"assignments":[{"to":"Name","task":"what they should do"}]}')
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
            jobs = [(a, f"Your task: {text}\n\n") for a in staff]
        _add(cid, mgr["name"], "system",
             "Plan: " + "; ".join(f"{a['name']} → {e.split(': ', 1)[-1].strip()}" for a, e in jobs))
        await _run(cid, team, jobs)
        await _run(cid, team, [(mgr, "All assigned work is done. Write the final combined answer for the Boss.\n\n")])
    else:
        mentioned = {m.lower() for m in re.findall(r"@([\w-]+)", text)}
        targets = [a for a in team if a["name"].lower() in mentioned] or team
        extra = "" if text else "Colleagues replied above. Add to the discussion only if useful, otherwise reply exactly PASS.\n\n"
        await _run(cid, team, [(a, extra) for a in targets])

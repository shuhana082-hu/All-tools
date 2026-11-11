import os, sqlite3, time, secrets
from functools import wraps
from flask import Flask, request, jsonify, session, render_template, Response, abort

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

DB = os.environ.get("DB_PATH", "data.db")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")

DEFAULTS = {
    "site_name": "No Rules YT", "handle": "@no_rules_yt_",
    "tagline": "Games, tools and fun stuff, made with zero rules.",
    "announce": "", "tiktok": "", "youtube": "", "instagram": "",
    "followers": "0", "likes": "0", "views": "0", "videos": "0",
    "footer": "Made with care", "chat_enabled": "1", "chat_title": "World Chat",
}
ITEM_FIELDS = ["kind", "title", "descr", "badge", "tags", "icon", "custom_svg",
               "image", "mode", "url", "html", "visible", "pos"]


def q(sql, args=(), write=False):
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        cur = c.execute(sql, args)
        if write:
            c.commit()
            return cur.lastrowid
        return [dict(r) for r in cur.fetchall()]
    finally:
        c.close()


def init():
    q("""CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT, title TEXT, descr TEXT, badge TEXT, tags TEXT, icon TEXT,
        custom_svg TEXT, image TEXT, mode TEXT, url TEXT, html TEXT,
        visible INTEGER DEFAULT 1, pos INTEGER DEFAULT 0)""", write=True)
    q("CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT)", write=True)
    q("""CREATE TABLE IF NOT EXISTS chat(id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, text TEXT, ts INTEGER, ip TEXT)""", write=True)
    q("CREATE TABLE IF NOT EXISTS online(sid TEXT PRIMARY KEY, ts INTEGER)", write=True)
    q("CREATE TABLE IF NOT EXISTS banned(ip TEXT PRIMARY KEY)", write=True)


init()


def settings():
    s = dict(DEFAULTS)
    for r in q("SELECT k,v FROM settings"):
        s[r["k"]] = r["v"]
    return s


def ip():
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "")


def admin_only(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("admin"):
            return jsonify(error="unauthorized"), 401
        return f(*a, **k)
    return w


# ---------- pages ----------
@app.get("/")
def home():
    return render_template("index.html")


@app.get("/admin")
def admin_page():
    return render_template("admin.html")


@app.get("/p/<int:i>")
def hosted(i):
    r = q("SELECT html,visible FROM items WHERE id=? AND mode='html'", (i,))
    if not r or not r[0]["visible"]:
        abort(404)
    return Response(r[0]["html"] or "", mimetype="text/html")


# ---------- public api ----------
@app.get("/api/data")
def data():
    items = []
    for r in q("SELECT * FROM items WHERE visible=1 ORDER BY pos ASC, id DESC"):
        r["href"] = r["url"] if r["mode"] == "link" else "/p/%d" % r["id"]
        r.pop("html", None)
        items.append(r)
    return jsonify(settings=settings(), items=items)


_last = {}


@app.route("/api/chat", methods=["GET", "POST"])
def chat():
    s = settings()
    if request.method == "POST":
        if s["chat_enabled"] != "1":
            return jsonify(error="Chat is turned off"), 403
        if q("SELECT 1 FROM banned WHERE ip=?", (ip(),)):
            return jsonify(error="You are blocked from chat"), 403
        now = time.time()
        if now - _last.get(ip(), 0) < 1.2:
            return jsonify(error="Slow down"), 429
        _last[ip()] = now
        j = request.get_json(silent=True) or {}
        name = str(j.get("name", "")).strip()[:20]
        text = str(j.get("text", "")).strip()[:300]
        if not name or not text:
            return jsonify(error="Name and message required"), 400
        q("INSERT INTO chat(name,text,ts,ip) VALUES(?,?,?,?)", (name, text, int(now), ip()), write=True)
        return jsonify(ok=True)
    sid = request.args.get("sid", "")[:40]
    now = int(time.time())
    if sid:
        q("INSERT OR REPLACE INTO online(sid,ts) VALUES(?,?)", (sid, now), write=True)
    q("DELETE FROM online WHERE ts<?", (now - 30,), write=True)
    after = int(request.args.get("after", 0) or 0)
    if after == 0:
        msgs = q("SELECT id,name,text,ts FROM (SELECT id,name,text,ts FROM chat ORDER BY id DESC LIMIT 50) ORDER BY id")
    else:
        msgs = q("SELECT id,name,text,ts FROM chat WHERE id>? ORDER BY id LIMIT 100", (after,))
    n = q("SELECT COUNT(*) c FROM online")[0]["c"]
    return jsonify(msgs=msgs, online=n, enabled=s["chat_enabled"] == "1", title=s["chat_title"])


# ---------- admin api ----------
@app.post("/api/admin/login")
def login():
    j = request.get_json(silent=True) or {}
    ok = (secrets.compare_digest(str(j.get("user", "")), ADMIN_USER)
          and secrets.compare_digest(str(j.get("pass", "")), ADMIN_PASS))
    if not ok:
        time.sleep(1)
        return jsonify(error="Wrong username or password"), 401
    session["admin"] = True
    return jsonify(ok=True)


@app.post("/api/admin/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/admin/me")
def me():
    return jsonify(admin=bool(session.get("admin")))


@app.get("/api/admin/items")
@admin_only
def a_items():
    return jsonify(items=q("SELECT * FROM items ORDER BY pos ASC, id DESC"))


@app.post("/api/admin/items")
@admin_only
def a_save():
    j = request.get_json(silent=True) or {}
    vals = {k: j.get(k, "") for k in ITEM_FIELDS}
    vals["visible"] = 1 if j.get("visible", 1) else 0
    vals["pos"] = int(j.get("pos") or 0)
    if not str(vals["title"]).strip():
        return jsonify(error="Title is required"), 400
    if vals["mode"] not in ("link", "html"):
        vals["mode"] = "link"
    if j.get("id"):
        sets = ",".join(k + "=?" for k in ITEM_FIELDS)
        q("UPDATE items SET %s WHERE id=?" % sets, [vals[k] for k in ITEM_FIELDS] + [j["id"]], write=True)
        return jsonify(id=j["id"])
    cols = ",".join(ITEM_FIELDS)
    i = q("INSERT INTO items(%s) VALUES(%s)" % (cols, ",".join("?" * len(ITEM_FIELDS))),
          [vals[k] for k in ITEM_FIELDS], write=True)
    return jsonify(id=i)


@app.delete("/api/admin/items/<int:i>")
@admin_only
def a_del(i):
    q("DELETE FROM items WHERE id=?", (i,), write=True)
    return jsonify(ok=True)


@app.route("/api/admin/settings", methods=["GET", "POST"])
@admin_only
def a_settings():
    if request.method == "POST":
        j = request.get_json(silent=True) or {}
        for k in DEFAULTS:
            if k in j:
                q("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (k, str(j[k])), write=True)
    return jsonify(settings=settings())


@app.get("/api/admin/chat")
@admin_only
def a_chat():
    return jsonify(msgs=q("SELECT id,name,text,ts,ip FROM chat ORDER BY id DESC LIMIT 200"),
                   banned=[r["ip"] for r in q("SELECT ip FROM banned")])


@app.delete("/api/admin/chat/<int:i>")
@admin_only
def a_chat_del(i):
    q("DELETE FROM chat WHERE id=?", (i,), write=True)
    return jsonify(ok=True)


@app.post("/api/admin/chat/clear")
@admin_only
def a_chat_clear():
    q("DELETE FROM chat", write=True)
    return jsonify(ok=True)


@app.post("/api/admin/ban")
@admin_only
def a_ban():
    j = request.get_json(silent=True) or {}
    if j.get("ip"):
        if j.get("undo"):
            q("DELETE FROM banned WHERE ip=?", (j["ip"],), write=True)
        else:
            q("INSERT OR IGNORE INTO banned(ip) VALUES(?)", (j["ip"],), write=True)
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)

# -*- coding: utf-8 -*-
"""
e签宝 · 功能价值教练 (Feature Value Coach)
==========================================
售前功能场景/价值沉淀 → 早会分享 → 群内同步学习 → 抢答考核 → 积分排名
一站式轻量 Web 工具，本地运行，团队局域网共享。

启动：python app.py
访问：http://127.0.0.1:5055
"""

import os
import re
import json
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, g, flash
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Render 等云平台挂载持久磁盘到 DATA_DIR（默认当前目录，本地开发不受影响）
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    # 持久盘不可用时（如免费版未挂载），回退到项目目录，保证服务可启动
    DATA_DIR = BASE_DIR
    os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "coach.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 数据库方言：设置 DATABASE_URL（以 postgres:// 或 postgresql:// 开头）时启用 Postgres，
# 否则回退到本地 SQLite 文件。两者共用同一套业务 SQL（占位符 ? 在 Postgres 下自动转为 %s）。
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_PG = bool(DATABASE_URL) and DATABASE_URL.lower().startswith(("postgres://", "postgresql://"))

psycopg2 = None
DictCursor = None
PgIntegrityError = ()
if USE_PG:
    try:
        import psycopg2
        from psycopg2.extras import DictCursor
        from psycopg2 import IntegrityError as PgIntegrityError
    except Exception:
        psycopg2 = None
        DictCursor = None
        PgIntegrityError = ()

app = Flask(__name__)
app.secret_key = "esign-feature-coach-2026"
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

# ---------- 数据库 ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK(role IN ('sfr','business','admin')),
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    name TEXT NOT NULL,
    category TEXT,
    scenario TEXT,
    value_point TEXT,
    owner_sfr_id INTEGER,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','filled','shared')),
    filled_date TEXT,
    shared_date TEXT,
    FOREIGN KEY (owner_sfr_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS learning_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    feature_id INTEGER NOT NULL,
    learned_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(user_id, feature_id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (feature_id) REFERENCES features(id)
);
CREATE TABLE IF NOT EXISTS quizzes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id INTEGER,
    question TEXT NOT NULL,
    answer_hint TEXT,
    sfr_id INTEGER NOT NULL,
    status TEXT DEFAULT 'active' CHECK(status IN ('active','closed')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    closed_at TEXT,
    published_at TEXT,
    deadline TEXT,
    duration_min INTEGER DEFAULT 30,
    auto_generated INTEGER DEFAULT 0,
    judge_mode TEXT DEFAULT 'manual',
    quiz_type TEXT DEFAULT 'qa',
    options TEXT,
    correct_answer TEXT,
    full_score INTEGER DEFAULT 1,
    FOREIGN KEY (feature_id) REFERENCES features(id),
    FOREIGN KEY (sfr_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    answered_at TEXT DEFAULT (datetime('now','localtime')),
    is_correct INTEGER,
    judged_by INTEGER,
    judged_at TEXT,
    judge_reason TEXT,
    auto_judged INTEGER DEFAULT 0,
    score INTEGER DEFAULT 0,
    UNIQUE(quiz_id, user_id),
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (judged_by) REFERENCES users(id)
);
"""

# Postgres 版建表语句：AUTOINCREMENT -> SERIAL；时间默认值改用 to_char(now(), ...)
SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK(role IN ('sfr','business','admin')),
    created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
);
CREATE TABLE IF NOT EXISTS features (
    id SERIAL PRIMARY KEY,
    code TEXT,
    name TEXT NOT NULL,
    category TEXT,
    scenario TEXT,
    value_point TEXT,
    owner_sfr_id INTEGER,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','filled','shared')),
    filled_date TEXT,
    shared_date TEXT,
    FOREIGN KEY (owner_sfr_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS learning_records (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    feature_id INTEGER NOT NULL,
    learned_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
    UNIQUE(user_id, feature_id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (feature_id) REFERENCES features(id)
);
CREATE TABLE IF NOT EXISTS quizzes (
    id SERIAL PRIMARY KEY,
    feature_id INTEGER,
    question TEXT NOT NULL,
    answer_hint TEXT,
    sfr_id INTEGER NOT NULL,
    status TEXT DEFAULT 'active' CHECK(status IN ('active','closed')),
    created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
    closed_at TEXT,
    published_at TEXT,
    deadline TEXT,
    duration_min INTEGER DEFAULT 30,
    auto_generated INTEGER DEFAULT 0,
    judge_mode TEXT DEFAULT 'manual',
    quiz_type TEXT DEFAULT 'qa',
    options TEXT,
    correct_answer TEXT,
    full_score INTEGER DEFAULT 1,
    FOREIGN KEY (feature_id) REFERENCES features(id),
    FOREIGN KEY (sfr_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS answers (
    id SERIAL PRIMARY KEY,
    quiz_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    answered_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
    is_correct INTEGER,
    judged_by INTEGER,
    judged_at TEXT,
    judge_reason TEXT,
    auto_judged INTEGER DEFAULT 0,
    score INTEGER DEFAULT 0,
    UNIQUE(quiz_id, user_id),
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (judged_by) REFERENCES users(id)
);
"""


class _DB:
    """兼容 SQLite / Postgres 的连接封装：业务代码统一用 ? 占位符，
    Postgres 下自动把 ? 转成 %s；按列名取数两种方言均支持。"""

    def __init__(self, raw, dialect, cur_factory=None):
        self._raw = raw
        self.dialect = dialect
        self._cur_factory = cur_factory

    def execute(self, sql, params=()):
        if self.dialect == "pg":
            sql = sql.replace("?", "%s")
        cur = self._raw.cursor(cursor_factory=self._cur_factory) if self._cur_factory \
            else self._raw.cursor()
        cur.execute(sql, tuple(params))
        return cur

    def commit(self):
        self._raw.commit()

    def close(self):
        try:
            self._raw.close()
        except Exception:
            pass


def _make_raw():
    """返回一个 (raw_conn, dialect, cur_factory) 三元组。"""
    if USE_PG and psycopg2 is not None:
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return psycopg2.connect(url), "pg", DictCursor
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn, "sqlite", None


def _split_statements(schema):
    out = []
    for part in schema.split(";"):
        part = part.strip()
        if part:
            out.append(part)
    return out


_DB_INITIALIZED = False


def _ensure_db():
    """延迟且容错的库初始化：首次请求时建表+灌示例数据；连不上也不崩溃，下次请求重试。"""
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
    try:
        init_db()
        seed_data()
        _DB_INITIALIZED = True
    except Exception as e:
        app.logger.error("数据库初始化失败（将在下次请求重试）: %s", e)


def get_db():
    if "db" not in g:
        _ensure_db()
        raw, dialect, cur_factory = _make_raw()
        g.db = _DB(raw, dialect, cur_factory)
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    raw, dialect, cur_factory = _make_raw()
    try:
        if dialect == "pg":
            for stmt in _split_statements(SCHEMA_PG):
                cur = raw.cursor(cursor_factory=cur_factory) if cur_factory else raw.cursor()
                cur.execute(stmt)
        else:
            raw.executescript(SCHEMA)
        raw.commit()
    finally:
        raw.close()
    migrate_db()


def migrate_db():
    """为已存在的库补充新列，保证升级后兼容（两套方言分别处理）。"""
    raw, dialect, cur_factory = _make_raw()
    try:
        if dialect == "pg":
            cur = raw.cursor()
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='quizzes'")
            q_cols = [r[0] for r in cur.fetchall()]
            for col, ddl in [
                ("published_at", "TEXT"),
                ("deadline", "TEXT"),
                ("duration_min", "INTEGER DEFAULT 30"),
                ("auto_generated", "INTEGER DEFAULT 0"),
                ("judge_mode", "TEXT DEFAULT 'manual'"),
                ("quiz_type", "TEXT DEFAULT 'qa'"),
                ("options", "TEXT"),
                ("correct_answer", "TEXT"),
                ("full_score", "INTEGER DEFAULT 1"),
            ]:
                if col not in q_cols:
                    try:
                        raw.cursor().execute(f"ALTER TABLE quizzes ADD COLUMN {col} {ddl}")
                    except Exception:
                        pass
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='answers'")
            a_cols = [r[0] for r in cur.fetchall()]
            for col, ddl in [("judge_reason", "TEXT"), ("auto_judged", "INTEGER DEFAULT 0"),
                            ("correct_answer", "TEXT"), ("wrong_summary", "TEXT"),
                            ("score", "INTEGER DEFAULT 0")]:
                if col not in a_cols:
                    try:
                        raw.cursor().execute(f"ALTER TABLE answers ADD COLUMN {col} {ddl}")
                    except Exception:
                        pass
        else:
            conn = raw
            conn.row_factory = sqlite3.Row
            q_cols = [r["name"] for r in conn.execute("PRAGMA table_info(quizzes)")]
            for col, ddl in [
                ("published_at", "TEXT"),
                ("deadline", "TEXT"),
                ("duration_min", "INTEGER DEFAULT 30"),
                ("auto_generated", "INTEGER DEFAULT 0"),
                ("judge_mode", "TEXT DEFAULT 'manual'"),
                ("quiz_type", "TEXT DEFAULT 'qa'"),
                ("options", "TEXT"),
                ("correct_answer", "TEXT"),
                ("full_score", "INTEGER DEFAULT 1"),
            ]:
                if col not in q_cols:
                    try:
                        conn.execute(f"ALTER TABLE quizzes ADD COLUMN {col} {ddl}")
                    except Exception:
                        pass
            a_cols = [r["name"] for r in conn.execute("PRAGMA table_info(answers)")]
            for col, ddl in [("judge_reason", "TEXT"), ("auto_judged", "INTEGER DEFAULT 0"),
                            ("correct_answer", "TEXT"), ("wrong_summary", "TEXT"),
                            ("score", "INTEGER DEFAULT 0")]:
                if col not in a_cols:
                    try:
                        conn.execute(f"ALTER TABLE answers ADD COLUMN {col} {ddl}")
                    except Exception:
                        pass
        raw.commit()
    finally:
        raw.close()


# ---------- 辅助函数 ----------

def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("uid"):
            return redirect(url_for("login_page"))
        return fn(*args, **kwargs)
    return wrapper


def require_role(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            u = current_user()
            if not u or u["role"] not in roles:
                flash("权限不足", "danger")
                return redirect(url_for("index"))
            return fn(*args, **kwargs)
        return wrapper
    return deco


def stats():
    db = get_db()
    total = db.execute("SELECT COUNT(*) c FROM features").fetchone()["c"]
    filled = db.execute("SELECT COUNT(*) c FROM features WHERE status IN ('filled','shared')").fetchone()["c"]
    shared = db.execute("SELECT COUNT(*) c FROM features WHERE status='shared'").fetchone()["c"]
    sfrs = db.execute("SELECT COUNT(*) c FROM users WHERE role='sfr'").fetchone()["c"]
    biz = db.execute("SELECT COUNT(*) c FROM users WHERE role='business'").fetchone()["c"]
    learned_total = db.execute("SELECT COUNT(*) c FROM learning_records").fetchone()["c"]
    active_quiz = db.execute("SELECT COUNT(*) c FROM quizzes WHERE status='active'").fetchone()["c"]
    return dict(total=total, filled=filled, shared=shared,
                sfrs=sfrs, business=biz, learned_total=learned_total,
                active_quiz=active_quiz,
                fill_rate=round(filled / total * 100, 1) if total else 0,
                share_rate=round(shared / total * 100, 1) if total else 0)


# ---------- 路由：登录 / 注册 / 退出 ----------

@app.route("/login", methods=["GET", "POST"])
def login_page():
    """登录页：已注册用户填写姓名即可进入，身份由管理员预先设定。"""
    if session.get("uid"):
        return redirect(url_for("index"))
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("请输入姓名", "danger")
            return render_template("login.html")
        db = get_db()
        u = db.execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()
        if not u:
            flash("未找到该用户，请联系管理员创建账号", "danger")
            return render_template("login.html")
        session["uid"] = u["id"]
        flash(f"已登录：{u['name']}", "info")
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ---------- 路由：仪表盘 ----------

@app.route("/")
@require_login
def index():
    u = current_user()
    s = stats()
    db = get_db()
    # SFR 个人进度
    my_filled = my_shared = my_pending = 0
    if u and u["role"] == "sfr":
        row = db.execute("""SELECT
            SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) pending,
            SUM(CASE WHEN status='filled' THEN 1 ELSE 0 END) filled,
            SUM(CASE WHEN status='shared' THEN 1 ELSE 0 END) shared
            FROM features WHERE owner_sfr_id=?""", (u["id"],)).fetchone()
        my_pending, my_filled, my_shared = row["pending"] or 0, row["filled"] or 0, row["shared"] or 0
    # 商务学习进度
    my_learned = 0
    if u and u["role"] == "business":
        my_learned = db.execute(
            "SELECT COUNT(*) c FROM learning_records WHERE user_id=?", (u["id"],)).fetchone()["c"]
    # 排行榜 top5（总分 = SUM(score)；多选按命中分，问答对1分）
    ranking = db.execute("""
        SELECT u.name, SUM(COALESCE(a.score,0)) score
        FROM users u LEFT JOIN answers a ON a.user_id=u.id
        WHERE u.role='business'
        GROUP BY u.id ORDER BY score DESC, u.name LIMIT 5
    """).fetchall()
    return render_template("index.html", s=s, u=u, my_pending=my_pending,
                           my_filled=my_filled, my_shared=my_shared,
                           my_learned=my_learned, ranking=ranking)


# ---------- 路由：用户管理（仅管理员） ----------

@app.route("/users")
@require_role("admin")
def users_page():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY role, name").fetchall()
    return render_template("users.html", users=users, u=current_user())


@app.route("/users/add", methods=["POST"])
@require_role("admin")
def add_user():
    name = request.form.get("name", "").strip()
    role = request.form.get("role", "business")
    if not name:
        flash("请输入姓名", "danger")
        return redirect(url_for("users_page"))
    if role not in ("sfr", "business", "admin"):
        role = "business"
    db = get_db()
    try:
        db.execute("INSERT INTO users(name,role) VALUES(?,?)", (name, role))
        db.commit()
        flash(f"已添加用户 {name}", "success")
    except (sqlite3.IntegrityError, PgIntegrityError):
        flash("用户名已存在", "danger")
    return redirect(url_for("users_page"))


@app.route("/users/<int:uid>/role", methods=["POST"])
@require_role("admin")
def change_role(uid):
    """管理员修改用户身份（SFR/商务AR/管理员）。用户自己选错身份时由管理员在此纠正。"""
    new_role = request.form.get("role", "").strip()
    if new_role not in ("sfr", "business", "admin"):
        flash("非法身份", "danger")
        return redirect(url_for("users_page"))
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not target:
        flash("用户不存在", "danger")
        return redirect(url_for("users_page"))
    db.execute("UPDATE users SET role=? WHERE id=?", (new_role, uid))
    db.commit()
    flash(f"已将 {target['name']} 的身份修改为 {('SFR' if new_role=='sfr' else '商务AR' if new_role=='business' else '管理员')}", "success")
    return redirect(url_for("users_page"))


@app.route("/users/<int:uid>/delete")
@require_role("admin")
def delete_user(uid):
    db = get_db()
    # 防止管理员删掉自己导致无人可管理
    if uid == session.get("uid"):
        flash("不能删除当前登录的管理员账号", "danger")
        return redirect(url_for("users_page"))
    # 先清理所有关联数据，避免外键约束报错
    db.execute("DELETE FROM answers WHERE user_id=?", (uid,))
    db.execute("DELETE FROM answers WHERE quiz_id IN (SELECT id FROM quizzes WHERE sfr_id=?)", (uid,))
    db.execute("DELETE FROM quizzes WHERE sfr_id=?", (uid,))
    db.execute("DELETE FROM learning_records WHERE user_id=?", (uid,))
    db.execute("UPDATE features SET owner_sfr_id=NULL WHERE owner_sfr_id=?", (uid,))
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit()
    flash("已删除用户", "info")
    return redirect(url_for("users_page"))


# ---------- 路由：功能清单 ----------

def format_content(text):
    """自动整理功能场景/价值文本的格式：
    - 去除首尾空白与多余空行
    - 将「≥3 个连续换行」压缩为单空行
    - 去除粘贴进来的常见噪音（邮箱签名、横线、大括号包裹等）
    - 行内行首多余空格规整
    """
    if not text:
        return ""
    t = text.strip()
    # 压缩多余连续空行（最多保留 1 个空行）
    t = re.sub(r"\n{3,}", "\n\n", t)
    # 每行去掉行首行尾多余空白
    lines = [ln.rstrip() for ln in t.splitlines()]
    t = "\n".join(lines)
    # 再去一次连续空行（去除行尾空白后再压缩）
    t = re.sub(r"\n{3,}", "\n\n", t)
    # 清理常见的粘贴噪音：孤立分隔线或 emoji-only 行
    lines = []
    for ln in t.splitlines():
        s = ln.strip()
        if re.fullmatch(r"[-=*_—~•·#]{3,}", s):      # 孤立的分隔线
            continue
        if re.fullmatch(r"[─━═─=]+", s):             # 粗横线
            continue
        lines.append(ln)
    t = "\n".join(lines).strip()
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t


@app.route("/features")
@require_login
def features_page():
    db = get_db()
    u = current_user()
    q = request.args.get("q", "").strip()
    cat = request.args.get("cat", "").strip()
    status = request.args.get("status", "").strip()
    mine = request.args.get("mine", "").strip()
    tab = request.args.get("tab", "").strip()   # all / unshared / shared
    owner = request.args.get("owner", "").strip()  # 负责人筛选："unclaimed" 或用户 id
    sql = """SELECT f.*, u.name owner_name FROM features f
             LEFT JOIN users u ON u.id=f.owner_sfr_id WHERE 1=1"""
    args = []
    if q:
        sql += " AND (f.name LIKE ? OR f.code LIKE ? OR f.scenario LIKE ?)"
        kw = f"%{q}%"
        args += [kw, kw, kw]
    if cat:
        sql += " AND f.category=?"
        args.append(cat)
    # tab 与 status 二选一优先（tab 是顶部快捷切换）
    if tab == "unshared":
        sql += " AND f.status != 'shared'"
    elif tab == "shared":
        sql += " AND f.status='shared'"
    elif status:
        sql += " AND f.status=?"
        args.append(status)
    if mine == "1" and u and u["role"] == "sfr":
        sql += " AND f.owner_sfr_id=?"
        args.append(u["id"])
    if owner:
        if owner == "unclaimed":
            sql += " AND f.owner_sfr_id IS NULL"
        elif owner.isdigit():
            sql += " AND f.owner_sfr_id=?"
            args.append(int(owner))
    sql += " ORDER BY f.code, f.id"
    rows = db.execute(sql, args).fetchall()
    cats = [r["category"] for r in db.execute(
        "SELECT DISTINCT category FROM features WHERE category IS NOT NULL").fetchall()]
    # 负责人下拉选项：所有 SFR + "未认领"
    owners = [r for r in db.execute(
        "SELECT id,name FROM users WHERE role='sfr' ORDER BY name").fetchall()]
    # SFR 未认领数量（用于顶部提示）
    unclaimed = 0
    if u and u["role"] == "sfr":
        unclaimed = db.execute(
            "SELECT COUNT(*) c FROM features WHERE owner_sfr_id IS NULL OR owner_sfr_id!=?",
            (u["id"],)).fetchone()["c"]
        my_count = db.execute(
            "SELECT COUNT(*) c FROM features WHERE owner_sfr_id=?", (u["id"],)).fetchone()["c"]
    else:
        my_count = 0
    # 用于顶部 tab 角标
    unshared_count = db.execute(
        "SELECT COUNT(*) c FROM features WHERE status != 'shared'").fetchone()["c"]
    shared_count = db.execute(
        "SELECT COUNT(*) c FROM features WHERE status='shared'").fetchone()["c"]
    return render_template("features.html", features=rows, cats=cats, owners=owners,
                           q=q, cat=cat, status=status, mine=mine, tab=tab, owner=owner,
                           unclaimed=unclaimed, my_count=my_count, u=u,
                           unshared_count=unshared_count, shared_count=shared_count)


@app.route("/features/import", methods=["GET", "POST"])
@require_role("admin")
def import_features():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            # 手动添加
            code = request.form.get("code", "").strip()
            name = request.form.get("name", "").strip()
            category = request.form.get("category", "").strip()
            if not name:
                flash("功能名称必填", "danger")
                return redirect(url_for("import_features"))
            db = get_db()
            db.execute("INSERT INTO features(code,name,category) VALUES(?,?,?)",
                       (code or None, name, category or None))
            db.commit()
            flash(f"已添加功能：{name}", "success")
            return redirect(url_for("import_features"))
        # Excel 导入
        from openpyxl import load_workbook
        path = os.path.join(UPLOAD_DIR, f"import_{datetime.now().strftime('%H%M%S')}.xlsx")
        f.save(path)
        wb = load_workbook(path)
        ws = wb.active
        db = get_db()
        cnt = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or not row[0]:
                continue
            code = str(row[0]).strip() if len(row) > 0 else None
            name = str(row[1]).strip() if len(row) > 1 and row[1] else None
            category = str(row[2]).strip() if len(row) > 2 and row[2] else None
            if not name:
                continue
            try:
                db.execute("INSERT INTO features(code,name,category) VALUES(?,?,?)",
                           (code, name, category))
                cnt += 1
            except Exception:
                pass
        db.commit()
        flash(f"成功导入 {cnt} 个功能", "success")
        return redirect(url_for("features_page"))
    return render_template("import.html", u=current_user())


@app.route("/features/<int:fid>")
def feature_detail(fid):
    db = get_db()
    f = db.execute("SELECT f.*, u.name owner_name FROM features f LEFT JOIN users u ON u.id=f.owner_sfr_id WHERE f.id=?", (fid,)).fetchone()
    if not f:
        flash("功能不存在", "danger")
        return redirect(url_for("features_page"))
    learned = db.execute("SELECT lr.*, u.name FROM learning_records lr JOIN users u ON u.id=lr.user_id WHERE lr.feature_id=?", (fid,)).fetchall()
    return render_template("feature_detail.html", f=f, learned=learned, u=current_user())


@app.route("/features/<int:fid>/assign", methods=["POST"])
def assign_feature(fid):
    sfr_id = request.form.get("sfr_id", type=int)
    db = get_db()
    db.execute("UPDATE features SET owner_sfr_id=? WHERE id=?", (sfr_id, fid))
    db.commit()
    flash("已分配", "success")
    return redirect(url_for("features_page"))


@app.route("/features/<int:fid>/claim", methods=["POST"])
@require_role("sfr", "admin")
def claim_feature(fid):
    u = current_user()
    db = get_db()
    f = db.execute("SELECT * FROM features WHERE id=?", (fid,)).fetchone()
    if not f:
        flash("功能不存在", "danger")
        return redirect(url_for("features_page"))
    db.execute("UPDATE features SET owner_sfr_id=? WHERE id=?", (u["id"], fid))
    db.commit()
    flash(f"已认领「{f['name']}」，负责人已更新为 {u['name']}", "success")
    return redirect(request.referrer or url_for("features_page"))


@app.route("/features/<int:fid>/delete")
def delete_feature(fid):
    db = get_db()
    # 先清理关联数据，避免外键约束报错
    db.execute("DELETE FROM answers WHERE quiz_id IN (SELECT id FROM quizzes WHERE feature_id=?)", (fid,))
    db.execute("DELETE FROM quizzes WHERE feature_id=?", (fid,))
    db.execute("DELETE FROM learning_records WHERE feature_id=?", (fid,))
    db.execute("DELETE FROM features WHERE id=?", (fid,))
    db.commit()
    flash("已删除功能", "info")
    return redirect(url_for("features_page"))


@app.route("/features/clear-all")
@require_role("admin")
def clear_all_features():
    db = get_db()
    # 按外键依赖顺序删除：先子表后主表
    db.execute("DELETE FROM answers")
    db.execute("DELETE FROM quizzes")
    db.execute("DELETE FROM learning_records")
    db.execute("DELETE FROM features")
    db.commit()
    flash("已清空全部功能及相关数据", "warning")
    return redirect(url_for("features_page"))


# ---------- 路由：SFR 认领/填写（已集成到功能清单） ----------

@app.route("/fill/<int:fid>", methods=["GET", "POST"])
@require_login
def fill_form(fid):
    u = current_user()
    db = get_db()
    f = db.execute("SELECT * FROM features WHERE id=?", (fid,)).fetchone()
    if not f:
        flash("功能不存在", "danger")
        return redirect(url_for("features_page"))
    if request.method == "POST":
        scenario = format_content(request.form.get("scenario", ""))
        value_point = format_content(request.form.get("value_point", ""))
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        if scenario and value_point:
            # 填写完成 → 自动分享到学习中心（跳过 filled 中间态）
            status = "shared"
            filled_date = now_str
            shared_date = f["shared_date"] or now_str  # 首次分享才记日期
        else:
            status = "pending"
            filled_date = None
            shared_date = f["shared_date"]  # 保留原值
        # SFR/管理员填写时，负责人自动变为填写者
        if u and u["role"] in ("sfr", "admin"):
            db.execute("""UPDATE features SET scenario=?, value_point=?, status=?,
                                      filled_date=?, shared_date=?, owner_sfr_id=? WHERE id=?""",
                       (scenario, value_point, status, filled_date, shared_date, u["id"], fid))
            if not f["owner_sfr_id"]:
                flash("已保存并自动同步到学习中心，您已成为该功能负责人", "success")
            else:
                flash("已保存并自动同步到学习中心，负责人已更新为 " + u["name"], "success")
        else:
            db.execute("""UPDATE features SET scenario=?, value_point=?, status=?, filled_date=?, shared_date=?
                          WHERE id=?""", (scenario, value_point, status, filled_date, shared_date, fid))
            flash("已保存并自动同步到学习中心", "success")
        db.commit()
        return redirect(url_for("features_page"))
    return render_template("fill_form.html", f=f, u=u)


@app.route("/features/<int:fid>/share", methods=["POST"])
@require_role("sfr", "admin")
def share_feature(fid):
    db = get_db()
    f = db.execute("SELECT * FROM features WHERE id=?", (fid,)).fetchone()
    if not f:
        flash("功能不存在", "danger")
        return redirect(url_for("features_page"))
    if f["status"] != "shared":
        db.execute("UPDATE features SET status='shared', shared_date=? WHERE id=?",
                   (datetime.now().strftime("%Y-%m-%d %H:%M"), fid))
        db.commit()
        flash("已标记为已分享，去生成群分享文案吧", "success")
    return redirect(url_for("share_text", fid=fid))


@app.route("/features/batch-share", methods=["POST"])
@require_role("sfr", "admin")
def batch_share():
    fids_raw = request.form.getlist("fids")
    fids = [int(x) for x in fids_raw if x.isdigit()]
    if not fids:
        flash("请先勾选要分享的功能", "warning")
        return redirect(url_for("features_page"))
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for fid in fids:
        db.execute("UPDATE features SET status='shared', shared_date=? WHERE id=? AND status!='shared'",
                   (now, fid))
    db.commit()
    placeholders = ",".join("?" for _ in fids)
    feats = db.execute(
        f"""SELECT f.*, u.name owner_name FROM features f
            LEFT JOIN users u ON u.id=f.owner_sfr_id
            WHERE f.id IN ({placeholders}) ORDER BY f.code, f.id""", fids).fetchall()
    flash(f"已标记 {len(feats)} 个功能为已分享", "success")
    feats_json = json.dumps([{
        "id": r["id"],
        "name": r["name"],
        "category": r["category"] or "",
        "scenario": r["scenario"] or "（待填写）",
        "value": r["value_point"] or "（待填写）",
        "owner": r["owner_name"] or "-",
    } for r in feats], ensure_ascii=False)
    return render_template("share_batch.html", features=feats, u=current_user(),
                           features_json=feats_json)


@app.route("/share-text/<int:fid>")
def share_text(fid):
    db = get_db()
    f = db.execute("SELECT f.*, u.name owner_name FROM features f LEFT JOIN users u ON u.id=f.owner_sfr_id WHERE f.id=?", (fid,)).fetchone()
    if not f:
        return "功能不存在", 404
    return render_template("share_text.html", f=f, u=current_user())


# ---------- 路由：商务学习中心 ----------

@app.route("/learn")
@require_login
def learn_page():
    u = current_user()
    db = get_db()
    rows = db.execute("""SELECT f.*, u.name owner_name,
        (SELECT 1 FROM learning_records lr WHERE lr.user_id=? AND lr.feature_id=f.id) learned
        FROM features f LEFT JOIN users u ON u.id=f.owner_sfr_id
        WHERE f.status='shared' ORDER BY f.shared_date DESC, f.id""", (u["id"],)).fetchall()
    total_shared = len(rows)
    my_learned = sum(1 for r in rows if r["learned"])
    return render_template("learn.html", features=rows, u=u,
                           total_shared=total_shared, my_learned=my_learned)


@app.route("/learn/<int:fid>/toggle", methods=["POST"])
@require_role("business", "admin")
def toggle_learn(fid):
    u = current_user()
    db = get_db()
    exists = db.execute("SELECT id FROM learning_records WHERE user_id=? AND feature_id=?", (u["id"], fid)).fetchone()
    if exists:
        db.execute("DELETE FROM learning_records WHERE id=?", (exists["id"],))
        db.commit()
        return jsonify({"learned": False})
    else:
        db.execute("INSERT INTO learning_records(user_id,feature_id) VALUES(?,?)", (u["id"], fid))
        db.commit()
        return jsonify({"learned": True})


# ---------- 路由：考核抢答 ----------

def quiz_options_list(q):
    """将 quizzes.options 字段解析为 [(下标, 字母, 选项文本), ...]"""
    opts = [o for o in (q["options"] or "").split("|||") if o]
    return [(i, chr(ord("A") + i), o) for i, o in enumerate(opts)]

@app.route("/quiz")
@require_login
def quiz_page():
    u = current_user()
    db = get_db()
    rows = db.execute("""SELECT q.*, f.name feature_name, u.name sfr_name,
        (SELECT COUNT(*) FROM answers a WHERE a.quiz_id=q.id) ans_count,
        (SELECT COUNT(*) FROM answers a WHERE a.quiz_id=q.id AND a.is_correct=1) correct_count
        FROM quizzes q
        LEFT JOIN features f ON f.id=q.feature_id
        JOIN users u ON u.id=q.sfr_id
        ORDER BY q.status, q.created_at DESC""").fetchall()
    quizzes = []
    for r in rows:
        d = dict(r)
        dl_ts = None
        if r["deadline"]:
            try:
                dl_ts = int(datetime.strptime(r["deadline"], "%Y-%m-%d %H:%M:%S").timestamp())
            except Exception:
                pass
        d["deadline_ts"] = dl_ts
        d["options_list"] = quiz_options_list(r) if (r["quiz_type"] or "qa") == "multi" else []
        quizzes.append(d)
    # 商务待答题
    my_answers = {}
    if u["role"] == "business":
        ar = db.execute("SELECT quiz_id, is_correct, score FROM answers WHERE user_id=?", (u["id"],)).fetchall()
        my_answers = {r["quiz_id"]: {"correct": r["is_correct"], "score": r["score"]} for r in ar}
    return render_template("quiz.html", quizzes=quizzes, u=u, my_answers=my_answers,
                           now_ts=int(datetime.now().timestamp()))


@app.route("/api/generate-quiz")
@require_role("sfr", "admin")
def api_generate_quiz():
    fid = request.args.get("feature_id", type=int)
    qtype = request.args.get("type", "multi")
    db = get_db()
    f = db.execute("SELECT name,scenario,value_point FROM features WHERE id=?", (fid,)).fetchone()
    if not f:
        return jsonify({"error": "功能不存在"}), 404
    if not (f["value_point"] or f["scenario"]):
        return jsonify({"error": "该功能尚未填写场景/价值，无法生成考题"}), 400
    try:
        from ai_helper import ai_generate_quiz
        res = ai_generate_quiz(f["name"], f["scenario"], f["value_point"], qtype=qtype)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not res or not res.get("question"):
        return jsonify({"error": "生成失败，请手动填写"}), 500
    if qtype == "multi":
        return jsonify({
            "question": res["question"],
            "reference": res.get("reference", ""),
            "options": res.get("options", []),
            "correct_answer": res.get("correct_answer", ""),
        })
    return jsonify({"question": res["question"], "reference": res.get("reference", "")})


@app.route("/quiz/auto-generate", methods=["POST"])
@require_role("sfr", "admin")
def quiz_auto_generate():
    """系统批量自动出题：按功能清单中已填写场景/价值的功能，为每个功能自动生成一道多选题并发布。
    可选 fids（勾选指定功能）；不传则对全部已填写且尚未有进行中判断题的功能生成。"""
    db = get_db()
    fids = [int(x) for x in request.form.getlist("fids") if x.isdigit()]
    if fids:
        ph = ",".join("?" for _ in fids)
        feats = db.execute(
            f"SELECT id,name,scenario,value_point FROM features WHERE id IN ({ph}) "
            f"AND (value_point IS NOT NULL AND value_point!='')", fids).fetchall()
    else:
        feats = db.execute(
            """SELECT id,name,scenario,value_point FROM features
               WHERE (value_point IS NOT NULL AND value_point!='')
                 AND id NOT IN (SELECT DISTINCT feature_id FROM quizzes WHERE feature_id IS NOT NULL AND status='active')
               ORDER BY name""").fetchall()
    if not feats:
        flash("没有可自动出题的功能（需先填写场景/价值，或都已出过进行中的题）", "warning")
        return redirect(url_for("quiz_page"))

    u = current_user()
    now = datetime.now()
    duration = 30
    deadline = now + timedelta(minutes=duration)
    made, failed = 0, 0
    try:
        from ai_helper import ai_generate_quiz
    except Exception:
        ai_generate_quiz = None
    for f in feats:
        q = None
        if ai_generate_quiz:
            try:
                q = ai_generate_quiz(f["name"], f["scenario"] or "", f["value_point"] or "", qtype="multi")
            except Exception:
                q = None
        if not q or not q.get("question") or not q.get("options"):
            failed += 1
            continue
        opts = [o for o in q.get("options", [])][:4]
        while len(opts) < 4:
            opts.append("（选项）")
        ca = "".join(c for c in (q.get("correct_answer") or "").upper() if c in "ABCD")
        if not ca:
            ca = "A"
        full_score = len(ca)
        options_text = "|||".join(opts)
        db.execute("""INSERT INTO quizzes(feature_id,question,answer_hint,sfr_id,status,
                                    published_at,deadline,duration_min,auto_generated,judge_mode,
                                    quiz_type,options,correct_answer,full_score)
                      VALUES(?,?,?,?,'active',?,?,?,1,'ai','multi',?,?,?)""",
                   (f["id"], q["question"], q.get("reference", ""), u["id"],
                    now.strftime("%Y-%m-%d %H:%M:%S"), deadline.strftime("%Y-%m-%d %H:%M:%S"),
                    duration, options_text, ca, full_score))
        made += 1
    db.commit()
    flash(f"系统自动出题完成：成功发布 {made} 道多选题" + (f"，{failed} 道生成失败" if failed else "") + "（满分=正确答案数），30分钟内可抢答", "success")
    return redirect(url_for("quiz_page"))


@app.route("/health")
def health_check():
    """Render 健康检查：不依赖登录、不访问 AI，确保负载均衡认为服务已就绪。"""
    return "ok", 200


@app.route("/api/ai-test")
def api_ai_test():
    """AI 配置自检：浏览器直接访问即可看到 AI 是否可用及失败原因。"""
    try:
        from ai_helper import ai_test
        return jsonify(ai_test())
    except BaseException as e:
        return jsonify({"ok": False, "msg": f"自检接口异常: {type(e).__name__}: {e}"}), 200


@app.route("/api/ai-status")
def api_ai_status():
    """返回最近一次 AI 调用状态（不发起新请求）。"""
    from ai_helper import get_ai_status
    return jsonify(get_ai_status())


@app.route("/api/ai-gen-scenario/<int:fid>")
@require_role("sfr", "admin")
def api_ai_gen_scenario(fid):
    """AI 辅助生成功能的使用场景和价值点，供 SFR 参考。"""
    try:
        db = get_db()
        f = db.execute("SELECT name, category, code FROM features WHERE id=?", (fid,)).fetchone()
        if not f:
            return jsonify({"ok": False, "msg": "功能不存在"})
        from ai_helper import ai_generate_scenario_value
        result = ai_generate_scenario_value(f["name"], f["category"] or "", f["code"] or "")
        if result.get("scenario") or result.get("value_point"):
            return jsonify({"ok": True, "data": result})
        else:
            return jsonify({
                "ok": False,
                "msg": "AI 生成失败，请检查 AI 配置或手动填写",
                "detail": result.get("error", "未知原因")
            })
    except BaseException as e:
        return jsonify({"ok": False, "msg": f"生成异常: {type(e).__name__}: {e}"}), 200


@app.route("/quiz/create", methods=["GET", "POST"])
@require_role("sfr", "admin")
def quiz_create():
    db = get_db()
    if request.method == "POST":
        feature_id = request.form.get("feature_id", type=int)
        question = request.form.get("question", "").strip()
        answer_hint = request.form.get("answer_hint", "").strip()
        quiz_type = request.form.get("quiz_type", "qa").strip() or "qa"
        if not feature_id:
            flash("请选择关联功能", "danger")
            return redirect(url_for("quiz_create"))
        if not question:
            flash("考题不能为空", "danger")
            return redirect(url_for("quiz_create"))
        # 收集多选题选项与正确答案
        options_text, correct_answer = None, None
        if quiz_type == "multi":
            opts = []
            for i in range(1, 5):
                o = request.form.get(f"opt{i}", "").strip()
                if o:
                    opts.append(o)
            ca_raw = request.form.get("correct_answer", "").strip().upper()
            # 标准化："A,C" / "AC" / "A C" / "1,2" 统一为字母
            ca_letters = []
            for token in re.split(r"[\s,，;；、]+", ca_raw):
                token = token.strip().upper()
                if not token:
                    continue
                if token.isalpha():
                    ca_letters.append(token)
                elif token.isdigit():
                    idx = int(token) - 1
                    if 0 <= idx < 4:
                        ca_letters.append(chr(ord("A") + idx))
            if len(opts) < 2:
                flash("多选题至少需要 2 个选项", "danger")
                return redirect(url_for("quiz_create"))
            if not ca_letters:
                flash("请填写多选题的正确答案（如 A,C 或 1,3）", "danger")
                return redirect(url_for("quiz_create"))
            # 去重、排序
            seen = set(); letters = []
            for c in ca_letters:
                if c not in seen and c in "ABCD":
                    seen.add(c); letters.append(c)
            letters.sort()
            options_text = "|||".join(opts)
            correct_answer = "".join(letters)
        u = current_user()
        now = datetime.now()
        duration = 30
        deadline = now + timedelta(minutes=duration)
        if quiz_type == "multi":
            full_score = len(correct_answer) if correct_answer else 1
        else:
            full_score = 1
        db.execute("""INSERT INTO quizzes(feature_id,question,answer_hint,sfr_id,status,
                                    published_at,deadline,duration_min,auto_generated,judge_mode,
                                    quiz_type,options,correct_answer,full_score)
                      VALUES(?,?,?,?,'active',?,?,?,1,'ai',?,?,?,?)""",
                   (feature_id, question, answer_hint, u["id"],
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    deadline.strftime("%Y-%m-%d %H:%M:%S"), duration,
                    quiz_type, options_text, correct_answer, full_score))
        db.commit()
        typ_label = "多选题" if quiz_type == "multi" else "问答题"
        flash(f"已发布 {typ_label}（{'满分'+str(full_score)+'分' if quiz_type=='multi' else '1分'}，30分钟内可抢答）", "success")
        return redirect(url_for("quiz_page"))
    features = db.execute(
        "SELECT * FROM features WHERE value_point IS NOT NULL AND value_point!='' ORDER BY name").fetchall()
    return render_template("quiz_create.html", features=features, u=current_user())


@app.route("/quiz/<int:qid>/answer", methods=["POST"])
@require_role("business", "admin")
def quiz_answer(qid):
    u = current_user()
    db = get_db()
    q = db.execute("SELECT * FROM quizzes WHERE id=?", (qid,)).fetchone()
    if not q or q["status"] != "active":
        flash("该题已关闭", "warning")
        return redirect(url_for("quiz_page"))
    # 30 分钟限时检查
    if q["deadline"]:
        try:
            dl = datetime.strptime(q["deadline"], "%Y-%m-%d %H:%M:%S")
            if datetime.now() > dl:
                flash(f"已超过作答时限（{q['duration_min'] or 30}分钟），无法抢答", "warning")
                return redirect(url_for("quiz_page"))
        except Exception:
            pass
    exists = db.execute("SELECT id FROM answers WHERE quiz_id=? AND user_id=?", (qid, u["id"])).fetchone()
    if exists:
        flash("你已经回答过这道题了", "warning")
        return redirect(url_for("quiz_page"))

    quiz_type = q["quiz_type"] or "qa"
    # ---------- 多选题：勾选选项作答 ----------
    if quiz_type == "multi":
        selected = request.form.getlist("choice")   # 值形如 "0"、"1"，对应选项下标
        options = [o for o in (q["options"] or "").split("|||") if o]
        if not selected:
            flash("请至少选择一个选项", "danger")
            return redirect(url_for("quiz_page"))
        # 下标 → 字母 A/B/C/D
        letters = []
        for s in selected:
            if s.isdigit() and int(s) < len(options):
                letters.append(chr(ord("A") + int(s)))
        letters = sorted(set(letters))
        user_letters = "".join(letters)
        # 构建展示文本（如 "A.xxx / C.yyy"）
        display_parts = []
        for s in sorted(set(selected), key=lambda x: int(x) if x.isdigit() else 0):
            if s.isdigit() and int(s) < len(options):
                idx = int(s)
                display_parts.append(f"{chr(ord('A')+idx)}. {options[idx]}")
        content = " / ".join(display_parts) if display_parts else user_letters
        db.execute("INSERT INTO answers(quiz_id,user_id,content) VALUES(?,?,?)", (qid, u["id"], content))
        db.commit()
        # 分值制：正确答案集合 full；答题选的正确项命中数 = 得分（每个正确答案1分）
        correct_set = set((q["correct_answer"] or "").upper()) - set(" \t,，;；")
        user_set = set(user_letters)
        hit = len(correct_set & user_set)          # 命中的正确答案数
        full_score = int(q["full_score"] or 1)      # 全对满分（=正确答案数）
        if full_score <= 0:
            full_score = len(correct_set) or 1
        score = hit
        correct = 1 if score == full_score else 0   # 全对才标记为"答对"
        ca_text = "".join(sorted(correct_set))
        reason = f"本题满分 {full_score} 分（每正确选项1分）。正确答案 {ca_text}，你选择了 {user_letters or '无'}，命中 {hit} 分。"
        db.execute("""UPDATE answers SET is_correct=?, score=?, judge_reason=?, auto_judged=1
                      WHERE quiz_id=? AND user_id=?""",
                   (correct, score, reason, qid, u["id"]))
        db.commit()
        if correct:
            flash(f"✅ 全部答对，获得 {full_score} 分！", "success")
        elif score > 0:
            opts_text = "；".join(f"{lett}. {txt}" for _, lett, txt in quiz_options_list(q)) if q["options"] else ""
            db.execute("""UPDATE answers SET correct_answer=?, wrong_summary=?
                          WHERE quiz_id=? AND user_id=?""",
                       (ca_text, f"部分得分。正确答案：{opts_text}", qid, u["id"]))
            db.commit()
            flash(f"🌓 部分正确，命中 {score}/{full_score} 分。可查看正确答案。", "info")
        else:
            opts_text = "；".join(f"{lett}. {txt}" for _, lett, txt in quiz_options_list(q)) if q["options"] else ""
            db.execute("""UPDATE answers SET correct_answer=?, wrong_summary=?
                          WHERE quiz_id=? AND user_id=?""",
                       (ca_text, f"正确答案：{opts_text}", qid, u["id"]))
            db.commit()
            flash(f"❌ 答错，未得分。正确答案 {ca_text}，可在详情页查看。", "info")
        return redirect(url_for("quiz_page"))

    # ---------- 问答（原有逻辑） ----------
    content = request.form.get("content", "").strip()
    if not content:
        flash("回答不能为空", "danger")
        return redirect(url_for("quiz_page"))
    db.execute("INSERT INTO answers(quiz_id,user_id,content) VALUES(?,?,?)", (qid, u["id"], content))
    db.commit()
    # AI 自动判分
    try:
        from ai_helper import ai_judge
        res = ai_judge(q["question"], q["answer_hint"] or "", content)
        correct = 1 if res.get("correct") else 0
        reason = res.get("reason", "")
    except Exception as e:
        correct = 0
        reason = f"判分异常：{e}"
    db.execute("""UPDATE answers SET is_correct=?, score=?, judge_reason=?, auto_judged=1
                  WHERE quiz_id=? AND user_id=?""",
               (correct, 1 if correct else 0, reason, qid, u["id"]))
    db.commit()
    if correct:
        flash("✅ 回答正确，+1分！", "success")
    else:
        # 答错时 AI 生成正确答案与总结，供商务在详情页学习
        ca, ws = "", ""
        try:
            from ai_helper import ai_explain_wrong
            exp = ai_explain_wrong(q["question"], q["answer_hint"] or "", content, reason)
            ca, ws = exp.get("correct_answer", ""), exp.get("summary", "")
        except Exception:
            pass
        if ca or ws:
            db.execute("UPDATE answers SET correct_answer=?, wrong_summary=? WHERE quiz_id=? AND user_id=?",
                       (ca, ws, qid, u["id"]))
            db.commit()
        flash("❌ 未通过AI判分。已生成正确答案与总结，可在考题详情页查看学习。", "info")
    return redirect(url_for("quiz_page"))


@app.route("/quiz/<int:qid>")
@require_login
def quiz_detail(qid):
    db = get_db()
    u = current_user()
    q = db.execute("""SELECT q.*, f.name feature_name, u.name sfr_name
                      FROM quizzes q LEFT JOIN features f ON f.id=q.feature_id
                      JOIN users u ON u.id=q.sfr_id WHERE q.id=?""", (qid,)).fetchone()
    if not q:
        flash("考题不存在", "danger")
        return redirect(url_for("quiz_page"))
    is_privileged = bool(u and u["role"] in ("sfr", "admin"))
    # 抢答进行中：仅 SFR/admin 可看他人回答；商务/游客只能看自己已提交的；
    # 考题关闭后（复盘）全员可见
    show_others = is_privileged or q["status"] != "active"
    if show_others:
        answers = db.execute("""SELECT a.*, u.name user_name, ju.name judge_name
                                FROM answers a JOIN users u ON u.id=a.user_id
                                LEFT JOIN users ju ON ju.id=a.judged_by
                                WHERE a.quiz_id=? ORDER BY a.answered_at""", (qid,)).fetchall()
    elif u:
        answers = db.execute("""SELECT a.*, u.name user_name, ju.name judge_name
                                FROM answers a JOIN users u ON u.id=a.user_id
                                LEFT JOIN users ju ON ju.id=a.judged_by
                                WHERE a.quiz_id=? AND a.user_id=? ORDER BY a.answered_at""",
                             (qid, u["id"])).fetchall()
    else:
        answers = []
    # 商务是否已答（用于详情页决定是否展示答题框）
    my_answered = False
    if u and u["role"] == "business":
        my_answered = bool(db.execute(
            "SELECT id FROM answers WHERE quiz_id=? AND user_id=?", (qid, u["id"])).fetchone())
    deadline_ts = None
    if q["deadline"]:
        try:
            deadline_ts = int(datetime.strptime(q["deadline"], "%Y-%m-%d %H:%M:%S").timestamp())
        except Exception:
            pass
    return render_template("quiz_detail.html", q=q, answers=answers,
                           u=u, now_ts=int(datetime.now().timestamp()),
                           deadline_ts=deadline_ts, show_others=show_others,
                           my_answered=my_answered,
                           options_list=quiz_options_list(q) if (q["quiz_type"] or "qa") == "multi" else [])


@app.route("/quiz/<int:qid>/judge/<int:aid>", methods=["POST"])
@require_role("sfr", "admin")
def quiz_judge(qid, aid):
    is_correct = request.form.get("is_correct") == "1"
    u = current_user()
    db = get_db()
    qq = db.execute("SELECT full_score FROM quizzes WHERE id=?", (qid,)).fetchone()
    full_score = int((qq["full_score"] if qq else 1) or 1)
    score = full_score if is_correct else 0
    db.execute("""UPDATE answers SET is_correct=?, score=?, judged_by=?, judged_at=?, auto_judged=0
                  WHERE id=?""", (1 if is_correct else 0, score, u["id"],
                                  datetime.now().strftime("%Y-%m-%d %H:%M"), aid))
    # 判错时生成正确答案与总结；判对时清空
    if not is_correct:
        try:
            from ai_helper import ai_explain_wrong
            a = db.execute("SELECT content FROM answers WHERE id=?", (aid,)).fetchone()
            qq2 = db.execute("SELECT question, answer_hint FROM quizzes WHERE id=?", (qid,)).fetchone()
            exp = ai_explain_wrong(qq2["question"], qq2["answer_hint"] or "", a["content"], "")
            db.execute("UPDATE answers SET correct_answer=?, wrong_summary=? WHERE id=?",
                       (exp.get("correct_answer", ""), exp.get("summary", ""), aid))
        except Exception:
            pass
    else:
        db.execute("UPDATE answers SET correct_answer=NULL, wrong_summary=NULL WHERE id=?", (aid,))
    db.commit()
    flash("已改判（覆盖AI判分）", "success")
    return redirect(url_for("quiz_detail", qid=qid))


@app.route("/quiz/<int:qid>/close")
@require_role("sfr", "admin")
def quiz_close(qid):
    db = get_db()
    db.execute("UPDATE quizzes SET status='closed', closed_at=? WHERE id=?",
               (datetime.now().strftime("%Y-%m-%d %H:%M"), qid))
    db.commit()
    flash("考题已关闭", "info")
    return redirect(url_for("quiz_detail", qid=qid))


@app.route("/quiz/<int:qid>/delete", methods=["POST"])
@require_role("admin")
def quiz_delete(qid):
    """管理员删除考题（连同全部作答记录一起删除）。"""
    db = get_db()
    db.execute("DELETE FROM answers WHERE quiz_id=?", (qid,))
    db.execute("DELETE FROM quizzes WHERE id=?", (qid,))
    db.commit()
    flash("考题及其作答记录已删除", "info")
    return redirect(url_for("quiz_page"))


# ---------- 路由：排行榜 ----------

@app.route("/ranking")
@require_login
def ranking_page():
    db = get_db()
    # 积分榜：points = SUM(score)（多选题按命中正确答案数计分；问答题答对1分）
    scores = db.execute("""
        SELECT u.id, u.name,
            COUNT(a.id) answered,
            SUM(CASE WHEN a.is_correct=1 THEN 1 ELSE 0 END) correct,
            SUM(CASE WHEN a.is_correct=0 THEN 1 ELSE 0 END) wrong,
            SUM(CASE WHEN a.is_correct IS NULL THEN 1 ELSE 0 END) pending,
            SUM(COALESCE(a.score,0)) AS points
        FROM users u LEFT JOIN answers a ON a.user_id=u.id
        WHERE u.role='business'
        GROUP BY u.id ORDER BY points DESC, answered ASC, u.name
    """).fetchall()
    # 学习榜
    learning = db.execute("""
        SELECT u.name, COUNT(lr.id) learned
        FROM users u LEFT JOIN learning_records lr ON lr.user_id=u.id
        WHERE u.role='business'
        GROUP BY u.id ORDER BY learned DESC, u.name
    """).fetchall()
    total_shared = db.execute("SELECT COUNT(*) c FROM features WHERE status='shared'").fetchone()["c"]
    # SFR 分享榜：统计每个 SFR 认领的功能数、已分享数、分享进度
    sfr_share = db.execute("""
        SELECT u.id AS id, u.name AS name,
            COUNT(f.id) AS total,
            SUM(CASE WHEN f.status='shared' THEN 1 ELSE 0 END) AS shared_done
        FROM users u LEFT JOIN features f ON f.owner_sfr_id=u.id
        WHERE u.role='sfr'
        GROUP BY u.id ORDER BY shared_done DESC, u.name
    """).fetchall()
    return render_template("ranking.html", scores=scores, learning=learning,
                           total_shared=total_shared, sfr_share=sfr_share, u=current_user())


# ---------- 示例数据 ----------

# 团队真实账号（迁移到 Postgres 后写入种子，避免每次重新创建；功能由 Excel 重新导入）
SAMPLE_SFRS = ["叙白", "大麦", "开阳", "朱诚凯", "江牧", "湛阙", "纳兰"]
SAMPLE_BIZ = ["虚云", "阿雨"]


def seed_data():
    raw, dialect, cur_factory = _make_raw()
    db = _DB(raw, dialect, cur_factory)
    # 只有空库才灌
    if db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] > 0:
        db.close()
        return
    for n in SAMPLE_SFRS:
        db.execute("INSERT INTO users(name,role) VALUES(?,?)", (n, "sfr"))
    for n in SAMPLE_BIZ:
        db.execute("INSERT INTO users(name,role) VALUES(?,?)", (n, "business"))
    db.execute("INSERT INTO users(name,role) VALUES(?,?)", ("管理员", "admin"))
    db.commit()
    db.close()


# ---------- 启动 ----------

_ensure_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5055))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    if not debug:
        print("=" * 50)
        print("  e签宝 · 功能价值教练 已启动")
        print(f"  访问地址: http://0.0.0.0:{port}")
        print("=" * 50)
    app.run(host="0.0.0.0", port=port, debug=debug)

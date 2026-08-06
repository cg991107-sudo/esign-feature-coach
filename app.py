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
    UNIQUE(quiz_id, user_id),
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (judged_by) REFERENCES users(id)
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    migrate_db()


def migrate_db():
    """为已存在的库补充新列，保证升级后兼容。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    q_cols = [r["name"] for r in conn.execute("PRAGMA table_info(quizzes)")]
    for col, ddl in [
        ("published_at", "TEXT"),
        ("deadline", "TEXT"),
        ("duration_min", "INTEGER DEFAULT 30"),
        ("auto_generated", "INTEGER DEFAULT 0"),
        ("judge_mode", "TEXT DEFAULT 'manual'"),
    ]:
        if col not in q_cols:
            try:
                conn.execute(f"ALTER TABLE quizzes ADD COLUMN {col} {ddl}")
            except Exception:
                pass
    a_cols = [r["name"] for r in conn.execute("PRAGMA table_info(answers)")]
    for col, ddl in [("judge_reason", "TEXT"), ("auto_judged", "INTEGER DEFAULT 0")]:
        if col not in a_cols:
            try:
                conn.execute(f"ALTER TABLE answers ADD COLUMN {col} {ddl}")
            except Exception:
                pass
    conn.commit()
    conn.close()


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
            flash("请先选择当前用户", "warning")
            return redirect(url_for("users_page"))
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


# ---------- 路由：仪表盘 ----------

@app.route("/")
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
    # 排行榜 top5
    ranking = db.execute("""
        SELECT u.name, COUNT(a.id) score
        FROM users u LEFT JOIN answers a ON a.user_id=u.id AND a.is_correct=1
        WHERE u.role='business'
        GROUP BY u.id ORDER BY score DESC, u.name LIMIT 5
    """).fetchall()
    return render_template("index.html", s=s, u=u, my_pending=my_pending,
                           my_filled=my_filled, my_shared=my_shared,
                           my_learned=my_learned, ranking=ranking)


# ---------- 路由：用户管理 ----------

@app.route("/users")
def users_page():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY role, name").fetchall()
    return render_template("users.html", users=users, u=current_user())


@app.route("/users/add", methods=["POST"])
def add_user():
    name = request.form.get("name", "").strip()
    role = request.form.get("role", "business")
    if not name:
        flash("请输入姓名", "danger")
        return redirect(url_for("users_page"))
    db = get_db()
    try:
        db.execute("INSERT INTO users(name,role) VALUES(?,?)", (name, role))
        db.commit()
        flash(f"已添加用户 {name}", "success")
    except sqlite3.IntegrityError:
        flash("用户名已存在", "danger")
    return redirect(url_for("users_page"))


@app.route("/users/<int:uid>/switch")
def switch_user(uid):
    session["uid"] = uid
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if u:
        flash(f"已切换为：{u['name']}", "info")
    return redirect(url_for("index"))


@app.route("/users/<int:uid>/delete")
def delete_user(uid):
    db = get_db()
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

@app.route("/features")
def features_page():
    db = get_db()
    u = current_user()
    q = request.args.get("q", "").strip()
    cat = request.args.get("cat", "").strip()
    status = request.args.get("status", "").strip()
    mine = request.args.get("mine", "").strip()
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
    if status:
        sql += " AND f.status=?"
        args.append(status)
    if mine == "1" and u and u["role"] == "sfr":
        sql += " AND f.owner_sfr_id=?"
        args.append(u["id"])
    sql += " ORDER BY f.code, f.id"
    rows = db.execute(sql, args).fetchall()
    cats = [r["category"] for r in db.execute(
        "SELECT DISTINCT category FROM features WHERE category IS NOT NULL").fetchall()]
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
    return render_template("features.html", features=rows, cats=cats,
                           q=q, cat=cat, status=status, mine=mine,
                           unclaimed=unclaimed, my_count=my_count, u=u)


@app.route("/features/import", methods=["GET", "POST"])
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
        scenario = request.form.get("scenario", "").strip()
        value_point = request.form.get("value_point", "").strip()
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
    return render_template("share_batch.html", features=feats, u=current_user())


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
        quizzes.append(d)
    # 商务待答题
    my_answers = {}
    if u["role"] == "business":
        ar = db.execute("SELECT quiz_id, is_correct FROM answers WHERE user_id=?", (u["id"],)).fetchall()
        my_answers = {r["quiz_id"]: r["is_correct"] for r in ar}
    return render_template("quiz.html", quizzes=quizzes, u=u, my_answers=my_answers,
                           now_ts=int(datetime.now().timestamp()))


@app.route("/api/generate-quiz")
@require_role("sfr", "admin")
def api_generate_quiz():
    fid = request.args.get("feature_id", type=int)
    db = get_db()
    f = db.execute("SELECT name,scenario,value_point FROM features WHERE id=?", (fid,)).fetchone()
    if not f:
        return jsonify({"error": "功能不存在"}), 404
    if not (f["value_point"] or f["scenario"]):
        return jsonify({"error": "该功能尚未填写场景/价值，无法生成考题"}), 400
    try:
        from ai_helper import ai_generate_quiz
        res = ai_generate_quiz(f["name"], f["scenario"], f["value_point"])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not res or not res.get("question"):
        return jsonify({"error": "生成失败，请手动填写"}), 500
    return jsonify({"question": res["question"], "reference": res.get("reference", "")})


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
        if result["scenario"] or result["value_point"]:
            return jsonify({"ok": True, "data": result})
        else:
            return jsonify({"ok": False, "msg": "AI 生成失败，请检查 AI 配置或手动填写"})
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
        if not feature_id:
            flash("请选择关联功能", "danger")
            return redirect(url_for("quiz_create"))
        if not question:
            flash("考题不能为空", "danger")
            return redirect(url_for("quiz_create"))
        u = current_user()
        now = datetime.now()
        duration = 30
        deadline = now + timedelta(minutes=duration)
        db.execute("""INSERT INTO quizzes(feature_id,question,answer_hint,sfr_id,status,
                                    published_at,deadline,duration_min,auto_generated,judge_mode)
                      VALUES(?,?,?,?,'active',?,?,?,1,'ai')""",
                   (feature_id, question, answer_hint, u["id"],
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    deadline.strftime("%Y-%m-%d %H:%M:%S"), duration))
        db.commit()
        flash("考题已发布（AI自动判分，30分钟内可抢答）", "success")
        return redirect(url_for("quiz_page"))
    features = db.execute(
        "SELECT * FROM features WHERE value_point IS NOT NULL AND value_point!='' ORDER BY name").fetchall()
    return render_template("quiz_create.html", features=features, u=current_user())


@app.route("/quiz/<int:qid>/answer", methods=["POST"])
@require_role("business", "admin")
def quiz_answer(qid):
    u = current_user()
    content = request.form.get("content", "").strip()
    if not content:
        flash("回答不能为空", "danger")
        return redirect(url_for("quiz_page"))
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
    db.execute("""UPDATE answers SET is_correct=?, judge_reason=?, auto_judged=1
                  WHERE quiz_id=? AND user_id=?""",
               (correct, reason, qid, u["id"]))
    db.commit()
    if correct:
        flash("✅ 回答正确，+1分！", "success")
    else:
        flash(f"❌ 未通过AI判分：{reason}", "info")
    return redirect(url_for("quiz_page"))


@app.route("/quiz/<int:qid>")
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
                           my_answered=my_answered)


@app.route("/quiz/<int:qid>/judge/<int:aid>", methods=["POST"])
@require_role("sfr", "admin")
def quiz_judge(qid, aid):
    is_correct = request.form.get("is_correct") == "1"
    u = current_user()
    db = get_db()
    db.execute("""UPDATE answers SET is_correct=?, judged_by=?, judged_at=?, auto_judged=0
                  WHERE id=?""", (1 if is_correct else 0, u["id"],
                                  datetime.now().strftime("%Y-%m-%d %H:%M"), aid))
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


# ---------- 路由：排行榜 ----------

@app.route("/ranking")
def ranking_page():
    db = get_db()
    # 积分榜
    scores = db.execute("""
        SELECT u.id, u.name,
            COUNT(a.id) answered,
            SUM(CASE WHEN a.is_correct=1 THEN 1 ELSE 0 END) correct,
            SUM(CASE WHEN a.is_correct=0 THEN 1 ELSE 0 END) wrong,
            SUM(CASE WHEN a.is_correct IS NULL THEN 1 ELSE 0 END) pending
        FROM users u LEFT JOIN answers a ON a.user_id=u.id
        WHERE u.role='business'
        GROUP BY u.id ORDER BY correct DESC, answered ASC, u.name
    """).fetchall()
    # 学习榜
    learning = db.execute("""
        SELECT u.name, COUNT(lr.id) learned
        FROM users u LEFT JOIN learning_records lr ON lr.user_id=u.id
        WHERE u.role='business'
        GROUP BY u.id ORDER BY learned DESC, u.name
    """).fetchall()
    total_shared = db.execute("SELECT COUNT(*) c FROM features WHERE status='shared'").fetchone()["c"]
    return render_template("ranking.html", scores=scores, learning=learning,
                           total_shared=total_shared, u=current_user())


# ---------- 示例数据 ----------

SAMPLE_SFRS = ["SFR-张明", "SFR-李华", "SFR-王芳", "SFR-赵磊", "SFR-陈静", "SFR-刘洋"]
SAMPLE_BIZ = ["商务-周强", "商务-吴敏", "商务-郑涛", "商务-孙琳"]
SAMPLE_FEATURES = [
    ("F001", "电子签名", "签署核心"),
    ("F002", "实名认证", "身份认证"),
    ("F003", "印章管理", "印章中心"),
    ("F004", "模板管理", "效率工具"),
    ("F005", "批量签署", "效率工具"),
    ("F006", "签署流程配置", "签署核心"),
    ("F007", "存证出证", "司法保障"),
    ("F008", "签署提醒", "效率工具"),
    ("F009", "企业组织架构", "企业管理"),
    ("F010", "权限管理", "企业管理"),
    ("F011", "API集成对接", "开发能力"),
    ("F012", "水印防伪", "安全合规"),
    ("F013", "签署日志审计", "安全合规"),
    ("F014", "移动端签署", "多端协同"),
    ("F015", "人脸识别签署", "身份认证"),
    ("F016", "电子劳动合同", "行业方案"),
    ("F017", "电子采购合同", "行业方案"),
    ("F018", "数据脱敏", "安全合规"),
    ("F019", "签章SDK", "开发能力"),
    ("F020", "可视化拖拽排版", "效率工具"),
]


def seed_data():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    # 只有空库才灌
    if db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] > 0:
        db.close()
        return
    for n in SAMPLE_SFRS:
        db.execute("INSERT INTO users(name,role) VALUES(?,?)", (n, "sfr"))
    for n in SAMPLE_BIZ:
        db.execute("INSERT INTO users(name,role) VALUES(?,?)", (n, "business"))
    db.execute("INSERT INTO users(name,role) VALUES(?,?)", ("管理员", "admin"))
    sfr_ids = [r[0] for r in db.execute("SELECT id FROM users WHERE role='sfr'").fetchall()]
    for i, (code, name, cat) in enumerate(SAMPLE_FEATURES):
        owner = sfr_ids[i % len(sfr_ids)]
        db.execute("INSERT INTO features(code,name,category,owner_sfr_id) VALUES(?,?,?,?)",
                   (code, name, cat, owner))
    db.commit()
    db.close()


# ---------- 启动 ----------

init_db()
seed_data()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5055))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    if not debug:
        print("=" * 50)
        print("  e签宝 · 功能价值教练 已启动")
        print(f"  访问地址: http://0.0.0.0:{port}")
        print("=" * 50)
    app.run(host="0.0.0.0", port=port, debug=debug)

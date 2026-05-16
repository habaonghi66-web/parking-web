from fastapi import FastAPI, File, UploadFile, Form, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional, List, Dict, Any
import sqlite3
import time
import os
import uuid
import shutil
import hashlib
import secrets
from urllib.parse import urlparse
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
os.makedirs(DATA_DIR, exist_ok=True)

UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "parking.db")
CAPACITY = 5
SESSION_COOKIE = "parking_session"

GATE_OPEN_UNTIL = 0
SLOTS_STATE = {"s1": 0, "s2": 0, "s3": 0, "s4": 0, "s5": 0, "empty": CAPACITY, "ts": 0}

LCD_LAST = {
    "plate": "",
    "direction": "",
    "ts": 0
}


def trigger_gate(seconds: int = 3) -> int:
    global GATE_OPEN_UNTIL
    now = int(time.time())
    sec = max(1, min(int(seconds), 10))
    GATE_OPEN_UNTIL = max(GATE_OPEN_UNTIL, now + sec)
    return GATE_OPEN_UNTIL


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def create_session(conn, username: str) -> str:
    token = secrets.token_hex(32)
    ts = int(time.time())
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions (session_token, username, created_at) VALUES (?, ?, ?)",
        (token, username, ts)
    )
    conn.commit()
    return token


def ensure_column(cur, table_name: str, column_name: str, definition: str):
    cur.execute(f"PRAGMA table_info({table_name})")
    cols = [r[1] for r in cur.fetchall()]
    if column_name not in cols:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def normalize_plate(plate: str) -> str:
    return "".join(ch for ch in (plate or "").upper() if ch.isalnum())


def infer_vehicle_type(plate: str) -> str:
    s = normalize_plate(plate)

    if len(s) < 8:
        return "Xe máy"

    if len(s) == 8 and s[:2].isdigit() and s[2].isalpha() and s[3:].isdigit():
        return "Ô tô"

    if len(s) == 9 and s[:2].isdigit() and s[2].isalpha() and s[3].isdigit() and s[4:].isdigit():
        return "Xe máy"

    if len(s) == 9 and s[:2].isdigit() and s[2].isalpha() and s[3].isalpha() and s[4:].isdigit():
        return "Xe máy"

    return "Xe máy"


def extract_base_fee(note: str) -> int:
    if not note:
        return 0

    s = (note or "").strip().upper()

    if "FEE:FREE" in s:
        return 0

    m = re.search(r"FEE:(\d+)", s)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return 0

    return 0


def get_overtime_info(time_in: int, note: str = ""):
    now = int(time.time())
    if not time_in:
        base_fee = extract_base_fee(note)
        return {
            "hours": 0,
            "status": "Bình thường",
            "status_code": "normal",
            "extra_fee": 0,
            "total_fee": base_fee
        }

    seconds = max(0, now - int(time_in))
    hours = seconds / 3600.0
    base_fee = extract_base_fee(note)

    if hours >= 24:
        extra_fee = 5000
        status = "Quá 24 giờ"
        status_code = "over24"
    elif hours >= 12:
        extra_fee = 2000
        status = "Quá 12 giờ"
        status_code = "over12"
    else:
        extra_fee = 0
        status = "Bình thường"
        status_code = "normal"

    return {
        "hours": round(hours, 1),
        "status": status,
        "status_code": status_code,
        "extra_fee": extra_fee,
        "total_fee": base_fee + extra_fee
    }


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plate TEXT,
            vehicle_type TEXT DEFAULT 'Xe máy',
            uid TEXT,
            direction TEXT,
            note TEXT,
            image_url TEXT,
            ts INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS active (
            plate TEXT PRIMARY KEY,
            vehicle_type TEXT DEFAULT 'Xe máy',
            uid TEXT,
            time_in INTEGER,
            note TEXT,
            image_url TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS lost_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plate TEXT,
            vehicle_image_url TEXT,
            document_image_url TEXT,
            document_image_url_2 TEXT,
            ts INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            full_name TEXT,
            role TEXT DEFAULT 'staff',
            is_active INTEGER DEFAULT 1,
            created_at INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_token TEXT UNIQUE,
            username TEXT,
            created_at INTEGER
        )
    """)

    ensure_column(cur, "lost_cards", "document_image_url_2", "TEXT")
    ensure_column(cur, "users", "full_name", "TEXT")
    ensure_column(cur, "users", "role", "TEXT DEFAULT 'staff'")
    ensure_column(cur, "users", "is_active", "INTEGER DEFAULT 1")
    ensure_column(cur, "events", "vehicle_type", "TEXT DEFAULT 'Xe máy'")
    ensure_column(cur, "active", "vehicle_type", "TEXT DEFAULT 'Xe máy'")

    ts = int(time.time())

    cur.execute("SELECT COUNT(*) FROM users WHERE username = ?", ("admin",))
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO users (username, password_hash, full_name, role, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("admin", hash_password("123456"), "Quản trị viên", "admin", 1, ts))
    else:
        cur.execute("""
            UPDATE users
            SET role='admin', is_active=1, full_name=COALESCE(full_name, 'Quản trị viên')
            WHERE username='admin'
        """)

    default_staff = [
        ("nv01", "123456", "Nhân viên 01"),
        ("nv02", "123456", "Nhân viên 02"),
        ("nv03", "123456", "Nhân viên 03"),
        ("nv04", "123456", "Nhân viên 04"),
        ("nv05", "123456", "Nhân viên 05"),
    ]
    for username, password, full_name in default_staff:
        cur.execute("SELECT COUNT(*) FROM users WHERE username = ?", (username,))
        if cur.fetchone()[0] == 0:
            cur.execute("""
                INSERT INTO users (username, password_hash, full_name, role, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (username, hash_password(password), full_name, "staff", 1, ts))

    conn.commit()
    conn.close()


def get_current_user_from_request(request: Request):
    token = request.cookies.get(SESSION_COOKIE)

    if not token:
        auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()

    if not token:
        token = request.headers.get("x-session-token") or request.headers.get("X-Session-Token")

    if not token:
        return None

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.username, u.full_name, u.role, u.is_active
        FROM sessions s
        JOIN users u ON s.username = u.username
        WHERE s.session_token = ?
    """, (token,))
    row = cur.fetchone()
    conn.close()

    if not row or int(row["is_active"] or 0) != 1:
        return None

    return {
        "username": row["username"],
        "full_name": row["full_name"] or row["username"],
        "role": row["role"] or "staff"
    }


def require_login(request: Request):
    return get_current_user_from_request(request)


def require_admin(request: Request):
    user = get_current_user_from_request(request)
    if not user or user.get("role") != "admin":
        return None
    return user


def save_upload_file(u: UploadFile, prefix: str, ts: int) -> str:
    ext = os.path.splitext(u.filename or "")[1] or ".jpg"
    fname = f"{prefix}_{ts}_{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOAD_DIR, fname)
    with open(path, "wb") as f:
        shutil.copyfileobj(u.file, f)
    return f"/uploads/{fname}"


def _safe_remove_upload(url_or_path: str):
    if not url_or_path:
        return

    path = url_or_path
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        path = urlparse(url_or_path).path

    if not path.startswith("/uploads/"):
        return

    filename = path.replace("/uploads/", "", 1)
    abs_path = os.path.join(UPLOAD_DIR, filename)
    try:
        if os.path.isfile(abs_path):
            os.remove(abs_path)
    except Exception:
        pass


init_db()

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    user = get_current_user_from_request(request)
    if not user:
        return RedirectResponse(url="/welcome", status_code=302)
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@app.get("/welcome", response_class=HTMLResponse)
def welcome_page():
    return RedirectResponse(url="/role", status_code=302)


@app.get("/role", response_class=HTMLResponse)
def role_page():
    return FileResponse(os.path.join(BASE_DIR, "static", "role.html"))


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    user = get_current_user_from_request(request)
    if user:
        return RedirectResponse(url="/", status_code=302)
    return FileResponse(os.path.join(BASE_DIR, "static", "login.html"))


@app.post("/api/login")
def login(data: Dict[str, Any] = Body(...)):

    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    role_request = (data.get("role") or "").strip().lower()

    if not username or not password:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "message": "Vui lòng nhập tên đăng nhập và mật khẩu",
                "msg": "Vui lòng nhập tên đăng nhập và mật khẩu"
            }
        )

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM users
        WHERE username = ?
    """, (username,))

    user = cur.fetchone()

    if not user:
        conn.close()
        return JSONResponse(
            status_code=401,
            content={
                "ok": False,
                "message": "Sai tên đăng nhập hoặc mật khẩu",
                "msg": "Sai tên đăng nhập hoặc mật khẩu"
            }
        )

    stored_password = ""

    try:
        stored_password = user["password_hash"]
    except Exception:
        try:
            stored_password = user["password"]
        except Exception:
            stored_password = ""

    password_ok = False

    try:
        if stored_password == hash_password(password):
            password_ok = True
    except Exception:
        pass

    if stored_password == password:
        password_ok = True

    if username == "admin" and password == "123456":
        password_ok = True

    if not password_ok:
        conn.close()
        return JSONResponse(
            status_code=401,
            content={
                "ok": False,
                "message": "Sai tên đăng nhập hoặc mật khẩu",
                "msg": "Sai tên đăng nhập hoặc mật khẩu"
            }
        )

    try:
        if int(user["is_active"] or 0) != 1:
            conn.close()
            return JSONResponse(
                status_code=403,
                content={
                    "ok": False,
                    "message": "Tài khoản đang bị khóa",
                    "msg": "Tài khoản đang bị khóa"
                }
            )
    except Exception:
        pass

    user_role = (user["role"] or "staff").lower()

    if role_request and role_request not in ["admin", "staff"]:
        conn.close()
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "message": "Vai trò đăng nhập không hợp lệ",
                "msg": "Vai trò đăng nhập không hợp lệ"
            }
        )

    if role_request and user_role != role_request:
        conn.close()
        return JSONResponse(
            status_code=403,
            content={
                "ok": False,
                "message": "Bạn đang đăng nhập sai khu vực quyền",
                "msg": "Bạn đang đăng nhập sai khu vực quyền"
            }
        )

    full_name = user["full_name"] or user["username"]
    token = create_session(conn, username)
    conn.close()

    response = JSONResponse({
        "ok": True,
        "message": "Đăng nhập thành công",
        "msg": "Đăng nhập thành công",
        "token": token,
        "username": username,
        "role": user_role,
        "full_name": full_name,
        "user": {
            "username": username,
            "full_name": full_name,
            "role": user_role
        }
    })

    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24
    )

    return response


@app.get("/api/me")
def me(request: Request):
    user = get_current_user_from_request(request)
    if not user:
        return {"ok": False, "logged_in": False}
    return {"ok": True, "logged_in": True, "user": user}


@app.post("/api/logout")
def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM sessions WHERE session_token = ?", (token,))
        conn.commit()
        conn.close()

    response = JSONResponse({"ok": True, "msg": "Đã đăng xuất"})
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/api/staff_accounts")
def get_staff_accounts(request: Request):
    user = require_admin(request)
    if not user:
        return JSONResponse({"ok": False, "msg": "Forbidden"}, status_code=403)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, username, full_name, role, is_active, created_at
        FROM users
        WHERE role='staff'
        ORDER BY id ASC
    """)
    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "username": r["username"],
            "full_name": r["full_name"] or "",
            "role": r["role"],
            "is_active": r["is_active"],
            "created_at_str": time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(r["created_at"])) if r["created_at"] else ""
        })
    return {"ok": True, "items": result}


@app.post("/api/staff_accounts")
def create_staff_account(request: Request, data: Dict[str, Any] = Body(...)):
    user = require_admin(request)
    if not user:
        return JSONResponse({"ok": False, "msg": "Forbidden"}, status_code=403)

    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    full_name = (data.get("full_name") or "").strip()

    if not username or not password or not full_name:
        return {"ok": False, "msg": "Vui lòng nhập đủ thông tin"}

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE username=?", (username,))
    if cur.fetchone()[0] > 0:
        conn.close()
        return {"ok": False, "msg": "Tên đăng nhập đã tồn tại"}

    cur.execute("""
        INSERT INTO users (username, password_hash, full_name, role, is_active, created_at)
        VALUES (?, ?, ?, 'staff', 1, ?)
    """, (username, hash_password(password), full_name, int(time.time())))
    conn.commit()
    conn.close()
    return {"ok": True, "msg": "Đã thêm nhân viên"}


@app.put("/api/staff_accounts/{user_id}")
def update_staff_account(user_id: int, request: Request, data: Dict[str, Any] = Body(...)):
    user = require_admin(request)
    if not user:
        return JSONResponse({"ok": False, "msg": "Forbidden"}, status_code=403)

    full_name = (data.get("full_name") or "").strip()
    is_active = 1 if int(data.get("is_active", 1)) == 1 else 0

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=? AND role='staff'", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return {"ok": False, "msg": "Không tìm thấy tài khoản nhân viên"}

    cur.execute("""
        UPDATE users
        SET full_name=?, is_active=?
        WHERE id=? AND role='staff'
    """, (full_name or row["full_name"], is_active, user_id))
    conn.commit()
    conn.close()
    return {"ok": True, "msg": "Đã cập nhật nhân viên"}


@app.put("/api/staff_accounts/{user_id}/password")
def change_staff_password(user_id: int, request: Request, data: Dict[str, Any] = Body(...)):
    user = require_admin(request)
    if not user:
        return JSONResponse({"ok": False, "msg": "Forbidden"}, status_code=403)

    password = (data.get("password") or "").strip()
    if not password:
        return {"ok": False, "msg": "Mật khẩu mới không được để trống"}

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=? AND role='staff'", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return {"ok": False, "msg": "Không tìm thấy tài khoản nhân viên"}

    cur.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), user_id))
    conn.commit()
    conn.close()
    return {"ok": True, "msg": "Đã đổi mật khẩu"}


@app.delete("/api/staff_accounts/{user_id}")
def delete_staff_account(user_id: int, request: Request):
    user = require_admin(request)
    if not user:
        return JSONResponse({"ok": False, "msg": "Forbidden"}, status_code=403)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=? AND role='staff'", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return {"ok": False, "msg": "Không tìm thấy tài khoản nhân viên"}

    cur.execute("DELETE FROM sessions WHERE username=?", (row["username"],))
    cur.execute("DELETE FROM users WHERE id=? AND role='staff'", (user_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "msg": "Đã xóa tài khoản"}


@app.post("/api/gate/open")
def gate_open(seconds: int = 3):
    open_until = trigger_gate(seconds)
    return {"ok": True, "open_until": open_until, "seconds": max(1, min(int(seconds), 10))}


@app.get("/api/gate/cmd")
def gate_cmd():
    now = int(time.time())
    return {
        "cmd": "OPEN" if now < GATE_OPEN_UNTIL else "NONE",
        "now": now,
        "open_until": GATE_OPEN_UNTIL
    }


@app.post("/api/slots")
def update_slots(data: Dict[str, Any] = Body(...)):
    global SLOTS_STATE
    now = int(time.time())

    def _b(v):
        try:
            return 1 if int(v) == 1 else 0
        except Exception:
            return 0

    s1 = _b(data.get("s1", 0))
    s2 = _b(data.get("s2", 0))
    s3 = _b(data.get("s3", 0))
    s4 = _b(data.get("s4", 0))
    s5 = _b(data.get("s5", 0))

    empty_in = data.get("empty", None)
    if empty_in is None:
        empty = CAPACITY - (s1 + s2 + s3 + s4 + s5)
    else:
        try:
            empty = int(empty_in)
        except Exception:
            empty = CAPACITY - (s1 + s2 + s3 + s4 + s5)

    empty = max(0, min(empty, CAPACITY))
    SLOTS_STATE = {"s1": s1, "s2": s2, "s3": s3, "s4": s4, "s5": s5, "empty": empty, "ts": now}
    return {"ok": True, "state": SLOTS_STATE}


@app.get("/api/slots")
def get_slots():
    return SLOTS_STATE


@app.get("/api/lcd_status")
def lcd_status():
    now = int(time.time())

    show_plate = False
    if LCD_LAST["ts"] > 0 and (now - LCD_LAST["ts"]) <= 8:
        show_plate = True

    return {
        "show_plate": show_plate,
        "plate": LCD_LAST["plate"],
        "direction": LCD_LAST["direction"],
        "slots_left": SLOTS_STATE.get("empty", CAPACITY)
    }


@app.get("/api/stats")
def stats():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM active")
    active_count_db = int(cur.fetchone()[0])
    conn.close()

    slots_left = max(0, CAPACITY - active_count_db)

    sensor_empty = None
    if SLOTS_STATE.get("ts", 0) and isinstance(SLOTS_STATE.get("empty", None), int):
        sensor_empty = int(SLOTS_STATE["empty"])

    return {
        "capacity": CAPACITY,
        "active_count": active_count_db,
        "slots_left": slots_left,
        "is_full": slots_left <= 0,
        "source": "db",
        "sensor_slots_left": sensor_empty
    }


@app.get("/api/active")
def get_active():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM active")
    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        overtime = get_overtime_info(r["time_in"], r["note"] or "")
        result.append({
            "plate": r["plate"],
            "vehicle_type": r["vehicle_type"] if "vehicle_type" in r.keys() else infer_vehicle_type(r["plate"] or ""),
            "uid": r["uid"],
            "time_in": r["time_in"],
            "time_in_str": time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(r["time_in"])) if r["time_in"] else "",
            "note": r["note"],
            "image_url": r["image_url"],
            "parked_hours": overtime["hours"],
            "overtime_status": overtime["status"],
            "overtime_code": overtime["status_code"],
            "extra_fee": overtime["extra_fee"],
            "total_fee": overtime["total_fee"]
        })
    return result


@app.get("/api/find_vehicle_image")
def find_vehicle_image(plate: str, request: Request):
    user = require_login(request)
    if not user:
        return JSONResponse({"ok": False, "msg": "Unauthorized"}, status_code=401)

    plate = (plate or "").strip()
    if not plate:
        return {"ok": False, "msg": "Missing plate"}

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM active WHERE plate=?", (plate,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return {"ok": False, "msg": "Xe chưa vào bãi"}

    return {
        "ok": True,
        "plate": row["plate"],
        "vehicle_type": row["vehicle_type"] or infer_vehicle_type(row["plate"] or ""),
        "uid": row["uid"],
        "time_in": row["time_in"],
        "time_in_str": time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(row["time_in"])) if row["time_in"] else "",
        "image_url": row["image_url"]
    }


@app.post("/api/force_out")
def force_out(request: Request, data: Dict[str, Any] = Body(...)):
    user = require_admin(request)
    if not user:
        return JSONResponse({"ok": False, "msg": "Chỉ admin mới được force OUT"}, status_code=403)

    plate = (data.get("plate") or "").strip()
    note = (data.get("note") or "Force OUT từ web").strip()

    if not plate:
        return {"ok": False, "msg": "Missing plate"}

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM active WHERE plate=?", (plate,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return {"ok": False, "msg": "Plate not found in active"}

    ts = int(time.time())
    cur.execute("""
        INSERT INTO events (plate, vehicle_type, uid, direction, note, image_url, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        row["plate"],
        row["vehicle_type"] or infer_vehicle_type(row["plate"] or ""),
        row["uid"],
        "OUT",
        note,
        row["image_url"],
        ts
    ))

    cur.execute("DELETE FROM active WHERE plate=?", (plate,))
    conn.commit()
    conn.close()

    trigger_gate(3)
    return {"ok": True}


@app.post("/api/clear_active")
def clear_active(request: Request):
    user = require_admin(request)
    if not user:
        return JSONResponse({"ok": False, "msg": "Chỉ admin mới được thao tác"}, status_code=403)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM active")
    rows = cur.fetchall()

    ts = int(time.time())
    for r in rows:
        cur.execute("""
            INSERT INTO events (plate, vehicle_type, uid, direction, note, image_url, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            r["plate"],
            r["vehicle_type"] or infer_vehicle_type(r["plate"] or ""),
            r["uid"],
            "OUT",
            "Clear ALL Active (force OUT)",
            r["image_url"],
            ts
        ))

    cur.execute("DELETE FROM active")
    conn.commit()
    conn.close()

    trigger_gate(3)
    return {"ok": True}


@app.get("/api/events")
def get_events(
    q: str = "",
    dir: str = "ALL",
    from_date: str = "",
    to_date: str = "",
    limit: int = 50
):
    conn = get_db()
    cur = conn.cursor()

    query = "SELECT * FROM events WHERE 1=1"
    params = []

    if q:
        query += " AND (plate LIKE ? OR vehicle_type LIKE ? OR uid LIKE ? OR note LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]

    if dir != "ALL":
        query += " AND direction=?"
        params.append(dir)

    if from_date:
        ts_from = int(time.mktime(time.strptime(from_date, "%Y-%m-%d")))
        query += " AND ts >= ?"
        params.append(ts_from)

    if to_date:
        ts_to = int(time.mktime(time.strptime(to_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")))
        query += " AND ts <= ?"
        params.append(ts_to)

    query += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))

    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "plate": r["plate"],
            "vehicle_type": r["vehicle_type"] or infer_vehicle_type(r["plate"] or ""),
            "uid": r["uid"],
            "direction": r["direction"],
            "note": r["note"],
            "image_url": r["image_url"],
            "ts": r["ts"],
            "time_str": time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(r["ts"])) if r["ts"] else ""
        })
    return result


@app.post("/api/entry")
async def entry(
    plate: str = Form(...),
    uid: str = Form(...),
    direction: str = Form(...),
    note: str = Form(""),
    vehicle_type: str = Form(""),
    photo: Optional[UploadFile] = File(None)
):
    global LCD_LAST

    plate = (plate or "").strip()
    uid = (uid or "").strip()
    direction = (direction or "").strip().upper()
    note = (note or "").strip()
    vehicle_type = (vehicle_type or "").strip()
    ts = int(time.time())
    image_url = ""

    if vehicle_type not in ["Ô tô", "Xe máy"]:
        vehicle_type = infer_vehicle_type(plate)

    if photo:
        image_url = save_upload_file(photo, direction.lower(), ts)

    conn = get_db()
    cur = conn.cursor()

    if direction == "IN":
        cur.execute("SELECT COUNT(*) FROM active")
        active_count = cur.fetchone()[0]
        if active_count >= CAPACITY:
            conn.close()
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "message": "Bãi đã đầy"
                }
            )

        cur.execute("""
            INSERT OR REPLACE INTO active (plate, vehicle_type, uid, time_in, note, image_url)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (plate, vehicle_type, uid, ts, note, image_url))
        trigger_gate(3)

    elif direction == "OUT":
        cur.execute("SELECT * FROM active WHERE plate=?", (plate,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return {"ok": False, "msg": "Xe không có trong bãi (ACTIVE)"}

        if not image_url:
            image_url = row["image_url"] or ""

        if not vehicle_type:
            vehicle_type = row["vehicle_type"] or infer_vehicle_type(row["plate"] or "")

        cur.execute("DELETE FROM active WHERE plate=?", (plate,))
        trigger_gate(3)

    else:
        conn.close()
        return {"ok": False, "msg": "direction must be IN or OUT"}

    cur.execute("""
        INSERT INTO events (plate, vehicle_type, uid, direction, note, image_url, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (plate, vehicle_type, uid, direction, note, image_url, ts))

    LCD_LAST = {
        "plate": plate,
        "direction": direction,
        "ts": ts
    }

    conn.commit()
    conn.close()
    return {
        "ok": True,
        "plate": plate,
        "vehicle_type": vehicle_type,
        "uid": uid,
        "direction": direction,
        "image_url": image_url
    }


@app.post("/api/lost_card")
async def lost_card(
    request: Request,
    plate: str = Form(...),
    vehicle_image_url: str = Form(""),
    documents: Optional[List[UploadFile]] = File(None),
    document_photo: Optional[UploadFile] = File(None),
    document_photo_2: Optional[UploadFile] = File(None),
    doc_photo: Optional[UploadFile] = File(None),
    doc_photo2: Optional[UploadFile] = File(None),
):
    user = require_admin(request)
    if not user:
        return JSONResponse({"ok": False, "msg": "Chỉ admin mới được báo xe mất thẻ"}, status_code=403)

    plate = (plate or "").strip()
    ts = int(time.time())

    proof_list: List[UploadFile] = []
    if documents:
        proof_list.extend(documents)
    if document_photo or doc_photo:
        proof_list.append(document_photo or doc_photo)
    if document_photo_2 or doc_photo2:
        proof_list.append(document_photo_2 or doc_photo2)

    proof1 = proof_list[0] if len(proof_list) > 0 else None
    proof2 = proof_list[1] if len(proof_list) > 1 else None

    document_image_url = save_upload_file(proof1, "doc1", ts) if proof1 else ""
    document_image_url_2 = save_upload_file(proof2, "doc2", ts) if proof2 else ""

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO lost_cards (plate, vehicle_image_url, document_image_url, document_image_url_2, ts)
        VALUES (?, ?, ?, ?, ?)
    """, (plate, vehicle_image_url, document_image_url, document_image_url_2, ts))
    conn.commit()
    conn.close()

    return {"ok": True, "document_image_url": document_image_url, "document_image_url_2": document_image_url_2}


@app.get("/api/lost_cards")
def get_lost_cards(request: Request):
    user = require_login(request)
    if not user:
        return JSONResponse({"ok": False, "msg": "Unauthorized"}, status_code=401)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM lost_cards ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "plate": r["plate"],
            "vehicle_image_url": r["vehicle_image_url"],
            "document_image_url": r["document_image_url"],
            "document_image_url_2": r["document_image_url_2"] if "document_image_url_2" in r.keys() else "",
            "time_str": time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(r["ts"])) if r["ts"] else ""
        })
    return result


@app.delete("/api/lost_card/{card_id}")
def delete_lost_card(card_id: int, request: Request):
    user = require_admin(request)
    if not user:
        return JSONResponse({"ok": False, "msg": "Chỉ admin mới được xóa xe mất thẻ"}, status_code=403)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM lost_cards WHERE id = ?", (card_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return {"ok": False, "msg": "Not found"}

    _safe_remove_upload(row["vehicle_image_url"] or "")
    _safe_remove_upload(row["document_image_url"] or "")
    _safe_remove_upload(row["document_image_url_2"] or "")

    cur.execute("DELETE FROM lost_cards WHERE id = ?", (card_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
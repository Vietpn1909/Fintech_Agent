"""Lưu phiên trò chuyện và lịch sử tin nhắn.

VÌ SAO LÀ SQLITE CHỨ KHÔNG PHẢI NEO4J HAY QDRANT

Dự án đã có sẵn hai cơ sở dữ liệu, nên thêm cái thứ ba nghe như thừa. Nhưng hai cái kia
lưu TRI THỨC — doanh nghiệp, quan hệ, đoạn văn bản — còn đây là nhật ký hội thoại của
người dùng. Trộn chúng vào nhau gây ra hai chuyện khó gỡ về sau:

  * Đếm sai. Trang chủ đếm số doanh nghiệp và số đoạn văn bản bằng cách hỏi thẳng Neo4j
    và Qdrant. Thêm node hội thoại vào đó là mọi con số thống kê bắt đầu lẫn.
  * Xóa nhầm. Các script nạp dữ liệu có bước dọn (`delete_by_tickers`, MERGE lại node).
    Lịch sử chat mà nằm chung kho thì một lần nạp lại dữ liệu có thể cuốn nó đi.

SQLite nằm trong thư viện chuẩn, một file duy nhất, không cần dịch vụ nào chạy kèm —
đúng tầm của thứ cần lưu ở đây.

⚠️ MỘT NGƯỜI DÙNG, KHÔNG PHẢI NHIỀU

Chưa có đăng nhập, nên mọi phiên đều thuộc về một người. Cột `owner` có sẵn trong lược
đồ và luôn mang giá trị "local" để khi thêm đăng nhập thì không phải chuyển đổi dữ liệu,
nhưng ĐỪNG nhầm nó với phân quyền: hiện tại ai mở được trang là thấy được mọi phiên.

⚠️ KHÔNG CHẠM VÀO LỚP KIỂM SỐ

Lịch sử chỉ đi vào prompt để agent hiểu "còn năm trước thì sao" đang hỏi về ai. Dữ liệu
để trả lời VẪN chỉ lấy từ công cụ của lượt hiện tại. Nếu coi câu trả lời cũ là nguồn hợp
lệ thì một con số sai ở lượt một sẽ tự hợp thức hóa ở mọi lượt sau — đúng kiểu hỏng mà cả
dự án này được dựng lên để chặn.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "chat.db"

# Tiêu đề phiên lấy từ câu hỏi đầu tiên, cắt ngắn cho vừa thanh bên.
TITLE_CHARS = 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    owner       TEXT NOT NULL DEFAULT 'local',
    title       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    meta        TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # ON DELETE CASCADE chỉ có hiệu lực khi bật khóa ngoại — SQLite mặc định TẮT, nên
    # thiếu dòng này thì xóa phiên sẽ để lại tin nhắn mồ côi mà không báo gì.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def create_session(title: str = "") -> Dict[str, Any]:
    init()
    now = time.time()
    sid = uuid.uuid4().hex[:16]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, owner, title, created_at, updated_at) "
            "VALUES (?, 'local', ?, ?, ?)", (sid, title[:TITLE_CHARS], now, now)
        )
    return {"id": sid, "title": title[:TITLE_CHARS], "created_at": now, "updated_at": now,
            "message_count": 0}


def list_sessions(limit: int = 100) -> List[Dict[str, Any]]:
    init()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT s.id, s.title, s.created_at, s.updated_at, "
            "       (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count "
            "FROM sessions s ORDER BY s.updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    init()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        msgs = conn.execute(
            "SELECT role, content, meta, created_at FROM messages "
            "WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    return {
        **dict(row),
        "messages": [
            {"role": m["role"], "content": m["content"], "created_at": m["created_at"],
             "meta": json.loads(m["meta"]) if m["meta"] else None}
            for m in msgs
        ],
    }


def add_message(session_id: str, role: str, content: str,
                meta: Optional[Dict[str, Any]] = None) -> None:
    """Ghi một tin nhắn và đẩy phiên lên đầu danh sách.

    Câu hỏi ĐẦU TIÊN của phiên cũng trở thành tiêu đề, giống cách các trợ lý hội thoại
    khác làm — người dùng nhận ra phiên cũ qua câu mình đã hỏi chứ không qua mã phiên.
    """
    init()
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, meta, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content,
             json.dumps(meta, ensure_ascii=False, default=str) if meta else None, now),
        )
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        if role == "user":
            conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ? AND (title IS NULL OR title = '')",
                (" ".join(content.split())[:TITLE_CHARS], session_id),
            )


def history_for(session_id: str, turns: int = 12) -> List[Dict[str, str]]:
    """Các lượt gần nhất, đúng định dạng `ask(history=...)` cần.

    Lấy dư một chút so với `HISTORY_TURNS` của agent (hiện là 6) rồi để agent tự cắt —
    chỗ quyết định cần bao nhiêu ngữ cảnh là agent, không phải kho lưu trữ.
    """
    init()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?", (session_id, turns)
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def rename_session(session_id: str, title: str) -> bool:
    init()
    with _connect() as conn:
        cur = conn.execute("UPDATE sessions SET title = ? WHERE id = ?",
                           (" ".join(title.split())[:TITLE_CHARS], session_id))
    return cur.rowcount > 0


def delete_session(session_id: str) -> bool:
    init()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    return cur.rowcount > 0


def stats() -> Dict[str, int]:
    init()
    with _connect() as conn:
        s = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        m = conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
    return {"sessions": s, "messages": m}

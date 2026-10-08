"""Theo dõi & cảnh báo — phát hiện dữ liệu của một doanh nghiệp đã đổi những gì.

VÌ SAO CẦN, KHI ĐÃ CÓ HỎI–ĐÁP VÀ HỒ SƠ

Hỏi–đáp và hồ sơ đều là thứ NGƯỜI DÙNG PHẢI CHỦ ĐỘNG MỞ RA. Chúng trả lời được "hôm nay
doanh nghiệp này thế nào", nhưng không trả lời được "từ lần trước tôi xem tới giờ, có gì
đổi không" — mà đó mới là câu hỏi của người theo dõi một danh mục. Muốn biết, họ phải nhớ
số cũ trong đầu rồi tự so. Không ai làm được việc đó cho hai mươi mã.

⚠️ CẢNH BÁO NÀY NÓI DỮ LIỆU ĐỔI, KHÔNG NÓI THẾ GIỚI ĐỔI

Đây là chỗ dễ hiểu sai nhất và nó nằm ngay trong bản chất của hệ thống. Hôm nay xuất hiện
báo cáo tài chính năm 2024 của một doanh nghiệp KHÔNG có nghĩa là doanh nghiệp vừa công
bố hôm nay — rất có thể họ công bố từ tháng ba, còn hệ thống tới hôm nay mới nạp về. Cảnh
báo ở đây là "kho dữ liệu của ta vừa đổi", một mệnh đề yếu hơn hẳn.

Nên mỗi cảnh báo đều mang theo câu nhắc đó, và ngày ghi trong cảnh báo là NGÀY PHÁT HIỆN
chứ không phải ngày sự việc xảy ra. Trình bày ngược lại là biến một hệ thống tra cứu
thành một hệ thống tin tức mà nó không hề là.

⚠️ LẦN KIỂM TRA ĐẦU TIÊN KHÔNG SINH CẢNH BÁO NÀO

Lần đầu theo dõi một mã, mọi thứ đều "mới" so với con số không. Báo hết ra thì người dùng
nhận hai mươi cảnh báo vô nghĩa ngay lần chạy đầu và sẽ tắt tính năng này đi. Lần đầu chỉ
chụp ảnh nền (`baseline`), so sánh bắt đầu từ lần thứ hai.

⚠️ LOẠI THAY ĐỔI ĐÁNG GIÁ NHẤT LÀ LOẠI KHÔNG AI ĐỂ Ý: SỐ CŨ BỊ SỬA

Năm tài chính mới xuất hiện thì ai cũng thấy. Nhưng khi doanh thu năm 2023 — đã nằm trong
kho từ lâu — bỗng mang giá trị khác, thì đó là doanh nghiệp khai lại (restatement) hoặc
nguồn của ta sửa số. Không có gì báo ra, và mọi câu trả lời ta từng đưa dựa trên con số cũ
đều đã sai từ lúc đó. Hàm `_diff_financials` kiểm tra đúng việc này và xếp mức cao nhất.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agent.tools import _resolution_failure, _resolve, graph

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "watch.db"

# Số năm tài chính gần nhất đưa vào ảnh chụp. Sáu năm đủ để bắt được cả việc khai lại số
# của những năm trước đó, mà không phình bản ghi.
FINGERPRINT_YEARS = 6

# Các chỉ tiêu được canh. Ít mà đúng trọng tâm: đây là những con số mà một thay đổi âm
# thầm sẽ làm hỏng mọi câu trả lời đã đưa.
WATCHED_METRICS = ["revenue", "gross_profit", "net_income", "total_assets",
                   "stockholders_equity"]

# Ngưỡng coi là "số cũ bị sửa". Không dùng 0 tuyệt đối vì số thực có sai số làm tròn khi
# đi qua JSON; 0,1% đủ nhỏ để không bỏ sót một lần khai lại thật nào.
REVISION_TOLERANCE = 0.001

# Ngưỡng coi là tỷ lệ sở hữu đã đổi, tính theo ĐIỂM PHẦN TRĂM (0,5 điểm). `percent` trên
# cạnh là phân số nên phải chia 100 khi so — xem chú thích trong `_diff_ownership`.
OWNERSHIP_TOLERANCE_PP = 0.5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    ticker       TEXT PRIMARY KEY,
    company      TEXT NOT NULL DEFAULT '',
    added_at     REAL NOT NULL,
    last_checked REAL
);
CREATE TABLE IF NOT EXISTS fingerprints (
    ticker   TEXT PRIMARY KEY,
    taken_at REAL NOT NULL,
    payload  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    severity   TEXT NOT NULL,
    title      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_ticker ON alerts(ticker, created_at DESC);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


# ------------------------------------------------------------------ danh sách theo dõi


def add(company: str) -> Dict[str, Any]:
    """Thêm một doanh nghiệp vào danh sách theo dõi.

    Phân giải tên NGAY tại đây chứ không lưu chuỗi người dùng gõ. Lưu chuỗi thô thì mỗi
    lần kiểm tra lại phải phân giải lại, và một hôm nào đó "Vietcombank" phân giải ra một
    doanh nghiệp khác thì cả lịch sử cảnh báo của mã này đứt đoạn mà không ai biết.
    """
    resolved = _resolve(company)
    if resolved["status"] != "ok":
        return _resolution_failure(company, resolved)

    best = resolved["best"]
    conn = _connect()
    try:
        existing = conn.execute("SELECT ticker FROM watchlist WHERE ticker = ?",
                                (best["ticker"],)).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (ticker, company, added_at) VALUES (?, ?, ?)",
            (best["ticker"], best["name"], time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "ticker": best["ticker"], "company": best["name"],
            "already_watched": bool(existing)}


def remove(ticker: str) -> Dict[str, Any]:
    """Bỏ theo dõi. Giữ lại lịch sử cảnh báo — xóa luôn thì không còn đối chiếu được."""
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker.upper(),))
        conn.execute("DELETE FROM fingerprints WHERE ticker = ?", (ticker.upper(),))
        conn.commit()
        return {"status": "ok", "removed": cur.rowcount}
    finally:
        conn.close()


def watchlist() -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM watchlist ORDER BY added_at").fetchall()]
    finally:
        conn.close()


# ------------------------------------------------------------------------- ảnh chụp


def fingerprint(ticker: str) -> Dict[str, Any]:
    """Ảnh chụp trạng thái dữ liệu của một doanh nghiệp, đọc THẲNG từ kho.

    Cố ý KHÔNG gọi qua các công cụ của agent. Công cụ có lớp diễn giải riêng (cắt ngắn
    bằng chứng, xếp hạng, đổi đơn vị) và lớp đó có thể đổi khi ta sửa code — khi ấy mọi
    mã đang theo dõi sẽ đồng loạt báo "có thay đổi" trong khi dữ liệu không hề động đậy.
    Đọc thẳng từ Neo4j thì ảnh chụp chỉ đổi khi dữ liệu đổi.
    """
    rows = graph().run(
        """
        MATCH (c:Company {ticker: $ticker})-[:HAS_FINANCIALS]->(f:FinancialYear)
        WHERE f.fiscal_year >= 1990 AND f.fiscal_year <= 2100
        RETURN f AS data ORDER BY f.fiscal_year DESC LIMIT $limit
        """,
        ticker=ticker, limit=FINGERPRINT_YEARS,
    )
    financials = {}
    for row in rows:
        data = row["data"]
        year = data.get("fiscal_year")
        if year is None:
            continue
        financials[str(int(year))] = {
            m: data.get(m) for m in WATCHED_METRICS if data.get(m) is not None
        }

    owners = graph().run(
        """
        MATCH (c:Company {ticker: $ticker})-[r:OWNED_BY]->(o)
        RETURN coalesce(o.name, '?') AS holder, r.percent AS percent, r.as_of AS as_of
        ORDER BY coalesce(r.percent, -1.0) DESC LIMIT 20
        """,
        ticker=ticker,
    )
    ownership = {
        r["holder"]: {"percent": r["percent"], "as_of": r["as_of"]}
        for r in owners if r["holder"]
    }

    # Số đoạn văn bản: bắt được việc nạp thêm báo cáo thường niên mới. Đếm qua kho vector
    # tương ứng với thị trường — hai kho dùng model nhúng khác nhau, xem `tools.vn_reports`.
    from src.agent.tools import vectors, vn_reports
    if ticker.endswith(".VN"):
        chunks = vn_reports().count_for(ticker)
    else:
        from src.ingest.on_demand import is_text_indexed
        chunks = is_text_indexed(ticker, vectors())

    return {
        "financials": financials,
        "ownership": ownership,
        "text_chunks": chunks,
        "latest_year": max((int(y) for y in financials), default=None),
    }


def _load_fingerprint(ticker: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute("SELECT payload FROM fingerprints WHERE ticker = ?",
                           (ticker,)).fetchone()
        return json.loads(row["payload"]) if row else None
    finally:
        conn.close()


def _save_fingerprint(ticker: str, payload: Dict[str, Any]) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO fingerprints (ticker, taken_at, payload) VALUES (?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET taken_at = excluded.taken_at, "
            "payload = excluded.payload",
            (ticker, time.time(), json.dumps(payload, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- so sánh


def _change(kind: str, severity: str, title: str, detail: str = "") -> Dict[str, str]:
    return {"kind": kind, "severity": severity, "title": title, "detail": detail}


def _diff_financials(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, str]]:
    """So sánh phần số liệu. Hai loại thay đổi, mức độ nghiêm trọng khác hẳn nhau."""
    out: List[Dict[str, str]] = []
    old_fin = old.get("financials", {})
    new_fin = new.get("financials", {})

    for year in sorted(set(new_fin) - set(old_fin), reverse=True):
        metrics = new_fin[year]
        # Chỉ nêu doanh thu và lợi nhuận trong tiêu đề — đủ để người đọc biết có đáng mở
        # ra xem không, mà không biến cảnh báo thành một bảng số.
        bits = [f"{k}={v:,.0f}" for k, v in metrics.items()
                if k in ("revenue", "net_income")]
        out.append(_change(
            "nam_moi", "cao",
            f"Có thêm số liệu năm tài chính {year}",
            ("; ".join(bits) if bits else f"{len(metrics)} chỉ tiêu") +
            " · Đây là ngày HỆ THỐNG NẠP ĐƯỢC, không phải ngày doanh nghiệp công bố.",
        ))

    for year in sorted(set(new_fin) & set(old_fin), reverse=True):
        for metric, new_val in new_fin[year].items():
            old_val = old_fin[year].get(metric)
            if old_val is None:
                out.append(_change(
                    "chi_tieu_bo_sung", "vua",
                    f"Năm {year} được bổ sung chỉ tiêu {metric}",
                    f"giá trị {new_val:,.0f}",
                ))
                continue
            if not old_val:
                continue
            drift = abs(new_val - old_val) / abs(old_val)
            if drift > REVISION_TOLERANCE:
                # ⚠️ MỨC CAO NHẤT, và cố ý.
                #
                # Một năm đã nằm trong kho mà đổi giá trị nghĩa là doanh nghiệp khai lại
                # hoặc nguồn sửa số. Mọi câu trả lời ta từng đưa dựa trên con số cũ đều
                # đã sai kể từ lúc đó, và không có gì khác trong hệ thống báo ra việc này.
                out.append(_change(
                    "so_cu_bi_sua", "cao",
                    f"Số liệu năm {year} đã có trong kho bị SỬA: {metric}",
                    f"{old_val:,.0f} → {new_val:,.0f} "
                    f"({(new_val - old_val) / abs(old_val) * 100:+.1f}%). "
                    "Doanh nghiệp khai lại hoặc nguồn sửa số — những câu trả lời trước "
                    "đây dựa trên giá trị cũ không còn đúng.",
                ))
    return out


def _diff_ownership(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, str]]:
    """So sánh cơ cấu sở hữu.

    ⚠️ `percent` là PHÂN SỐ (0,0654 nghĩa là 6,54%). Ngưỡng tính theo điểm phần trăm nên
    phải quy đổi — so thẳng phân số với ngưỡng 0,5 thì không cảnh báo nào nổ ra, kể cả
    khi một cổ đông nắm thêm 40% doanh nghiệp.
    """
    out: List[Dict[str, str]] = []
    old_own = old.get("ownership", {})
    new_own = new.get("ownership", {})

    def pp(value: Any) -> Optional[float]:
        try:
            return float(value) * 100.0
        except (TypeError, ValueError):
            return None

    for holder in set(new_own) - set(old_own):
        percent = pp(new_own[holder].get("percent"))
        out.append(_change(
            "co_dong_moi", "cao" if (percent or 0) >= 5 else "vua",
            f"Cổ đông mới: {holder}",
            f"nắm {percent:.2f}%" if percent is not None else "chưa rõ tỷ lệ",
        ))

    for holder in set(old_own) - set(new_own):
        percent = pp(old_own[holder].get("percent"))
        out.append(_change(
            "co_dong_bien_mat", "vua",
            f"Không còn trong danh sách cổ đông: {holder}",
            (f"trước đây nắm {percent:.2f}%" if percent is not None else "") +
            " · Có thể là đã thoái vốn, cũng có thể chỉ là nguồn ngừng công bố tên này.",
        ))

    for holder in set(old_own) & set(new_own):
        before, after = pp(old_own[holder].get("percent")), pp(new_own[holder].get("percent"))
        if before is None or after is None:
            continue
        if abs(after - before) >= OWNERSHIP_TOLERANCE_PP:
            out.append(_change(
                "ty_le_so_huu_doi", "cao" if abs(after - before) >= 5 else "vua",
                f"Tỷ lệ sở hữu đổi: {holder}",
                f"{before:.2f}% → {after:.2f}% ({after - before:+.2f} điểm phần trăm)",
            ))
    return out


def _diff_text(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, str]]:
    before, after = old.get("text_chunks") or 0, new.get("text_chunks") or 0
    if after > before:
        return [_change(
            "van_ban_moi", "vua",
            f"Có thêm văn bản báo cáo: {after - before} đoạn",
            f"{before} → {after} đoạn. Thường là một báo cáo thường niên vừa được nạp.",
        )]
    if after < before:
        # Giảm là chuyện BẤT THƯỜNG — không có luồng nào xóa bớt đoạn văn trong vận hành
        # bình thường. Nhiều khả năng một lần nạp lại đã hỏng giữa chừng.
        return [_change(
            "van_ban_mat", "cao",
            f"Văn bản báo cáo GIẢM: {before} → {after} đoạn",
            "Không luồng nạp nào xóa bớt đoạn văn khi chạy đúng — nhiều khả năng một lần "
            "nạp lại đã hỏng giữa chừng. Nên kiểm tra kho vector.",
        )]
    return []


def compare(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, str]]:
    """Toàn bộ phép so sánh. Tách riêng khỏi I/O để kiểm thử được mà không cần dịch vụ."""
    return (_diff_financials(old, new) + _diff_ownership(old, new)
            + _diff_text(old, new))


# ------------------------------------------------------------------------ kiểm tra


_SEVERITY_ORDER = {"cao": 0, "vua": 1, "thap": 2}


def check(ticker: str, save: bool = True) -> Dict[str, Any]:
    """Kiểm tra một mã: chụp ảnh mới, so với ảnh cũ, ghi lại cảnh báo.

    `save=False` để chạy thử mà không động vào ảnh nền — có ích khi muốn xem lại cùng một
    khác biệt nhiều lần trong lúc gỡ lỗi. Chạy thật thì PHẢI lưu, nếu không mỗi lần kiểm
    tra lại báo y nguyên những thay đổi đã báo hôm trước.
    """
    ticker = ticker.upper()
    current = fingerprint(ticker)
    previous = _load_fingerprint(ticker)

    if previous is None:
        if save:
            _save_fingerprint(ticker, current)
        # Xem chú thích đầu tệp: lần đầu chỉ lập nền, không báo gì.
        return {"status": "baseline", "ticker": ticker, "changes": [],
                "note": "Lần đầu theo dõi mã này — đã lưu ảnh nền, so sánh bắt đầu từ "
                        "lần kiểm tra sau."}

    changes = compare(previous, current)
    changes.sort(key=lambda c: _SEVERITY_ORDER.get(c["severity"], 9))

    if save:
        _save_fingerprint(ticker, current)
        conn = _connect()
        try:
            now = time.time()
            conn.executemany(
                "INSERT INTO alerts (ticker, kind, severity, title, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(ticker, c["kind"], c["severity"], c["title"], c["detail"], now)
                 for c in changes],
            )
            conn.execute("UPDATE watchlist SET last_checked = ? WHERE ticker = ?",
                         (now, ticker))
            conn.commit()
        finally:
            conn.close()

    return {"status": "ok", "ticker": ticker, "changes": changes,
            "checked_at": time.time()}


def check_all(save: bool = True) -> Dict[str, Any]:
    """Kiểm tra toàn bộ danh sách theo dõi. Một mã hỏng KHÔNG làm dừng các mã còn lại."""
    results, failures = [], []
    for row in watchlist():
        try:
            results.append({**check(row["ticker"], save=save), "company": row["company"]})
        except Exception as exc:  # noqa: BLE001 — xem chú thích trên
            # Gộp lỗi của một mã vào danh sách riêng thay vì để nó dừng cả vòng: khi chạy
            # tự động sau mỗi lần cập nhật, một mã hỏng mà chặn mười chín mã kia thì lần
            # chạy đó coi như mất trắng.
            failures.append({"ticker": row["ticker"], "error": str(exc)})
    total = sum(len(r["changes"]) for r in results)
    return {"status": "ok", "checked": len(results), "changes_total": total,
            "results": results, "failures": failures}


def recent_alerts(ticker: Optional[str] = None, days: float = 30.0,
                  limit: int = 50) -> List[Dict[str, Any]]:
    """Cảnh báo đã ghi trong khoảng thời gian gần đây."""
    since = time.time() - days * 86400.0
    conn = _connect()
    try:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM alerts WHERE ticker = ? AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT ?",
                (ticker.upper(), since, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM alerts WHERE created_at >= ? ORDER BY created_at DESC "
                "LIMIT ?", (since, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

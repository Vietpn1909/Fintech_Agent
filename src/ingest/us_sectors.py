"""Mã ngành SIC cho doanh nghiệp Mỹ — để doanh nghiệp Mỹ làm TÍN HIỆU NGÀNH cho Việt Nam.

VÌ SAO CẦN

Doanh nghiệp Việt Nam có trường `sector` (13 ngành), doanh nghiệp Mỹ thì không có gì.
Muốn hỏi "ngành thép thế giới năm nay ra sao" để đánh giá HPG thì phải biết doanh nghiệp
Mỹ nào là doanh nghiệp thép. SEC gắn sẵn cho mỗi doanh nghiệp một mã SIC (Standard
Industrial Classification) trong hồ sơ đăng ký — đó là nguồn chính thức, cùng nguồn với
toàn bộ dữ liệu Mỹ đang dùng, nên không thêm loại độ tin cậy nào mới.

⚠️ SIC LÀ MÃ DOANH NGHIỆP TỰ KHAI KHI ĐĂNG KÝ, VÀ NÓ CŨ

Một doanh nghiệp đăng ký là "phần mềm" từ 1995 có thể nay chủ yếu bán phần cứng; SEC
không cập nhật theo. Chấp nhận được vì ở đây ta lấy TRUNG VỊ của cả nhóm hàng chục doanh
nghiệp — vài doanh nghiệp xếp lệch không đổi được trung vị — nhưng phải nói ra, không giấu.

Mỗi mã lấy xong được ghi ngay xuống `data/processed/us_sic.jsonl`, nên mất mạng giữa
chừng thì chạy lại chỉ lấy tiếp phần còn thiếu.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from config.settings import PROCESSED_DIR
from src.ingest.edgar import SUBMISSIONS_URL, _get

CACHE_PATH = PROCESSED_DIR / "us_sic.jsonl"


def fetch_sic(cik: str) -> Dict[str, Any]:
    """Mã SIC và mô tả cho một CIK. Lỗi thì trả về bản ghi có `error`, không ném ra."""
    cik10 = str(cik).strip().zfill(10)
    try:
        data = _get(SUBMISSIONS_URL.format(cik10=cik10)).json()
    except Exception as exc:  # noqa: BLE001
        # Ghi lại lỗi thay vì bỏ qua: lần chạy sau biết mã nào cần thử lại, và tổng kết
        # nói đúng được bao nhiêu mã KHÔNG có ngành — thay vì lặng lẽ thiếu.
        return {"cik": cik10, "error": str(exc)[:200]}
    sic = (data.get("sic") or "").strip()
    return {
        "cik": cik10,
        "sic": int(sic) if sic.isdigit() else None,
        "sic_desc": data.get("sicDescription") or None,
    }


def load_cache() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not CACHE_PATH.exists():
        return out
    for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        out[row["cik"]] = row
    return out


def fetch_all(ciks: Iterable[str], progress=None) -> Dict[str, Dict[str, Any]]:
    """Lấy SIC cho cả danh sách, BỎ QUA mã đã lấy thành công ở lần trước."""
    cache = load_cache()
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    pending = [str(c).zfill(10) for c in ciks
               if str(c).zfill(10) not in cache or "error" in cache[str(c).zfill(10)]]
    with CACHE_PATH.open("a", encoding="utf-8") as fh:
        for i, cik in enumerate(pending, 1):
            row = fetch_sic(cik)
            cache[cik] = row
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            if progress:
                progress(i, len(pending), row)
    return cache


def write_to_graph(rows: List[Dict[str, Any]]) -> int:
    """Gắn `sic` và `sic_desc` lên node Company theo CIK."""
    from src.graph.store import GraphStore

    good = [r for r in rows if r.get("sic")]
    if not good:
        return 0
    g = GraphStore()
    try:
        g.run(
            """
            UNWIND $rows AS row
            MATCH (c:Company)
            WHERE c.cik = row.cik OR c.cik = toString(toInteger(row.cik))
            SET c.sic = row.sic, c.sic_desc = row.sic_desc
            """,
            rows=good,
        )
    finally:
        g.close()
    return len(good)

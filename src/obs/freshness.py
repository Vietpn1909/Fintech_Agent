"""Dữ liệu hiện có mới tới đâu — đo bằng chính dữ liệu, không tin vào sổ ghi chép.

VÌ SAO KHÔNG CHỈ DỰA VÀO "LẦN CHẠY GẦN NHẤT"

Cách dễ nhất để biết dữ liệu có cũ không là xem lần cuối chạy script nạp là khi nào. Cách
đó sai ở đúng chỗ nguy hiểm: script chạy XONG không có nghĩa là dữ liệu VÀO được. Nó có
thể chạy ở chế độ thử, có thể lỗi giữa chừng sau khi đã ghi một nửa, có thể ghi vào một
collection khác vì gõ nhầm tham số.

Nên mỗi nguồn ở đây được đo bằng hai thứ độc lập:

    trạng thái THẬT   đếm thẳng trong Neo4j/Qdrant — cái này không nói dối
    lần chạy cuối     đọc từ sổ ghi chép `data/refresh_state.json`

Khi hai thứ lệch nhau thì chính sự lệch đó là tín hiệu: "chạy 2 ngày trước nhưng số bản
ghi không đổi" nghĩa là lần chạy ấy đã hỏng mà không ai biết.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
STATE_PATH = ROOT / "data" / "refresh_state.json"


def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Sổ hỏng thì coi như chưa từng chạy — thà chạy thừa một lần còn hơn dừng hẳn.
        return {}


def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def record_run(step: str, status: str, **fields: Any) -> None:
    state = load_state()
    state[step] = {"at": time.time(), "status": status, **fields}
    save_state(state)


def age_days(step: str) -> Optional[float]:
    entry = load_state().get(step)
    if not entry or "at" not in entry:
        return None
    return (time.time() - float(entry["at"])) / 86400.0


def snapshot() -> Dict[str, Any]:
    """Ảnh chụp trạng thái THẬT của kho dữ liệu. Chỉ đọc, không sửa gì.

    Mỗi con số ở đây là một thứ có thể đối chiếu trước/sau một lần cập nhật. Không có
    chúng thì "đã cập nhật xong" chỉ là lời nói.
    """
    from src.graph.store import GraphStore
    from src.vector.store import (
        VN_PROFILE_COLLECTION, VN_REPORT_COLLECTION, VN_REPORT_DIM, VN_REPORT_MODEL,
        VectorStore,
    )

    out: Dict[str, Any] = {}
    graph = GraphStore()
    try:
        out["companies_us"] = graph.run(
            "MATCH (c:Company) WHERE c.cik IS NOT NULL RETURN count(c) AS n")[0]["n"]
        out["companies_vn"] = graph.run(
            "MATCH (c:Company {market:'VN'}) RETURN count(c) AS n")[0]["n"]
        out["financial_years"] = graph.run(
            "MATCH ()-[:HAS_FINANCIALS]->(f) RETURN count(f) AS n")[0]["n"]
        # Năm tài chính mới nhất, LỌC BỎ giá trị vô lý — xem `anomalies()` bên dưới.
        out["vn_latest_year"] = graph.run(
            "MATCH (c:Company {market:'VN'})-[:HAS_FINANCIALS]->(f) "
            "WHERE f.fiscal_year >= 1990 AND f.fiscal_year <= 2100 "
            "RETURN max(f.fiscal_year) AS y")[0]["y"]
        out["us_latest_year"] = graph.run(
            "MATCH (c:Company)-[:HAS_FINANCIALS]->(f) WHERE c.cik IS NOT NULL "
            "AND f.fiscal_year >= 1990 AND f.fiscal_year <= 2100 "
            "RETURN max(f.fiscal_year) AS y")[0]["y"]
        out["latest_filing_date"] = graph.run(
            "MATCH (f:Filing) RETURN max(f.filing_date) AS d")[0]["d"]
        out["ownership_edges"] = graph.run(
            "MATCH ()-[r:OWNED_BY]->() RETURN count(r) AS n")[0]["n"]
    finally:
        graph.close()

    out["chunks_10k"] = VectorStore().count()
    out["chunks_vn_profile"] = VectorStore(collection=VN_PROFILE_COLLECTION).count()
    out["chunks_vn_reports"] = VectorStore(
        collection=VN_REPORT_COLLECTION, model_name=VN_REPORT_MODEL, dim=VN_REPORT_DIM).count()
    return out


def anomalies() -> Dict[str, Any]:
    """Những thứ trong dữ liệu mà chỉ nhìn tổng số thì không thấy.

    ⚠️ CẬP NHẬT ĐỊNH KỲ MÀ KHÔNG KIỂM CHẤT LƯỢNG THÌ CHỈ LÀ TÍCH THÊM RÁC ĐỀU ĐẶN.

    Ví dụ có thật đang nằm trong đồ thị: PRTH có hai năm tài chính ghi là 43465 và 43830
    — đó là SỐ SÊ-RI NGÀY của Excel (2018-12-31 và 2019-12-31), lọt vào từ XBRL do chính
    doanh nghiệp khai sai. Chúng vô hình trước mọi phép đếm, nhưng `max(fiscal_year)` thì
    trả về 43830, và bất kỳ logic nào hỏi "dữ liệu mới nhất tới năm nào" đều nhận câu trả
    lời vô nghĩa.
    """
    from src.graph.store import GraphStore

    graph = GraphStore()
    try:
        bad_year = graph.run(
            "MATCH (c:Company)-[:HAS_FINANCIALS]->(f:FinancialYear) "
            "WHERE f.fiscal_year < 1990 OR f.fiscal_year > 2100 "
            "RETURN c.ticker AS ticker, f.fiscal_year AS year LIMIT 20")
        no_currency = graph.run(
            "MATCH (c:Company {market:'VN'})-[:HAS_FINANCIALS]->(f) "
            "WHERE f.currency IS NULL RETURN count(f) AS n")[0]["n"]
        orphan_fy = graph.run(
            "MATCH (f:FinancialYear) WHERE NOT ()-[:HAS_FINANCIALS]->(f) "
            "RETURN count(f) AS n")[0]["n"]
    finally:
        graph.close()

    return {
        "implausible_fiscal_year": bad_year,
        "vn_years_without_currency": no_currency,
        "orphan_financial_years": orphan_fy,
    }

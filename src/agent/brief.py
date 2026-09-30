"""Hồ sơ phân tích tự động — agent tự chạy nhiều bước thay vì chờ hỏi từng câu.

VÌ SAO KHÔNG PHẢI MỘT CÂU HỎI DÀI

Người dùng muốn biết "doanh nghiệp này thế nào" phải gõ ít nhất năm câu: số liệu mấy năm,
rủi ro doanh nghiệp tự nêu, ai là cổ đông, đối thủ là ai, hệ thống có gì chưa có. Mỗi câu
là một lượt chờ, và người không quen sẽ không biết phải hỏi gì.

Tệ hơn: gộp tất cả vào MỘT câu hỏi dài thì khối định tuyến chỉ được gọi tối đa ba công cụ
mỗi vòng, nên nó sẽ tự chọn ba thứ nó cho là quan trọng rồi bỏ phần còn lại — im lặng.

⚠️ AI QUYẾT ĐỊNH CHẠY GÌ: MÃ LỆNH, KHÔNG PHẢI LLM

Đây là khác biệt với khối định tuyến của agent hỏi–đáp. Ở đó LLM chọn công cụ vì câu hỏi
có thể là bất cứ thứ gì. Ở đây câu hỏi luôn là "doanh nghiệp này thế nào", nên danh sách
việc phải làm là CỐ ĐỊNH và viết sẵn được. Để LLM tự chọn chỉ thêm một chỗ hỏng mà không
thêm khả năng nào.

Và MỌI CON SỐ đều do mã lệnh tính: tăng trưởng, biên lợi nhuận, tỷ lệ sở hữu. LLM chỉ
nhận số đã tính rồi viết thành câu. Nhờ vậy lớp đối chiếu ở `verify.py` vẫn soi được từng
con số trong lời văn — nếu để LLM tự nhẩm thì nó sẽ sinh ra những con số không có trong
dữ liệu nguồn, và lớp đối chiếu sẽ kêu đúng, chỉ là kêu về chính lỗi ta tự tạo ra.

⚠️ MỘT MỤC BẮT BUỘC: "ĐIỀU HỆ THỐNG KHÔNG BIẾT"

Một hồ sơ trông đầy đủ là thứ nguy hiểm: người đọc mặc định những gì không được nhắc tới
là không đáng kể. Trong khi thực tế có thể là hệ thống không có dữ liệu quý, không có giá
cổ phiếu, hoặc báo cáo thường niên mới nhất đọc được đã bốn năm tuổi. Mục cuối nói thẳng
ra, và nó được sinh từ chính dữ liệu thiếu chứ không phải viết tay.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from src.agent.tools import (
    company_coverage, graph_neighbors, lookup_financials, search_filings,
)

# Chỉ tiêu đưa vào bảng xu hướng. Ít mà đúng trọng tâm hơn là đủ mười bốn: doanh thu và
# lợi nhuận cho biết quy mô và hiệu quả, tổng tài sản và vốn chủ cho biết cấu trúc.
TREND_METRICS = ["revenue", "gross_profit", "net_income", "total_assets",
                 "stockholders_equity"]

# Ngân hàng không có `gross_profit`; bỏ qua chỉ tiêu thiếu chứ không coi là lỗi.
DEFAULT_YEARS = 5


def _pct(new: float, old: float) -> Optional[float]:
    """Tăng trưởng phần trăm. None khi không tính được — KHÔNG trả 0."""
    if old in (None, 0) or new is None:
        return None
    return (new - old) / abs(old) * 100.0


def _cagr(new: float, old: float, years: int) -> Optional[float]:
    """Tăng trưởng kép. Chỉ tính khi cả hai đầu đều dương — lỗ thì CAGR vô nghĩa.

    Đây là chỗ rất dễ sinh ra con số trông hợp lý mà vô nghĩa: doanh nghiệp lỗ 100 tỷ rồi
    lãi 50 tỷ thì công thức vẫn ra một số, nhưng số đó không nói lên điều gì.
    """
    if not old or not new or old <= 0 or new <= 0 or years <= 0:
        return None
    return ((new / old) ** (1.0 / years) - 1.0) * 100.0


def collect_financials(ticker: str, years: int = DEFAULT_YEARS) -> Dict[str, Any]:
    """Số liệu nhiều năm + các con số phái sinh, TÍNH BẰNG MÃ LỆNH."""
    raw = lookup_financials(ticker)
    if raw.get("status") != "ok":
        return {"status": raw.get("status"), "detail": raw}

    rows = sorted(raw.get("years", []), key=lambda r: r.get("fiscal_year", 0))
    rows = rows[-years:]
    if not rows:
        return {"status": "no_data"}

    first, last = rows[0], rows[-1]
    span = (last.get("fiscal_year", 0) - first.get("fiscal_year", 0)) or 1

    derived: Dict[str, Any] = {}
    for metric in TREND_METRICS:
        a, b = first.get(metric), last.get(metric)
        if a is None or b is None:
            continue
        change, cagr = _pct(b, a), _cagr(b, a, span)
        # ⚠️ LÀM TRÒN TRƯỚC KHI ĐƯA CHO LLM, không phải lúc hiển thị.
        #
        # Đưa 96.62985909861554 thì mô hình chép nguyên xi vào câu văn — "mức tăng trưởng
        # 96,62985909861554%". Vừa khó đọc, vừa giả vờ chính xác tới mười bốn chữ số cho
        # một phép chia hai con số làm tròn tới đồng.
        derived[metric] = {
            "first_year": first.get("fiscal_year"), "first_value": a,
            "last_year": last.get("fiscal_year"), "last_value": b,
            "change_pct": round(change, 1) if change is not None else None,
            "cagr_pct": round(cagr, 1) if cagr is not None else None,
        }

    # Biên lợi nhuận của NĂM GẦN NHẤT. Tính ở đây thay vì để LLM nhẩm: nó là phép chia
    # đơn giản, nhưng một lần nhẩm sai là một con số sai có dẫn nguồn đầy đủ.
    margins = {}
    revenue = last.get("revenue")
    if revenue:
        for name, metric in (("gross_margin_pct", "gross_profit"),
                             ("net_margin_pct", "net_income"),
                             ("operating_margin_pct", "operating_income")):
            if last.get(metric) is not None:
                margins[name] = round(last[metric] / revenue * 100.0, 1)

    return {
        "status": "ok",
        "ticker": raw.get("ticker"), "company": raw.get("company"),
        "source": raw.get("source"), "currency": last.get("currency") or raw.get("currency"),
        "years_covered": [r.get("fiscal_year") for r in rows],
        "latest": last,
        "trend": derived,
        "margins_latest": margins,
    }


def collect_ownership(ticker: str, limit: int = 8) -> Dict[str, Any]:
    """Cổ đông và các bên liên quan qua cạnh OWNED_BY.

    ⚠️ Đồ thị có cạnh theo CẢ HAI CHIỀU: "ai nắm doanh nghiệp này" và "doanh nghiệp này
    nắm ai". Gộp chung là cách nhanh nhất để đảo ngược ý nghĩa — lỗi đã từng xảy ra thật
    ("FPT Telecom nắm 45,7% của FPT", trong khi sự thật ngược lại).
    """
    result = graph_neighbors(entity=ticker, relations=["OWNED_BY"], limit=30)
    if result.get("status") not in ("ok", "no_relations"):
        return {"status": result.get("status"), "detail": result}

    holders, holdings = [], []
    for rel in result.get("relations", []):
        # ⚠️ `percent` trên cạnh là PHÂN SỐ (0.4654), không phải phần trăm. Đưa thẳng vào
        # lời văn thì thành "nắm 0,47%" thay vì 46,5% — sai một trăm lần mà vẫn trông như
        # một con số hợp lý, và lớp đối chiếu sẽ không bắt được vì nó có trong nguồn.
        raw_pct = rel.get("percent")
        try:
            pct = float(raw_pct) * 100.0 if raw_pct not in (None, "") else None
        except (TypeError, ValueError):
            pct = None
        entry = {"name": rel.get("neighbor"), "evidence": rel.get("evidence"),
                 "percent": round(pct, 2) if pct is not None else None,
                 "as_of": rel.get("as_of")}
        # `direction` nói cạnh đi vào hay đi ra khỏi doanh nghiệp đang xét.
        (holders if rel.get("direction") == "outgoing" else holdings).append(entry)

    # Cổ đông lớn nhất lên đầu. Cạnh thiếu tỷ lệ xuống cuối thay vì nhảy lên đầu.
    holders.sort(key=lambda x: x["percent"] if x["percent"] is not None else -1, reverse=True)
    holdings.sort(key=lambda x: x["percent"] if x["percent"] is not None else -1, reverse=True)

    return {
        "status": "ok",
        "entity": result.get("entity"),
        "holders": holders[:limit],          # ai nắm cổ phần của doanh nghiệp này
        "holdings": holdings[:limit],        # doanh nghiệp này nắm cổ phần ở đâu
        "total_edges": len(result.get("relations", [])),
    }


def collect_relations(ticker: str, limit: int = 10) -> Dict[str, Any]:
    """Quan hệ KINH DOANH — cố ý loại OWNED_BY ra khỏi mục này."""
    result = graph_neighbors(entity=ticker, limit=30)
    if result.get("status") not in ("ok", "no_relations"):
        return {"status": result.get("status"), "detail": result}
    rels = [r for r in result.get("relations", []) if r.get("relation") != "OWNED_BY"]
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for rel in rels[:limit * 2]:
        by_type.setdefault(rel.get("relation", "?"), []).append({
            "neighbor": rel.get("neighbor"), "evidence": (rel.get("evidence") or "")[:220],
        })
    return {"status": "ok", "by_type": by_type, "total": len(rels)}


def collect_text(ticker: str, query: str, top_k: int = 4) -> Dict[str, Any]:
    """Trích đoạn báo cáo. Giữ nguyên nhãn nguồn để hồ sơ dẫn được tới số trang."""
    result = search_filings(query=query, companies=[ticker], top_k=top_k, auto_ingest=False)
    if result.get("status") != "ok":
        return {"status": result.get("status"), "detail": result}
    return {
        "status": "ok",
        "hits": [
            {"title": h.get("item_title"), "year": h.get("fiscal_year"),
             "item": h.get("item"), "text": (h.get("text") or "")[:600],
             "note": h.get("source_note")}
            for h in result.get("results", [])
        ],
    }


def collect_peers(ticker: str) -> Dict[str, Any]:
    """Vị trí của doanh nghiệp trong nhóm cùng loại.

    Nhập muộn (trong thân hàm) để tránh vòng nhập: `peers` nhập từ `tools`, còn `tools`
    nhập `brief` khi công cụ `company_brief` được gọi.
    """
    from src.agent.peers import benchmark

    try:
        return benchmark(ticker)
    except Exception as exc:  # noqa: BLE001
        # Một bước hỏng KHÔNG được làm hỏng cả hồ sơ. Năm bước kia vẫn có giá trị, và
        # trạng thái lỗi ở đây sẽ hiện ra thành một mục nói rõ lý do chứ không biến mất.
        return {"status": "loi", "ly_do": str(exc)[:200]}


def gaps_from(coverage: Dict[str, Any], financials: Dict[str, Any],
              risks: Dict[str, Any], ownership: Dict[str, Any],
              relations: Optional[Dict[str, Any]] = None,
              peers: Optional[Dict[str, Any]] = None) -> List[str]:
    """Những thứ hệ thống KHÔNG có — sinh từ dữ liệu thiếu, không viết tay.

    Một hồ sơ trông đầy đủ khiến người đọc mặc định phần không được nhắc là không đáng
    kể. Mục này tồn tại để phá đúng giả định đó.
    """
    out: List[str] = []
    if financials.get("status") == "ok":
        years = financials.get("years_covered") or []
        out.append(f"Chỉ có số liệu NĂM ({len(years)} năm: {min(years)}–{max(years)}), "
                   f"không có số liệu quý.")
    else:
        out.append("Không có số liệu tài chính nào.")

    if risks.get("status") != "ok" or not risks.get("hits"):
        out.append("Không có văn bản báo cáo thường niên — không trả lời được câu hỏi về "
                   "rủi ro hay chiến lược do doanh nghiệp tự nêu.")
    else:
        years = {h.get("year") for h in risks["hits"] if h.get("year")}
        if years:
            newest = max(int(y) for y in years)
            age = time.localtime().tm_year - newest
            if age >= 2:
                out.append(f"Báo cáo thường niên mới nhất đọc được là năm {newest}, "
                           f"cách hiện tại {age} năm.")
        if any("OCR" in (h.get("title") or "") for h in risks["hits"]):
            out.append("Chữ trong báo cáo được máy đọc từ bản scan (OCR) nên có thể sai "
                       "chính tả.")

    if ownership.get("status") != "ok" or not ownership.get("holders"):
        out.append("Không có dữ liệu cổ đông.")

    # Quan hệ kinh doanh (đối thủ, nhà cung cấp, khách hàng) được trích từ văn bản hồ sơ
    # SEC, nên doanh nghiệp Việt Nam gần như luôn trống mục này. Nói thẳng ra, vì một mục
    # trống không có lời giải thích trông y hệt "doanh nghiệp này không có đối thủ nào".
    if not (relations or {}).get("by_type"):
        out.append("Không có quan hệ kinh doanh (đối thủ, nhà cung cấp, khách hàng) trong "
                   "đồ thị — những quan hệ này được trích từ văn bản hồ sơ SEC, nên doanh "
                   "nghiệp không niêm yết tại Mỹ thường không có.")

    if (peers or {}).get("status") != "ok":
        out.append("Không dựng được nhóm doanh nghiệp cùng loại để so sánh, nên mọi con "
                   "số ở trên chỉ đứng một mình — không biết chúng là cao hay thấp so "
                   "với những doanh nghiệp tương tự.")
    elif peers.get("loai_vi_khac_dong_tien"):
        out.append(f"{peers['loai_vi_khac_dong_tien']} doanh nghiệp cùng loại bị loại "
                   "khỏi bảng so sánh vì báo cáo bằng đồng tiền khác.")

    out.append("Không có giá cổ phiếu, vốn hóa hay chỉ số định giá — hệ thống chỉ đọc báo "
               "cáo tài chính và văn bản công bố.")
    return out


def collect(company: str, years: int = DEFAULT_YEARS) -> Dict[str, Any]:
    """Chạy toàn bộ các bước thu thập. Danh sách bước là CỐ ĐỊNH, xem chú thích đầu tệp."""
    started = time.time()
    coverage = company_coverage(company)
    if coverage.get("status") not in ("ok", None):
        # ambiguous / not_found / backend_unavailable — trả nguyên trạng để bên gọi xử lý
        return {"status": coverage.get("status"), "company": company, "detail": coverage}

    ticker = coverage.get("ticker") or company
    financials = collect_financials(ticker, years)
    risks = collect_text(ticker, "rủi ro chính và quản trị rủi ro")
    strategy = collect_text(ticker, "chiến lược phát triển và định hướng")
    ownership = collect_ownership(ticker)
    relations = collect_relations(ticker)
    peers = collect_peers(ticker)

    return {
        "status": "ok",
        "ticker": ticker,
        "company": coverage.get("company") or financials.get("company") or company,
        "coverage": coverage,
        "financials": financials,
        "risks": risks,
        "strategy": strategy,
        "ownership": ownership,
        "relations": relations,
        "peers": peers,
        "gaps": gaps_from(coverage, financials, risks, ownership, relations, peers),
        "seconds": round(time.time() - started, 1),
        "tool_calls": 7,
    }

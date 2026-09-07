"""Bộ công cụ của agent.

Mỗi công cụ là một hàm Python thuần, KHÔNG gọi LLM. Nhờ vậy chúng kiểm thử được độc lập
và cho kết quả xác định — LLM chỉ quyết định GỌI công cụ nào với tham số gì, còn việc
lấy dữ liệu luôn là mã lệnh thường.

MỘT QUYẾT ĐỊNH QUAN TRỌNG: KHÔNG ĐỂ LLM VIẾT CYPHER

Cách làm phổ biến là đưa lược đồ đồ thị cho LLM rồi bảo nó sinh câu Cypher. Với model
lớn chạy trên cloud thì tạm ổn, nhưng với model 12-24B chạy local thì hỏng theo ba
đường cùng lúc:

    1. Sinh sai cú pháp -> truy vấn lỗi, agent bí.
    2. Sinh đúng cú pháp nhưng sai ngữ nghĩa (nhầm chiều cạnh, sai tên thuộc tính)
       -> trả về kết quả rỗng hoặc sai mà KHÔNG có dấu hiệu gì.
    3. Rủi ro chèn lệnh: một câu hỏi khéo léo có thể khiến LLM sinh ra lệnh xóa dữ liệu.

Ở đây làm ngược lại: các câu Cypher được viết sẵn và tham số hóa. LLM chỉ chọn công cụ
và điền tham số có kiểu rõ ràng (mã chứng khoán, tên chỉ tiêu, năm, ngưỡng). Ít linh
hoạt hơn, nhưng đổi lại là thứ mà đồ án cần: kết quả đúng và lặp lại được.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.graph.schema import RELATION_NAMES
from src.graph.store import GraphStore
from src.ingest.on_demand import ensure_text_available, is_text_indexed, resolve_ticker
from src.ingest.xbrl import METRIC_LABELS
from src.vector.store import VectorStore

# Toán tử cho phép trong bộ lọc sàng lọc. Danh sách trắng, không nhận chuỗi tùy ý.
_OPERATORS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "="}

_graph: Optional[GraphStore] = None
_vectors: Optional[VectorStore] = None


def graph() -> GraphStore:
    global _graph
    if _graph is None:
        _graph = GraphStore()
    return _graph


def vectors() -> VectorStore:
    global _vectors
    if _vectors is None:
        _vectors = VectorStore()
    return _vectors


# --------------------------------------------------------------------- tra cứu số liệu


def lookup_financials(
    company: str,
    metrics: Optional[List[str]] = None,
    years: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Tra số liệu tài chính chính xác từ dữ liệu XBRL đã nạp.

    Đây là công cụ agent PHẢI dùng cho mọi câu hỏi về con số. Số trả về là số doanh
    nghiệp tự khai với SEC, kèm số hiệu bản khai để truy vết.
    """
    candidates = resolve_ticker(company)
    if not candidates:
        return {"status": "not_found", "query": company}

    best = candidates[0]
    rows = graph().run(
        """
        MATCH (c:Company {ticker: $ticker})-[:HAS_FINANCIALS]->(fy:FinancialYear)
        WHERE $years IS NULL OR fy.fiscal_year IN $years
        RETURN fy AS data ORDER BY fy.fiscal_year DESC LIMIT 12
        """,
        ticker=best["ticker"], years=years,
    )
    if not rows:
        return {"status": "no_data", "ticker": best["ticker"], "company": best["name"]}

    wanted = metrics or list(METRIC_LABELS.keys())
    records = []
    for row in rows:
        data = row["data"]
        entry = {
            "fiscal_year": data.get("fiscal_year"),
            "period_end": data.get("period_end"),
            "accession": data.get("accession"),
            "derived": data.get("derived", []),
        }
        for m in wanted:
            if data.get(m) is not None:
                entry[m] = data[m]
        records.append(entry)

    return {
        "status": "ok",
        "ticker": best["ticker"],
        "company": best["name"],
        "source": "XBRL do doanh nghiệp khai báo với SEC",
        "years": records,
    }


def compare_financials(
    companies: List[str], metric: str = "revenue", year: Optional[int] = None
) -> Dict[str, Any]:
    """So sánh một chỉ tiêu giữa nhiều doanh nghiệp trong cùng một năm.

    Công cụ này BÁO RÕ doanh nghiệp nào không có số liệu, thay vì lặng lẽ bỏ qua.

    Lý do: không phải doanh nghiệp nào cũng khai đủ mọi chỉ tiêu. Amazon không dùng mã
    ResearchAndDevelopmentExpense — họ gộp vào khoản "Technology and infrastructure".
    Nếu công cụ im lặng bỏ Amazon ra khỏi bảng so sánh chi phí R&D, agent sẽ kết luận
    "Apple chi cho R&D nhiều nhất trong nhóm", một câu SAI mà không ai phát hiện được.
    Trả về danh sách thiếu để agent nói rõ phạm vi so sánh.
    """
    resolved, name_map, unresolved = [], {}, []
    for name in companies:
        cands = resolve_ticker(name)
        if cands:
            resolved.append(cands[0]["ticker"])
            name_map[cands[0]["ticker"]] = cands[0]["name"]
        else:
            unresolved.append(name)

    if not resolved:
        return {"status": "not_found", "query": companies}

    result = graph().run(
        """
        MATCH (c:Company)-[:HAS_FINANCIALS]->(fy:FinancialYear)
        WHERE c.ticker IN $tickers AND fy[$metric] IS NOT NULL
          AND ($year IS NULL OR fy.fiscal_year = $year)
        RETURN c.ticker AS ticker, c.name AS company,
               fy.fiscal_year AS fiscal_year, fy[$metric] AS value,
               coalesce(fy.currency, 'USD') AS currency
        ORDER BY fy.fiscal_year DESC, value DESC
        """,
        tickers=resolved, metric=metric, year=year,
    )
    found = {r["ticker"] for r in result}

    # ⚠️ KHÔNG BAO GIỜ ĐƯỢC XẾP HẠNG SỐ LIỆU KHÁC ĐỒNG TIỀN.
    #
    # Doanh nghiệp ngoài Mỹ khai theo tiền bản địa: Toyota bằng JPY, ASML bằng EUR, TSMC
    # bằng TWD. Câu Cypher ORDER BY value DESC sẽ xếp Toyota (48.036 tỷ JPY) đứng trên
    # Apple (416 tỷ USD) — sai hoàn toàn, vì 48.036 tỷ JPY chỉ khoảng 320 tỷ USD.
    #
    # Hệ thống không có tỷ giá nên KHÔNG quy đổi. Thay vào đó nó nói thật: tách kết quả
    # theo đồng tiền và báo rõ rằng không so sánh trực tiếp được.
    currencies = sorted({r["currency"] for r in result})
    mixed = len(currencies) > 1
    return {
        "status": "ok" if result else "no_data",
        "metric": metric,
        "label": METRIC_LABELS.get(metric, metric),
        "rows": result,
        "currencies": currencies,
        "mixed_currency_warning": (
            "Các doanh nghiệp này báo cáo bằng những đồng tiền khác nhau "
            f"({', '.join(currencies)}). Hệ thống KHÔNG quy đổi tỷ giá, nên không được "
            "xếp hạng hay so sánh trực tiếp các con số này với nhau."
        ) if mixed else None,
        # Hai trường dưới đây để agent nói thật về phạm vi so sánh
        "missing_metric": [
            {"ticker": t, "company": name_map[t]} for t in resolved if t not in found
        ],
        "unresolved_names": unresolved,
    }


def screen_companies(
    filters: List[Dict[str, Any]],
    fiscal_year: int,
    order_by: str = "revenue",
    limit: int = 20,
    currency: str = "USD",
) -> Dict[str, Any]:
    """Sàng lọc toàn bộ ~6.000 doanh nghiệp theo điều kiện số liệu.

    filters: [{"metric": "revenue", "op": "gt", "value": 1e10}, ...]

    Đây là công cụ tận dụng độ phủ rộng của tầng số liệu — trả lời được những câu như
    "doanh nghiệp nào có doanh thu trên 10 tỷ và chi R&D trên 20% doanh thu năm 2024",
    điều mà vector search không bao giờ làm được.

    Tên chỉ tiêu và toán tử đều được kiểm tra với danh sách trắng trước khi ghép vào
    câu Cypher, nên không có đường chèn lệnh.

    ⚠️ BẮT BUỘC LỌC THEO ĐỒNG TIỀN, và đây là lỗi tôi đã sửa ở một chỗ mà bỏ sót ở đây.

    Sau khi hỗ trợ đa tiền tệ, câu hỏi "doanh nghiệp nào doanh thu trên 200 tỷ USD năm
    2024" trả về:

        EC    ECOPETROL S.A.              133.330  <- peso Colombia
        KEP   KOREA ELECTRIC POWER        92.578   <- won Hàn Quốc
        TM    TOYOTA MOTOR                45.095   <- yên Nhật

    Không một doanh nghiệp Mỹ nào lọt vào top 8, vì con số danh nghĩa bằng tiền bản địa
    luôn lớn hơn. Ngưỡng "200 tỷ" trở nên vô nghĩa khi mỗi dòng một đơn vị khác nhau.

    Đây là cùng một lớp lỗi đã được chặn ở compare_financials và ở câu kiểm chứng của
    script 05 — nhưng sửa triệu chứng ở hai nơi mà bỏ sót gốc rễ ở đây thì hệ thống vẫn
    trả lời sai. Mặc định lọc USD; muốn sàng lọc theo đồng tiền khác thì nêu rõ.
    """
    conditions, params = [], {
        "year": int(fiscal_year), "limit": int(limit), "currency": currency,
    }

    for i, f in enumerate(filters):
        metric, op = f.get("metric"), f.get("op", "gt")
        if metric not in METRIC_LABELS or op not in _OPERATORS:
            continue
        params[f"v{i}"] = float(f["value"])
        conditions.append(f"fy.`{metric}` IS NOT NULL AND fy.`{metric}` {_OPERATORS[op]} $v{i}")

    if not conditions:
        return {"status": "invalid_filters", "allowed_metrics": list(METRIC_LABELS.keys())}

    if order_by not in METRIC_LABELS:
        order_by = "revenue"

    rows = graph().run(
        f"""
        MATCH (c:Company)-[:HAS_FINANCIALS]->(fy:FinancialYear)
        WHERE fy.fiscal_year = $year
          AND coalesce(fy.currency, 'USD') = $currency
          AND {' AND '.join(conditions)}
        RETURN c.ticker AS ticker, c.name AS company, c.tier AS tier,
               coalesce(fy.currency, 'USD') AS currency,
               fy.revenue AS revenue, fy.net_income AS net_income,
               fy.`{order_by}` AS order_value
        ORDER BY order_value DESC LIMIT $limit
        """,
        **params,
    )
    return {
        "status": "ok", "fiscal_year": fiscal_year, "currency": currency,
        "matches": len(rows), "rows": rows,
        "note": f"Chỉ xét doanh nghiệp báo cáo bằng {currency}. "
                "Doanh nghiệp dùng đồng tiền khác không nằm trong kết quả này.",
    }


# --------------------------------------------------------------------- tìm kiếm văn bản


def search_filings(
    query: str,
    companies: Optional[List[str]] = None,
    items: Optional[List[str]] = None,
    years: Optional[List[int]] = None,
    top_k: int = 6,
    auto_ingest: bool = True,
) -> Dict[str, Any]:
    """Tìm kiếm ngữ nghĩa trong văn bản báo cáo.

    `auto_ingest` là điểm khiến công cụ này mang tính agentic: nếu người dùng hỏi về một
    doanh nghiệp chưa có trong vector store, công cụ TỰ ĐI LẤY (tải 10-K, bóc tách, nhúng
    vector — khoảng 40 giây) rồi mới tìm, thay vì trả lời "không có dữ liệu".

    ⚠️ HAI CƠ CHẾ CHỐNG HỎNG, cả hai đều sinh ra từ lỗi thật gặp khi chạy đánh giá.

    1. ÉP KIỂU THAM SỐ.
       LLM truyền `items: [1, 7]` (số nguyên) trong khi Qdrant lưu trường `item` là chuỗi
       ("1", "1A", "7"). Bộ lọc số nguyên không khớp chuỗi nào, nên trả về RỖNG TUYỆT ĐỐI
       dù dữ liệu nằm ngay đó. Không có ngoại lệ nào được ném ra — hệ thống chỉ bình thản
       báo "không tìm thấy". Đây là lỗi làm hỏng toàn bộ 5 câu hỏi định tính trong lần
       đánh giá đầu tiên.

    2. TỰ BỎ BỘ LỌC KHI KHÔNG CÓ KẾT QUẢ.
       Bộ lọc quá chặt (sai mục, sai năm) là chuyện thường khi LLM tự điền tham số. Trả
       về rỗng là kết cục tệ nhất; tìm lại không lọc rồi nói rõ đã nới điều kiện thì vẫn
       hữu ích. Trường `relaxed_filter` cho agent biết để trình bày trung thực.
    """
    tickers, ingested = None, []

    # Ép kiểu: chấp nhận 1, "1", "1A" và quy tất cả về chuỗi viết hoa
    if items:
        items = [str(i).strip().upper() for i in items if str(i).strip()]
    if years:
        coerced_years = []
        for y in years:
            try:
                coerced_years.append(int(y))
            except (TypeError, ValueError):
                continue
        years = coerced_years or None

    if companies:
        tickers = []
        for name in companies:
            cands = resolve_ticker(name)
            if not cands:
                continue
            ticker = cands[0]["ticker"]
            tickers.append(ticker)
            if auto_ingest and is_text_indexed(ticker, vectors()) == 0:
                result = ensure_text_available(ticker)
                if result.get("status") == "indexed":
                    ingested.append(
                        {"ticker": ticker, "chunks": result["chunks"], "seconds": result["seconds"]}
                    )

    hits = vectors().search(query, top_k=top_k, tickers=tickers, items=items, years=years)

    # Không có kết quả mà đang bật bộ lọc -> nới dần thay vì đầu hàng.
    # Thứ tự nới: bỏ năm trước (dễ sai nhất vì năm tài chính lệch năm dương lịch),
    # rồi bỏ mục, cuối cùng chỉ giữ lọc theo công ty.
    relaxed = None
    if not hits and (items or years):
        hits = vectors().search(query, top_k=top_k, tickers=tickers, items=items)
        relaxed = "đã bỏ lọc theo năm"
    if not hits and items:
        hits = vectors().search(query, top_k=top_k, tickers=tickers)
        relaxed = "đã bỏ lọc theo mục và năm"

    return {
        "status": "ok" if hits else "no_hits",
        "query": query,
        "relaxed_filter": relaxed,  # agent phải nói rõ nếu điều kiện đã bị nới
        "just_ingested": ingested,  # để agent nói thật là nó vừa đi lấy dữ liệu
        "results": [
            {
                "ticker": h["ticker"], "company": h["company"],
                "fiscal_year": h["fiscal_year"], "item": h["item"],
                "item_title": h["item_title"], "score": round(h["score"], 3),
                "text": h["text"], "chunk_id": h["chunk_id"],
            }
            for h in hits
        ],
    }


# --------------------------------------------------------------------- truy vấn đồ thị


def resolve_graph_entity(name: str, limit: int = 3) -> List[Dict]:
    """Tìm node trong đồ thị từ tên người dùng (hoặc LLM) đưa vào.

    ⚠️ PHÉP CHỨA CHUỖI THUẦN TÚY LÀ KHÔNG ĐỦ — lỗi thật gặp khi chạy đánh giá.

    Agent hỏi đường đi giữa "TSMC" và "Microsoft". Node trong đồ thị tên là
    "Taiwan Semiconductor Manufacturing Company", và "TSMC" KHÔNG phải chuỗi con của nó,
    nên find_entity trả về rỗng và công cụ báo `no_path` — trong khi đường đi
    TSMC -> NVIDIA -> Microsoft nằm sẵn trong đồ thị.

    Thất bại này còn nguy hiểm ở chỗ nó qua mặt được thước đo: câu trả lời vẫn nhắc đủ ba
    cái tên nên recall thực thể chấm 100%, dù năng lực suy luận bắc cầu đã hỏng hoàn toàn.

    Ba mức tra, dừng ở mức nào có kết quả:
      1. Khớp trực tiếp trong đồ thị (chứa chuỗi)
      2. Bảng viết tắt trong ontology: "TSMC" -> "Taiwan Semiconductor Manufacturing Company"
      3. Phân giải qua mã chứng khoán rồi tra lại bằng tên chính thức
    """
    from src.graph.schema import CANONICAL_COMPANIES

    # Bảng viết tắt phải tra TRƯỚC phép khớp trực tiếp.
    # "TSMC" khớp trực tiếp vào "TSMC Arizona Corporation" (công ty con) và "AMD" khớp
    # vào "AMD Zynq SoC" (một sản phẩm) — cả hai đều là tiền tố hợp lệ nhưng sai thực
    # thể. Tên viết tắt đã biết thì phải dùng tên đầy đủ, không để phép khớp chuỗi đoán.
    alias = CANONICAL_COMPANIES.get((name or "").strip().lower())
    if alias:
        via_alias = graph().find_entity(alias, limit=limit)
        if via_alias:
            return via_alias

    direct = graph().find_entity(name, limit=limit)
    if direct:
        return direct

    for candidate in resolve_ticker(name, limit=2):
        via_ticker = graph().run(
            """
            MATCH (c:Company)
            WHERE c.ticker = $ticker
              AND EXISTS { MATCH (c)-[r]-() WHERE type(r) <> 'HAS_FINANCIALS' }
            RETURN c.name AS name, labels(c)[0] AS type, c.ticker AS ticker
            LIMIT 1
            """,
            ticker=candidate["ticker"],
        )
        if via_ticker:
            return via_ticker

        # Tên chính thức của SEC thường dài hơn tên trong đồ thị; thử vài từ đầu
        head = " ".join(candidate["name"].split()[:3])
        via_name = graph().find_entity(head, limit=limit)
        if via_name:
            return via_name

    return []


def graph_neighbors(entity: str, relations: Optional[List[str]] = None, limit: int = 12) -> Dict[str, Any]:
    """Các thực thể nối trực tiếp với một thực thể, kèm câu văn làm bằng chứng.

    Mặc định 12 quan hệ, không phải 30. Mỗi quan hệ mang theo một câu bằng chứng, nên 30
    quan hệ là khoảng 6.000 ký tự đổ vào ngữ cảnh của LLM. Với model chạy local, chi phí
    nạp prompt tỷ lệ thuận với độ dài — và đo thật cho thấy nạp prompt chiếm gần như toàn
    bộ thời gian trả lời. 12 quan hệ đã đủ để lập luận, mà rẻ hơn một nửa.
    """
    if relations:
        relations = [r for r in relations if r in RELATION_NAMES]

    matches = resolve_graph_entity(entity)
    if not matches:
        return {"status": "entity_not_found", "entity": entity,
                "hint": "Thực thể chưa có trong đồ thị. Đồ thị chỉ phủ cụm doanh nghiệp trọng tâm."}

    name = matches[0]["name"]
    rows = graph().neighbors(name, relations=relations, limit=limit)
    # Bằng chứng chỉ cần đủ để người đọc kiểm chứng, không cần cả đoạn văn
    for row in rows:
        if row.get("evidence"):
            row["evidence"] = row["evidence"][:200]
    return {
        "status": "ok" if rows else "no_relations",
        "entity": name, "entity_type": matches[0]["type"],
        "also_matched": [m["name"] for m in matches[1:]],
        "relations": rows,
    }


def graph_path(source: str, target: str, max_hops: int = 3) -> Dict[str, Any]:
    """Chuỗi liên kết giữa hai thực thể — thứ RAG thuần không làm được.

    Vector search chỉ tìm được đoạn văn nhắc tới cả hai thực thể cùng lúc. Khi không có
    đoạn nào như vậy, nó bó tay. Đồ thị thì đi qua các mắt xích trung gian để dựng lại
    chuỗi liên kết, kèm bằng chứng cho từng mắt xích.
    """
    src = resolve_graph_entity(source, limit=1)
    tgt = resolve_graph_entity(target, limit=1)
    if not src or not tgt:
        return {"status": "entity_not_found",
                "missing": source if not src else target}

    paths = graph().path_between(src[0]["name"], tgt[0]["name"], max_hops=max_hops)
    return {
        "status": "ok" if paths else "no_path",
        "source": src[0]["name"], "target": tgt[0]["name"], "paths": paths,
    }


# --------------------------------------------------------------------- tiện ích


def company_coverage(company: str) -> Dict[str, Any]:
    """Cho biết hệ thống đang có gì về một doanh nghiệp — để agent nói thật với người dùng."""
    cands = resolve_ticker(company)
    if not cands:
        return {"status": "not_found", "query": company}

    best = cands[0]
    rows = graph().run(
        """
        MATCH (c:Company {ticker: $ticker})
        OPTIONAL MATCH (c)-[:HAS_FINANCIALS]->(fy:FinancialYear)
        RETURN c.tier AS tier, count(fy) AS years,
               min(fy.fiscal_year) AS first_year, max(fy.fiscal_year) AS last_year
        """,
        ticker=best["ticker"],
    )
    info = rows[0] if rows else {}
    return {
        "status": "ok",
        "ticker": best["ticker"], "company": best["name"],
        "tier": info.get("tier", "chưa có trong đồ thị"),
        "financial_years": info.get("years", 0),
        "year_range": [info.get("first_year"), info.get("last_year")],
        "text_chunks": is_text_indexed(best["ticker"], vectors()),
        "alternatives": [{"ticker": c["ticker"], "name": c["name"]} for c in cands[1:4]],
    }

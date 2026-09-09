"""Nạp dữ liệu theo yêu cầu — mở rộng độ phủ tới toàn bộ 8.001 doanh nghiệp.

VẤN ĐỀ NÀY GIẢI QUYẾT ĐIỀU GÌ

Lập chỉ mục trước văn bản 10-K của cả 8.001 doanh nghiệp tốn khoảng 16GB tải và một
ngày chạy liên tục, mà phần lớn trong số đó sẽ không bao giờ có ai hỏi tới. Ngược lại,
chỉ lập chỉ mục 500 công ty lớn thì hệ thống sẽ trả lời "tôi không có dữ liệu" cho một
câu hỏi hoàn toàn hợp lệ về công ty thứ 501.

Cách giải: lập chỉ mục sẵn nhóm hay được hỏi, và khi gặp công ty chưa có thì TỰ ĐI LẤY
ngay trong lúc trả lời. Mất 1-3 phút cho lần đầu, sau đó nằm luôn trong index.

Đây cũng chính là ranh giới giữa "RAG có thêm bộ định tuyến" và "agent thật": agent
nhận ra mình thiếu thông tin và tự hành động để bù đắp, thay vì trả lời rằng không biết.

BA MỨC PHỦ

    metrics  — có số liệu tài chính (mặc định, toàn bộ 8.001 doanh nghiệp)
    text     — có thêm văn bản 10-K trong vector store  <- module này nâng lên mức đó
    graph    — có thêm quan hệ trong đồ thị tri thức (tốn LLM, chỉ cụm trọng tâm)
"""

from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from qdrant_client.http import models as qm

from config.settings import settings
from src.graph.store import GraphStore
from src.ingest.edgar import download_companyfacts, download_filing, list_filings, load_ticker_map
from src.ingest.pipeline import process_filing
from src.vector.store import VectorStore

# Hậu tố pháp lý bỏ đi khi so khớp tên công ty do người dùng gõ
_SUFFIX = re.compile(
    r"\b(inc|incorporated|corp|corporation|llc|ltd|limited|plc|co|company|holdings?|"
    r"group|sa|se|nv|ag|the)\b\.?",
    re.IGNORECASE,
)


def _simplify(text: str) -> str:
    text = _SUFFIX.sub("", (text or "").lower())
    return re.sub(r"[^a-z0-9 ]", " ", text).strip()


def _head(simplified: str) -> str:
    """Từ đầu tiên của tên đã đơn giản hóa — phần MANG BẢN SẮC của doanh nghiệp.

    "Saga Communications" và "Acacia Communications" giống nhau tới 85% nếu so cả chuỗi,
    vì chúng dùng chung từ "communications". Nhưng cái phân biệt hai công ty nằm ở từ đầu:
    "saga" với "acacia" — hai từ chẳng liên quan gì. So thêm ở đầu chặn được đúng kiểu
    nhầm này.
    """
    return (simplified or "").split(" ")[0]


# --- Ngưỡng cho nhánh khớp gần đúng (bắt lỗi gõ sai) ---
#
# Ba con số này được chọn từ những ca SAI đo được trên dữ liệu thật, không phải đoán:
#
#     altera            ~ altria              0,833   <- phải LOẠI
#     brother industries~ thor industries     0,848   <- phải LOẠI
#     acacia comm...    ~ saga comm...        0,850   <- phải LOẠI
#     microsft          ~ microsoft           0,941   <- phải GIỮ (gõ thiếu 1 ký tự)
#     amazn / gogle / teslla                  0,909   <- phải GIỮ
#
# Ngưỡng cũ 0,82 nằm DƯỚI cả ba ca sai. 0,88 tách sạch hai nhóm mà vẫn dung thứ lỗi gõ.
FUZZY_MIN_RATIO = 0.88
# Từ đầu cũng phải giống, để từ chung ở đuôi không kéo điểm lên hộ.
FUZZY_MIN_HEAD_RATIO = 0.80
# Chuỗi càng ngắn càng dễ giống nhau do ngẫu nhiên. Dưới 5 ký tự thì khớp gần đúng
# không còn ý nghĩa thống kê, chỉ sinh nhiễu.
FUZZY_MIN_LEN = 5

# Thứ tự hạng, mạnh trước yếu sau. "substring" xếp CUỐI vì nó gần như luôn sai.
_MATCH_NAMES = ("ticker", "exact", "prefix", "word", "fuzzy", "substring")

# Kiểu khớp KHÔNG đủ tin cậy để tự động chọn. Vẫn trả về, nhưng chỉ như một gợi ý.
WEAK_MATCHES = {"substring"}


_graph_ticker_by_cik: Optional[Dict[str, str]] = None


def _canonical_ticker(cik: str, fallback: str) -> str:
    """Trả về mã chứng khoán mà ĐỒ THỊ đang dùng cho doanh nghiệp có CIK này.

    VÌ SAO CẦN BƯỚC NÀY

    Bảng mã của SEC có 10.391 mã cho 8.001 doanh nghiệp, vì một công ty có thể niêm yết
    nhiều hạng cổ phiếu: Alphabet có GOOG, GOOGL và GOOGM cùng trỏ về một CIK. Khi nạp
    đồ thị ta gộp theo CIK và chỉ giữ MỘT mã.

    Hậu quả nếu bỏ qua: người dùng hỏi "Alphabet", bộ phân giải trả về GOOG, nhưng node
    trong Neo4j lại mang mã GOOGL -> truy vấn trả về rỗng, và hệ thống báo "không có dữ
    liệu" về một trong những doanh nghiệp lớn nhất thế giới mà nó ĐANG CÓ đầy đủ dữ liệu.

    CIK là mã định danh duy nhất SEC cấp cho mỗi doanh nghiệp, không đổi theo hạng cổ
    phiếu. Neo nó vào CIK thì hai bên luôn khớp, bất kể đồ thị đã chọn mã nào.
    """
    global _graph_ticker_by_cik
    if _graph_ticker_by_cik is None:
        try:
            store = GraphStore()
            rows = store.run(
                "MATCH (c:Company) WHERE c.cik IS NOT NULL RETURN c.cik AS cik, c.ticker AS ticker"
            )
            store.close()
            _graph_ticker_by_cik = {r["cik"]: r["ticker"] for r in rows if r["ticker"]}
        except Exception:  # noqa: BLE001 - chưa nạp đồ thị thì cứ dùng mã của SEC
            _graph_ticker_by_cik = {}
    return _graph_ticker_by_cik.get(cik, fallback)


def resolve_ticker(query: str, limit: int = 5) -> List[Dict[str, str]]:
    """Chuyển tên công ty người dùng gõ thành mã chứng khoán.

    Người dùng gõ "Tesla", "Coca Cola", "nvidia" chứ hiếm khi gõ đúng mã. Hàm này xử lý
    theo ba mức, dừng ở mức nào có kết quả tốt:
      1. Khớp chính xác mã chứng khoán ("NVDA")
      2. Tên công ty chứa trọn cụm từ đã đơn giản hóa
      3. Độ tương tự chuỗi (bắt lỗi gõ sai: "microsft" -> Microsoft)
    """
    tmap = load_ticker_map()
    q = (query or "").strip()
    if not q:
        return []

    # Mức 1: đúng mã
    if q.upper() in tmap:
        info = tmap[q.upper()]
        return [{
            "ticker": _canonical_ticker(info["cik"], q.upper()),
            "name": info["name"], "cik": info["cik"],
            "match": "ticker", "confidence": "high",
        }]

    q_simple = _simplify(q)
    if not q_simple:
        return []

    # Tên viết tắt phổ biến -> tên đầy đủ. "TSMC" không phải mã chứng khoán (mã là TSM)
    # và cũng không xuất hiện trong tên chính thức "TAIWAN SEMICONDUCTOR MANUFACTURING
    # CO LTD", nên nếu không tra bảng này thì hỏi "TSMC" sẽ ra rỗng.
    from src.graph.schema import CANONICAL_COMPANIES

    alias = CANONICAL_COMPANIES.get(q_simple) or CANONICAL_COMPANIES.get(q.strip().lower())
    if alias:
        alias_simple = _simplify(alias)
        if alias_simple and alias_simple != q_simple:
            q_simple = alias_simple

    # Khớp theo RANH GIỚI TỪ, không phải chuỗi con thuần.
    # Nếu chỉ dùng chuỗi con, hỏi "Ford" sẽ trả về CRAWFORD & CO — vì "craw-ford" có
    # chứa "ford" và tên lại NGẮN HƠN "Ford Motor Co", nên thắng ở tiêu chí độ dài.
    word_re = re.compile(rf"(?<![a-z0-9]){re.escape(q_simple)}(?![a-z0-9])")

    q_head = _head(q_simple)

    scored = []
    for ticker, info in tmap.items():
        name_simple = _simplify(info["name"])
        if not name_simple:
            continue

        # Hạng càng nhỏ càng khớp sát. Trong cùng hạng thì tên ngắn hơn thắng.
        if name_simple == q_simple:
            rank = 1                                   # trùng khít: "alphabet"
        elif name_simple.startswith(q_simple + " "):
            rank = 2                                   # mở đầu bằng: "ford motor"
        elif word_re.search(name_simple):
            rank = 3                                   # đúng từ ở giữa tên
        elif q_simple in name_simple:
            # CHUỖI CON THUẦN TÚY — hạng cuối, và bị đánh dấu là KHÔNG ĐÁNG TIN.
            #
            # Đo trên dữ liệu thật, nhánh này sai 100%:
            #     "acer" nằm trong "M-ACER-ich"   -> Macerich (bất động sản)
            #     "asco" nằm trong "M-ASCO"       -> Masco
            #     "ey"   nằm trong "A-EY-e"       -> AEye
            # Đều là trùng ký tự ngẫu nhiên giữa chừng một từ khác. Vẫn giữ lại để gợi ý
            # "có phải bạn muốn hỏi…", nhưng không bao giờ được tự động chọn.
            rank = 5
        else:
            # Khớp gần đúng, chỉ để bắt lỗi gõ sai. Ba điều kiện phải cùng thỏa.
            if len(q_simple) < FUZZY_MIN_LEN:
                continue
            ratio = SequenceMatcher(None, q_simple, name_simple).ratio()
            if ratio < FUZZY_MIN_RATIO:
                continue
            if SequenceMatcher(None, q_head, _head(name_simple)).ratio() < FUZZY_MIN_HEAD_RATIO:
                continue
            scored.append((4, -ratio, ticker, info))   # gõ sai: "microsft"
            continue

        scored.append((rank, len(name_simple), ticker, info))

    if not scored:
        return []

    scored.sort()

    # Một doanh nghiệp có thể có nhiều mã (Alphabet: GOOG, GOOGL, GOOGM). Gộp theo CIK
    # để không trả về ba dòng cho cùng một công ty.
    out, seen_cik = [], set()
    for rank, _, ticker, info in scored:
        if info["cik"] in seen_cik:
            continue
        seen_cik.add(info["cik"])
        match = _MATCH_NAMES[rank]
        out.append(
            {"ticker": _canonical_ticker(info["cik"], ticker),
             "name": info["name"], "cik": info["cik"],
             "match": match,
             "confidence": "weak" if match in WEAK_MATCHES else "high"}
        )
        if len(out) >= limit:
            break
    return out


_vn_map: Optional[Dict[str, Dict[str, str]]] = None


def vn_companies() -> Dict[str, Dict[str, str]]:
    """Bảng tra doanh nghiệp niêm yết tại Việt Nam đã nạp vào đồ thị: mã -> thông tin.

    Đọc một lần rồi giữ lại. Không có thì trả về bảng rỗng, và toàn bộ phần phân giải
    tên hoạt động y như trước — nạp dữ liệu Việt Nam là tùy chọn, không phải bắt buộc.
    """
    global _vn_map
    if _vn_map is None:
        try:
            store = GraphStore()
            rows = store.run(
                "MATCH (c:Company {market:'VN'}) "
                "RETURN c.symbol AS symbol, c.ticker AS ticker, c.name AS name"
            )
            store.close()
            _vn_map = {
                (r["symbol"] or "").upper(): {
                    "ticker": r["ticker"], "name": r["name"] or r["symbol"],
                    "cik": None, "market": "VN",
                }
                for r in rows if r.get("symbol")
            }
        except Exception:  # noqa: BLE001 — chưa nạp dữ liệu VN thì bỏ qua
            _vn_map = {}
    return _vn_map


def _resolve_vn(query: str) -> List[Dict[str, str]]:
    """Khớp câu hỏi với doanh nghiệp Việt Nam: đúng mã, hoặc tên chứa trọn cụm từ."""
    table = vn_companies()
    if not table:
        return []

    raw = (query or "").strip()
    upper = raw.upper()
    # Người dùng có thể gõ thẳng "FPT.VN" để chỉ đích danh sàn Việt Nam
    if upper.endswith(".VN"):
        upper = upper[:-3]

    if upper in table:
        info = table[upper]
        return [{**info, "match": "ticker", "confidence": "high"}]

    simple = _simplify(raw)
    if len(simple) < 4:
        return []
    hits = []
    for symbol, info in table.items():
        name_simple = _simplify(info["name"])
        if name_simple and (name_simple == simple or name_simple.startswith(simple + " ")):
            hits.append({**info, "match": "exact", "confidence": "high"})
    return hits


def _suggest(query: str, k: int = 3) -> List[Dict[str, str]]:
    """Vài cái tên gần nhất, CHỈ để gợi ý — không bao giờ được tự động chọn.

    Vì sao cần: luật khớp ở trên cố tình siết chặt, nên "amazn" bị từ chối thẳng. Từ chối
    là đúng, nhưng một lời từ chối cụt lủn thì vô dụng với người dùng. Hàm này nới ngưỡng
    xuống rất thấp để đưa ra phỏng đoán, và dán nhãn `weak` để `resolve_company` không
    bao giờ chọn chúng — người dùng đọc rồi tự quyết.

    Đây là chỗ AN TOÀN để nới lỏng, vì kết quả không đi thẳng vào câu trả lời.
    """
    q = _simplify(query)
    if len(q) < 3:
        return []
    q_head = _head(q)

    pool = []
    for ticker, info in load_ticker_map().items():
        name_simple = _simplify(info["name"])
        if not name_simple:
            continue
        score = max(
            SequenceMatcher(None, q, name_simple).ratio(),
            SequenceMatcher(None, q_head, _head(name_simple)).ratio(),
        )
        if score >= 0.72:
            pool.append((-score, ticker, info))

    pool.sort()
    out, seen = [], set()
    for _, ticker, info in pool:
        if info["cik"] in seen:
            continue
        seen.add(info["cik"])
        out.append({
            "ticker": _canonical_ticker(info["cik"], ticker),
            "name": info["name"], "cik": info["cik"],
            "match": "guess", "confidence": "weak",
        })
        if len(out) >= k:
            break
    return out


def resolve_company(query: str, limit: int = 5) -> Dict[str, Any]:
    """Phân giải tên công ty, VÀ TỪ CHỐI khi không đủ chắc chắn.

    ⚠️ ĐÂY LÀ LỚP CHẶN QUAN TRỌNG NHẤT CỦA TOÀN HỆ THỐNG.

    Lỗi thật đã xảy ra trước khi có hàm này: người dùng hỏi doanh thu "Acer", hệ thống
    trả về `status: ok` kèm doanh thu đầy đủ của MACERICH — một quỹ bất động sản trung
    tâm thương mại. Hỏi "Altera" thì ra ALTRIA, công ty thuốc lá. Không một thông báo lỗi
    nào. Số thì đúng, chủ thể thì sai, và người đọc không có cách nào biết.

    Đây đúng là kiểu hỏng mà cả dự án được xây ra để ngăn: mọi con số đều lấy từ XBRL
    chứ không cho mô hình tự đọc, nhưng công sức đó thành vô nghĩa nếu con số đúng bị
    gắn nhầm tên doanh nghiệp.

    SIẾT LUẬT KHỚP THÔI LÀ CHƯA ĐỦ. Dù có siết đến đâu thì một ngày nào đó vẫn sẽ có ca
    khớp sai — bảng mã SEC có hơn 10.000 tên và luôn có những cái tình cờ giống nhau.
    Nên tầng phòng thủ thật nằm ở đây: khớp yếu thì KHÔNG được tự động chọn, mà phải trả
    về "không tìm thấy" kèm gợi ý. Thà nói không biết còn hơn nói sai một cách tự tin.

    Trả về:
        {"status": "ok", "best": {...}, "alternatives": [...]}
        {"status": "not_found", "query": ..., "suggestions": [...]}
    """
    # ⚠️ PHẢI CÓ CÁCH NÓI "Ý TÔI LÀ BÊN MỸ", KHÔNG CHỈ "Ý TÔI LÀ BÊN VIỆT NAM".
    #
    # Hậu tố `.VN` cho phép chỉ đích danh sàn Việt Nam. Nhưng chiều ngược lại thì không có
    # gì cả: gọi lại bằng mã trần "ABT" sẽ lại rơi vào nhánh nhập nhằng ngay bên dưới, nên
    # agent hỏi người dùng, người dùng trả lời "bên Mỹ", rồi agent không có cách nào diễn
    # đạt điều đó — hỏi vòng vo mãi không thoát.
    #
    # Ở mức 30 mã VN30 (8 mã trùng) thì hiếm khi gặp. Khi vũ trụ Việt Nam lên 1.586 mã thì
    # có 302 mã trùng với doanh nghiệp Mỹ ĐANG CÓ dữ liệu — trong đó có ABT (Abbott), ADP,
    # AIG. Lúc đó đây không còn là chuyện hiếm mà là ngõ cụt thường trực.
    raw = (query or "").strip()
    force_us = raw.upper().endswith(".US")
    if force_us:
        raw = raw[:-3]
        query = raw

    candidates = resolve_ticker(query, limit=limit)
    strong = [c for c in candidates if c.get("confidence") == "high"]
    vn = [] if force_us else _resolve_vn(query)

    # ⚠️ MÃ TRÙNG GIỮA HAI SÀN — KHÔNG ĐƯỢC TỰ CHỌN BÊN NÀO.
    #
    # 8/30 mã trong rổ VN30 trùng với mã của SEC, và chúng là hai doanh nghiệp hoàn toàn
    # khác nhau:
    #     ACB   Ngân hàng Á Châu        <->  AURORA CANNABIS INC
    #     MSN   Tập đoàn Masan          <->  EMERSON RADIO CORP
    #     PLX   Petrolimex              <->  Protalix BioTherapeutics
    #     TPB   TPBank                  <->  Turning Point Brands
    #
    # Ưu tiên cứng bên nào cũng sinh ra đúng loại lỗi vừa mới sửa: trả về số liệu đầy đủ
    # của một doanh nghiệp hoàn toàn khác mà không báo gì. Nên khi cả hai cùng khớp,
    # trả về `ambiguous` kèm cả hai để agent hỏi lại người dùng.
    if strong and vn:
        return {
            "status": "ambiguous", "query": query,
            "options": [strong[0], vn[0]],
            "hint": (f"Mã '{query}' vừa là doanh nghiệp Mỹ ({strong[0]['name']}) vừa là "
                     f"doanh nghiệp Việt Nam ({vn[0]['name']}). Hãy hỏi lại người dùng ý "
                     f"nào, rồi gọi lại với '{vn[0]['ticker']}' cho bên Việt Nam hoặc "
                     f"'{strong[0]['ticker']}.US' cho bên Mỹ."),
        }

    if vn and not strong:
        return {"status": "ok", "best": vn[0], "alternatives": vn[1:limit]}

    if not strong:
        # Ứng viên yếu vẫn trả về, nhưng dán nhãn rõ là gợi ý — để agent có thể hỏi lại
        # "có phải bạn muốn hỏi…" thay vì im lặng bịa ra một câu trả lời.
        return {
            "status": "not_found",
            "query": query,
            "suggestions": candidates[:3] or _suggest(query),
        }

    return {"status": "ok", "best": strong[0], "alternatives": strong[1:limit]}


def is_text_indexed(ticker: str, store: Optional[VectorStore] = None) -> int:
    """Số chunk của công ty này đang có trong vector store. 0 nghĩa là chưa lập chỉ mục."""
    store = store or VectorStore()
    result = store.client.count(
        collection_name=store.collection,
        count_filter=qm.Filter(
            must=[qm.FieldCondition(key="ticker", match=qm.MatchValue(value=ticker.upper()))]
        ),
        exact=True,
    )
    return result.count


def ingest_company_text(
    ticker: str,
    n_filings: int = 1,
    forms: Optional[List[str]] = None,
    vector_store: Optional[VectorStore] = None,
) -> Dict:
    """Tải và lập chỉ mục văn bản 10-K của một công ty ngay tại thời điểm cần.

    Trả về dict mô tả kết quả để agent nói lại với người dùng một cách trung thực
    ("tôi vừa lập chỉ mục báo cáo FY2024 của công ty này, mất 84 giây").
    """
    ticker = ticker.upper()
    forms = forms or ["10-K", "20-F"]  # 20-F là form của doanh nghiệp ngoài Mỹ niêm yết ADR
    started = time.time()
    store = vector_store or VectorStore()

    existing = is_text_indexed(ticker, store)
    if existing:
        return {
            "ticker": ticker, "status": "already_indexed",
            "chunks": existing, "seconds": 0.0,
        }

    try:
        refs = list_filings(ticker, forms, n_filings)
    except ValueError as exc:
        return {"ticker": ticker, "status": "unknown_ticker", "error": str(exc)}

    if not refs:
        return {
            "ticker": ticker, "status": "no_filings",
            "error": f"SEC không có bản khai {'/'.join(forms)} nào cho {ticker}",
        }

    total_chunks, indexed = 0, []
    for ref in refs:
        try:
            path = download_filing(ref)
            _, _, vector_chunks, _ = process_filing(path)
            if not vector_chunks:
                continue
            total_chunks += store.upsert_chunks(vector_chunks)
            indexed.append(ref.doc_id)
        except Exception as exc:  # noqa: BLE001
            return {
                "ticker": ticker, "status": "error",
                "error": f"{ref.doc_id}: {str(exc)[:160]}",
                "chunks": total_chunks,
            }

    # Số liệu XBRL của công ty này cũng nên có sẵn cho lần hỏi sau
    try:
        download_companyfacts(ticker)
    except Exception:  # noqa: BLE001
        pass

    # Ghi nhận công ty đã lên mức phủ "text"
    try:
        graph = GraphStore()
        graph.set_tier([ticker], "text")
        graph.close()
    except Exception:  # noqa: BLE001
        pass

    return {
        "ticker": ticker,
        "status": "indexed" if total_chunks else "no_content",
        "chunks": total_chunks,
        "documents": indexed,
        "seconds": round(time.time() - started, 1),
    }


def ensure_text_available(company_or_ticker: str) -> Dict:
    """Điểm vào cho agent: nhận tên hoặc mã, đảm bảo văn bản đã sẵn sàng để tìm kiếm.

    ⚠️ Ở ĐÂY KHỚP SAI CÒN TỆ HƠN CẢ TRẢ VỀ SỐ SAI.

    Trả về số sai thì chỉ hỏng một câu trả lời. Còn hàm này TẢI VỀ và GHI VĨNH VIỄN văn
    bản 10-K vào vector store: khớp nhầm nghĩa là báo cáo của một doanh nghiệp khác nằm
    lại trong chỉ mục dưới mã sai, và mọi câu hỏi sau đó về mã ấy đều lấy nhầm nguồn.
    Nên chỗ này bắt buộc dùng resolve_company, tuyệt đối không lấy ứng viên đầu tiên.
    """
    resolved = resolve_company(company_or_ticker)
    if resolved["status"] != "ok":
        return {"status": "not_found", "query": company_or_ticker,
                "suggestions": [s["name"] for s in resolved.get("suggestions", [])]}

    best = resolved["best"]
    result = ingest_company_text(best["ticker"])
    result["company"] = best["name"]
    result["alternatives"] = [c["ticker"] for c in resolved.get("alternatives", [])[:3]]
    return result

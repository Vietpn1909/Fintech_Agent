"""Bộ câu hỏi đánh giá cho phần VIỆT NAM.

VÌ SAO PHẢI CÓ TỆP NÀY

Bộ đánh giá cũ có 37 câu và KHÔNG câu nào về doanh nghiệp Việt Nam — toàn NVIDIA,
Microsoft, TSMC. Trong khi phần Việt Nam mới là khối lớn nhất: 1.532 doanh nghiệp, 50.214
đoạn báo cáo thường niên, ba nguồn dữ liệu, một mã phải OCR.

Nghĩa là câu "hệ thống đạt 100% độ chính xác số liệu" chỉ đúng với phía Mỹ. Phía Việt
Nam chưa từng được đo, nên về mặt bằng chứng thì nó ngang với chưa kiểm gì.

NĂM NHÓM, MỖI NHÓM HỎI MỘT THỨ KHÁC NHAU

    vn_numeric     tra số từ VCI — đáp án lấy thẳng từ đồ thị, chấm bằng máy
    vn_qualitative nội dung báo cáo thường niên — đo có trích đúng doanh nghiệp không
    vn_ownership   quan hệ sở hữu — thứ phía Mỹ không có
    vn_refusal     ⚠️ PHẢI TỪ CHỐI: hỏi cái hệ thống không có, câu trả lời đúng là
                   "không có dữ liệu" chứ không phải một câu nghe hợp lý
    vn_currency    ⚠️ PHẢI CẢNH BÁO: so sánh doanh nghiệp VND với doanh nghiệp USD

Hai nhóm cuối quan trọng không kém ba nhóm đầu. Một hệ thống trả lời đúng mọi câu hỏi
trả lời được, nhưng cũng "trả lời" cả những câu nó không có dữ liệu, thì vẫn là hệ thống
hỏng — chỉ là hỏng ở chỗ không ai nghĩ tới mà đo.

⚠️ ĐÁP ÁN SINH TỪ DỮ LIỆU THẬT, KHÔNG GÕ TAY

Mọi con số trong bộ này đọc thẳng từ Neo4j tại thời điểm sinh. Gõ tay đáp án là tự đưa
lỗi chép nhầm vào chính cái thước đo — và khi thước đo sai thì mọi kết luận rút ra từ nó
đều vô giá trị, kể cả những kết luận có vẻ tốt.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from src.eval.testset import EvalQuestion
from src.graph.store import GraphStore
from src.ingest.xbrl import METRIC_LABELS

# Chỉ tiêu dùng để hỏi. Bỏ `gross_profit` vì ngân hàng không có, mà rổ VN30 quá nửa là
# ngân hàng — hỏi chỉ tiêu mà doanh nghiệp không khai là đo cách hệ thống báo thiếu, chứ
# không đo độ chính xác số liệu. Việc đó đã có nhóm vn_refusal lo.
VN_METRICS = ["revenue", "net_income", "total_assets", "stockholders_equity"]

# Rổ mã để hỏi: lấy VN30 vì đó là nhóm có đủ cả số liệu, báo cáo thường niên lẫn quan hệ
# sở hữu — hỏi một mã UPCOM nhỏ thì phần lớn câu sẽ rơi vào "không có dữ liệu".
VN30 = [
    "ACB", "BCM", "BID", "BVH", "CTG", "DGC", "FPT", "GAS", "GVR", "HDB",
    "HPG", "MBB", "MSN", "MWG", "PLX", "POW", "SAB", "SHB", "SSB", "SSI",
    "STB", "TCB", "TPB", "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB",
]

_TEMPLATES = [
    "{label} của {company} trong năm tài chính {year} là bao nhiêu?",
    "Cho biết {label} năm {year} của {company}.",
    "{company} ghi nhận {label} bao nhiêu trong năm {year}?",
]


# ⚠️ THỰC THỂ KỲ VỌNG DÙNG MÃ TRẦN, KHÔNG DÙNG DẠNG ".VN".
#
# Lần chạy đầu đặt kỳ vọng là "GVR.VN" và recall ra 0% cho cả ba câu sở hữu — nhưng đọc
# câu trả lời thì nó nêu đúng doanh nghiệp, chỉ viết là "(GVR)". Hậu tố ".VN" là quy ước
# NỘI BỘ của hệ thống để phân biệt hai sàn; agent không có lý do gì phải in nó ra cho
# người dùng.
#
# Một thước đo phạt cách diễn đạt thay vì phạt nội dung sai thì tự nó vô dụng: nó kêu khi
# mọi thứ đều đúng, nên rồi sẽ có người tắt nó đi. Mã trần vẫn bắt được đúng loại hỏng
# cần bắt — trả lời về nhầm doanh nghiệp thì mã đó sẽ không xuất hiện.
def _expected(ticker: str) -> list:
    return [ticker.replace(".VN", "")]


def _askable_name(row: Dict[str, Any]) -> str:
    """Dạng tên phân giải được về đúng mã này, thử từ ngắn gọn tới đầy đủ."""
    from src.ingest.on_demand import resolve_company

    ticker = row["ticker"]
    for form in (row.get("short_vi"), row.get("name_vi"), row.get("name")):
        if not form:
            continue
        result = resolve_company(form)
        if result.get("status") == "ok" and (result.get("best") or {}).get("ticker") == ticker:
            return form
    return ticker


def generate_numeric(symbols: Optional[List[str]] = None, years: Optional[List[int]] = None,
                     per_company: int = 1, seed: int = 7) -> List[EvalQuestion]:
    """Câu hỏi tra số, đáp án đọc thẳng từ đồ thị.

    Hỏi bằng TÊN TIẾNG VIỆT chứ không bằng mã, vì đó là cách người dùng thật sự hỏi —
    và vì đường phân giải tên tiếng Việt chính là chỗ đã từng có ba lỗi khớp sai.
    """
    rng = random.Random(seed)
    symbols = symbols or VN30
    years = years or [2025, 2024, 2023]
    store = GraphStore()
    out: List[EvalQuestion] = []

    for symbol in symbols:
        rows = store.run(
            """
            MATCH (c:Company {market:'VN', symbol: $symbol})-[:HAS_FINANCIALS]->(fy:FinancialYear)
            WHERE fy.fiscal_year IN $years
            RETURN c.ticker AS ticker, c.name AS name, c.name_vi AS name_vi,
                   c.short_vi AS short_vi, fy AS data
            ORDER BY fy.fiscal_year DESC
            """,
            symbol=symbol, years=years,
        )
        if not rows:
            continue

        # ⚠️ TÊN HỎI PHẢI PHÂN GIẢI ĐƯỢC VỀ ĐÚNG DOANH NGHIỆP ĐÓ.
        #
        # Lần sinh đầu dùng thẳng tên rút gọn, và ra câu "ACB ghi nhận vốn chủ sở hữu bao
        # nhiêu…". Mã "ACB" trùng với Aurora Cannabis bên Mỹ nên agent sẽ HỎI LẠI — hành
        # vi đúng, nhưng bộ đánh giá lại chấm là sai. Thước đo mà phạt hành vi đúng thì
        # mọi con số nó đưa ra đều vô nghĩa.
        #
        # Nên thử lần lượt các dạng tên và lấy dạng đầu tiên phân giải về đúng mã. Không
        # dạng nào đạt thì dùng mã đầy đủ kèm hậu tố .VN — vẫn là câu hỏi hợp lệ, chỉ là
        # không đo được nhánh khớp tên. Việc đo nhánh đó đã có `test_resolver` lo.
        head = rows[0]
        asked = _askable_name(head)
        pool = [
            (m, r["data"]["fiscal_year"], r["data"][m], r["data"].get("currency", "VND"))
            for r in rows for m in VN_METRICS if r["data"].get(m) is not None
        ]
        if not pool:
            continue
        rng.shuffle(pool)

        for metric, year, value, currency in pool[:per_company]:
            out.append(EvalQuestion(
                qid=f"vnnum_{symbol}_{metric}_{year}",
                category="vn_numeric",
                question=rng.choice(_TEMPLATES).format(
                    label=METRIC_LABELS[metric].lower(), company=asked, year=year),
                grading="numeric",
                expected_value=float(value),
                expected_unit=currency,
                expected_entities=_expected(head["ticker"]),
                ground_truth=(f"{METRIC_LABELS[metric]} của {head['name']} năm {year} là "
                              f"{value:,.0f} {currency} (nguồn: VCI)."),
                meta={"ticker": head["ticker"], "metric": metric, "fiscal_year": year,
                      "asked_as": asked,
                      "entity_forms": [head["ticker"].replace(".VN", ""), asked,
                                       head["name"]]},
            ))
    store.close()
    return out


def generate_qualitative(store: Optional[GraphStore] = None) -> List[EvalQuestion]:
    """Câu hỏi về nội dung báo cáo thường niên.

    Không có một đáp án số duy nhất, nên chấm bằng `expected_entities`: câu trả lời đúng
    BẮT BUỘC nhắc tới mã doanh nghiệp. Đây là thước đo yếu hơn phần số, nhưng nó bắt được
    đúng loại hỏng nguy hiểm nhất ở đây — trả lời về nhầm doanh nghiệp.
    """
    specs = [
        ("FPT", "FPT nêu những rủi ro chính nào trong báo cáo thường niên?"),
        ("VNM", "Chiến lược phát triển của Vinamilk là gì?"),
        ("ACB", "Ngân hàng Á Châu quản trị rủi ro tín dụng như thế nào?"),
        ("MSN", "Masan nêu những rủi ro chính nào?"),
        ("SAB", "Sabeco nêu những rủi ro gì về thị trường?"),
        ("TPB", "TPBank nói gì về chuyển đổi số trong báo cáo thường niên?"),
    ]
    return [
        EvalQuestion(
            qid=f"vnqual_{sym}", category="vn_qualitative", question=q, grading="ragas",
            expected_entities=_expected(sym),
            ground_truth=("Câu trả lời phải trích từ báo cáo thường niên của doanh nghiệp "
                          "này, kèm năm và số trang."),
            meta={"ticker": f"{sym}.VN", "entity_forms": [sym]},
        )
        for sym, q in specs
    ]


def generate_ownership(limit: int = 3) -> List[EvalQuestion]:
    """Câu hỏi về cổ đông — phần dữ liệu phía Mỹ không có."""
    store = GraphStore()
    rows = store.run(
        """
        MATCH (c:Company {market:'VN'})<-[r:OWNED_BY]-(o)
        WHERE c.symbol IN $syms AND o.name IS NOT NULL
        RETURN c.symbol AS symbol, c.ticker AS ticker, c.name AS name,
               c.name_vi AS name_vi, c.short_vi AS short_vi,
               collect(o.name)[0..3] AS owners, count(o) AS n
        ORDER BY n DESC LIMIT $limit
        """,
        syms=VN30, limit=limit,
    )
    store.close()
    return [
        EvalQuestion(
            qid=f"vnown_{r['symbol']}", category="vn_ownership",
            # Hỏi bằng TÊN chứ không bằng mã trần. Mã "SAB" trùng với SAB Biotherapeutics
            # bên Mỹ nên hệ thống sẽ hỏi lại — hành vi đúng, nhưng câu hỏi này muốn đo
            # nhánh tra quan hệ sở hữu chứ không phải nhánh gỡ nhập nhằng. Việc đo nhánh
            # kia đã có NHÓM 9 trong `tests/test_vietnam.py` lo.
            question=f"Những cổ đông lớn của {_askable_name(r)} là ai?",
            grading="ragas", expected_entities=_expected(r["ticker"]),
            ground_truth=f"Các cổ đông ghi nhận được: {', '.join(r['owners'])}.",
            meta={"ticker": r["ticker"], "owner_count": r["n"],
                  "entity_forms": [r["ticker"].replace(".VN", ""), _askable_name(r),
                                   r["name"]]},
        )
        for r in rows
    ]


# ⚠️ NHÓM NÀY ĐO CHIỀU NGƯỢC LẠI: câu trả lời ĐÚNG là "không có dữ liệu".
#
# Ba lỗi nặng nhất tìm được trong dự án đều thuộc loại này — hệ thống trả lời trôi chảy
# về thứ nó không có: doanh thu Acer hóa ra là của Macerich, "SAB (Sabeco)" bị báo không
# có dữ liệu trong khi báo cáo nằm sẵn trong kho, hỏi Sabeco thì ra một công ty UPCOM.
#
# `must_refuse` là các cụm mà câu trả lời đúng phải chứa ít nhất một; `must_not_contain`
# là dấu hiệu nó đã bịa ra một con số.
REFUSAL_CASES = [
    {
        "qid": "vnref_no_ar",
        "question": "Công ty Cổ phần Xi măng Bỉm Sơn nêu những rủi ro gì trong báo cáo thường niên?",
        "why": "mã ngoài VN30, hệ thống chưa nạp báo cáo thường niên của doanh nghiệp này",
        "must_refuse": ["không có", "chưa có", "không tìm thấy", "chưa nạp"],
    },
    {
        "qid": "vnref_future",
        "question": "Doanh thu của FPT năm 2027 là bao nhiêu?",
        "why": "năm tương lai, không thể có số liệu",
        "must_refuse": ["không có", "chưa có", "không tìm thấy", "chưa công bố", "tương lai"],
    },
    {
        "qid": "vnref_fake",
        "question": "Doanh thu năm 2025 của Tập đoàn Thịnh Vượng Xanh Việt Nam là bao nhiêu?",
        "why": "doanh nghiệp không tồn tại",
        "must_refuse": ["không có", "không tìm thấy", "không tồn tại", "chưa có"],
    },
]


def generate_refusal() -> List[EvalQuestion]:
    return [
        EvalQuestion(
            qid=c["qid"], category="vn_refusal", question=c["question"], grading="refusal",
            ground_truth=f"Phải trả lời là không có dữ liệu ({c['why']}).",
            meta={"must_refuse": c["must_refuse"], "why": c["why"]},
        )
        for c in REFUSAL_CASES
    ]


# ⚠️ Hệ thống KHÔNG quy đổi tỷ giá. Câu trả lời đúng cho câu hỏi dưới đây là nói rõ điều
# đó, chứ không phải xếp hạng hai con số khác đơn vị rồi đưa ra một "người thắng".
def generate_currency() -> List[EvalQuestion]:
    return [
        EvalQuestion(
            qid="vncur_fpt_msft", category="vn_currency",
            question="Doanh thu của FPT và Microsoft năm 2024, bên nào lớn hơn?",
            grading="currency",
            ground_truth=("Phải nói rõ hai doanh nghiệp báo cáo bằng hai đồng tiền khác "
                          "nhau (VND và USD) và hệ thống không quy đổi tỷ giá."),
            meta={"must_warn": ["không quy đổi", "khác nhau", "VND", "đồng tiền",
                                "không so sánh"]},
        ),
    ]


def build_all(per_company: int = 1, numeric_symbols: Optional[List[str]] = None
              ) -> List[EvalQuestion]:
    return (generate_numeric(symbols=numeric_symbols, per_company=per_company)
            + generate_qualitative()
            + generate_ownership()
            + generate_refusal()
            + generate_currency())

"""Sinh bộ câu hỏi kiểm thử.

HAI LOẠI CÂU HỎI, HAI CÁCH CHẤM KHÁC NHAU

Loại 1 — CÓ ĐÁP ÁN CHÍNH XÁC (sinh tự động từ dữ liệu XBRL)
    "Doanh thu thuần của NVIDIA trong năm tài chính 2026 là bao nhiêu?"
    Đáp án: 215.938.000.000 USD — lấy thẳng từ số doanh nghiệp khai với SEC.

    Chấm bằng cách DÒ SỐ trong câu trả lời. Không cần LLM làm giám khảo, không có chỗ
    cho tranh cãi. Đây là thước đo mạnh nhất của dự án: nó chứng minh trực tiếp rằng hệ
    thống không bịa số. Sinh tự động nên muốn bao nhiêu câu cũng được.

Loại 2 — CÂU HỎI ĐỊNH TÍNH (viết tay)
    "NVIDIA nêu những rủi ro gì liên quan tới kiểm soát xuất khẩu sang Trung Quốc?"
    Không có một đáp án đúng duy nhất, phải chấm bằng RAGAS với LLM làm giám khảo.

VÌ SAO PHẢI TÁCH RA

Nếu chỉ dùng RAGAS, mọi kết luận đều phụ thuộc vào chất lượng của LLM giám khảo — mà
giám khảo ở đây là model local, vốn chính là thứ đang cần đánh giá. Chấm bằng chính
công cụ mình đang đánh giá là một lỗi phương pháp luận, và hội đồng chấm đồ án sẽ hỏi.

Thước đo dò số không có vấn đề đó: nó xác định, lặp lại được, và không dùng LLM.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from config.settings import PROCESSED_DIR
from src.graph.store import GraphStore
from src.ingest.xbrl import METRIC_LABELS

EVAL_PATH = PROCESSED_DIR / "eval_questions.json"


@dataclass
class EvalQuestion:
    qid: str
    category: str          # numeric | comparison | screening | qualitative | multihop
    question: str
    grading: str           # "numeric" (dò số) hoặc "ragas" (LLM giám khảo)
    expected_value: Optional[float] = None
    expected_unit: str = ""
    expected_entities: Optional[List[str]] = None   # tên buộc phải xuất hiện trong câu trả lời
    ground_truth: str = ""                          # đáp án tham chiếu, dùng cho RAGAS
    meta: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------- sinh câu hỏi số liệu

_NUMERIC_TEMPLATES = [
    "{label} của {company} trong năm tài chính {year} là bao nhiêu?",
    "Cho biết {label} năm {year} của {company}.",
    "{company} ghi nhận {label} bao nhiêu trong năm tài chính {year}?",
]

_GROWTH_TEMPLATE = (
    "{label} của {company} năm tài chính {year} tăng hay giảm bao nhiêu phần trăm "
    "so với năm {prev}?"
)


def generate_numeric_questions(
    tickers: List[str], metrics: List[str], years: List[int], per_company: int = 3, seed: int = 42
) -> List[EvalQuestion]:
    """Sinh câu hỏi tra số từ dữ liệu thật trong Neo4j, đáp án lấy thẳng từ XBRL."""
    rng = random.Random(seed)
    store = GraphStore()
    questions: List[EvalQuestion] = []

    for ticker in tickers:
        rows = store.run(
            """
            MATCH (c:Company {ticker: $ticker})-[:HAS_FINANCIALS]->(fy:FinancialYear)
            WHERE fy.fiscal_year IN $years
            RETURN c.name AS company, fy AS data ORDER BY fy.fiscal_year DESC
            """,
            ticker=ticker, years=years,
        )
        if not rows:
            continue

        company = rows[0]["company"]
        pool = []
        for row in rows:
            data = row["data"]
            for metric in metrics:
                if data.get(metric) is not None:
                    pool.append((
                        metric, data["fiscal_year"], data[metric],
                        data.get("accession", ""), data.get("currency", "USD"),
                    ))

        rng.shuffle(pool)
        # Đồng tiền lấy từ chính dữ liệu, không gán cứng "USD": TSMC báo cáo bằng TWD,
        # ASML bằng EUR. Ghi nhầm đơn vị trong đáp án chuẩn khiến bộ câu hỏi kiểm thử
        # tự nó đã sai, và mọi kết luận rút ra từ nó đều mất giá trị.
        for metric, year, value, accession, currency in pool[:per_company]:
            questions.append(
                EvalQuestion(
                    qid=f"num_{ticker}_{metric}_{year}",
                    category="numeric",
                    question=rng.choice(_NUMERIC_TEMPLATES).format(
                        label=METRIC_LABELS[metric].lower(), company=company, year=year
                    ),
                    grading="numeric",
                    expected_value=float(value),
                    expected_unit=f"{currency}/shares" if metric == "eps_diluted" else currency,
                    expected_entities=[ticker],
                    ground_truth=(
                        f"{METRIC_LABELS[metric]} của {company} năm tài chính {year} là "
                        f"{value:,.0f} {currency} (nguồn: XBRL, bản khai {accession})."
                    ),
                    meta={"ticker": ticker, "metric": metric, "fiscal_year": year},
                )
            )

    store.close()
    return questions


def generate_comparison_questions(
    groups: List[List[str]], metric: str, year: int
) -> List[EvalQuestion]:
    """Sinh câu hỏi so sánh. Đáp án chuẩn là công ty đứng đầu, xác định từ dữ liệu."""
    store = GraphStore()
    questions: List[EvalQuestion] = []

    for i, group in enumerate(groups):
        rows = store.run(
            """
            MATCH (c:Company)-[:HAS_FINANCIALS]->(fy:FinancialYear)
            WHERE c.ticker IN $tickers AND fy.fiscal_year = $year AND fy[$metric] IS NOT NULL
              AND coalesce(fy.currency, 'USD') = 'USD'
            RETURN c.ticker AS ticker, c.name AS company, fy[$metric] AS value
            ORDER BY value DESC
            """,
            tickers=group, year=year, metric=metric,
        )
        if len(rows) < 2:
            continue

        winner = rows[0]
        names = ", ".join(r["company"] for r in rows)
        questions.append(
            EvalQuestion(
                qid=f"cmp_{metric}_{year}_{i}",
                category="comparison",
                question=(
                    f"Trong nhóm {names}, doanh nghiệp nào có "
                    f"{METRIC_LABELS[metric].lower()} cao nhất năm tài chính {year}, và bao nhiêu?"
                ),
                grading="numeric",
                expected_value=float(winner["value"]),
                expected_unit="USD",
                expected_entities=[winner["ticker"]],
                ground_truth=(
                    f"{winner['company']} ({winner['ticker']}) cao nhất với "
                    f"{winner['value']:,.0f} USD trong năm tài chính {year}."
                ),
                meta={"metric": metric, "year": year, "ranking": rows},
            )
        )

    store.close()
    return questions


def generate_screening_questions(fiscal_year: int = 2024) -> List[EvalQuestion]:
    """Sinh câu hỏi sàng lọc — loại câu chỉ trả lời được nhờ độ phủ 6.000 doanh nghiệp."""
    store = GraphStore()
    questions: List[EvalQuestion] = []

    specs = [
        ("revenue", 2e11, "doanh thu trên 200 tỷ USD"),
        ("net_income", 5e10, "lợi nhuận sau thuế trên 50 tỷ USD"),
        ("rnd_expense", 2e10, "chi phí R&D trên 20 tỷ USD"),
    ]

    for metric, threshold, desc in specs:
        rows = store.run(
            f"""
            MATCH (c:Company)-[:HAS_FINANCIALS]->(fy:FinancialYear)
            WHERE fy.fiscal_year = $year AND fy.`{metric}` > $threshold
              AND coalesce(fy.currency, 'USD') = 'USD' 
            RETURN c.ticker AS ticker, c.name AS company, fy.`{metric}` AS value
            ORDER BY value DESC LIMIT 15
            """,
            year=fiscal_year, threshold=threshold,
        )
        if not rows:
            continue

        questions.append(
            EvalQuestion(
                qid=f"scr_{metric}_{fiscal_year}",
                category="screening",
                question=f"Những doanh nghiệp niêm yết nào có {desc} trong năm tài chính {fiscal_year}?",
                grading="ragas",
                expected_entities=[r["ticker"] for r in rows[:5]],
                ground_truth=(
                    f"Các doanh nghiệp có {desc} năm {fiscal_year} gồm: "
                    + ", ".join(f"{r['company']} ({r['value'] / 1e9:.1f} tỷ USD)" for r in rows[:8])
                    + "."
                ),
                meta={"metric": metric, "threshold": threshold, "matches": len(rows)},
            )
        )

    store.close()
    return questions


# --------------------------------------------------------------- câu hỏi định tính

# Viết tay. Đây là những câu mà vector search hoặc đồ thị phải làm việc thật sự —
# cố ý chọn cả những câu mà RAG thuần túy sẽ trả lời kém, để phép so sánh có ý nghĩa.
QUALITATIVE_QUESTIONS: List[Dict[str, Any]] = [
    {
        "qid": "qa_nvda_export",
        "category": "qualitative",
        "question": "NVIDIA nêu những rủi ro nào liên quan tới quy định kiểm soát xuất khẩu sang Trung Quốc?",
        "expected_entities": ["NVDA"],
        "ground_truth": (
            "NVIDIA cho biết các quy định kiểm soát xuất khẩu của chính phủ Mỹ hạn chế việc "
            "bán sản phẩm trung tâm dữ liệu tiên tiến sang Trung Quốc, buộc họ phải xin giấy "
            "phép, làm giảm doanh thu tại thị trường này, và có thể khiến khách hàng chuyển "
            "sang đối thủ không chịu ràng buộc tương tự."
        ),
    },
    {
        "qid": "qa_msft_ai_capex",
        "category": "qualitative",
        "question": "Microsoft giải thích thế nào về việc tăng mạnh chi đầu tư cho hạ tầng AI và đám mây?",
        "expected_entities": ["MSFT"],
        "ground_truth": (
            "Microsoft cho biết chi đầu tư tăng mạnh để mở rộng công suất trung tâm dữ liệu "
            "phục vụ nhu cầu Azure và các dịch vụ AI, gồm chi cho máy chủ, chip tăng tốc và "
            "cơ sở hạ tầng, nhằm đáp ứng nhu cầu khách hàng vượt quá công suất hiện có."
        ),
    },
    {
        "qid": "qa_aapl_supply",
        "category": "qualitative",
        "question": "Apple mô tả rủi ro tập trung chuỗi cung ứng của họ ra sao?",
        "expected_entities": ["AAPL"],
        "ground_truth": (
            "Apple cho biết phần lớn hoạt động sản xuất và lắp ráp tập trung ở một số ít đối "
            "tác và khu vực địa lý, chủ yếu tại châu Á, nên gián đoạn do thiên tai, dịch bệnh, "
            "căng thẳng địa chính trị hoặc vấn đề của nhà cung cấp đơn nguồn đều có thể ảnh "
            "hưởng nghiêm trọng tới hoạt động kinh doanh."
        ),
    },
    {
        "qid": "qa_amd_compete",
        "category": "qualitative",
        "question": "AMD xác định những đối thủ cạnh tranh chính nào trong mảng trung tâm dữ liệu?",
        "expected_entities": ["AMD"],
        "ground_truth": (
            "AMD nêu Intel và NVIDIA là các đối thủ cạnh tranh chính, đồng thời đề cập tới các "
            "chip tăng tốc do chính các nhà cung cấp dịch vụ đám mây tự phát triển."
        ),
    },
    {
        "qid": "qa_googl_regulatory",
        "category": "qualitative",
        "question": "Alphabet nêu những rủi ro pháp lý và chống độc quyền nào?",
        "expected_entities": ["GOOGL"],
        "ground_truth": (
            "Alphabet cho biết đang đối mặt với các vụ kiện chống độc quyền và điều tra của cơ "
            "quan quản lý tại Mỹ, Liên minh châu Âu và nhiều khu vực khác, liên quan tới tìm "
            "kiếm, quảng cáo và cửa hàng ứng dụng, có thể dẫn tới phạt tiền lớn hoặc buộc thay "
            "đổi mô hình kinh doanh."
        ),
    },
]

# Câu hỏi bắc cầu — chỉ trả lời được nhờ đồ thị tri thức.
# Cố ý chọn những câu KHÔNG có đoạn văn nào nhắc cả hai thực thể cùng lúc, nên vector
# search về nguyên tắc phải thua. Đây là phần chứng minh giá trị của GraphRAG.
MULTIHOP_QUESTIONS: List[Dict[str, Any]] = [
    {
        "qid": "mh_nvda_tsmc_msft",
        "category": "multihop",
        "question": (
            "Nếu năng lực sản xuất của TSMC bị gián đoạn, ảnh hưởng lan tới Microsoft qua "
            "những mắt xích nào? Nêu rõ chuỗi liên kết."
        ),
        "expected_entities": ["TSMC", "NVIDIA", "Microsoft"],
        "ground_truth": (
            "TSMC sản xuất chip cho NVIDIA; NVIDIA cung cấp GPU cho Microsoft để vận hành hạ "
            "tầng Azure và dịch vụ AI. Gián đoạn tại TSMC vì thế truyền qua NVIDIA tới khả "
            "năng mở rộng công suất đám mây của Microsoft."
        ),
    },
    {
        "qid": "mh_asml_ecosystem",
        "category": "multihop",
        "question": "ASML nằm ở vị trí nào trong chuỗi cung ứng dẫn tới các chip AI của NVIDIA?",
        "expected_entities": ["ASML", "TSMC", "NVIDIA"],
        "ground_truth": (
            "ASML cung cấp thiết bị quang khắc EUV cho các xưởng đúc như TSMC; TSMC dùng thiết "
            "bị đó để sản xuất GPU cho NVIDIA. ASML vì vậy là mắt xích thượng nguồn hai bậc so "
            "với chip AI của NVIDIA."
        ),
    },
    {
        "qid": "mh_cloud_competitors",
        "category": "multihop",
        "question": (
            "Vì sao các nhà cung cấp dịch vụ đám mây vừa là khách hàng lớn nhất vừa là đối thủ "
            "của NVIDIA? Nêu bằng chứng từ báo cáo."
        ),
        "expected_entities": ["NVIDIA"],
        "ground_truth": (
            "Các nhà cung cấp đám mây lớn mua GPU của NVIDIA với khối lượng lớn cho hạ tầng AI, "
            "đồng thời tự phát triển chip tăng tốc riêng, nên vừa là khách hàng vừa cạnh tranh "
            "trực tiếp — NVIDIA nêu rõ điều này trong phần Rủi ro."
        ),
    },
]


def build_testset(
    tickers: Optional[List[str]] = None,
    years: Optional[List[int]] = None,
    numeric_per_company: int = 3,
) -> List[EvalQuestion]:
    """Ghép toàn bộ bộ câu hỏi."""
    tickers = tickers or ["NVDA", "MSFT", "AAPL", "GOOGL", "AMD", "TSM", "INTC", "AVGO"]
    years = years or [2024, 2025, 2026]
    metrics = ["revenue", "net_income", "gross_profit", "rnd_expense", "operating_income", "total_assets"]

    questions: List[EvalQuestion] = []
    questions += generate_numeric_questions(tickers, metrics, years, per_company=numeric_per_company)
    questions += generate_comparison_questions(
        [["NVDA", "AMD", "INTC"], ["MSFT", "GOOGL", "AAPL"], ["TSM", "ASML", "AMAT"]],
        metric="revenue", year=2024,
    )
    questions += generate_screening_questions(2024)

    for spec in QUALITATIVE_QUESTIONS + MULTIHOP_QUESTIONS:
        questions.append(EvalQuestion(grading="ragas", **spec))

    return questions


def save_testset(questions: List[EvalQuestion], path=EVAL_PATH) -> None:
    path.write_text(
        json.dumps([q.to_dict() for q in questions], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_testset(path=EVAL_PATH) -> List[EvalQuestion]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [EvalQuestion(**item) for item in raw]

"""Trích xuất số liệu tài chính chính xác từ XBRL.

VÌ SAO PHẦN NÀY QUAN TRỌNG NHẤT VỀ MẶT ĐỘ TIN CẬY

Điểm yếu chí mạng của mọi hệ RAG tài chính là con số. Khi LLM đọc một bảng biểu đã
bị làm phẳng thành văn bản, nó rất dễ lấy nhầm cột (năm nay / năm trước), nhầm đơn vị
(triệu / tỷ), hoặc đơn giản là bịa ra một con số nghe hợp lý. Chấm điểm faithfulness
bằng RAGAS sẽ phơi bày ngay điều này.

Giải pháp ở đây: KHÔNG để LLM đọc số từ văn bản. Mọi con số tài chính lấy từ file
XBRL companyfacts.json — đây là dữ liệu có cấu trúc do CHÍNH DOANH NGHIỆP khai báo và
nộp cho SEC, mỗi con số gắn với một mã chuẩn us-gaap, một kỳ báo cáo, một đơn vị và
một số hiệu bản khai. Agent tra số bằng tra cứu từ điển, không bằng suy đoán.

MỘT KHÓ KHĂN THẬT: cùng khái niệm "doanh thu" nhưng mỗi doanh nghiệp khai bằng một mã
khác nhau. Apple dùng RevenueFromContractWithCustomerExcludingAssessedTax, công ty
khác dùng Revenues hoặc SalesRevenueNet. Vì vậy mỗi chỉ tiêu được định nghĩa bằng một
DANH SÁCH mã ưu tiên, thử lần lượt cho tới khi tìm thấy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

# Định nghĩa chỉ tiêu: khóa nội bộ -> danh sách mã us-gaap theo thứ tự ưu tiên
METRIC_TAGS: Dict[str, List[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "rnd_expense": ["ResearchAndDevelopmentExpense"],
    "sga_expense": [
        "SellingGeneralAndAdministrativeExpense",
        "GeneralAndAdministrativeExpense",
    ],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "stockholders_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "eps_diluted": ["EarningsPerShareDiluted"],
}

# Các loại hồ sơ THƯỜNG NIÊN được chấp nhận.
#
# ⚠️ Chỉ nhận "10-K" là bỏ sót toàn bộ doanh nghiệp ngoài Mỹ. Tập đoàn quốc tế niêm yết
# ADR tại Mỹ nộp form 20-F (Canada nộp 40-F), không nộp 10-K. Đo thật: TSMC, Toyota, SAP,
# Alibaba, Sony, Shell, Novo Nordisk đều có 0 năm số liệu cho tới khi thêm 20-F vào đây.
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

# Bảng mã theo chuẩn IFRS.
#
# ⚠️ Lỗ hổng thứ hai, độc lập với lỗ hổng form. Doanh nghiệp ngoài Mỹ được phép lập báo
# cáo theo IFRS thay vì US-GAAP, và khi đó họ khai vào bộ mã "ifrs-full" chứ không phải
# "us-gaap". TSMC, SAP, Novo Nordisk, Shell KHÔNG có một mã us-gaap nào. Nhận form 20-F
# mà không đọc IFRS thì vẫn ra 0 số liệu — phải sửa cả hai mới có tác dụng.
IFRS_METRIC_TAGS: Dict[str, List[str]] = {
    "revenue": ["Revenue", "RevenueFromContractsWithCustomers", "RevenueFromSaleOfGoods"],
    "cost_of_revenue": ["CostOfSales", "CostOfMerchandiseSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["ProfitLossFromOperatingActivities", "OperatingIncomeLoss"],
    "net_income": ["ProfitLoss", "ProfitLossAttributableToOwnersOfParent"],
    "rnd_expense": ["ResearchAndDevelopmentExpense"],
    "sga_expense": ["SellingGeneralAndAdministrativeExpense", "AdministrativeExpense"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "stockholders_equity": ["Equity", "EquityAttributableToOwnersOfParent"],
    "cash_and_equivalents": ["CashAndCashEquivalents"],
    "operating_cash_flow": ["CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
    "eps_diluted": ["DilutedEarningsLossPerShare"],
}

# Nhãn tiếng Việt để hiển thị cho người dùng cuối
METRIC_LABELS: Dict[str, str] = {
    "revenue": "Doanh thu thuần",
    "cost_of_revenue": "Giá vốn hàng bán",
    "gross_profit": "Lợi nhuận gộp",
    "operating_income": "Lợi nhuận từ hoạt động kinh doanh",
    "net_income": "Lợi nhuận sau thuế",
    "rnd_expense": "Chi phí R&D",
    "sga_expense": "Chi phí bán hàng & quản lý",
    "total_assets": "Tổng tài sản",
    "total_liabilities": "Tổng nợ phải trả",
    "stockholders_equity": "Vốn chủ sở hữu",
    "cash_and_equivalents": "Tiền và tương đương tiền",
    "operating_cash_flow": "Dòng tiền từ hoạt động kinh doanh",
    "capex": "Chi đầu tư tài sản cố định",
    "eps_diluted": "EPS pha loãng",
}


def _pick_unit(units: Dict[str, list]) -> Optional[str]:
    """Chọn đơn vị tiền tệ cho một mã.

    ⚠️ CHỈ NHẬN USD LÀ BỎ SÓT TOÀN BỘ DOANH NGHIỆP NGOÀI MỸ.

    Đo thật trên dữ liệu doanh thu: ASML khai bằng EUR, Sony bằng JPY, Novo Nordisk bằng
    DKK, TSMC bằng TWD. Bộ lọc chỉ nhận "USD" khiến ba doanh nghiệp đầu ra 0 số liệu, còn
    TSMC và Toyota chỉ vớt được vài bản ghi USD lẻ tẻ nên số liệu dừng ở năm 2012.

    NHƯNG chấp nhận bừa mọi đơn vị còn nguy hiểm hơn: so sánh 45 nghìn tỷ JPY với 400 tỷ
    USD như hai con số cùng loại là sai nghiêm trọng. Vì vậy đồng tiền được GHI LẠI theo
    từng số liệu, và tầng trên có trách nhiệm không trộn lẫn.

    Quy tắc chọn: lấy đồng tiền có NHIỀU BẢN GHI NHẤT — tức đồng tiền doanh nghiệp thực
    sự dùng để lập báo cáo.

    Ưu tiên USD một cách máy móc nghe hợp lý hơn (dễ so sánh) nhưng cho kết quả tệ: TSMC
    khai 49 bản ghi TWD và chỉ 9 bản ghi USD lẻ tẻ, SAP khai 27 bản ghi EUR và đúng 1 bản
    ghi USD. Bám vào USD khiến TSMC mất 9 năm dữ liệu và SAP chỉ còn duy nhất một năm.
    Lấy đồng tiền chính cho chuỗi số liệu liền mạch và nhất quán qua các năm.
    """
    candidates = {
        unit: len(records)
        for unit, records in units.items()
        if (len(unit) == 3 and unit.isalpha() and unit.isupper())
        or (unit.endswith("/shares") and len(unit.split("/")[0]) == 3)
    }
    if not candidates:
        return None

    # Nhiều bản ghi nhất thắng; hòa thì USD thắng cho dễ so sánh
    return max(candidates, key=lambda u: (candidates[u], u.startswith("USD")))


@dataclass
class FinancialFact:
    ticker: str
    company: str
    metric: str          # khóa trong METRIC_TAGS
    label: str           # nhãn tiếng Việt
    fiscal_year: int
    value: float
    unit: str            # "USD", "EUR", "JPY", "USD/shares"...
    currency: str        # mã tiền tệ ISO, tách riêng để tầng trên không trộn lẫn đồng tiền
    us_gaap_tag: str     # mã gốc, để người dùng đối chiếu lại với SEC
    period_start: str
    period_end: str
    form: str
    accession: str       # số hiệu bản khai gốc -> truy vết tới tận nguồn

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def build_fiscal_year_map(taxonomy: dict) -> Dict[str, int]:
    """Dựng bảng tra: ngày kết thúc kỳ -> nhãn năm tài chính do CHÍNH DOANH NGHIỆP đặt.

    ⚠️ BẪY THỨ BA, chỉ lộ ra khi mở rộng từ 4 lên 6.000 doanh nghiệp.

    Suy năm tài chính từ ngày kết thúc kỳ nghe rất hợp lý, nhưng SAI với một nhóm lớn
    doanh nghiệp, và không có quy tắc ngày tháng nào đúng cho tất cả:

        Walmart  kết thúc 31/01/2025 -> họ tự gọi là fiscal 2025  (theo năm KẾT THÚC)
        NVIDIA   kết thúc 26/01/2025 -> họ tự gọi là fiscal 2025  (theo năm KẾT THÚC)
        Target   kết thúc 01/02/2025 -> họ tự gọi là fiscal 2024  (theo năm BẮT ĐẦU)
        Home Depot kết thúc 02/02/2025 -> họ tự gọi là fiscal 2024 (theo năm BẮT ĐẦU)

    Bốn doanh nghiệp, ngày kết thúc chênh nhau đúng hai ngày, hai cách đặt tên ngược
    nhau. Lấy năm của ngày kết thúc sẽ khiến Target và Home Depot lệch một năm — người
    dùng hỏi "doanh thu Target 2024" sẽ nhận về số của năm 2025.

    CÁCH LẤY ĐÚNG: dùng chính nhãn của doanh nghiệp, vốn có sẵn trong dữ liệu.

    Nhớ lại rằng trường "fy" là năm tài chính của BẢN KHAI. Vậy thì trong mỗi bản khai,
    kỳ báo cáo CHÍNH (kỳ có ngày kết thúc muộn nhất) chính là kỳ mà "fy" đang mô tả.
    Các kỳ còn lại trong bản khai đó chỉ là số so sánh của năm trước. Nên:

        với mỗi bản khai -> lấy kỳ kết thúc muộn nhất -> gán fy của bản khai cho kỳ đó

    Làm vậy cho mọi bản khai của doanh nghiệp sẽ phủ kín mọi năm, mỗi năm được gán đúng
    cái tên mà doanh nghiệp tự dùng.
    """
    primary: Dict[str, tuple] = {}  # accession -> (ngày kết thúc muộn nhất, fy)

    for tag_list in list(METRIC_TAGS.values()) + list(IFRS_METRIC_TAGS.values()):
        for tag in tag_list:
            node = taxonomy.get(tag)
            if not node:
                continue
            for records in node.get("units", {}).values():
                for rec in records:
                    if rec.get("form") not in ANNUAL_FORMS or rec.get("fp") != "FY":
                        continue
                    accn, end, fy = rec.get("accn"), rec.get("end"), rec.get("fy")
                    if not accn or not end or fy is None:
                        continue
                    current = primary.get(accn)
                    if current is None or end > current[0]:
                        primary[accn] = (end, int(fy))

    return {end: fy for end, fy in primary.values()}


def _pick_annual(unit_records: List[dict], fy_map: Optional[Dict[str, int]] = None) -> Dict[int, dict]:
    """Lọc lấy đúng số liệu CẢ NĂM từ danh sách bản ghi thô của một mã us-gaap.

    ⚠️ CÁI BẪY LỚN NHẤT CỦA XBRL — đọc kỹ trước khi sửa hàm này.

    Trường "fy" trong companyfacts.json KHÔNG phải năm tài chính của con số. Nó là năm
    tài chính của BẢN KHAI chứa con số đó. Vì mỗi bản 10-K trình bày 3 năm số liệu để
    so sánh, cùng một con số sẽ xuất hiện nhiều lần với các "fy" khác nhau:

        fy=2024  start=2021-09-26  end=2022-09-24  val=394,33B   <- đây là FY2022!
        fy=2024  start=2022-09-25  end=2023-09-30  val=383,29B   <- đây là FY2023!
        fy=2024  start=2023-10-01  end=2024-09-28  val=391,04B   <- đây mới là FY2024

    Lấy "fy" làm năm tài chính sẽ khiến toàn bộ số liệu lệch một tới hai năm — một lỗi
    âm thầm, không báo lỗi, và làm hỏng mọi câu trả lời của hệ thống. Năm tài chính
    được xác định qua NGÀY KẾT THÚC KỲ ("end"), rồi tra sang nhãn của chính doanh
    nghiệp bằng build_fiscal_year_map — xem hàm đó để biết vì sao không thể suy trực
    tiếp từ ngày tháng.

    Các quy tắc còn lại:
      - Chỉ nhận hồ sơ thường niên đã kiểm toán (10-K của doanh nghiệp Mỹ, 20-F/40-F
        của doanh nghiệp nước ngoài — xem ANNUAL_FORMS).
      - Chỉ tiêu theo KỲ (doanh thu, lợi nhuận, dòng tiền — có trường "start"): độ dài
        kỳ phải nằm trong 300-400 ngày, để loại số liệu quý và số liệu lũy kế bất
        thường. Chỉ tiêu tại THỜI ĐIỂM (tổng tài sản, tiền mặt) không có "start".
      - Một năm xuất hiện nhiều lần thì giữ bản khai MỚI NHẤT (accession lớn nhất), vì
        đó là số đã được điều chỉnh/soát xét gần nhất.
      - Nhãn năm tài chính lấy từ `fy_map` (nhãn do chính doanh nghiệp đặt, xem
        build_fiscal_year_map). Chỉ khi kỳ đó không có trong bảng — thường là các năm
        rất cũ mà bản khai gốc đã ngoài phạm vi dữ liệu — mới lùi về lấy năm của ngày
        kết thúc kỳ.
    """
    fy_map = fy_map or {}
    best: Dict[int, dict] = {}

    for rec in unit_records:
        if rec.get("form") not in ANNUAL_FORMS:
            continue

        start, end = rec.get("start"), rec.get("end")
        if not end:
            continue

        try:
            end_date = date.fromisoformat(end)
        except (TypeError, ValueError):
            continue

        if start:
            try:
                days = (end_date - date.fromisoformat(start)).days
            except (TypeError, ValueError):
                continue
            if not 300 <= days <= 400:
                continue

        fiscal_year = fy_map.get(end, end_date.year)

        prev = best.get(fiscal_year)
        if prev is None or rec.get("accn", "") > prev.get("accn", ""):
            best[fiscal_year] = rec

    return best


def extract_facts(companyfacts_path: Path, ticker: str) -> List[FinancialFact]:
    """Đọc companyfacts.json từ đĩa -> danh sách số liệu đã chuẩn hóa."""
    return extract_facts_from_data(
        json.loads(companyfacts_path.read_text(encoding="utf-8")), ticker
    )


def extract_facts_from_data(data: dict, ticker: str) -> List[FinancialFact]:
    """Như extract_facts nhưng nhận dữ liệu đã parse sẵn.

    Tách ra để bước nạp hàng loạt đọc thẳng từng thành viên trong companyfacts.zip mà
    không phải giải nén 18GB ra đĩa.

    ⚠️ BẪY THỨ HAI: doanh nghiệp ĐỔI MÃ KHAI BÁO GIỮA CHỪNG.

    NVIDIA khai doanh thu bằng RevenueFromContractWithCustomerExcludingAssessedTax cho
    các năm cũ, rồi chuyển sang Revenues từ khoảng FY2023. Alphabet đi theo chiều
    ngược lại. Nếu duyệt danh sách mã ưu tiên rồi DỪNG ở mã đầu tiên có dữ liệu, ta sẽ
    khóa vào đúng cái mã đã bị bỏ dùng và mất trắng những năm gần nhất — đúng những năm
    người dùng quan tâm nhất.

    Cách xử lý: GỘP dữ liệu của tất cả các mã ứng viên theo từng năm. Duyệt theo thứ tự
    ưu tiên, mỗi năm chỉ điền một lần, nên mã ưu tiên cao vẫn thắng khi trùng năm, còn
    các mã sau chỉ lấp vào những năm còn trống.
    """
    company = data.get("entityName", ticker)
    all_facts = data.get("facts", {})

    # Gộp hai bộ chuẩn thành một không gian tra cứu. Ưu tiên us-gaap khi trùng tên mã,
    # vì bảng mã us-gaap của ta đã được kiểm chứng kỹ hơn.
    taxonomy = {**all_facts.get("ifrs-full", {}), **all_facts.get("us-gaap", {})}

    # Dựng một lần cho cả doanh nghiệp rồi dùng lại cho mọi chỉ tiêu
    fy_map = build_fiscal_year_map(taxonomy)

    facts: List[FinancialFact] = []

    for metric in METRIC_TAGS:
        # Thử mã us-gaap trước, rồi tới mã IFRS cho những năm còn trống
        tag_candidates = METRIC_TAGS[metric] + IFRS_METRIC_TAGS.get(metric, [])
        merged: Dict[int, tuple] = {}  # năm -> (bản ghi, mã, đơn vị)

        for tag in tag_candidates:
            node = taxonomy.get(tag)
            if not node:
                continue

            units = node.get("units", {})
            unit_key = _pick_unit(units)
            if unit_key is None:
                continue

            for fy, rec in _pick_annual(units[unit_key], fy_map).items():
                if fy not in merged:  # mã ưu tiên cao đã điền thì không ghi đè
                    merged[fy] = (rec, tag, unit_key)

        for fy, (rec, tag, unit_key) in merged.items():
            facts.append(
                FinancialFact(
                    ticker=ticker,
                    company=company,
                    metric=metric,
                    label=METRIC_LABELS[metric],
                    fiscal_year=int(fy),
                    value=float(rec["val"]),
                    unit=unit_key,
                    currency=unit_key.split("/")[0],
                    us_gaap_tag=tag,
                    period_start=rec.get("start", ""),
                    period_end=rec.get("end", ""),
                    form=rec.get("form", ""),
                    accession=rec.get("accn", ""),
                )
            )

    facts.extend(_derive_missing(facts, ticker, company))
    return facts


def _derive_missing(facts: List[FinancialFact], ticker: str, company: str) -> List[FinancialFact]:
    """Tính các chỉ tiêu doanh nghiệp không khai trực tiếp.

    Alphabet không dùng mã GrossProfit bao giờ — họ chỉ khai doanh thu và giá vốn, để
    người đọc tự trừ. Bỏ qua thì hệ thống sẽ trả lời "không có dữ liệu" cho một câu hỏi
    hoàn toàn trả lời được. Số suy ra được đánh dấu us_gaap_tag = "DERIVED:..." để phân
    biệt rạch ròi với số do doanh nghiệp khai, giữ tính minh bạch khi truy vết nguồn.
    """
    lut = facts_to_lookup(facts)
    derived: List[FinancialFact] = []

    revenue = lut.get("revenue", {})
    cogs = lut.get("cost_of_revenue", {})
    have_gp = lut.get("gross_profit", {})

    for year, rev in revenue.items():
        if year in have_gp or year not in cogs:
            continue
        # Không trừ hai số khác đồng tiền
        if rev.currency != cogs[year].currency:
            continue
        derived.append(
            FinancialFact(
                ticker=ticker,
                company=company,
                metric="gross_profit",
                label=METRIC_LABELS["gross_profit"],
                fiscal_year=year,
                value=rev.value - cogs[year].value,
                unit=rev.unit,
                currency=rev.currency,
                us_gaap_tag="DERIVED: Revenues - CostOfRevenue",
                period_start=rev.period_start,
                period_end=rev.period_end,
                form=rev.form,
                accession=rev.accession,
            )
        )

    return derived


def facts_to_lookup(facts: List[FinancialFact]) -> Dict[str, Dict[int, FinancialFact]]:
    """Chuyển thành từ điển tra cứu nhanh: metric -> {năm -> fact}."""
    out: Dict[str, Dict[int, FinancialFact]] = {}
    for f in facts:
        out.setdefault(f.metric, {})[f.fiscal_year] = f
    return out


def _vi_number(x: float, decimals: int = 2) -> str:
    """Định dạng số theo quy ước Việt Nam: dấu chấm ngăn nghìn, dấu phẩy thập phân."""
    s = f"{x:,.{decimals}f}"                  # 1,234.56 (quy ước Anh-Mỹ)
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def format_value(fact: FinancialFact) -> str:
    """Định dạng số cho người đọc: 391035000000 USD -> '391,04 tỷ USD'.

    Luôn in kèm mã tiền tệ. Doanh nghiệp ngoài Mỹ khai bằng EUR/JPY/TWD, và một con số
    không ghi đồng tiền là một con số vô nghĩa khi đặt cạnh số của doanh nghiệp khác.
    """
    v, cur = fact.value, fact.currency
    if fact.unit.endswith("/shares"):
        return f"{_vi_number(v)} {cur}/cp"
    for divisor, suffix in ((1e12, "nghìn tỷ"), (1e9, "tỷ"), (1e6, "triệu")):
        if abs(v) >= divisor:
            return f"{_vi_number(v / divisor)} {suffix} {cur}"
    return f"{_vi_number(v, 0)} {cur}"


def growth_pct(lut: Dict[str, Dict[int, FinancialFact]], metric: str, year: int) -> float | None:
    """Tăng trưởng so với cùng kỳ năm trước, tính bằng %. Trả về None nếu thiếu dữ liệu."""
    row = lut.get(metric, {})
    cur, prev = row.get(year), row.get(year - 1)
    if not cur or not prev or prev.value == 0:
        return None
    return (cur.value - prev.value) / abs(prev.value) * 100


def facts_to_year_rows(facts: List[FinancialFact], name: str = "") -> List[dict]:
    """Gộp các FinancialFact rời rạc thành MỘT bản ghi cho mỗi (công ty, năm tài chính).

    VÌ SAO PHẢI GỘP — đây là quyết định cần thiết khi mở rộng lên 8.001 doanh nghiệp.

    Thiết kế ban đầu tạo một node Neo4j cho mỗi (chỉ tiêu, năm). Với 4 công ty thì đó là
    945 node, hoàn toàn ổn. Với 8.001 công ty × 15 chỉ tiêu × ~10 năm thì thành khoảng
    1,2 TRIỆU node — Neo4j chịu được, nhưng lệnh MERGE lúc nạp chậm hơn cả chục lần và
    mọi truy vấn so sánh đều phải gom ngược lại từ các node rời.

    Gộp theo năm: 8.001 × ~10 = ~80.000 node, mỗi node mang đủ 15 chỉ tiêu làm thuộc
    tính. Câu hỏi kiểu "công ty nào biên lợi nhuận gộp trên 60% năm 2024" trở thành một
    phép lọc thuộc tính đơn giản thay vì một phép gom nhóm nhiều bước.

    Chỉ tiêu suy ra (không do doanh nghiệp khai) được liệt kê trong trường `derived` để
    người dùng luôn phân biệt được số gốc và số tính lại.
    """
    by_year: Dict[int, dict] = {}

    for f in facts:
        row = by_year.setdefault(
            f.fiscal_year,
            {
                "key": f"{f.ticker}|{f.fiscal_year}",
                "ticker": f.ticker,
                "company": name or f.company,
                "fiscal_year": f.fiscal_year,
                "period_end": "",
                "accession": "",
                "currency": f.currency,
                "derived": [],
            },
        )
        row[f.metric] = f.value

        if f.us_gaap_tag.startswith("DERIVED"):
            row["derived"].append(f.metric)

        # Lấy ngày kết thúc kỳ và số hiệu bản khai từ chỉ tiêu doanh thu, vì đó là chỉ
        # tiêu theo kỳ đáng tin cậy nhất và gần như doanh nghiệp nào cũng khai.
        if f.metric == "revenue":
            row["period_end"] = f.period_end
            row["accession"] = f.accession
            row["currency"] = f.currency

    return sorted(by_year.values(), key=lambda r: r["fiscal_year"])

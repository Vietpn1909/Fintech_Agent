"""Gợi ý đầu tư cho doanh nghiệp Việt Nam: Nên mua / Theo dõi / Tránh.

⚠️ ĐÂY LÀ GỢI Ý DO AI ĐƯA RA, CẦN CÂN NHẮC KỸ TRƯỚC KHI THỰC HIỆN THEO.

Câu trên không phải trang trí. Nó được mã lệnh chèn vào MỌI kết quả (xem `DISCLAIMER`),
không giao cho LLM tự viết — giao cho LLM thì có lần nó sẽ quên, và không gì báo ra.

AI CHẤM ĐIỂM: MÃ LỆNH, KHÔNG PHẢI LLM

Cùng nguyên tắc với hồ sơ tự động và so sánh ngành. Mỗi nhóm yếu tố có công thức viết sẵn,
trọng số công bố ở `WEIGHTS`, ngưỡng công bố ở `LEVELS`. LLM chỉ nhận điểm đã tính rồi
viết thành lời, và lời ấy vẫn qua lớp đối chiếu số. Người đọc không đồng ý với phương
pháp thì thấy ngay chỗ để không đồng ý — thay vì phải tin một con số do mô hình "cảm thấy".

BẢY NHÓM YẾU TỐ

    tăng trưởng           CAGR doanh thu, CAGR lợi nhuận                         20
    sinh lời              ROE, biên ròng — kèm PHÂN VỊ TRONG NGÀNH               20
    sức khỏe tài chính    nợ phải trả/vốn chủ, tiền mặt/tổng tài sản             15
    chất lượng lợi nhuận  dòng tiền kinh doanh/lợi nhuận, lợi nhuận cốt lõi      10
    ổn định               số năm lỗ, biến động biên lợi nhuận                    10
    ngành toàn cầu        doanh nghiệp Mỹ cùng ngành (global_industry.py)        10
    vĩ mô thế giới        chỉ số FRED qua bảng độ nhạy ngành (macro.py)          15

⚠️ NGÂN HÀNG VÀ BẢO HIỂM CHẤM BẰNG BẢNG RIÊNG

Đo được: dòng tiền kinh doanh của VCB/lợi nhuận nhảy từ −1,16 (2023) lên 3,30 (2025) —
với ngân hàng, tiền gửi và cho vay đều chảy qua dòng tiền kinh doanh, nên tỷ lệ này vô
nghĩa. Nợ/vốn chủ cao cũng là bản chất của ngân hàng, không phải rủi ro. Áp chung một bảng
thì mọi ngân hàng bị chấm "Tránh". Nên ngân hàng bỏ hẳn nhóm chất lượng lợi nhuận, và sức
khỏe tài chính đo bằng vốn chủ/tổng tài sản.

⚠️ "KHÔNG TÍNH ĐƯỢC" KHÁC "TRUNG TÍNH"

Nhóm nào thiếu dữ liệu thì bị LOẠI và trọng số chia lại cho các nhóm còn lại — không
được cho 50 điểm như thể nó trung tính. Loại quá nhiều (độ phủ dưới 70% tổng trọng số)
thì KHÔNG xếp mức nào cả, trả "không đủ dữ liệu". Cho điểm trên một nền thiếu là đoán, và
một lời đoán mang nhãn "Nên mua" là thứ nguy hiểm nhất hệ thống này có thể sinh ra.

⚠️ HAI CHỐT CHẶN CỨNG

Doanh nghiệp LỖ năm gần nhất hoặc VỐN CHỦ ÂM thì không bao giờ được "Nên mua", dù điểm
tổng cao tới đâu. Không có chốt này thì một doanh nghiệp đang lỗ trong một ngành được vĩ
mô thuận lợi vẫn có thể leo lên "Nên mua" nhờ hai nhóm ngoại cảnh.
"""

from __future__ import annotations

import statistics
import time
from typing import Any, Dict, List, Optional

from src.agent import global_industry, macro
from src.agent.tools import _resolution_failure, _resolve, graph

DISCLAIMER = (
    "⚠️ Đây là gợi ý do AI đưa ra, cần cân nhắc kỹ trước khi thực hiện theo. "
    "Mức \"Nên mua / Theo dõi / Tránh\" dựa trên báo cáo tài chính năm, tình hình doanh "
    "nghiệp Mỹ cùng ngành và chỉ số vĩ mô thế giới — KHÔNG dựa trên giá cổ phiếu hiện "
    "tại: một doanh nghiệp tốt vẫn có thể là khoản đầu tư tệ nếu giá đã quá cao."
)

WEIGHTS: Dict[str, float] = {
    "tang_truong": 20, "sinh_loi": 20, "suc_khoe": 15, "chat_luong_ln": 10,
    "on_dinh": 10, "nganh_toan_cau": 10, "vi_mo": 15,
}
GROUP_NAME = {
    "tang_truong": "Tăng trưởng", "sinh_loi": "Sinh lời", "suc_khoe": "Sức khỏe tài chính",
    "chat_luong_ln": "Chất lượng lợi nhuận", "on_dinh": "Ổn định",
    "nganh_toan_cau": "Ngành toàn cầu (DN Mỹ cùng ngành)", "vi_mo": "Vĩ mô thế giới",
}

# Ngưỡng điểm tổng -> mức gợi ý. Thứ tự giảm dần.
LEVELS = [(70.0, "Nên mua"), (45.0, "Theo dõi"), (0.0, "Tránh")]

# Nhóm bị loại THEO THIẾT KẾ cho từng hồ sơ ngành. Khác với "thiếu dữ liệu": đây là nhóm
# không có nghĩa với ngành đó, nên không tính vào độ phủ.
PROFILE_EXCLUDED = {
    "thuong": set(),
    "ngan_hang": {"chat_luong_ln"},
    "bao_hiem": {"chat_luong_ln"},
}

MIN_COVERAGE = 0.70        # tỷ lệ trọng số tối thiểu phải tính được
MIN_YEARS = 3              # số năm tối thiểu để tính tăng trưởng và ổn định
MIN_SECTOR_PEERS = 5       # số DN tối thiểu để tính phân vị trong ngành
STALE_YEARS = 2            # năm tài chính mới nhất cũ hơn mức này -> không xếp mức

# Chế độ gợi ý danh sách mặc định bỏ doanh nghiệp doanh thu dưới 500 tỷ đồng. Không có dữ
# liệu giá và khối lượng giao dịch nên không lọc được theo thanh khoản; quy mô doanh thu là
# chỉ dấu thay thế thô. Không có lọc này thì đầu danh sách toàn mã UPCOM siêu nhỏ có tỷ lệ
# đẹp trên một nền doanh thu vài tỷ đồng.
DEFAULT_MIN_REVENUE = 500e9

SECTOR_ALIASES = {
    "ngân hàng": "Banks", "ngan hang": "Banks", "bank": "Banks", "banks": "Banks",
    "bảo hiểm": "Insurance", "bao hiem": "Insurance", "insurance": "Insurance",
    "chứng khoán": "Financial Services", "chung khoan": "Financial Services",
    "tài chính": "Financial Services", "financial services": "Financial Services",
    "bất động sản": "Real Estate", "bat dong san": "Real Estate", "real estate": "Real Estate",
    "công nghệ": "Technology", "cong nghe": "Technology", "technology": "Technology",
    "viễn thông": "Telecommunications", "telecommunications": "Telecommunications",
    "dầu khí": "Oil & Gas", "dau khi": "Oil & Gas", "oil & gas": "Oil & Gas",
    "điện": "Utilities", "tiện ích": "Utilities", "utilities": "Utilities",
    "thép": "Basic Materials", "vật liệu": "Basic Materials", "nguyên vật liệu": "Basic Materials",
    "hóa chất": "Basic Materials", "basic materials": "Basic Materials",
    "công nghiệp": "Industrials", "xây dựng": "Industrials", "industrials": "Industrials",
    "hàng tiêu dùng": "Consumer Goods", "thực phẩm": "Consumer Goods", "consumer goods": "Consumer Goods",
    "bán lẻ": "Consumer Services", "dịch vụ tiêu dùng": "Consumer Services",
    "hàng không": "Consumer Services", "consumer services": "Consumer Services",
    "y tế": "Health Care", "dược": "Health Care", "health care": "Health Care",
}


# ------------------------------------------------------------------------- tiện ích


def _linear(value: float, worst: float, best: float) -> float:
    """Thang 0–100 theo đường thẳng. `worst` > `best` thì chiều ngược (giá trị nhỏ là tốt)."""
    if best == worst:
        return 50.0
    return max(0.0, min(100.0, (value - worst) / (best - worst) * 100.0))


def _div(a: Any, b: Any) -> Optional[float]:
    if a is None or b in (None, 0):
        return None
    return a / b


def _cagr(first: float, last: float, years: int) -> Optional[float]:
    if not first or not last or first <= 0 or last <= 0 or years <= 0:
        return None
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


def _item(ten: str, gia_tri: Any, diem: Optional[float], giai_thich: str = "") -> Dict[str, Any]:
    return {"ten": ten, "gia_tri": round(gia_tri, 2) if isinstance(gia_tri, float) else gia_tri,
            "diem": round(diem, 1) if diem is not None else None, "giai_thich": giai_thich}


def _group(items: List[Dict[str, Any]], missing_reason: str = "") -> Dict[str, Any]:
    scored = [i["diem"] for i in items if i["diem"] is not None]
    if not scored:
        return {"status": "thieu_du_lieu", "diem": None, "chi_tieu": items,
                "ly_do": missing_reason or "không có chỉ tiêu nào tính được"}
    return {"status": "ok", "diem": round(sum(scored) / len(scored), 1), "chi_tieu": items}


def profile_of(sector: Optional[str]) -> str:
    if sector == "Banks":
        return "ngan_hang"
    if sector == "Insurance":
        return "bao_hiem"
    return "thuong"


def _percentile(value: float, pool: List[float]) -> Optional[float]:
    """Phần trăm doanh nghiệp trong nhóm có giá trị THẤP HƠN. 100 là cao nhất nhóm."""
    if len(pool) < MIN_SECTOR_PEERS:
        return None
    below = sum(1 for v in pool if v < value)
    return below / len(pool) * 100.0


# ----------------------------------------------------------------------- tải dữ liệu


def _load(tickers: List[str]) -> Dict[str, Dict[str, Any]]:
    """Số liệu nhiều năm cho nhiều mã trong MỘT truy vấn, sắp tăng dần theo năm."""
    rows = graph().run(
        """
        UNWIND $tickers AS t
        MATCH (c:Company {ticker: t})-[:HAS_FINANCIALS]->(f:FinancialYear)
        WHERE f.fiscal_year >= 1990 AND f.fiscal_year <= 2100
        RETURN t AS ticker, c.name AS name, c.sector AS sector, f AS data
        """,
        tickers=tickers,
    )
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        entry = out.setdefault(r["ticker"], {"ticker": r["ticker"], "name": r["name"],
                                             "sector": r["sector"], "years": []})
        entry["years"].append(dict(r["data"]))
    for entry in out.values():
        entry["years"].sort(key=lambda y: y.get("fiscal_year", 0))
    return out


def build_context(sector: Optional[str], companies: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Phần dùng chung cho mọi doanh nghiệp cùng ngành: phân bố ROE/biên ròng, vĩ mô, ngành Mỹ.

    ⚠️ CHẾ ĐỘ MỘT MÃ VÀ CHẾ ĐỘ DANH SÁCH DÙNG CHUNG HÀM NÀY — và đó là cố ý.

    Nếu hai chế độ tự tính phân vị theo hai cách, cùng một doanh nghiệp sẽ được "Nên mua"
    khi hỏi riêng và "Theo dõi" khi nằm trong danh sách. Người dùng thấy hai câu trả lời
    mâu thuẫn và mất lòng tin vào cả hai. Có ca kiểm thử khóa tính chất này lại.
    """
    by_year: Dict[int, Dict[str, List[float]]] = {}
    for c in companies.values():
        for y in c["years"]:
            fy = y.get("fiscal_year")
            if fy is None:
                continue
            bucket = by_year.setdefault(int(fy), {"roe": [], "margin": []})
            roe = _div(y.get("net_income"), y.get("stockholders_equity"))
            if roe is not None and (y.get("stockholders_equity") or 0) > 0:
                bucket["roe"].append(roe * 100.0)
            margin = _div(y.get("net_income"), y.get("revenue"))
            if margin is not None and (y.get("revenue") or 0) > 0:
                bucket["margin"].append(margin * 100.0)
    return {
        "sector": sector,
        "by_year": by_year,
        "macro": macro.sector_score(sector),
        "global": global_industry.score(sector),
    }


# ------------------------------------------------------------------------ các nhóm


def _growth(years: List[Dict[str, Any]]) -> Dict[str, Any]:
    window = years[-5:]
    if len(window) < MIN_YEARS:
        return _group([], f"chỉ có {len(window)} năm số liệu, cần ít nhất {MIN_YEARS}")
    first, last = window[0], window[-1]
    span = int(last["fiscal_year"]) - int(first["fiscal_year"])
    items = []

    rev = _cagr(first.get("revenue"), last.get("revenue"), span)
    if rev is not None:
        items.append(_item(f"CAGR doanh thu {first['fiscal_year']}–{last['fiscal_year']} (%)",
                           rev, _linear(rev, -10.0, 20.0)))

    ni0, ni1 = first.get("net_income"), last.get("net_income")
    if ni1 is not None and ni1 <= 0:
        items.append(_item("Lợi nhuận sau thuế năm gần nhất", ni1, 0.0,
                           "đang lỗ năm gần nhất"))
    elif ni0 is not None and ni0 <= 0 < (ni1 or 0):
        # Không tính được CAGR khi đầu kỳ lỗ. Cho 60 — tích cực nhưng không phải xuất sắc,
        # vì chuyển từ lỗ sang lãi một năm chưa chứng minh được xu hướng.
        items.append(_item("Lợi nhuận: từ lỗ chuyển sang lãi", ni1, 60.0,
                           "không tính được tăng trưởng kép vì đầu kỳ lỗ"))
    else:
        ni = _cagr(ni0, ni1, span)
        if ni is not None:
            items.append(_item(f"CAGR lợi nhuận {first['fiscal_year']}–{last['fiscal_year']} (%)",
                               ni, _linear(ni, -15.0, 25.0)))
    return _group(items)


def _profitability(last: Dict[str, Any], profile: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    items = []
    year_pool = ctx["by_year"].get(int(last.get("fiscal_year", 0)), {"roe": [], "margin": []})
    equity = last.get("stockholders_equity")

    roe = _div(last.get("net_income"), equity)
    if equity is not None and equity <= 0:
        items.append(_item("ROE", None, 0.0, "vốn chủ sở hữu âm"))
    elif roe is not None:
        roe *= 100.0
        best, worst = {"ngan_hang": (20.0, 5.0), "bao_hiem": (15.0, 3.0)}.get(profile, (20.0, 0.0))
        items.append(_item("ROE (%)", roe, _linear(roe, worst, best)))
        pct = _percentile(roe, year_pool["roe"])
        if pct is not None:
            items.append(_item("Phân vị ROE trong ngành", pct, pct,
                               f"so với {len(year_pool['roe'])} doanh nghiệp cùng ngành"))

    if profile == "ngan_hang":
        roa = _div(last.get("net_income"), last.get("total_assets"))
        if roa is not None:
            roa *= 100.0
            items.append(_item("ROA (%)", roa, _linear(roa, 0.5, 2.0)))
    else:
        margin = _div(last.get("net_income"), last.get("revenue"))
        if margin is not None and (last.get("revenue") or 0) > 0:
            margin *= 100.0
            pct = _percentile(margin, year_pool["margin"])
            if pct is not None:
                items.append(_item("Biên lợi nhuận ròng (%) — phân vị trong ngành", margin, pct,
                                   f"phân vị {pct:.0f} trong {len(year_pool['margin'])} DN"))
    return _group(items)


def _health(last: Dict[str, Any], profile: str) -> Dict[str, Any]:
    items = []
    equity, assets = last.get("stockholders_equity"), last.get("total_assets")
    if profile in ("ngan_hang", "bao_hiem"):
        ratio = _div(equity, assets)
        if ratio is not None:
            ratio *= 100.0
            worst, best = (5.0, 12.0) if profile == "ngan_hang" else (10.0, 35.0)
            items.append(_item("Vốn chủ / tổng tài sản (%)", ratio, _linear(ratio, worst, best),
                               "đệm vốn chống rủi ro — với ngân hàng, nợ cao là bản chất ngành"))
        return _group(items)

    if equity is not None and equity <= 0:
        items.append(_item("Nợ phải trả / vốn chủ", None, 0.0, "vốn chủ sở hữu âm"))
    else:
        de = _div(last.get("total_liabilities"), equity)
        if de is not None:
            items.append(_item("Nợ phải trả / vốn chủ (lần)", de, _linear(de, 3.0, 0.5)))
    cash = _div(last.get("cash_and_equivalents"), assets)
    if cash is not None:
        cash *= 100.0
        items.append(_item("Tiền mặt / tổng tài sản (%)", cash, _linear(cash, 0.0, 15.0)))
    return _group(items)


def _earnings_quality(years: List[Dict[str, Any]]) -> Dict[str, Any]:
    items = []
    ratios = [y["operating_cash_flow"] / y["net_income"] for y in years[-3:]
              if y.get("operating_cash_flow") is not None and (y.get("net_income") or 0) > 0]
    if ratios:
        avg = sum(ratios) / len(ratios)
        items.append(_item(f"Dòng tiền kinh doanh / lợi nhuận (TB {len(ratios)} năm)", avg,
                           _linear(avg, 0.0, 1.2),
                           "dưới 1 kéo dài: lợi nhuận trên sổ sách chưa thành tiền thật"))
    last = years[-1]
    core = _div(last.get("operating_income"), last.get("net_income"))
    if core is not None and (last.get("net_income") or 0) > 0:
        items.append(_item("Lợi nhuận hoạt động / lợi nhuận sau thuế", core,
                           _linear(core, 0.6, 1.0),
                           "thấp: lợi nhuận dựa vào khoản bất thường (bán tài sản, tài chính)"))
    return _group(items)


def _stability(years: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(years) < MIN_YEARS:
        return _group([], f"chỉ có {len(years)} năm số liệu")
    items = []
    losses = sum(1 for y in years if (y.get("net_income") or 0) < 0)
    items.append(_item(f"Số năm lỗ trong {len(years)} năm", losses,
                       {0: 100.0, 1: 50.0}.get(losses, 0.0)))
    margins = [y["net_income"] / y["revenue"] * 100.0 for y in years[-5:]
               if y.get("net_income") is not None and (y.get("revenue") or 0) > 0]
    if len(margins) >= 4 and statistics.mean(margins) > 0:
        cv = statistics.pstdev(margins) / statistics.mean(margins)
        items.append(_item("Hệ số biến động biên lợi nhuận", cv, _linear(cv, 1.0, 0.15),
                           "càng thấp càng ổn định"))
    return _group(items)


def _external(result: Dict[str, Any], label: str) -> Dict[str, Any]:
    if result.get("status") != "ok":
        return {"status": "thieu_du_lieu", "diem": None, "chi_tiet": result,
                "ly_do": result.get("ly_do", label + " không tính được")}
    return {"status": "ok", "diem": result["diem"], "chi_tiet": result}


# ----------------------------------------------------------------------- chấm điểm


def score_company(company: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Chấm điểm một doanh nghiệp đã nạp sẵn số liệu. Không chạm cơ sở dữ liệu."""
    years = company["years"]
    if not years:
        return {"status": "no_data", "ticker": company["ticker"]}

    last = years[-1]
    latest_year = int(last.get("fiscal_year", 0))
    profile = profile_of(company.get("sector"))
    excluded = PROFILE_EXCLUDED[profile]

    groups: Dict[str, Dict[str, Any]] = {
        "tang_truong": _growth(years),
        "sinh_loi": _profitability(last, profile, ctx),
        "suc_khoe": _health(last, profile),
        "on_dinh": _stability(years),
        "nganh_toan_cau": _external(ctx["global"], "tín hiệu ngành toàn cầu"),
        "vi_mo": _external(ctx["macro"], "vĩ mô"),
    }
    if "chat_luong_ln" not in excluded:
        groups["chat_luong_ln"] = _earnings_quality(years)

    profile_weight = sum(w for k, w in WEIGHTS.items() if k not in excluded)
    used = {k: g for k, g in groups.items() if g["status"] == "ok"}
    used_weight = sum(WEIGHTS[k] for k in used)
    coverage = used_weight / profile_weight

    for key, g in groups.items():
        g["ten"] = GROUP_NAME[key]
        g["trong_so_goc"] = WEIGHTS[key]
        # Trọng số THỰC dùng sau khi chia lại — người đọc cần thấy nhóm nào thực sự quyết định.
        g["trong_so_thuc"] = round(WEIGHTS[key] / used_weight * 100.0, 1) if key in used and used_weight else 0.0

    total = (sum(g["diem"] * WEIGHTS[k] for k, g in used.items()) / used_weight
             if used_weight else None)

    level, reasons = None, []
    current_year = time.localtime().tm_year
    if current_year - latest_year > STALE_YEARS:
        reasons.append(f"Số liệu mới nhất là năm {latest_year}, quá cũ để gợi ý.")
    if coverage < MIN_COVERAGE:
        reasons.append(f"Chỉ tính được {coverage * 100:.0f}% trọng số (cần tối thiểu "
                       f"{MIN_COVERAGE * 100:.0f}%) — không đủ dữ liệu để xếp mức.")
    if not reasons and total is not None:
        level = next(name for floor, name in LEVELS if total >= floor)

    # Hai chốt chặn cứng — xem chú thích đầu tệp.
    caps = []
    if level == "Nên mua":
        if (last.get("net_income") or 0) <= 0:
            caps.append("đang lỗ năm gần nhất")
        if (last.get("stockholders_equity") or 0) <= 0:
            caps.append("vốn chủ sở hữu âm")
        if caps:
            level = "Theo dõi"

    ranked = sorted(used.items(), key=lambda kv: kv[1]["diem"], reverse=True)
    return {
        "status": "ok" if level else "khong_xep_muc",
        "ticker": company["ticker"], "company": company["name"],
        "sector": company.get("sector"), "ho_so_cham": profile,
        "nam_so_lieu": latest_year,
        "muc": level,
        "diem_tong": round(total, 1) if total is not None else None,
        "do_phu_trong_so": round(coverage * 100.0, 1),
        "ly_do_khong_xep_muc": reasons,
        "chot_chan": (f"Điểm đủ mức 'Nên mua' nhưng bị hạ xuống 'Theo dõi' vì: "
                      f"{', '.join(caps)}.") if caps else None,
        "nhom": groups,
        "diem_manh": [GROUP_NAME[k] for k, g in ranked[:2] if g["diem"] >= 60],
        "diem_yeu": [GROUP_NAME[k] for k, g in ranked[-2:] if g["diem"] < 45],
        "doanh_thu_gan_nhat": last.get("revenue"),
        "currency": last.get("currency") or "VND",
    }


def _not_considered(result: Optional[Dict[str, Any]] = None) -> List[str]:
    """Những gì gợi ý này KHÔNG xét — sinh từ chính kết quả, không viết tay hết."""
    out = [
        "Giá cổ phiếu, vốn hóa và mọi chỉ số định giá (P/E, P/B) — hệ thống chưa có dữ liệu giá.",
        "Vĩ mô TRONG NƯỚC (lãi suất, tỷ giá, lạm phát Việt Nam) — nguồn FRED không có.",
        "Tin tức, sự kiện doanh nghiệp, kết quả kinh doanh theo QUÝ.",
        "Khẩu vị rủi ro, khung thời gian đầu tư và danh mục hiện có của bạn.",
    ]
    if result:
        for key, g in result.get("nhom", {}).items():
            if g.get("status") != "ok":
                out.append(f"Nhóm '{GROUP_NAME[key]}' không tính được: {g.get('ly_do', '')}")
        excluded = PROFILE_EXCLUDED.get(result.get("ho_so_cham", "thuong"), set())
        for key in excluded:
            out.append(f"Nhóm '{GROUP_NAME[key]}' không áp dụng cho ngành này theo thiết kế.")
    return out


def _methodology() -> Dict[str, Any]:
    return {
        "trong_so": {GROUP_NAME[k]: w for k, w in WEIGHTS.items()},
        "nguong": {name: floor for floor, name in LEVELS},
        "chot_chan": "Lỗ năm gần nhất hoặc vốn chủ âm thì không bao giờ 'Nên mua'.",
        "do_phu_toi_thieu_pct": MIN_COVERAGE * 100,
    }


# ------------------------------------------------------------------- hai chế độ dùng


def assess(company: str) -> Dict[str, Any]:
    """Chế độ 1: gợi ý cho MỘT doanh nghiệp Việt Nam."""
    resolved = _resolve(company)
    if resolved["status"] != "ok":
        return _resolution_failure(company, resolved)

    best = resolved["best"]
    ticker = best["ticker"]
    if not ticker.endswith(".VN"):
        # Doanh nghiệp Mỹ chỉ tham gia làm TÍN HIỆU NGÀNH ở giai đoạn này.
        return {"status": "chi_ho_tro_viet_nam", "ticker": ticker, "company": best["name"],
                "ly_do": ("Chức năng gợi ý đầu tư hiện chỉ xếp mức cho doanh nghiệp niêm yết "
                          "tại Việt Nam. Doanh nghiệp Mỹ được dùng làm tín hiệu ngành toàn "
                          "cầu, chưa được xếp mức riêng."),
                "canh_bao": DISCLAIMER}

    sector_rows = graph().run(
        "MATCH (c:Company {ticker: $t}) RETURN c.sector AS sector", t=ticker)
    sector = sector_rows[0]["sector"] if sector_rows else None
    peers = [r["t"] for r in graph().run(
        "MATCH (c:Company {market: 'VN', sector: $s}) RETURN c.ticker AS t", s=sector)] \
        if sector else [ticker]
    if ticker not in peers:
        peers.append(ticker)

    companies = _load(peers)
    ctx = build_context(sector, companies)
    if ticker not in companies:
        return {"status": "no_data", "ticker": ticker, "company": best["name"],
                "canh_bao": DISCLAIMER}

    result = score_company(companies[ticker], ctx)
    result["canh_bao"] = DISCLAIMER
    result["chua_xet"] = _not_considered(result)
    result["phuong_phap"] = _methodology()
    return result


def normalize_sector(name: str) -> Optional[str]:
    key = (name or "").strip().lower()
    if key in SECTOR_ALIASES:
        return SECTOR_ALIASES[key]
    for sector in global_industry.SECTOR_SIC:
        if sector.lower() == key:
            return sector
    return None


def suggest(sector: str, top: int = 10,
            min_revenue: float = DEFAULT_MIN_REVENUE) -> Dict[str, Any]:
    """Chế độ 2: xếp hạng doanh nghiệp Việt Nam trong một ngành."""
    resolved = normalize_sector(sector)
    if not resolved:
        return {"status": "khong_ro_nganh", "query": sector,
                "cac_nganh": sorted(global_industry.SECTOR_SIC),
                "canh_bao": DISCLAIMER}

    tickers = [r["t"] for r in graph().run(
        "MATCH (c:Company {market: 'VN', sector: $s}) RETURN c.ticker AS t", s=resolved)]
    companies = _load(tickers)
    ctx = build_context(resolved, companies)

    scored, small, unrated = [], 0, 0
    for comp in companies.values():
        r = score_company(comp, ctx)
        if r["status"] != "ok":
            unrated += 1
            continue
        if (r.get("doanh_thu_gan_nhat") or 0) < min_revenue:
            small += 1
            continue
        scored.append(r)
    scored.sort(key=lambda r: r["diem_tong"], reverse=True)

    counts = {name: sum(1 for r in scored if r["muc"] == name) for _, name in LEVELS}
    return {
        "status": "ok",
        "nganh": resolved,
        "so_doanh_nghiep_trong_nganh": len(companies),
        "so_duoc_xep_muc": len(scored),
        "bo_qua_vi_quy_mo_nho": small,
        "bo_qua_vi_thieu_du_lieu": unrated,
        "nguong_doanh_thu_toi_thieu": min_revenue,
        "phan_bo_muc": counts,
        # Bản rút gọn: chỉ giữ thứ cần để viết lời, không đổ cả chi tiết từng chỉ tiêu
        # của mười doanh nghiệp vào ngữ cảnh LLM.
        "danh_sach": [
            {"ticker": r["ticker"], "company": r["company"], "muc": r["muc"],
             "diem_tong": r["diem_tong"], "diem_manh": r["diem_manh"],
             "diem_yeu": r["diem_yeu"], "chot_chan": r["chot_chan"],
             "diem_nhom": {GROUP_NAME[k]: g["diem"] for k, g in r["nhom"].items()
                           if g["status"] == "ok"}}
            for r in scored[:top]
        ],
        "boi_canh_nganh": {"vi_mo": ctx["macro"], "nganh_toan_cau": ctx["global"]},
        "canh_bao": DISCLAIMER,
        "chua_xet": _not_considered() + [
            f"Đã bỏ {small} doanh nghiệp doanh thu dưới {min_revenue / 1e9:,.0f} tỷ đồng — "
            "không có dữ liệu thanh khoản nên quy mô là chỉ dấu thay thế thô."],
        "phuong_phap": _methodology(),
    }

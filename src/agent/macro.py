"""Bối cảnh vĩ mô thế giới — chỉ số chính thức từ FRED, quy về điểm theo từng ngành.

VÌ SAO KHÔNG ĐỂ LLM TỰ ĐÁNH GIÁ "TÌNH HÌNH THẾ GIỚI"

Mô hình chạy local không lên mạng được, và kiến thức của nó dừng ở ngày huấn luyện. Hỏi
nó "lãi suất Fed hiện bao nhiêu" thì nó trả lời bằng con số của quá khứ, rất tự tin. Nên
mọi chỉ số ở đây lấy từ FRED (Ngân hàng Dự trữ Liên bang St. Louis) — con số chính
thức, có ngày, kiểm chứng được — và mã lệnh tính tín hiệu, không phải LLM.

⚠️ VĨ MÔ TÁC ĐỘNG MỖI NGÀNH MỖI KHÁC, VÀ BẢNG ĐỘ NHẠY LÀ GIẢ ĐỊNH CỦA TÁC GIẢ

Lãi suất tăng thì bất động sản chịu thiệt (chi phí vốn tăng, người mua khó vay), nhưng
bảo hiểm có khi lại lợi (danh mục trái phiếu sinh lời hơn). Không có cách nào "đúng"
khách quan để quy một chỉ số vĩ mô thành điểm cho một ngành. Nên `SENSITIVITY` dưới đây
được viết RÕ RÀNG trong mã và công bố trong mọi kết quả — người đọc không đồng ý với một
giả định thì thấy ngay giả định nào, thay vì nó bị chôn trong trọng số của mô hình.

⚠️ FRED KHÔNG CÓ SỐ LIỆU VIỆT NAM

Đã thử: tỷ giá VND/USD (CCUSMA02VNM618N) và CPI Việt Nam (VNMCPIALLMINMEI) đều "series
does not exist". Nên bối cảnh ở đây là THẾ GIỚI tác động lên Việt Nam (lãi suất Mỹ, sức
mạnh đồng USD, giá hàng hóa, cầu tiêu dùng Mỹ), không phải vĩ mô trong nước. Mọi kết quả
phải nói rõ điều này — "đã xét vĩ mô" mà không nói là vĩ mô nào thì người đọc sẽ tưởng
lãi suất và lạm phát Việt Nam cũng đã được tính.

⚠️ SỐ LIỆU CŨ BỊ LOẠI, KHÔNG ĐƯỢC DÙNG NHƯ SỐ MỚI

Mỗi chuỗi có hạn tuổi riêng theo tần suất công bố. Quá hạn thì chuỗi đó bị loại khỏi
điểm và ghi vào `bi_loai` — không im lặng dùng con số của ba tháng trước như thể nó là
tình hình hôm nay.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from config.settings import PROCESSED_DIR, settings

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
CACHE_PATH = PROCESSED_DIR / "macro_cache.json"

# Tải lại sau mỗi 12 giờ. Chỉ số ngày cập nhật mỗi ngày làm việc, nên giữ lâu hơn thì
# lỡ mất số mới; gọi mỗi lần hỏi thì tốn mà không được gì.
CACHE_HOURS = 12

# Kỳ so sánh: thay đổi trong 6 tháng. Đủ dài để không bị nhiễu ngắn hạn, đủ ngắn để phản
# ánh "tình hình hiện tại" chứ không phải chu kỳ nhiều năm.
LOOKBACK_DAYS = 182

# id: (tên, đơn vị thay đổi, thang quy đổi, hạn tuổi tối đa tính bằng ngày)
#
# Đơn vị thay đổi:
#   "pp"  chênh lệch điểm phần trăm — dành cho lãi suất. Lãi suất từ 2% lên 3% là "tăng
#         1 điểm", tính theo phần trăm thì thành "tăng 50%" và nghe như thảm họa.
#   "%"   phần trăm thay đổi — dành cho giá và chỉ số.
#
# Thang quy đổi: mức thay đổi được coi là tín hiệu TỐI ĐA (±1). Lãi suất đổi 1 điểm trong
# sáu tháng là rất lớn; giá dầu đổi 30% mới tương đương.
#
# Hạn tuổi: chuỗi ngày ~2 tuần; chuỗi tháng của Mỹ công bố trễ ~1,5 tháng nên 100 ngày;
# chuỗi giá hàng hóa của IMF trễ tới 3 tháng nên 130 ngày.
SERIES: Dict[str, tuple] = {
    "DFF":          ("Lãi suất Fed",                        "pp", 1.0, 14),
    "DGS10":        ("Lợi suất trái phiếu Mỹ 10 năm",      "pp", 1.0, 14),
    "DTWEXBGS":     ("Chỉ số sức mạnh đồng USD",            "%", 8.0, 21),
    "DCOILBRENTEU": ("Giá dầu Brent",                       "%", 30.0, 14),
    "CPIAUCSL":     ("Lạm phát Mỹ (CPI)",                   "%", 3.0, 100),
    "WPU101":       ("Giá sắt thép Mỹ (PPI)",               "%", 15.0, 100),
    "PCOPPUSDM":    ("Giá đồng thế giới",                   "%", 20.0, 130),
    "PRUBBUSDM":    ("Giá cao su thế giới",                 "%", 20.0, 130),
    "RSAFS":        ("Doanh số bán lẻ Mỹ (cầu tiêu dùng)",  "%", 5.0, 100),
}

# ⚠️ BẢNG ĐỘ NHẠY — GIẢ ĐỊNH CỦA TÁC GIẢ, xem chú thích đầu tệp.
#
# Dấu (+/−): chỉ số TĂNG thì ngành đó LỢI (+) hay THIỆT (−). Độ lớn 0,5–1 là mức quan
# trọng tương đối. Ngành Việt Nam lấy theo trường `sector` của dữ liệu VCI.
#
# Lý do từng dòng ghi bên cạnh để người đọc phản biện được, không phải tin.
SENSITIVITY: Dict[str, Dict[str, float]] = {
    # Chi phí vốn và sức mua nhà phụ thuộc trực tiếp vào lãi suất.
    "Real Estate":        {"DFF": -1.0, "DGS10": -1.0},
    # USD mạnh gây áp lực tỷ giá, NHNN thường phải thắt thanh khoản -> tín dụng chậm lại.
    "Banks":              {"DFF": -0.5, "DTWEXBGS": -0.5},
    # Công ty chứng khoán nhạy nhất với dòng tiền: lãi suất cao kéo tiền khỏi cổ phiếu.
    "Financial Services": {"DFF": -1.0, "DTWEXBGS": -0.5},
    # Lợi suất trái phiếu cao làm danh mục đầu tư của doanh nghiệp bảo hiểm sinh lời hơn.
    "Insurance":          {"DGS10": +0.5},
    # Thép, hóa chất, cao su, kim loại: giá bán đi theo giá hàng hóa thế giới.
    "Basic Materials":    {"WPU101": +1.0, "PCOPPUSDM": +0.5, "PRUBBUSDM": +0.5},
    # Doanh thu dầu khí đi theo giá dầu.
    "Oil & Gas":          {"DCOILBRENTEU": +1.0},
    # Xây dựng, vận tải, sản xuất: dầu là chi phí đầu vào; cầu Mỹ là thị trường xuất khẩu.
    "Industrials":        {"DCOILBRENTEU": -0.5, "DTWEXBGS": -0.5, "RSAFS": +0.5},
    # Dệt may, thủy sản, đồ gỗ: Mỹ là thị trường xuất khẩu lớn nhất.
    "Consumer Goods":     {"RSAFS": +1.0, "DCOILBRENTEU": -0.5},
    # Hàng không (VJC) chịu nặng nhất giá nhiên liệu; bán lẻ chịu tỷ giá hàng nhập.
    "Consumer Services":  {"DCOILBRENTEU": -1.0, "DTWEXBGS": -0.5},
    # Nhiệt điện và khí: nhiên liệu là chi phí, giá bán điện bị điều tiết.
    "Utilities":          {"DCOILBRENTEU": -0.5},
    # Xuất khẩu dịch vụ IT sang Mỹ/Nhật: cầu Mỹ là động lực, lãi suất cao làm khách hàng
    # cắt ngân sách công nghệ.
    "Technology":         {"RSAFS": +0.5, "DFF": -0.5},
    # Nguyên liệu dược phần lớn nhập khẩu, thanh toán bằng USD.
    "Health Care":        {"DTWEXBGS": -0.5},
    # Thiết bị viễn thông nhập khẩu.
    "Telecommunications": {"DTWEXBGS": -0.5},
}


# ----------------------------------------------------------------------------- tải dữ liệu


def _fetch(series_id: str) -> List[Dict[str, Any]]:
    resp = requests.get(FRED_URL, params={
        "series_id": series_id, "api_key": settings.fred_api_key, "file_type": "json",
        "sort_order": "desc", "limit": 400,
    }, timeout=30)
    resp.raise_for_status()
    return [{"date": o["date"], "value": float(o["value"])}
            for o in resp.json().get("observations", []) if o["value"] not in (".", "")]


def load(force: bool = False) -> Dict[str, Any]:
    """Tất cả chuỗi, có đệm. Một chuỗi lỗi KHÔNG làm hỏng các chuỗi khác."""
    if not settings.fred_api_key:
        return {"status": "no_key", "series": {}}

    if not force and CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if time.time() - cached.get("fetched_at", 0) < CACHE_HOURS * 3600:
                return cached
        except json.JSONDecodeError:
            pass

    out: Dict[str, Any] = {"status": "ok", "fetched_at": time.time(), "series": {},
                           "errors": {}}
    for sid in SERIES:
        try:
            out["series"][sid] = _fetch(sid)
        except Exception as exc:  # noqa: BLE001
            out["errors"][sid] = str(exc)[:200]
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


# ------------------------------------------------------------------------------ tín hiệu


def _value_near(obs: List[Dict[str, Any]], target: date) -> Optional[Dict[str, Any]]:
    """Quan sát gần nhất nằm TRƯỚC hoặc ĐÚNG ngày mốc. `obs` đã sắp giảm dần theo ngày."""
    for o in obs:
        if datetime.strptime(o["date"], "%Y-%m-%d").date() <= target:
            return o
    return None


def signal(series_id: str, obs: List[Dict[str, Any]],
           today: Optional[date] = None) -> Dict[str, Any]:
    """Thay đổi trong 6 tháng của một chuỗi, quy về khoảng [−1, +1].

    Tách khỏi phần tải dữ liệu để kiểm thử được mà không cần mạng.
    """
    name, unit, scale, max_age = SERIES[series_id]
    today = today or date.today()
    if not obs:
        return {"id": series_id, "ten": name, "status": "khong_co_du_lieu"}

    latest = obs[0]
    latest_date = datetime.strptime(latest["date"], "%Y-%m-%d").date()
    age = (today - latest_date).days
    if age > max_age:
        return {"id": series_id, "ten": name, "status": "qua_cu",
                "ngay_moi_nhat": latest["date"], "tuoi_ngay": age, "han_tuoi": max_age}

    base = _value_near(obs, date.fromordinal(latest_date.toordinal() - LOOKBACK_DAYS))
    if not base:
        return {"id": series_id, "ten": name, "status": "khong_du_lich_su"}

    if unit == "pp":
        change = latest["value"] - base["value"]
    else:
        if not base["value"]:
            return {"id": series_id, "ten": name, "status": "khong_tinh_duoc"}
        change = (latest["value"] - base["value"]) / abs(base["value"]) * 100.0

    return {
        "id": series_id, "ten": name, "status": "ok",
        "don_vi": "điểm phần trăm" if unit == "pp" else "%",
        "tu_ngay": base["date"], "gia_tri_dau": round(base["value"], 3),
        "den_ngay": latest["date"], "gia_tri_cuoi": round(latest["value"], 3),
        "thay_doi": round(change, 2),
        # Kẹp vào [−1, 1]: một cú sốc giá dầu 80% không được phép chiếm trọn điểm vĩ mô.
        "tin_hieu": round(max(-1.0, min(1.0, change / scale)), 3),
    }


def sector_score(sector: Optional[str], data: Optional[Dict[str, Any]] = None,
                 today: Optional[date] = None) -> Dict[str, Any]:
    """Điểm vĩ mô 0–100 cho một ngành Việt Nam. 50 là trung tính.

    Trả `status` khác "ok" khi KHÔNG tính được — bên gọi phải loại nhóm vĩ mô khỏi điểm
    tổng và nói rõ lý do, không được coi như 50 điểm trung tính. "Không biết" và "trung
    tính" là hai điều khác nhau, và gộp chúng là giả vờ biết.
    """
    data = data if data is not None else load()
    if data.get("status") == "no_key":
        return {"status": "khong_co_api_key",
                "ly_do": "Chưa cấu hình FRED_API_KEY — chưa xét vĩ mô."}

    weights = SENSITIVITY.get(sector or "")
    if not weights:
        return {"status": "khong_co_bang_do_nhay",
                "ly_do": f"Chưa có giả định độ nhạy vĩ mô cho ngành '{sector}'."}

    used, dropped = [], []
    for sid, weight in weights.items():
        sig = signal(sid, data.get("series", {}).get(sid, []), today=today)
        if sig["status"] != "ok":
            dropped.append(sig)
            continue
        effect = weight * sig["tin_hieu"]
        used.append({**sig, "trong_so_nganh": weight, "tac_dong": round(effect, 3),
                     "chieu": "có lợi" if effect > 0.05 else
                              "bất lợi" if effect < -0.05 else "gần như trung tính"})

    if not used:
        return {"status": "khong_du_du_lieu", "bi_loai": dropped,
                "ly_do": "Mọi chỉ số vĩ mô liên quan tới ngành này đều thiếu hoặc quá cũ."}

    total_weight = sum(abs(u["trong_so_nganh"]) for u in used)
    raw = sum(u["tac_dong"] for u in used) / total_weight        # trong [−1, 1]
    return {
        "status": "ok",
        "nganh": sector,
        "diem": round(50.0 + 50.0 * raw, 1),
        "chi_so": used,
        "bi_loai": dropped,
        "pham_vi": ("Vĩ mô THẾ GIỚI tác động lên ngành (lãi suất Mỹ, sức mạnh USD, giá hàng "
                    "hóa, cầu tiêu dùng Mỹ). KHÔNG gồm lãi suất, tỷ giá hay lạm phát Việt "
                    "Nam — FRED không có các chuỗi này."),
        "gia_dinh": ("Chiều và mức tác động của từng chỉ số lên ngành là giả định của hệ "
                     "thống, công bố trong bảng SENSITIVITY của src/agent/macro.py."),
    }

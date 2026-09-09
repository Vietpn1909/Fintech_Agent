"""Kiểm thử tầng số liệu Việt Nam.

Ba nhóm, mỗi nhóm khóa lại một hành vi khác nhau:

  1. NHẬN ĐÚNG      mã Việt Nam không trùng mã Mỹ phải ra đúng doanh nghiệp Việt Nam
  2. BÁO NHẬP NHẰNG mã trùng cả hai sàn thì TUYỆT ĐỐI không được tự chọn một bên
  3. KHÔNG LẪN LỘN  số liệu VND không được trộn vào bảng xếp hạng theo USD

Nhóm 2 là quan trọng nhất. 8/30 mã trong rổ VN30 trùng với mã của SEC và trỏ tới những
doanh nghiệp hoàn toàn khác nhau — ACB là Ngân hàng Á Châu ở Việt Nam nhưng là AURORA
CANNABIS ở Mỹ. Ưu tiên cứng bên nào cũng tái tạo đúng lỗi "trả về số của doanh nghiệp
khác mà không báo gì" đã mất công sửa.

Chạy:  .venv/Scripts/python.exe tests/test_vietnam.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.agent.tools import compare_financials, lookup_financials
from src.ingest.on_demand import resolve_company

# Mã Việt Nam KHÔNG trùng mã SEC -> phải ra thẳng doanh nghiệp Việt Nam
MUST_RESOLVE_VN = ["FPT", "VNM", "HPG", "VIC", "VHM", "MBB", "VJC", "SSI", "STB", "DGC"]

# Mã trùng cả hai sàn -> phải báo nhập nhằng, không được tự chọn
MUST_BE_AMBIGUOUS = [
    ("ACB", "Ngân hàng Á Châu (VN) vs AURORA CANNABIS (Mỹ)"),
    ("MSN", "Tập đoàn Masan (VN) vs EMERSON RADIO (Mỹ)"),
    ("PLX", "Petrolimex (VN) vs Protalix BioTherapeutics (Mỹ)"),
    ("TPB", "TPBank (VN) vs Turning Point Brands (Mỹ)"),
    ("HDB", "HDBank (VN) vs HDFC BANK (Mỹ)"),
    # Hai ca dưới không trùng MÃ mà trùng TIỀN TỐ TÊN — vẫn phải báo nhập nhằng.
    # Chính hai ca này làm lộ ra rằng va chạm không chỉ xảy ra ở mã chứng khoán.
    ("GAS", "PV GAS (VN) vs GAS TRANSPORTER OF THE SOUTH (Mỹ, khớp tiền tố tên)"),
    ("SAB", "Sabeco (VN) vs SAB Biotherapeutics (Mỹ, khớp tiền tố tên)"),
    ("MWG", "Thế Giới Di Động (VN) vs Multi Ways Holdings (Mỹ)"),
]

# Mã Mỹ vẫn phải nguyên vẹn sau khi thêm dữ liệu Việt Nam
MUST_STAY_US = [("NVDA", "NVDA"), ("AAPL", "AAPL"), ("MSFT", "MSFT"), ("TSLA", "TSLA")]


def main() -> int:
    failures = []

    print("=" * 78)
    print("NHÓM 1 — mã Việt Nam riêng biệt, phải nhận đúng")
    print("=" * 78)
    for symbol in MUST_RESOLVE_VN:
        result = resolve_company(symbol)
        if result["status"] != "ok" or not result["best"]["ticker"].endswith(".VN"):
            failures.append(f"{symbol!r} lẽ ra là doanh nghiệp Việt Nam, nhận được {result}")
            print(f"  SAI   {symbol:6} -> {result.get('status')}")
            continue
        data = lookup_financials(company=symbol, metrics=["revenue"])
        year = (data.get("years") or [{}])[0]
        revenue = year.get("revenue")
        if data["status"] != "ok" or not revenue:
            failures.append(f"{symbol!r} phân giải được nhưng không có số liệu")
            print(f"  SAI   {symbol:6} -> {data['status']}, thiếu doanh thu")
            continue
        print(f"  đúng  {symbol:6} -> {result['best']['ticker']:9} "
              f"FY{year.get('fiscal_year')} {revenue / 1e12:8,.1f} nghìn tỷ VND")

    print()
    print("=" * 78)
    print("NHÓM 2 — mã trùng hai sàn, phải BÁO NHẬP NHẰNG chứ không tự chọn")
    print("=" * 78)
    for symbol, why in MUST_BE_AMBIGUOUS:
        result = resolve_company(symbol)
        if result["status"] != "ambiguous":
            got = result.get("best", {}).get("name", result.get("status"))
            failures.append(f"{symbol!r} lẽ ra phải báo nhập nhằng, nhưng tự chọn {got}")
            print(f"  SAI   {symbol:6} -> tự chọn {str(got)[:44]}")
            continue
        tool = lookup_financials(company=symbol)
        if tool["status"] != "ambiguous":
            failures.append(f"{symbol!r} công cụ nuốt mất thông tin nhập nhằng")
            print(f"  SAI   {symbol:6} -> công cụ trả về {tool['status']}")
            continue
        names = " | ".join(o["company"][:26] for o in tool["options"])
        print(f"  đúng  {symbol:6} -> nhập nhằng: {names}")
        print(f"        └ {why}")

    print()
    print("=" * 78)
    print("NHÓM 3 — dữ liệu Mỹ không bị ảnh hưởng")
    print("=" * 78)
    for query, want in MUST_STAY_US:
        result = resolve_company(query)
        got = result.get("best", {}).get("ticker") if result["status"] == "ok" else None
        if got != want:
            failures.append(f"{query!r} lẽ ra là {want}, nhận được {got}")
            print(f"  SAI   {query:6} -> {got}")
        else:
            print(f"  đúng  {query:6} -> {want}")

    print()
    print("=" * 78)
    print("NHÓM 4 — so sánh khác đồng tiền phải bị chặn")
    print("=" * 78)
    mixed = compare_financials(companies=["FPT", "Apple"], metric="revenue", year=2024)
    currencies = mixed.get("currencies") or []
    if mixed.get("mixed_currency_warning") and len(currencies) > 1:
        print(f"  đúng  FPT (VND) + Apple (USD) -> có cảnh báo, đồng tiền: {currencies}")
    else:
        failures.append("So sánh VND với USD mà KHÔNG có cảnh báo trộn đồng tiền")
        print(f"  SAI   thiếu cảnh báo, đồng tiền: {currencies}")

    print()
    print("=" * 78)
    total = len(MUST_RESOLVE_VN) + len(MUST_BE_AMBIGUOUS) + len(MUST_STAY_US) + 1
    if failures:
        print(f"THẤT BẠI: {len(failures)}/{total} ca sai")
        for item in failures:
            print(f"  · {item}")
        return 1
    print(f"TẤT CẢ {total} CA ĐỀU ĐÚNG")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

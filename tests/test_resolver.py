"""Kiểm thử hồi quy cho bộ phân giải tên doanh nghiệp.

VÌ SAO FILE NÀY TỒN TẠI

Đã xảy ra một lỗi nặng: hỏi doanh thu "Acer", hệ thống trả về `status: ok` kèm doanh thu
đầy đủ của MACERICH — một quỹ bất động sản trung tâm thương mại. Hỏi "Altera" thì ra
ALTRIA, công ty thuốc lá. Không có thông báo lỗi nào.

Đây là kiểu hỏng tệ nhất trong cả hệ thống: con số thì đúng, chủ thể thì sai, và người
đọc không có cách nào phát hiện. Nó vô hiệu hóa chính nguyên tắc trung tâm của dự án —
mọi con số đều lấy từ XBRL chứ không cho mô hình tự đọc.

Bộ kiểm thử này khóa hành vi đúng lại. Hai nhóm ca đều bắt buộc:

    NHÓM TỪ CHỐI  — những tên KHÔNG có trong SEC. Phải trả về not_found, tuyệt đối
                    không được đoán bừa ra một doanh nghiệp khác.
    NHÓM CHẤP NHẬN — những tên có thật. Phải vẫn nhận đúng, để việc siết chặt không
                    làm hỏng các truy vấn bình thường.

Nhóm thứ hai quan trọng ngang nhóm thứ nhất: sửa lỗi mà làm hỏng chức năng đang chạy thì
không phải là sửa.

Chạy:  .venv/Scripts/python.exe tests/test_resolver.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings  # noqa: F401  — chỉnh stdout sang UTF-8 cho Windows
from src.ingest.on_demand import resolve_company

# ---------------------------------------------------------------- các ca kiểm thử

# Tên phải BỊ TỪ CHỐI. Mỗi dòng kèm lý do và kết quả sai mà bản cũ đã trả về.
MUST_REJECT = [
    ("Acer",                  "Acer niêm yết ở Đài Loan; bản cũ trả về MACERICH (bất động sản)"),
    ("Altera",                "đã bị Intel mua; bản cũ trả về ALTRIA (thuốc lá)"),
    ("Acacia Communications", "đã bị Cisco mua; bản cũ trả về SAGA COMMUNICATIONS"),
    ("Brother Industries",    "niêm yết ở Nhật; bản cũ trả về THOR INDUSTRIES (xe nhà lưu động)"),
    ("EY",                    "công ty kiểm toán, không niêm yết; bản cũ trả về AEye"),
    ("ASCO",                  "bản cũ trả về MASCO"),
    ("Foxconn",               "niêm yết ở Đài Loan"),
    ("Deloitte",              "công ty kiểm toán, không niêm yết"),
    ("Anthropic",             "công ty tư nhân, không niêm yết"),
    ("Azure",                 "đây là SẢN PHẨM của Microsoft, không phải doanh nghiệp"),
    ("Amazon Web Services",   "đây là bộ phận của Amazon, không phải doanh nghiệp riêng"),
    ("Celestial AI",          "công ty tư nhân"),
    ("Avansys",               "không có trong danh sách SEC"),
    ("Boys & Girls Clubs of Silicon Valley", "tổ chức từ thiện lọt vào từ mục cộng đồng của 10-K"),
    ("Computershare Trust Company", "đại lý chuyển nhượng, câu bìa pháp lý trong 10-K"),
    ("ASUSTeK Computer Inc",  "niêm yết ở Đài Loan"),
]

# Tên phải VẪN NHẬN ĐÚNG, kèm mã chứng khoán bắt buộc.
MUST_ACCEPT = [
    ("NVDA", "NVDA"), ("NVIDIA", "NVDA"), ("nvidia", "NVDA"),
    ("Apple", "AAPL"), ("apple inc", "AAPL"),
    ("Microsoft", "MSFT"), ("microsft", "MSFT"),          # gõ sai một ký tự
    ("Alphabet", "GOOGL"), ("Google", "GOOGL"),           # qua bảng viết tắt
    ("TSMC", "TSM"), ("Taiwan Semiconductor", "TSM"),     # qua bảng viết tắt
    ("Tesla", "TSLA"), ("teslla", "TSLA"),                # gõ thừa một ký tự
    ("Coca Cola", "KO"),
    ("Walmart", "WMT"),
    ("Amazon", "AMZN"),
    ("Berkshire Hathaway", "BRK-B"),
    ("Intel", "INTC"),
    ("AMD", "AMD"), ("Advanced Micro Devices", "AMD"),
    ("Ford", "F"),
    ("Meta", "META"),
    ("Netflix", "NFLX"),
    ("Oracle", "ORCL"),
    ("Salesforce", "CRM"),
    ("Broadcom", "AVGO"),
    ("Qualcomm", "QCOM"),
    ("ASML", "ASML"),
    ("Toyota", "TM"),
    ("Sony", "SONY"),
]


def main() -> int:
    failures = []

    print("=" * 78)
    print("NHÓM 1 — phải TỪ CHỐI (không được đoán bừa)")
    print("=" * 78)
    for name, why in MUST_REJECT:
        result = resolve_company(name)
        if result["status"] == "ok":
            got = result["best"]
            failures.append(f"{name!r} lẽ ra phải bị từ chối, nhưng nhận thành "
                            f"{got['name']} ({got['ticker']}, khớp kiểu {got['match']})")
            print(f"  SAI   {name:38} -> {got['name'][:34]} ({got['match']})")
        else:
            hint = ", ".join(s["name"][:24] for s in result.get("suggestions", [])[:2])
            print(f"  đúng  {name:38} không tìm thấy"
                  + (f" · gợi ý: {hint}" if hint else ""))
        print(f"        └ {why}")

    print()
    print("=" * 78)
    print("NHÓM 2 — phải VẪN NHẬN ĐÚNG (siết chặt không được làm hỏng chức năng)")
    print("=" * 78)
    for name, want in MUST_ACCEPT:
        result = resolve_company(name)
        if result["status"] != "ok":
            failures.append(f"{name!r} lẽ ra phải nhận ra {want}, nhưng báo không tìm thấy")
            print(f"  SAI   {name:30} -> KHÔNG TÌM THẤY (mong đợi {want})")
        elif result["best"]["ticker"] != want:
            got = result["best"]
            failures.append(f"{name!r} lẽ ra là {want}, nhưng ra {got['ticker']} ({got['name']})")
            print(f"  SAI   {name:30} -> {got['ticker']} {got['name'][:30]} (mong đợi {want})")
        else:
            print(f"  đúng  {name:30} -> {want:6} ({result['best']['match']})")

    print()
    print("=" * 78)
    total = len(MUST_REJECT) + len(MUST_ACCEPT)
    if failures:
        print(f"THẤT BẠI: {len(failures)}/{total} ca sai")
        for f in failures:
            print(f"  · {f}")
        return 1

    print(f"TẤT CẢ {total} CA ĐỀU ĐÚNG "
          f"({len(MUST_REJECT)} ca từ chối · {len(MUST_ACCEPT)} ca chấp nhận)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

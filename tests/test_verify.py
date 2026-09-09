"""Kiểm thử bộ đối chiếu số ở câu trả lời cuối.

Bộ này khoá lại HAI hành vi ngược nhau, và cả hai đều quan trọng như nhau:

  PHẢI BẮT     con số mô hình tự sinh ra — chép sai, sai bậc độ lớn, hoặc bịa hẳn
  KHÔNG ĐƯỢC BÁO  con số hợp lệ được viết theo cách khác — làm tròn, đổi bậc, số âm,
                  hoặc nhắc lại điều kiện trong chính câu hỏi

Vế thứ hai mới là vế khó, và cũng là vế quyết định bộ này có dùng được hay không. Một
cảnh báo sai làm người dùng mất tin vào MỌI cảnh báo, kể cả những cái đúng. Mỗi ca "không
được báo" dưới đây đều lấy từ một lần báo nhầm THẬT đã xảy ra khi chạy trên dữ liệu thật.

Chạy:  .venv/Scripts/python.exe tests/test_verify.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.agent.verify import check_answer

# ---------------------------------------------------------------- PHẢI BẮT

MUST_FLAG = [
    (
        "chép sai số dù nguồn nằm ngay trong ngữ cảnh",
        "Lợi nhuận gộp của Apple FY2024 là 119.100.000.000 USD.",
        [{"gross_profit": 180683000000.0}],
        "",
        "119.100.000.000",
    ),
    (
        "sai bậc độ lớn 1.000 lần — số thô đúng nhưng cách đọc sai",
        "Doanh thu TSMC FY2024 là 2.894.307,70 tỷ TWD.",
        [{"revenue": 2894307700000.0}],
        "",
        "2.894.307,70",
    ),
    (
        "bịa thêm một doanh nghiệp không có trong kết quả công cụ",
        "1. Walmart: 642.637.000.000 USD\n2. Cigna: 247.121.000.000 USD",
        [{"ticker": "WMT", "revenue": 642637000000.0}],
        "",
        "247.121.000.000",
    ),
]

# ---------------------------------------------------------- KHÔNG ĐƯỢC BÁO

MUST_PASS = [
    (
        "số làm tròn kèm từ chỉ bậc",
        "Lợi nhuận gộp là 180,68 tỷ USD (180.683.000.000 USD).",
        [{"gross_profit": 180683000000.0}],
        "",
    ),
    (
        # Từng báo nhầm thật: bộ đọc cắt mất dấu trừ nên so 18.756 tỷ với -18.756 tỷ.
        "số ÂM — Intel lỗ trong năm 2024",
        "Intel FY2024 ghi nhận lợi nhuận sau thuế là -18756000000.0 USD.",
        [{"net_income": -18756000000.0}],
        "",
    ),
    (
        # Từng báo nhầm thật: ngưỡng lọc do người dùng nêu ra, không phải số liệu.
        "ngưỡng nhắc lại từ chính câu hỏi",
        "Các doanh nghiệp có doanh thu trên 200 tỷ USD gồm: Walmart 642.637.000.000 USD.",
        [{"ticker": "WMT", "revenue": 642637000000.0}],
        "Những doanh nghiệp nào có doanh thu trên 200 tỷ USD năm 2024?",
    ),
    (
        # Từng báo nhầm thật: nguồn là văn xuôi 10-K viết "$17.7 billion", câu trả lời
        # viết "17,7 tỷ USD". Không đọc từ chỉ bậc ở phía nguồn thì hai bên không khớp.
        "nguồn là văn xuôi tiếng Anh, câu trả lời là tiếng Việt",
        "Microsoft chi 17,7 tỷ USD cho hạ tầng.",
        ["Capital expenditures were $17.7 billion driven by AI infrastructure."],
        "",
    ),
    (
        "năm, phần trăm, số lượng — dưới ngưỡng, không xét",
        "Năm 2024 có 16 doanh nghiệp, tăng 12,5% so với 2023.",
        [{"revenue": 642637000000.0}],
        "",
    ),
    (
        "đổi bậc: nguồn ghi số thô, câu trả lời ghi nghìn tỷ",
        "Doanh thu Petrolimex năm 2024 là 284,0 nghìn tỷ VND.",
        [{"revenue": 284000000000000.0}],
        "",
    ),
]


def main() -> int:
    failures = []

    print("=" * 78)
    print("NHÓM 1 — PHẢI BẮT được số do mô hình tự sinh")
    print("=" * 78)
    for why, answer, obs, question, expect in MUST_FLAG:
        result = check_answer(answer, obs, question)
        found = [item["text"] for item in result["unverified"]]
        if expect in found:
            print(f"  đúng  {why}")
            print(f"        └ bắt được: {found}")
        else:
            failures.append(f"KHÔNG bắt được {expect!r} ({why}); chỉ thấy {found}")
            print(f"  SAI   {why}")
            print(f"        └ lẽ ra phải bắt {expect!r}, nhận được {found}")

    print()
    print("=" * 78)
    print("NHÓM 2 — KHÔNG ĐƯỢC báo nhầm số hợp lệ")
    print("=" * 78)
    for why, answer, obs, question in MUST_PASS:
        result = check_answer(answer, obs, question)
        if result["ok"]:
            print(f"  đúng  {why}  (đã đối chiếu {result['checked']} số)")
        else:
            found = [item["text"] for item in result["unverified"]]
            failures.append(f"BÁO NHẦM {found} ({why})")
            print(f"  SAI   {why}")
            print(f"        └ báo nhầm: {found}")

    print()
    print("=" * 78)
    total = len(MUST_FLAG) + len(MUST_PASS)
    if failures:
        print(f"THẤT BẠI: {len(failures)}/{total} ca sai")
        for item in failures:
            print(f"  · {item}")
        return 1
    print(f"TẤT CẢ {total} CA ĐỀU ĐÚNG "
          f"({len(MUST_FLAG)} ca phải bắt · {len(MUST_PASS)} ca không được báo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

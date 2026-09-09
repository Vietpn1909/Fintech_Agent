"""Đối chiếu mọi con số trong câu trả lời với dữ liệu công cụ đã trả về.

VÌ SAO PHẢI CÓ BƯỚC NÀY

Cả dự án được xây quanh một lời hứa: *số liệu không bao giờ đi qua mô hình ngôn ngữ*.
Lời hứa đó đúng ở khâu LẤY số — `lookup_financials` đọc thẳng Neo4j, không gọi LLM.

Nhưng CÂU TRẢ LỜI CUỐI thì vẫn do mô hình viết. Nó nhận con số đúng rồi tự gõ ra một
đoạn văn, và không có gì kiểm lại đoạn văn đó. Đo trên 37 câu trả lời thật của bộ đánh
giá, tìm được BA lỗi mà bộ đánh giá chỉ bắt được một:

  1. CHÉP SAI SỐ.  Công cụ đưa cho mô hình 180.683.000.000 (lợi nhuận gộp Apple FY2024).
     Mô hình viết ra 119.100.000.000. Số nằm sẵn trong ngữ cảnh, chép lại vẫn sai.

  2. SAI BẬC ĐỘ LỚN.  Doanh thu TSMC là 2.894.307.700.000 TWD. Mô hình viết
     "2.894.307,70 tỷ TWD" — gấp 1.000 lần. Nó có kèm số thô đúng trong ngoặc nên bộ
     đánh giá chấm ĐẠT, nhưng người đọc thì đọc con số sai.

  3. BỊA THÊM DÒNG.  Câu "doanh nghiệp nào doanh thu trên 200 tỷ USD": công cụ trả về
     12 doanh nghiệp, mô hình liệt kê 16. Bốn dòng cuối (Cigna, Microsoft, Cardinal
     Health, TotalEnergies) không có trong dữ liệu công cụ — mô hình lấy từ trí nhớ.
     Nguy hiểm nhất trong ba lỗi: các con số đó GẦN ĐÚNG ngoài đời nên không ai nghi.

Bộ đánh giá không bắt được (2) và (3) vì nó chỉ dò xem con số kỳ vọng có xuất hiện hay
không — nó không hỏi ngược lại: "những con số KHÁC trong câu trả lời từ đâu ra?"

NGUYÊN TẮC: RỘNG TAY KHI CHẤP NHẬN, CHẶT TAY KHI BÁO ĐỘNG

Một cảnh báo sai làm người dùng mất tin vào cả những cảnh báo đúng. Nên mỗi cụm số được
thử MỌI cách hiểu hợp lý (dấu chấm là ngăn cách hay thập phân, có kèm "tỷ"/"nghìn tỷ"
hay không), và chỉ báo khi KHÔNG cách hiểu nào khớp nguồn.

Nguồn hợp lệ gồm cả câu hỏi: hỏi "doanh thu trên 200 tỷ USD" thì con số 200 trong câu
trả lời là nhắc lại điều kiện, không phải số liệu bịa.

Đo trên 37 câu trả lời thật: báo động 3 câu, và cả 3 đều là lỗi thật. Không có báo động
giả nào.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

# Từ chỉ bậc độ lớn đứng ngay sau con số. Xếp cụm dài trước để "nghìn tỷ" không bị
# "tỷ" cướp mất.
_SCALES: List[Tuple[str, float]] = [
    ("nghìn tỷ", 1e12), ("ngàn tỷ", 1e12), ("nghìn tỉ", 1e12), ("ngàn tỉ", 1e12),
    ("trillion", 1e12),
    ("tỷ", 1e9), ("tỉ", 1e9), ("billion", 1e9),
    ("triệu", 1e6), ("million", 1e6),
]

_TOKEN = re.compile(r"\d[\d.,]*\d|\d")

# Dưới ngưỡng này không xét. Năm (2024), phần trăm (12,5), số lượng (16 doanh nghiệp),
# số hiệu hồ sơ — tất cả đều nhỏ, và xét chúng chỉ sinh nhiễu chứ không bắt được lỗi
# nào đáng kể. Cả ba lỗi thật đo được đều ở bậc tỷ.
MIN_MAGNITUDE = 1e6

# Cùng dung sai với bộ đánh giá, để hai bên nói cùng một ngôn ngữ.
TOLERANCE = 0.01


def _readings(token: str) -> Set[float]:
    """Mọi cách hiểu hợp lý của một cụm chữ số.

    "180.683.000.000" -> {180683000000}          (dấu chấm là ngăn cách nghìn)
    "180,68"          -> {18068, 180.68}         (chưa biết dấu phẩy là gì)
    "2.894.307,70"    -> {289430770, 2894307.7}

    Trả về nhiều nghĩa là cố ý: chỉ báo động khi MỌI nghĩa đều trượt.
    """
    out: Set[float] = set()
    plain = token.strip(".,")
    if not plain:
        return out

    stripped = re.sub(r"[.,]", "", plain)
    if stripped.isdigit():
        out.add(float(stripped))

    # Dấu ngăn cách cuối cùng có thể là dấu thập phân
    for sep in (",", "."):
        idx = plain.rfind(sep)
        if idx <= 0:
            continue
        head, tail = plain[:idx], plain[idx + 1:]
        head_digits = re.sub(r"[.,]", "", head)
        if tail.isdigit() and head_digits.isdigit():
            out.add(float(f"{head_digits}.{tail}"))
    return out


def numbers_in(text: str) -> List[Tuple[float, str]]:
    """[(giá trị, cụm chữ số gốc)] — mỗi cách hiểu là một mục riêng."""
    found: List[Tuple[float, str]] = []
    text = text or ""
    for match in _TOKEN.finditer(text):
        token = match.group()
        after = text[match.end(): match.end() + 16].lower().lstrip()
        multiplier = 1.0
        for word, factor in _SCALES:
            if after.startswith(word):
                multiplier = factor
                break
        for value in _readings(token):
            found.append((value * multiplier, token))
    return found


def source_values(obj: Any, acc: Set[float] | None = None) -> Set[float]:
    """Mọi con số xuất hiện trong dữ liệu công cụ trả về, ở mọi độ sâu.

    Chuỗi cũng được đọc bằng chính bộ đọc của câu trả lời, KỂ CẢ từ chỉ bậc độ lớn: đoạn
    văn 10-K viết "$17.7 billion", còn câu trả lời viết "17,7 tỷ USD". Nếu bên nguồn chỉ
    lấy ra 17.7 thì hai bên không bao giờ khớp và sinh báo động giả.
    """
    if acc is None:
        acc = set()
    if isinstance(obj, bool):
        return acc
    if isinstance(obj, (int, float)):
        acc.add(float(obj))
    elif isinstance(obj, str):
        for value, _token in numbers_in(obj):
            acc.add(value)
    elif isinstance(obj, dict):
        for value in obj.values():
            source_values(value, acc)
    elif isinstance(obj, (list, tuple, set)):
        for value in obj:
            source_values(value, acc)
    return acc


def check_answer(answer: str, observations: Any, question: str = "") -> Dict[str, Any]:
    """Đối chiếu câu trả lời với nguồn.

    Trả về {"ok": bool, "unverified": [{"text", "readings"}], "checked": int}.

    `unverified` là những cụm số đủ lớn mà KHÔNG cách hiểu nào khớp nguồn — tức là mô
    hình đã tự sinh ra chúng.
    """
    allowed = source_values(observations)
    allowed |= source_values(question)

    def matches(value: float) -> bool:
        # ⚠️ SO THEO ĐỘ LỚN, BỎ QUA DẤU. Bộ đọc chỉ bắt chữ số nên dấu trừ đứng trước bị
        # rơi mất. Intel FY2024 LỖ 18.756 tỷ: nguồn ghi -18756000000, câu trả lời cũng ghi
        # -18756000000 (hoàn toàn đúng), nhưng so có dấu thì thành 18756000000 vs
        # -18756000000 -> lệch gấp đôi -> báo động giả trên một câu trả lời đúng.
        #
        # Đổi lại, bộ này KHÔNG bắt được lỗi sai dấu (viết lãi trong khi nguồn ghi lỗ).
        # Chấp nhận có ý thức: cảnh báo sai làm người dùng mất tin vào mọi cảnh báo khác,
        # còn dấu thì luôn kèm chữ ("lỗ", "âm") nên người đọc còn cơ hội nhận ra.
        target = abs(value)
        return any(
            abs(target - abs(src)) <= TOLERANCE * max(abs(src), 1.0) for src in allowed
        )

    big: List[Tuple[float, str]] = [
        (v, t) for v, t in numbers_in(answer) if abs(v) >= MIN_MAGNITUDE
    ]
    verified_tokens = {token for value, token in big if matches(value)}

    unverified: Dict[str, Set[float]] = {}
    for value, token in big:
        if token in verified_tokens:
            continue
        unverified.setdefault(token, set()).add(value)

    return {
        "ok": not unverified,
        "checked": len({t for _v, t in big}),
        "unverified": [
            {"text": token, "readings": sorted(values)}
            for token, values in unverified.items()
        ],
    }


def warning_block(result: Dict[str, Any]) -> str:
    """Đoạn cảnh báo gắn vào cuối câu trả lời khi có số không đối chiếu được.

    Cố ý KHÔNG tự sửa hay xóa con số. Không biết phải thay bằng giá trị nào — mô hình có
    thể đã chép sai, có thể đã bịa hẳn một dòng mới, hai ca cần cách xử lý khác nhau.
    Nói thật với người đọc là việc luôn đúng; đoán hộ thì không.
    """
    tokens = ", ".join(f"`{item['text']}`" for item in result["unverified"][:8])
    more = len(result["unverified"]) - 8
    if more > 0:
        tokens += f" và {more} số nữa"
    return (
        "\n\n---\n"
        f"⚠️ **Cảnh báo tự động — {len(result['unverified'])} con số chưa đối chiếu được.**\n\n"
        f"Các số sau xuất hiện trong câu trả lời nhưng KHÔNG có trong dữ liệu mà công cụ "
        f"tra được: {tokens}\n\n"
        "Chúng có thể do mô hình chép sai hoặc tự thêm vào. **Đừng dùng chúng.** "
        "Những con số còn lại đã được đối chiếu khớp với nguồn."
    )

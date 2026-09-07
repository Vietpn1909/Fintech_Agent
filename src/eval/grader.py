"""Bộ chấm xác định — dò số trong câu trả lời, không dùng LLM.

VÌ SAO CẦN THƯỚC ĐO NÀY BÊN CẠNH RAGAS

RAGAS chấm bằng cách nhờ một LLM làm giám khảo. Trong dự án này, giám khảo sẽ là chính
model local đang chạy — tức là dùng công cụ đang cần đánh giá để tự đánh giá mình. Đó
là lỗi phương pháp luận, và là câu hỏi đầu tiên hội đồng chấm sẽ hỏi.

Thước đo ở đây không có vấn đề đó: câu hỏi "doanh thu NVIDIA FY2026 là bao nhiêu" có
đúng một đáp án, lấy từ số doanh nghiệp khai với SEC. Chấm bằng cách dò xem con số đó
có xuất hiện trong câu trả lời hay không. Xác định, lặp lại được, không tranh cãi.

KHÓ KHĂN THỰC TẾ: MỘT CON SỐ CÓ RẤT NHIỀU CÁCH VIẾT

    215.938.000.000 USD        (đầy đủ, quy ước Việt Nam)
    215,938,000,000 USD        (đầy đủ, quy ước Anh-Mỹ)
    215,94 tỷ USD              (rút gọn, dấu phẩy thập phân)
    215.94 billion             (rút gọn, tiếng Anh)
    khoảng 216 tỷ USD          (làm tròn)
    215,9 tỷ                   (không ghi đơn vị tiền)

Bộ dò phải nhận ra tất cả, nếu không nó sẽ chấm trượt những câu trả lời ĐÚNG và cho ra
điểm số bi quan sai lệch — còn tệ hơn không đo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

# Từ chỉ bậc độ lớn -> hệ số nhân.
# Có cả biến thể KHÔNG DẤU, vì model chạy local thỉnh thoảng trả lời thiếu dấu tiếng
# Việt ("215,94 ty USD"). Bỏ sót biến thể này sẽ khiến bộ chấm đánh trượt những câu trả
# lời đúng, tạo ra điểm số bi quan sai lệch — còn tệ hơn là không đo.
SCALE_WORDS = {
    "nghìn tỷ": 1e12, "ngàn tỷ": 1e12, "nghin ty": 1e12, "ngan ty": 1e12, "trillion": 1e12,
    "tỷ": 1e9, "tỉ": 1e9, "ty": 1e9, "ti": 1e9, "billion": 1e9, "bn": 1e9,
    "triệu": 1e6, "trieu": 1e6, "million": 1e6, "mn": 1e6,
    "nghìn": 1e3, "ngàn": 1e3, "nghin": 1e3, "ngan": 1e3, "thousand": 1e3,
}

# Một số, có thể kèm dấu ngăn nhóm và phần thập phân, rồi (tùy chọn) một từ chỉ bậc.
# Thứ tự trong nhóm chọn phải để cụm DÀI trước cụm ngắn, nếu không "nghìn tỷ" sẽ bị
# khớp thành "nghìn" và con số bị chia sai một tỷ lần.
_SCALE_ALTERNATION = "|".join(
    re.escape(w) for w in sorted(SCALE_WORDS, key=len, reverse=True)
)
# Dấu âm là BẮT BUỘC phải nhận. Lỗ hổng này đã đánh trượt 2 câu trả lời HOÀN TOÀN ĐÚNG
# trong bộ đánh giá: Intel lỗ -2,21 tỷ USD năm 2025, agent trả lời đúng y nguyên, nhưng
# bộ chấm bỏ qua dấu trừ, không khớp được, rồi bắt nhầm số "2025" (năm) làm ứng viên.
# Doanh nghiệp thua lỗ là chuyện rất thường — bỏ sót dấu âm là bỏ sót cả một nhóm dữ liệu.
_NUMBER_RE = re.compile(
    r"(?<![\w.,])"
    r"(-|−|\()?\s*"
    r"(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    # Cho phép dấu đóng ngoặc giữa con số và từ chỉ bậc. Kế toán viết số âm theo lối
    # "(2,21) tỷ USD"; thiếu chỗ này thì bắt được "2,21" nhưng mất chữ "tỷ", và giá trị
    # bị đọc thành 2,21 thay vì 2,21 tỷ — sai một tỷ lần mà vẫn trông như đọc được số.
    r"\)?\s*"
    rf"(?:({_SCALE_ALTERNATION})(?![\wàáâãèéêìíòóôõùúýăđĩũơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]))?",
    re.IGNORECASE,
)


def _parse_number(raw: str) -> Optional[float]:
    """Đọc một chuỗi số viết theo quy ước Việt Nam hoặc Anh-Mỹ.

    Chỗ khó là phân biệt dấu ngăn nhóm với dấu thập phân, vì hai quy ước dùng ngược nhau:
    "1.234" là một nghìn hai trăm ba tư theo quy ước Việt Nam, nhưng là một phẩy hai ba
    bốn theo quy ước Anh-Mỹ. Quy tắc suy luận:

      - Có cả "." và ","  -> dấu xuất hiện SAU cùng là dấu thập phân.
      - Chỉ có một loại dấu, và các nhóm sau nó đều đúng 3 chữ số -> đó là dấu ngăn nhóm.
      - Còn lại -> dấu thập phân.
    """
    text = raw.strip()
    has_dot, has_comma = "." in text, "," in text

    if has_dot and has_comma:
        decimal_sep = "." if text.rfind(".") > text.rfind(",") else ","
        group_sep = "," if decimal_sep == "." else "."
        text = text.replace(group_sep, "").replace(decimal_sep, ".")
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = text.split(sep)
        # Mọi nhóm sau dấu đầu tiên đều đúng 3 chữ số -> dấu ngăn nhóm.
        # NGOẠI TRỪ khi phần nguyên có số 0 đứng đầu: "0,216" chắc chắn là không phẩy hai
        # một sáu, vì không ai viết số 216 thành "0,216". Thiếu điều kiện này thì
        # "0,216 nghìn tỷ" bị đọc thành 216 nghìn tỷ — sai một nghìn lần.
        leading_zero = len(parts[0]) > 1 and parts[0].startswith("0") or parts[0] == "0"
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]) and not leading_zero:
            text = text.replace(sep, "")
        else:
            text = text.replace(sep, ".")

    try:
        return float(text)
    except ValueError:
        return None


def _ambiguous_alternative(raw: str) -> Optional[float]:
    """Cách đọc THỨ HAI của một chuỗi số thực sự nhập nhằng.

    Gặp thật khi chạy agent lần đầu: model trả lời "215,938 tỷ USD".

    Theo quy ước Việt Nam, dấu phẩy là dấu thập phân, nên đây là 215,938 tỷ — hoàn toàn
    đúng. Nhưng luật "nhóm sau dấu có đúng 3 chữ số thì đó là dấu ngăn nghìn" lại đọc
    thành 215938 tỷ, tức sai 1000 lần, và bộ chấm sẽ đánh TRƯỢT một câu trả lời ĐÚNG.

    Chuỗi như "215,938" là nhập nhằng thật, không có cách nào phân định chắc chắn chỉ từ
    bản thân nó. Nên thay vì đoán, ta trả về CẢ HAI cách đọc làm ứng viên. Điều này chỉ
    ảnh hưởng tới việc câu trả lời đúng có được ghi nhận hay không — một con số sai vẫn
    trượt, vì sai số cho phép chỉ 1% và hai cách đọc lệch nhau 1000 lần.
    """
    text = raw.strip()
    if text.count(".") + text.count(",") != 1:
        return None  # không có dấu, hoặc nhiều dấu -> không nhập nhằng

    sep = "." if "." in text else ","
    parts = text.split(sep)
    if len(parts) != 2 or len(parts[1]) != 3:
        return None  # nhóm sau không đúng 3 chữ số -> không nhập nhằng

    try:
        as_group = float(text.replace(sep, ""))       # 215,938 -> 215938
        as_decimal = float(text.replace(sep, "."))    # 215,938 -> 215.938
    except ValueError:
        return None
    return as_decimal if as_group != as_decimal else None


def extract_values(answer: str) -> List[float]:
    """Trích mọi con số trong câu trả lời, đã quy đổi về đơn vị gốc (USD).

    Với chuỗi nhập nhằng, trả về cả hai cách đọc (xem _ambiguous_alternative).
    """
    values: List[float] = []
    for match in _NUMBER_RE.finditer(answer or ""):
        # Nhóm 1 = dấu âm, nhóm 2 = con số, nhóm 3 = từ chỉ bậc.
        # Thêm nhóm dấu vào đầu regex làm LỆCH toàn bộ chỉ số nhóm phía sau — đúng loại
        # lỗi lặng lẽ: số vẫn đọc được, chỉ là đọc nhầm từ chỉ bậc thành con số.
        sign_token, raw, scale_word = match.group(1), match.group(2), (match.group(3) or "").lower()
        number = _parse_number(raw)
        if number is None:
            continue

        scale = SCALE_WORDS.get(scale_word, 1.0)

        # Dấu trừ tường minh: chắc chắn là số âm.
        # Dấu ngoặc mở: NHẬP NHẰNG, phải nhận cả hai khả năng.
        #
        # Kế toán viết số âm là "(2,21) tỷ", nhưng người ta cũng viết ngoặc để chú thích:
        # "2.894.307,70 tỷ TWD (2.894.307.700.000 TWD)". Coi mọi dấu "(" là số âm đã đánh
        # trượt một câu trả lời HOÀN TOÀN ĐÚNG trong bộ đánh giá — con số trong ngoặc bị
        # lật dấu thành âm rồi lệch 200% so với đáp án.
        #
        # Không phân định được từ bản thân chuỗi, nên trả về cả hai dấu làm ứng viên. An
        # toàn vì sai số cho phép chỉ 1%, mà một số và số đối của nó luôn lệch nhau 200%.
        if sign_token in ("-", "−"):
            signs = (-1.0,)
        elif sign_token == "(":
            signs = (1.0, -1.0)
        else:
            signs = (1.0,)

        alternative = _ambiguous_alternative(raw)
        for sign in signs:
            values.append(sign * number * scale)
            if alternative is not None:
                values.append(sign * alternative * scale)
    return values


@dataclass
class GradeResult:
    correct: bool
    matched_value: Optional[float]
    expected: float
    relative_error: Optional[float]
    entities_found: List[str]
    entities_missing: List[str]
    n_numbers_in_answer: int


def grade_numeric(
    answer: str,
    expected: float,
    expected_entities: Optional[List[str]] = None,
    tolerance: float = 0.01,
) -> GradeResult:
    """Chấm một câu hỏi tra số.

    `tolerance` mặc định 1%: đủ rộng để chấp nhận câu trả lời làm tròn hợp lý
    ("khoảng 216 tỷ" so với 215,938 tỷ lệch 0,03%), nhưng đủ chặt để bắt được sai số
    thật sự — nhầm năm hay nhầm công ty luôn lệch xa hơn 1% rất nhiều.
    """
    values = extract_values(answer)
    best_value, best_error = None, None

    for v in values:
        if expected == 0:
            error = abs(v)
        else:
            error = abs(v - expected) / abs(expected)
        if best_error is None or error < best_error:
            best_value, best_error = v, error

    entities = expected_entities or []
    upper = (answer or "").upper()
    found = [e for e in entities if e.upper() in upper]

    return GradeResult(
        correct=best_error is not None and best_error <= tolerance,
        matched_value=best_value,
        expected=expected,
        relative_error=best_error,
        entities_found=found,
        entities_missing=[e for e in entities if e not in found],
        n_numbers_in_answer=len(values),
    )


def grade_entities(answer: str, expected_entities: List[str]) -> float:
    """Tỷ lệ thực thể bắt buộc xuất hiện trong câu trả lời.

    Dùng cho câu hỏi sàng lọc và bắc cầu: không có một con số đúng duy nhất, nhưng câu
    trả lời đúng BẮT BUỘC phải nhắc tới những thực thể cụ thể. Đây là thước đo xác định
    bổ sung cho RAGAS, không phụ thuộc vào LLM giám khảo.
    """
    if not expected_entities:
        return 1.0
    upper = (answer or "").upper()
    hits = sum(1 for e in expected_entities if e.upper() in upper)
    return hits / len(expected_entities)

"""Đối chiếu mọi con số trong câu trả lời với dữ liệu công cụ đã trả về.

VÌ SAO PHẢI CÓ BƯỚC NÀY

Cả dự án được xây quanh một lời hứa: *số liệu không bao giờ đi qua mô hình ngôn ngữ*.
Lời hứa đó đúng ở khâu LẤY số — `lookup_financials` đọc thẳng Neo4j, không gọi LLM.

Nhưng CÂU TRẢ LỜI CUỐI thì vẫn do mô hình viết. Nó nhận con số đúng rồi tự gõ ra một
đoạn văn, và không có gì kiểm lại đoạn văn đó. Đo trên 37 câu trả lời thật của bộ đánh
giá, có hai lỗi lọt qua:

  1. CHÉP SAI SỐ.  Công cụ đưa cho mô hình 180.683.000.000 (lợi nhuận gộp Apple FY2024).
     Mô hình viết ra 119.100.000.000. Số nằm sẵn trong ngữ cảnh, chép lại vẫn sai.

  2. SAI BẬC ĐỘ LỚN.  Doanh thu TSMC là 2.894.307.700.000 TWD. Mô hình viết
     "2.894.307,70 tỷ TWD" — gấp 1.000 lần. Nó có kèm số thô đúng trong ngoặc nên bộ
     đánh giá chấm ĐẠT, nhưng người đọc thì đọc con số sai.

Bộ đánh giá không bắt được ca (2) vì nó chỉ dò xem con số kỳ vọng có xuất hiện hay
không — nó không hỏi ngược lại: "những con số KHÁC trong câu trả lời từ đâu ra?"

HAI LOẠI LỖI, HAI CÁCH BẮT

  không có nguồn   độ lớn của con số không khớp BẤT KỲ giá trị nào công cụ trả về
  sai dấu          độ lớn khớp, nhưng câu trả lời nói LÃI trong khi nguồn ghi LỖ
                   (hoặc ngược lại)

Loại thứ hai từng là điểm mù có chủ ý. Bộ đọc chỉ bắt chữ số nên dấu trừ rơi mất, và so
có dấu thì báo nhầm ngay một câu trả lời hoàn toàn đúng ("Intel lỗ -18.756.000.000").
Bản đầu chọn so theo độ lớn cho an toàn, chấp nhận mù dấu. Bản này đọc dấu từ CHỮ quanh
con số — dấu trừ đứng sát, hoặc các từ "lỗ", "âm", "loss" đứng trước trong cùng câu —
nên bắt được sai dấu mà không quay lại báo nhầm.

NGUYÊN TẮC: RỘNG TAY KHI CHẤP NHẬN, CHẶT TAY KHI BÁO ĐỘNG

Một cảnh báo sai làm người dùng mất tin vào cả những cảnh báo đúng. Nên:

  · mỗi cụm số được thử MỌI cách hiểu hợp lý (dấu chấm là ngăn cách hay thập phân, có
    kèm "tỷ"/"nghìn tỷ" hay không), và chỉ báo khi KHÔNG cách hiểu nào khớp nguồn;
  · câu hỏi cũng tính là nguồn — "doanh thu trên 200 tỷ USD" thì con số 200 trong câu
    trả lời là nhắc lại điều kiện;
  · số lấy từ VĂN XUÔI (đoạn 10-K, câu hỏi) được coi là không rõ dấu, nên không bao giờ
    sinh cảnh báo sai dấu. Chỉ số từ dữ liệu có cấu trúc — nơi dấu là sự thật — mới được
    đem ra so dấu.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

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
# nào đáng kể. Mọi lỗi thật đo được đều ở bậc tỷ.
MIN_MAGNITUDE = 1e6

# Cùng dung sai với bộ đánh giá, để hai bên nói cùng một ngôn ngữ.
TOLERANCE = 0.01

# Dấu của một con số lấy từ nguồn: +1 dương, -1 âm, 0 KHÔNG RÕ (văn xuôi, hoặc bằng 0).
Signed = Tuple[float, int]

# Từ cho biết con số đứng sau là âm / dương. Chỉ xét trong cùng một câu, và từ nào đứng
# GẦN con số hơn thì thắng — để "chuyển từ lỗ sang lãi 5,2 tỷ" được hiểu là lãi.
_NEGATIVE_WORDS = re.compile(r"\b(lỗ|âm|tổn thất|loss|losses|negative|deficit)\b")
_POSITIVE_WORDS = re.compile(r"\b(lãi|dương|profit|gain)\b")
# "18,76 tỷ USD (lỗ)" — dấu đặt SAU con số, trong ngoặc.
_NEGATIVE_AFTER = re.compile(r"^[^()\n]{0,16}\(\s*(lỗ|âm|loss)\b")
_SENTENCE_BREAKS = ("\n", ". ", ";", "|")


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


def _scan(text: str) -> Iterator[Tuple[float, str, int, int]]:
    """(giá trị, cụm chữ số gốc, vị trí đầu, vị trí cuối) — mỗi cách hiểu một mục."""
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
            yield value * multiplier, token, match.start(), match.end()


def numbers_in(text: str) -> List[Tuple[float, str]]:
    """[(giá trị, cụm chữ số gốc)] — mỗi cách hiểu là một mục riêng."""
    return [(value, token) for value, token, _s, _e in _scan(text)]


def _is_negative(text: str, start: int, end: int) -> bool:
    """Câu trả lời có đang nói con số ở [start:end] là số ÂM không.

    Ba dấu hiệu, xét theo thứ tự:
      1. dấu trừ đứng sát ngay trước ("-18756000000")
      2. "(lỗ)" / "(âm)" ngay sau ("18,76 tỷ USD (lỗ)")
      3. từ chỉ lỗ/lãi đứng trước trong cùng câu — từ nào GẦN con số hơn thì thắng

    Dấu trừ chỉ được tính khi ký tự trước nó KHÔNG phải chữ số: "150-200 tỷ" là khoảng
    giá trị, không phải số âm.
    """
    before = text[max(0, start - 40): start]
    tail = before.rstrip()
    if (
        tail.endswith(("-", "−", "–"))
        and len(before) - len(tail) <= 1
        and not tail[:-1].rstrip()[-1:].isdigit()
    ):
        return True

    if _NEGATIVE_AFTER.search(text[end: end + 30].lower()):
        return True

    for brk in _SENTENCE_BREAKS:
        cut = before.rfind(brk)
        if cut >= 0:
            before = before[cut + len(brk):]
    low = before.lower()
    neg = [m.end() for m in _NEGATIVE_WORDS.finditer(low)]
    pos = [m.end() for m in _POSITIVE_WORDS.finditer(low)]
    return bool(neg) and max(neg) > (max(pos) if pos else -1)


def source_values(obj: Any, acc: Optional[List[Signed]] = None) -> List[Signed]:
    """Mọi con số xuất hiện trong dữ liệu công cụ trả về, kèm dấu nếu biết chắc.

    Số trong dữ liệu có cấu trúc (JSON từ Neo4j) mang dấu thật: -18756000000 là lỗ.
    Số đọc ra từ CHUỖI — đoạn văn 10-K, câu hỏi — được coi là không rõ dấu, vì văn xuôi
    viết "a loss of $18.8 billion" với con số dương. Đem chúng ra so dấu là tự tạo báo
    động giả.

    Chuỗi cũng được đọc bằng chính bộ đọc của câu trả lời, KỂ CẢ từ chỉ bậc độ lớn: đoạn
    văn 10-K viết "$17.7 billion", còn câu trả lời viết "17,7 tỷ USD". Nếu bên nguồn chỉ
    lấy ra 17.7 thì hai bên không bao giờ khớp và sinh báo động giả.
    """
    if acc is None:
        acc = []
    if isinstance(obj, bool):
        return acc
    if isinstance(obj, (int, float)):
        acc.append((abs(float(obj)), (obj > 0) - (obj < 0)))
    elif isinstance(obj, str):
        for value, _token in numbers_in(obj):
            acc.append((abs(value), 0))
    elif isinstance(obj, dict):
        for value in obj.values():
            source_values(value, acc)
    elif isinstance(obj, (list, tuple, set)):
        for value in obj:
            source_values(value, acc)
    return acc


def check_answer(answer: str, observations: Any, question: str = "") -> Dict[str, Any]:
    """Đối chiếu câu trả lời với nguồn.

    Trả về {"ok", "checked", "unverified": [{"text", "readings", "reason"}]}, trong đó
    `reason` là "not_in_source" hoặc "sign_mismatch".
    """
    allowed = source_values(observations)
    source_values(question, allowed)

    def signs_of(magnitude: float) -> Optional[Set[int]]:
        """Dấu của mọi giá trị nguồn khớp độ lớn này. None nghĩa là không khớp gì."""
        found = {
            sign for src, sign in allowed
            if abs(magnitude - src) <= TOLERANCE * max(src, 1.0)
        }
        return found or None

    # Gom theo TỪNG LẦN XUẤT HIỆN chứ không theo chuỗi: cùng một con số có thể xuất hiện
    # hai chỗ, một chỗ nói lãi một chỗ nói lỗ.
    occurrences: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for value, token, start, end in _scan(answer):
        if abs(value) < MIN_MAGNITUDE:
            continue
        occ = occurrences.setdefault(
            (token, start), {"start": start, "end": end, "readings": set(), "signs": None}
        )
        occ["readings"].add(value)
        signs = signs_of(abs(value))
        if signs is not None:
            occ["signs"] = (occ["signs"] or set()) | signs

    problems: Dict[str, Dict[str, Any]] = {}
    for (token, _start), occ in occurrences.items():
        if occ["signs"] is None:
            reason = "not_in_source"
        elif 0 in occ["signs"]:
            continue  # có nguồn không rõ dấu khớp -> không đủ căn cứ để nói sai dấu
        else:
            said = -1 if _is_negative(answer, occ["start"], occ["end"]) else 1
            if said in occ["signs"]:
                continue
            reason = "sign_mismatch"
        problems.setdefault(token, {"text": token, "readings": set(), "reason": reason})
        problems[token]["readings"] |= occ["readings"]

    return {
        "ok": not problems,
        "checked": len({token for token, _start in occurrences}),
        "unverified": [
            {**item, "readings": sorted(item["readings"])} for item in problems.values()
        ],
    }


def retry_instruction(result: Dict[str, Any]) -> str:
    """Lời nhắc gửi lại cho mô hình khi lần viết đầu có số không đối chiếu được."""
    missing = [i["text"] for i in result["unverified"] if i["reason"] == "not_in_source"]
    flipped = [i["text"] for i in result["unverified"] if i["reason"] == "sign_mismatch"]
    lines = []
    if missing:
        lines.append("Các số sau KHÔNG có trong dữ liệu công cụ trả về: "
                     + ", ".join(missing[:8]) + ".")
    if flipped:
        lines.append("Các số sau bị SAI DẤU so với dữ liệu (lãi viết thành lỗ hoặc ngược "
                     "lại — số âm trong dữ liệu nghĩa là lỗ): " + ", ".join(flipped[:8]) + ".")
    lines.append(
        "Viết lại câu trả lời và CHỈ dùng những con số có thật trong dữ liệu ở trên, đúng "
        "cả dấu. Không thêm doanh nghiệp nào ngoài danh sách công cụ đã trả về. Không suy "
        "ra, không nhớ lại, không làm tròn sang bậc độ lớn khác."
    )
    return "\n".join(lines)


def warning_block(result: Dict[str, Any]) -> str:
    """Đoạn cảnh báo gắn vào cuối câu trả lời khi có số không đối chiếu được.

    Cố ý KHÔNG tự sửa hay xóa con số. Không biết phải thay bằng giá trị nào — mô hình có
    thể đã chép sai, có thể đã bịa hẳn một dòng mới, hai ca cần cách xử lý khác nhau.
    Nói thật với người đọc là việc luôn đúng; đoán hộ thì không.
    """
    def listing(reason: str) -> str:
        texts = [f"`{i['text']}`" for i in result["unverified"] if i["reason"] == reason]
        more = len(texts) - 8
        return ", ".join(texts[:8]) + (f" và {more} số nữa" if more > 0 else "")

    parts = []
    missing = listing("not_in_source")
    flipped = listing("sign_mismatch")
    if missing:
        parts.append(f"Không có trong dữ liệu mà công cụ tra được: {missing}")
    if flipped:
        parts.append(f"Sai dấu so với dữ liệu gốc (lãi/lỗ bị đảo): {flipped}")

    return (
        "\n\n---\n"
        f"⚠️ **Cảnh báo tự động — {len(result['unverified'])} con số chưa đối chiếu được.**\n\n"
        + "\n\n".join(parts)
        + "\n\nChúng có thể do mô hình chép sai hoặc tự thêm vào. **Đừng dùng chúng.** "
        "Những con số còn lại đã được đối chiếu khớp với nguồn."
    )

"""Chọn lọc chunk trước khi đưa vào LLM trích xuất.

VÌ SAO CẦN BƯỚC NÀY — CÓ SỐ ĐO CỤ THỂ

Chạy dàn trải toàn bộ 4.748 chunk mất khoảng 6 giờ GPU. Đo trên 336 bộ ba đầu tiên:

    Cạnh Company -> Company (thứ tạo suy luận bắc cầu):   3,6%
    Cạnh Company -> RiskFactor (hình sao, không tạo đường): 54,2%

Và trong 182 cạnh rủi ro có tới 135 tên rủi ro KHÁC NHAU, chỉ 17% được nhiều hơn một
doanh nghiệp nhắc tới. Nghĩa là phần lớn là lá đơn độc: tốn GPU nhưng không tạo ra một
đường đi nào giữa hai doanh nghiệp.

Nhưng khi thử 12 chunk CHỌN LỌC (bán dẫn, Item 1A/7, có từ khóa chuỗi cung ứng):

    Cạnh Company -> Company:  30%   (gấp 8 lần)
    SUPPLIED_BY:              8     (so với 1 trong toàn bộ 336 bộ ba dàn trải)

Kết luận: phương pháp trích xuất không có vấn đề gì. Vấn đề là TÍN HIỆU TẬP TRUNG Ở MỘT
PHẦN NHỎ CHUNK, còn phần lớn thời gian tiêu vào những đoạn không có gì để trích.

CÁCH CHỌN

Một quan hệ giữa hai doanh nghiệp chỉ tồn tại khi đoạn văn NHẮC TÊN một tổ chức khác
ngoài chính doanh nghiệp đang nộp báo cáo. Đó là điều kiện cần, và kiểm tra được bằng
so khớp chuỗi thuần túy — không tốn một lần gọi LLM nào.

Chunk được xếp hạng thay vì lọc cứng, để nếu còn thời gian thì cứ chạy tiếp xuống dưới.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from src.graph.schema import CANONICAL_COMPANIES
from src.ingest.chunker import Chunk

# Động từ báo hiệu có quan hệ giữa các thực thể. Không đủ để chọn chunk một mình,
# nhưng cộng điểm khi đi kèm tên riêng.
RELATION_CUES = re.compile(
    r"\b(compet\w*|rival|supplier|supplied|suppl\w+|foundr\w+|manufactur\w+ (?:for|by)|"
    r"customer|purchas\w+ from|rely on|relies on|reliant|depend\w* on|sole source|"
    r"single source|partner\w*|collaborat\w+|joint venture|acquir\w+|acquisition|"
    r"subsidiar\w+|merger|licens\w+ from|outsourc\w+)\b",
    re.IGNORECASE,
)

# Từ khóa cấu trúc kinh doanh — cho quan hệ mảng/sản phẩm, giá trị thấp hơn quan hệ
# giữa doanh nghiệp nhưng vẫn đáng trích.
STRUCTURE_CUES = re.compile(
    r"\b(reportable segment|operating segment|our segments|business segment|"
    r"product line|we (?:offer|sell|design|develop))\b",
    re.IGNORECASE,
)

# Các tổ chức hay xuất hiện trong hệ sinh thái công nghệ nhưng không phải doanh nghiệp
# niêm yết trong danh sách của ta — vẫn là mắt xích thật trong chuỗi cung ứng.
EXTRA_ORGS = {
    "globalfoundries", "openai", "anthropic", "bytedance", "huawei", "smic",
    "foxconn", "hon hai", "pegatron", "wistron", "quanta", "flex", "jabil",
    "samsung", "sk hynix", "kioxia", "tsmc", "umc", "asml", "tokyo electron",
    "european union", "department of commerce", "bureau of industry and security",
    "federal trade commission", "department of justice",
}


def _build_org_index() -> Set[str]:
    """Tập tên tổ chức để dò trong văn bản, đã hạ về chữ thường."""
    names: Set[str] = set(EXTRA_ORGS)
    for key, canonical in CANONICAL_COMPANIES.items():
        if len(key) >= 3:
            names.add(key)
        # Lấy phần định danh của tên chuẩn, bỏ hậu tố pháp lý
        head = re.split(r"[,.]", canonical)[0].strip().lower()
        if len(head) >= 3:
            names.add(head)
    return names


_ORG_INDEX = _build_org_index()


def _own_names(chunk: Chunk) -> Set[str]:
    """Các cách gọi chính doanh nghiệp đang nộp báo cáo — không tính là 'tổ chức khác'."""
    own = {chunk.ticker.lower()}
    head = re.split(r"[,.]", chunk.company)[0].strip().lower()
    if head:
        own.add(head)
        own.update(w for w in head.split() if len(w) > 3)
    return own


def score_chunk(chunk: Chunk) -> Tuple[int, Dict[str, int]]:
    """Chấm điểm một chunk theo khả năng chứa quan hệ đáng trích.

    Điểm càng cao càng đáng đưa vào LLM. Thành phần điểm được trả về kèm để gỡ lỗi và
    để giải thích được vì sao một chunk bị bỏ qua.
    """
    text = chunk.text
    low = text.lower()
    own = _own_names(chunk)

    # Đếm tổ chức KHÁC được nhắc tên. Đây là tín hiệu mạnh nhất: không có tổ chức nào
    # khác thì không thể có cạnh Company -> Company.
    external = {
        name for name in _ORG_INDEX
        if name not in own and re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", low)
    }

    cues = len(RELATION_CUES.findall(text))
    structure = len(STRUCTURE_CUES.findall(text))

    score = len(external) * 5 + min(cues, 6) * 2 + min(structure, 3) * 2

    # Item 1A và Item 7 là nơi tập trung quan hệ cạnh tranh / chuỗi cung ứng / hợp tác
    if chunk.item in ("1A", "7"):
        score += 2

    return score, {
        "external_orgs": len(external),
        "relation_cues": cues,
        "structure_cues": structure,
    }


def select_chunks(
    chunks: List[Chunk],
    min_score: int = 7,
    limit: Optional[int] = None,
    structure_budget: int = 300,
) -> List[Chunk]:
    """Lọc và sắp xếp chunk theo điểm giảm dần.

    ⚠️ ĐIỀU KIỆN CỨNG, KHÔNG PHẢI ĐIỂM SỐ: chunk phải NHẮC TÊN ÍT NHẤT MỘT TỔ CHỨC KHÁC.

    Bản đầu tiên của hàm này chỉ cộng điểm cho tên tổ chức rồi so với ngưỡng. Hậu quả:
    một đoạn nói lan man về "competition is intense" và "we rely on suppliers" mà không
    nêu tên ai vẫn đạt 20 điểm nhờ từ khóa, và lọt qua ngưỡng 12. Nhưng đoạn như vậy
    KHÔNG THỂ sinh ra cạnh Company -> Company, vì không có công ty thứ hai để nối.

    Không có tên riêng thì không có cạnh. Đó là điều kiện cần, nên phải là cổng chặn
    chứ không phải một thành phần cộng điểm.

    Ngoại lệ có kiểm soát: các đoạn mô tả mảng kinh doanh và sản phẩm (OPERATES_SEGMENT,
    OFFERS_PRODUCT) không cần nêu tên tổ chức khác nhưng vẫn có giá trị. Chúng được cấp
    một hạn mức riêng `structure_budget` để không chiếm hết thời gian chạy.

    Sắp xếp theo điểm giảm dần có lợi ích quan trọng: nếu phải dừng giữa chừng, những
    chunk giá trị nhất đã xong. Chạy theo thứ tự chữ cái thì NVIDIA và TSMC nằm gần cuối
    — đúng những công ty cần nhất cho câu hỏi bắc cầu lại phải chờ lâu nhất.
    """
    with_orgs, structural = [], []

    for c in chunks:
        score, detail = score_chunk(c)
        if score < min_score:
            continue
        if detail["external_orgs"] >= 1:
            with_orgs.append((score, c))
        elif detail["structure_cues"] >= 1:
            structural.append((score, c))

    with_orgs.sort(key=lambda pair: -pair[0])
    structural.sort(key=lambda pair: -pair[0])

    selected = [c for _, c in with_orgs] + [c for _, c in structural[:structure_budget]]
    return selected[:limit] if limit else selected


def selection_report(chunks: List[Chunk], min_score: int = 7) -> Dict[str, object]:
    """Thống kê để biết bộ lọc đã cắt bao nhiêu và giữ lại những gì."""
    from collections import Counter

    kept_items: Counter = Counter()
    kept_tickers: Counter = Counter()
    kept = 0

    for c in chunks:
        score, _ = score_chunk(c)
        if score >= min_score:
            kept += 1
            kept_items[c.item] += 1
            kept_tickers[c.ticker] += 1

    return {
        "total": len(chunks),
        "kept": kept,
        "ratio": kept / max(len(chunks), 1),
        "by_item": dict(kept_items.most_common()),
        "top_tickers": dict(kept_tickers.most_common(12)),
    }

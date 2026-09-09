"""Ontology của đồ thị tri thức + chuẩn hóa thực thể.

QUYẾT ĐỊNH THIẾT KẾ QUAN TRỌNG NHẤT CỦA CẢ DỰ ÁN: DÙNG BỘ QUAN HỆ ĐÓNG

Cách làm mặc định trong hầu hết hướng dẫn GraphRAG là bảo LLM "hãy trích xuất các
thực thể và quan hệ". Làm vậy trên 544 chunk sẽ sinh ra một đồ thị có hàng trăm loại
quan hệ khác nhau, tất cả đều diễn đạt cùng một ý:

    COMPETES_WITH, COMPETITOR_OF, COMPETES, IS_COMPETITOR, RIVAL_OF, FACES_COMPETITION...

Hậu quả không phải là đồ thị "hơi lộn xộn" — mà là đồ thị KHÔNG TRUY VẤN ĐƯỢC. Câu
Cypher tìm đối thủ của NVIDIA sẽ bỏ sót 80% cạnh vì chúng mang tên khác. Đồ thị nhìn
thì đẹp, nhưng vô dụng.

Cách xử lý ở đây: cố định trước một bộ nhãn thực thể và một bộ loại quan hệ. LLM chỉ
được CHỌN trong danh sách này, và JSON schema ép nó phải chọn (dùng enum). Bất cứ thứ
gì nằm ngoài danh sách đều bị loại ở bước kiểm tra. Đồ thị nhỏ hơn nhưng dùng được.

CHUẨN HÓA THỰC THỂ CŨNG QUAN TRỌNG NGANG NGỬA

Trong bốn bản 10-K, NVIDIA tự xưng là "NVIDIA Corporation", đối thủ gọi họ là "Nvidia",
văn bản khác viết "NVIDIA Corp." Nếu không gộp lại, đồ thị sẽ có ba node rời rạc và
mọi câu hỏi bắc cầu đều đứt. Đây là bài toán entity resolution — phần khó bị đánh giá
thấp nhất khi xây knowledge graph.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

# ---------------------------------------------------------------- nhãn thực thể

ENTITY_TYPES: Dict[str, str] = {
    "Company": "Doanh nghiệp, tổ chức kinh doanh (đối thủ, nhà cung cấp, khách hàng, công ty con)",
    "Segment": "Mảng kinh doanh doanh nghiệp báo cáo riêng (Data Center, Intelligent Cloud, iPhone)",
    "Product": "Sản phẩm hoặc dịch vụ cụ thể (H100, Azure OpenAI Service, iPhone 16)",
    "Technology": "Công nghệ hoặc nền tảng kỹ thuật (CUDA, generative AI, 5G, silicon photonics)",
    "RiskFactor": "Một loại rủi ro doanh nghiệp nêu ra (gián đoạn chuỗi cung ứng, kiểm soát xuất khẩu)",
    "Person": "Cá nhân được nêu tên (CEO, thành viên HĐQT)",
    "Regulator": "Cơ quan quản lý hoặc khung pháp lý (SEC, FTC, EU AI Act, BIS)",
    "Geography": "Quốc gia hoặc khu vực địa lý (Trung Quốc, Đài Loan, Liên minh châu Âu)",
}

# ---------------------------------------------------------------- loại quan hệ

# (tên quan hệ, nhãn nguồn hợp lệ, nhãn đích hợp lệ, mô tả cho LLM)
RELATION_TYPES: List[tuple] = [
    ("COMPETES_WITH", "Company", "Company", "Nguồn cạnh tranh trực tiếp với đích"),
    ("SUPPLIED_BY", "Company", "Company", "Nguồn phụ thuộc vào đích để có hàng hóa/dịch vụ đầu vào"),
    ("CUSTOMER_OF", "Company", "Company", "Nguồn bán hàng cho đích; đích là khách hàng"),
    ("PARTNERS_WITH", "Company", "Company", "Hợp tác chiến lược, liên doanh, đầu tư vào nhau"),
    ("SUBSIDIARY_OF", "Company", "Company", "Nguồn là công ty con / bị đích sở hữu"),
    ("ACQUIRED", "Company", "Company", "Nguồn đã mua lại đích"),
    ("OPERATES_SEGMENT", "Company", "Segment", "Nguồn báo cáo đích như một mảng kinh doanh"),
    ("OFFERS_PRODUCT", "Company", "Product", "Nguồn bán hoặc phát triển sản phẩm đích"),
    ("BELONGS_TO_SEGMENT", "Product", "Segment", "Sản phẩm nguồn được xếp vào mảng đích"),
    ("USES_TECHNOLOGY", "Company", "Technology", "Nguồn xây dựng trên hoặc phụ thuộc vào công nghệ đích"),
    ("EXPOSED_TO_RISK", "Company", "RiskFactor", "Nguồn nêu đích là một rủi ro của mình"),
    ("REGULATED_BY", "Company", "Regulator", "Nguồn chịu sự quản lý của đích"),
    ("OPERATES_IN", "Company", "Geography", "Nguồn có hoạt động kinh doanh đáng kể tại đích"),
    ("LED_BY", "Company", "Person", "Đích giữ vị trí lãnh đạo tại nguồn"),
]

RELATION_NAMES: List[str] = [r[0] for r in RELATION_TYPES]

# Quan hệ lấy từ nguồn CÓ CẤU TRÚC — không phải LLM trích ra.
#
# ⚠️ VÌ SAO KHÔNG BỎ THẲNG VÀO `RELATION_TYPES`.
#
# `RELATION_TYPES` là bộ enum ép vào JSON schema của LLM — mọi thứ nằm trong đó là thứ mô
# hình ĐƯỢC PHÉP TỰ CHẾ ra khi đọc văn bản. Quan hệ sở hữu thì không được vậy: "ai nắm bao
# nhiêu phần trăm" là số liệu công bố, không phải điều suy ra từ câu chữ. Cho mô hình quyền
# sinh ra `OWNED_BY` là mở đường để nó đọc câu "NVIDIA hợp tác với ARM" rồi kết luận NVIDIA
# sở hữu ARM.
#
# Nên chúng nằm riêng: agent TRUY VẤN được, nhưng LLM không SINH được.
STRUCTURED_RELATIONS: List[str] = ["OWNED_BY"]

# Bộ quan hệ agent được phép lọc khi duyệt đồ thị — gồm cả hai nguồn.
QUERYABLE_RELATIONS: List[str] = RELATION_NAMES + STRUCTURED_RELATIONS

# Cạnh HẠ TẦNG — nối doanh nghiệp với bản ghi năm tài chính và với hồ sơ đã nộp. Chúng
# không phải tri thức trích xuất được, và phải bị loại khỏi mọi truy vấn duyệt đồ thị.
#
# Vì sao đủ quan trọng để đặt thành hằng số: HAS_FINANCIALS có 48.025 cạnh, nhiều gấp
# 25 lần toàn bộ tri thức thật cộng lại. Quên loại chúng ra một lần là kết quả bị chúng
# nhấn chìm hoàn toàn — đã xảy ra thật, xem chú thích trong GraphStore.neighbors.
INFRA_RELATIONS: List[str] = ["HAS_FINANCIALS", "FILED"]
RELATION_SPEC: Dict[str, tuple] = {r[0]: (r[1], r[2], r[3]) for r in RELATION_TYPES}

# Ánh xạ các cách diễn đạt LLM hay tự chế về đúng nhãn trong bộ đóng.
# Danh sách này được bổ sung dần khi quan sát log trích xuất thực tế.
RELATION_ALIASES: Dict[str, str] = {
    "COMPETITOR_OF": "COMPETES_WITH",
    "COMPETES": "COMPETES_WITH",
    "RIVAL_OF": "COMPETES_WITH",
    "SUPPLIER_OF": "SUPPLIED_BY",
    "DEPENDS_ON": "SUPPLIED_BY",
    "RELIES_ON": "SUPPLIED_BY",
    "SELLS_TO": "CUSTOMER_OF",
    "PARTNER_OF": "PARTNERS_WITH",
    "COLLABORATES_WITH": "PARTNERS_WITH",
    "OWNS": "ACQUIRED",
    "PARENT_OF": "ACQUIRED",
    "HAS_SEGMENT": "OPERATES_SEGMENT",
    "PRODUCES": "OFFERS_PRODUCT",
    "SELLS": "OFFERS_PRODUCT",
    "FACES_RISK": "EXPOSED_TO_RISK",
    "SUBJECT_TO": "REGULATED_BY",
    "CEO_OF": "LED_BY",
}

# ---------------------------------------------------------------- chuẩn hóa tên

# Bốn doanh nghiệp mục tiêu: gắn cứng để chắc chắn không bao giờ bị tách node.
CANONICAL_COMPANIES: Dict[str, str] = {
    "apple": "Apple Inc.",
    "apple inc": "Apple Inc.",
    "microsoft": "Microsoft Corporation",
    "microsoft corp": "Microsoft Corporation",
    "msft": "Microsoft Corporation",
    "nvidia": "NVIDIA Corporation",
    "nvidia corp": "NVIDIA Corporation",
    "alphabet": "Alphabet Inc.",
    "google": "Alphabet Inc.",
    "google llc": "Alphabet Inc.",
    # Các bên xuất hiện thường xuyên trong hệ sinh thái, hay bị viết nhiều kiểu
    "tsmc": "Taiwan Semiconductor Manufacturing Company",
    "taiwan semiconductor": "Taiwan Semiconductor Manufacturing Company",
    "taiwan semiconductor manufacturing": "Taiwan Semiconductor Manufacturing Company",
    "amd": "Advanced Micro Devices",
    "advanced micro devices inc": "Advanced Micro Devices",
    "intel": "Intel Corporation",
    "intel corp": "Intel Corporation",
    "amazon": "Amazon.com, Inc.",
    "aws": "Amazon Web Services",
    "amazon web services": "Amazon Web Services",
    "meta": "Meta Platforms, Inc.",
    "facebook": "Meta Platforms, Inc.",
    "openai": "OpenAI",
    "samsung": "Samsung Electronics",
    "qualcomm": "Qualcomm Incorporated",
    "broadcom": "Broadcom Inc.",
    "arm": "Arm Holdings",
}

TICKER_TO_CANONICAL: Dict[str, str] = {
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corporation",
    "NVDA": "NVIDIA Corporation",
    "GOOGL": "Alphabet Inc.",
}

# Hậu tố pháp lý cần cắt bỏ trước khi so khớp tên
_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|incorporated|corp|corporation|llc|ltd|limited|plc|co|company|holdings|group|"
    r"n\.?v|s\.?a|ag|gmbh|lp|llp)\b\.?",
    re.IGNORECASE,
)


def normalize_name(name: str, entity_type: str = "Company") -> str:
    """Đưa tên thực thể về dạng chuẩn để gộp các biến thể viết khác nhau."""
    cleaned = re.sub(r"\s+", " ", (name or "").strip())
    cleaned = cleaned.strip("\"'“”.,;:()[]")
    if not cleaned:
        return ""

    if entity_type == "Company":
        key = _LEGAL_SUFFIXES.sub("", cleaned).strip(" ,.&-").lower()
        key = re.sub(r"\s+", " ", key)
        if key in CANONICAL_COMPANIES:
            return CANONICAL_COMPANIES[key]
        if cleaned.lower() in CANONICAL_COMPANIES:
            return CANONICAL_COMPANIES[cleaned.lower()]

    # Không thuộc danh sách chuẩn: giữ nguyên chữ hoa/thường của văn bản gốc,
    # chỉ chuẩn hóa khoảng trắng. Viết hoa lại toàn bộ sẽ làm hỏng các tên như "iPhone".
    return cleaned


def normalize_relation(relation: str) -> Optional[str]:
    """Đưa loại quan hệ về bộ đóng. Trả về None nếu không ánh xạ được -> cạnh bị loại."""
    key = re.sub(r"[^A-Z_]", "_", (relation or "").upper().replace(" ", "_")).strip("_")
    key = re.sub(r"_+", "_", key)
    if key in RELATION_SPEC:
        return key
    return RELATION_ALIASES.get(key)


def is_valid_triple(source_type: str, relation: str, target_type: str) -> bool:
    """Kiểm tra bộ ba có đúng ràng buộc nhãn nguồn/đích của ontology hay không.

    Bước này bắt được những lỗi kiểu LLM sinh ra
    (Product)-[:COMPETES_WITH]->(Company), vốn phá vỡ giả định của mọi câu Cypher
    viết sau này.
    """
    spec = RELATION_SPEC.get(relation)
    if not spec:
        return False
    src_ok, tgt_ok, _ = spec
    return source_type == src_ok and target_type == tgt_ok

"""Bóc tách văn bản 10-K từ HTML và chia theo mục (Item).

Vì sao phải chia theo Item thay vì cắt đều mỗi 1000 ký tự?

Một bản 10-K có cấu trúc pháp lý cố định do SEC quy định:
    Item 1   - Business          : mô tả mô hình kinh doanh, sản phẩm, công ty con
    Item 1A  - Risk Factors      : các rủi ro, ở đây có cả tên ĐỐI THỦ và NHÀ CUNG CẤP
    Item 3   - Legal Proceedings : kiện tụng
    Item 7   - MD&A              : ban lãnh đạo tự phân tích kết quả kinh doanh
    Item 7A  - Market Risk       : rủi ro thị trường
    Item 8   - Financial Statements

Biết chunk thuộc Item nào cho phép làm hai việc mà RAG thông thường không làm được:
  1. Lọc theo mục khi truy vấn ("rủi ro của NVIDIA" -> chỉ tìm trong Item 1A).
  2. Dùng prompt trích xuất khác nhau cho từng mục (Item 1A tập trung tìm quan hệ
     cạnh tranh/phụ thuộc, Item 7 tập trung tìm quan hệ tăng trưởng/mảng kinh doanh).
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Tên đầy đủ của từng Item trong 10-K. Dùng để nhận diện tiêu đề mục trong văn bản.
ITEM_TITLES: Dict[str, str] = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant",
    "6": "Selected Financial Data",
    "7": "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements with Accountants",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership",
    "13": "Certain Relationships and Related Transactions",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits, Financial Statement Schedules",
}

# Những mục chứa nội dung phân tích có giá trị. Các mục còn lại (thù lao ban lãnh đạo,
# quyền sở hữu chứng khoán...) bị bỏ qua để không làm loãng vector store.
CONTENT_ITEMS = ["0", "1", "1A", "1C", "3", "5", "7", "7A", "8", "15"]

# Item 15 là trường hợp đặc biệt.
# Với đa số doanh nghiệp, mục này chỉ là danh sách phụ lục -> rác thuần túy.
# Nhưng NVIDIA (và một số công ty khác) dùng câu "incorporated by reference" ở Item 8
# để đẩy TOÀN BỘ báo cáo tài chính hợp nhất xuống Item 15. Nếu loại bỏ Item 15 một cách
# máy móc, ta sẽ mất trắng 113k ký tự báo cáo tài chính của NVIDIA.
# Vì vậy: chỉ giữ Item 15 khi nó thật sự chứa báo cáo tài chính.
_FINANCIALS_MARKER = re.compile(
    r"consolidated (balance sheet|statements? of (income|operations))", re.IGNORECASE
)


@dataclass
class Section:
    item: str  # "1A"
    title: str  # "Risk Factors"
    text: str
    char_start: int
    char_end: int

    @property
    def length(self) -> int:
        return len(self.text)


def html_to_text(html_path: Path) -> str:
    """Chuyển HTML của bản khai thành văn bản thuần, giữ lại cấu trúc bảng."""
    soup = BeautifulSoup(html_path.read_bytes(), "lxml")

    # Bỏ phần không phải nội dung
    for tag in soup(["script", "style", "head"]):
        tag.decompose()

    # Bảng biểu chiếm phần lớn 10-K và chứa số liệu quan trọng.
    # Chuyển mỗi hàng thành một dòng ngăn cách bằng " | " để LLM đọc được.
    for table in soup.find_all("table"):
        rows: List[str] = []
        for tr in table.find_all("tr"):
            cells = [
                re.sub(r"\s+", " ", td.get_text(" ", strip=True))
                for td in tr.find_all(["td", "th"])
            ]
            cells = [c for c in cells if c and c not in {"$", "%", ")", "("}]
            if cells:
                rows.append(" | ".join(cells))
        table.replace_with("\n" + "\n".join(rows) + "\n")

    text = soup.get_text("\n")

    # Chuẩn hóa khoảng trắng: thay ký tự space đặc biệt của HTML, gộp dòng trống
    text = text.replace("\xa0", " ").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _item_pattern(item: str) -> re.Pattern:
    """Tạo regex nhận diện tiêu đề của một Item.

    Ba chi tiết dễ sai, đều đã gặp thật khi chạy trên hồ sơ của Apple/NVIDIA:

    1. Phải có negative lookahead (?![0-9A-Za-z]) sau số hiệu. Nếu không, mẫu tìm
       "Item 1" sẽ khớp luôn vào "Item 1A", khiến mục Business bị cắt còn 0 ký tự.

    2. Đầu dòng phải cho phép ký tự "|" đứng trước. Nhiều hồ sơ (NVIDIA) đặt tiêu đề
       mục bên trong ô bảng; sau khi ta chuyển bảng thành "ô | ô | ô" thì tiêu đề
       không còn nằm ở đầu dòng nữa.

    3. Không dùng  thay cho lookahead, vì "Item 1." và "Item 1A" đều thỏa .
    """
    return re.compile(
        rf"^[\s|]*item\s*{re.escape(item)}(?![0-9A-Za-z])\s*[\.\:\-–—]?\s*",
        re.IGNORECASE | re.MULTILINE,
    )


def _title_confirmed(text: str, pos: int, item: str) -> bool:
    """Kiểm tra ngay sau vị trí khớp có xuất hiện tên mục hay không.

    Dùng để loại các tham chiếu chéo trong câu văn ("see Item 1A") khỏi danh sách
    ứng viên. Lưu ý: cách này KHÔNG phân biệt được mục lục với thân bài — cả hai đều
    ghi đủ tên mục. Việc phân biệt đó do heuristic độ dài khối ở split_items đảm nhiệm.
    """
    # So khớp sau khi bỏ hết ký tự không phải chữ/số. Bắt buộc phải làm vậy:
    # trong hồ sơ của Microsoft, tiêu đề bị ngắt dòng ngay GIỮA MỘT TỪ (dòng đầu kết
    # thúc bằng "RIS", dòng sau bắt đầu bằng "K FACTORS"), nên so khớp chuỗi thô sẽ
    # trượt và toàn bộ mục Risk Factors dài 61k ký tự bị mất.
    window = re.sub(r"[^a-z0-9]", "", text[pos : pos + 260].lower())
    title_head = re.sub(r"[^a-z0-9]", "", ITEM_TITLES[item].lower())[:16]
    return title_head in window


def split_items(text: str) -> Dict[str, Section]:
    """Chia văn bản thành các Section theo Item.

    Thách thức thực tế: chuỗi "Item 1A" xuất hiện ít nhất hai lần trong mọi bản khai —
    một lần ở MỤC LỤC đầu tài liệu, một lần ở thân bài. Cách xử lý: với mỗi Item, thu
    thập tất cả vị trí khớp rồi chọn vị trí nào mở ra khối văn bản DÀI NHẤT trước khi
    gặp Item kế tiếp. Mục lục luôn thua vì các dòng ở đó nằm sát nhau.
    """
    ordered = list(ITEM_TITLES.keys())
    candidates: Dict[str, List[int]] = {}

    for item in ordered:
        positions = [m.start() for m in _item_pattern(item).finditer(text)]
        # Nếu có ít nhất một vị trí kèm đúng tên mục thì chỉ giữ nhóm đó,
        # loại bỏ các tham chiếu chéo giữa câu văn.
        confirmed = [p for p in positions if _title_confirmed(text, p, item)]
        positions = confirmed or positions
        if positions:
            candidates[item] = positions

    # Chọn vị trí tốt nhất cho từng Item bằng cách thử: với mỗi ứng viên, đo khoảng
    # cách tới ứng viên gần nhất của BẤT KỲ Item nào khác đứng sau nó.
    all_positions = sorted({p for ps in candidates.values() for p in ps})
    chosen: Dict[str, int] = {}

    for item, positions in candidates.items():
        best_pos, best_gap = None, -1
        for pos in positions:
            nxt = next((p for p in all_positions if p > pos), len(text))
            gap = nxt - pos
            if gap > best_gap:
                best_pos, best_gap = pos, gap
        # Khối dưới 400 ký tự gần như chắc chắn là dòng mục lục, không phải nội dung
        if best_pos is not None and best_gap >= 400:
            chosen[item] = best_pos

    # Cắt văn bản: mỗi Section chạy từ vị trí của nó tới vị trí Item kế tiếp
    boundaries = sorted(chosen.items(), key=lambda kv: kv[1])
    sections: Dict[str, Section] = {}

    for idx, (item, start) in enumerate(boundaries):
        end = boundaries[idx + 1][1] if idx + 1 < len(boundaries) else len(text)
        body = text[start:end].strip()
        sections[item] = Section(
            item=item,
            title=ITEM_TITLES[item],
            text=body,
            char_start=start,
            char_end=end,
        )

    return sections


# Tiêu đề mô tả -> mã Item tương ứng, dùng cho bản khai không đánh số Item.
# Chỉ liệt kê những tiêu đề ĐẶC TRƯNG. Cố tình bỏ "Business" và "Properties" vì hai từ
# này xuất hiện dày đặc trong câu văn thường và sẽ tạo ra vô số ranh giới giả.
DESCRIPTIVE_HEADINGS: List[tuple] = [
    ("1A", r"risk\s+factors"),
    ("1C", r"cybersecurity"),
    ("3", r"legal\s+proceedings"),
    ("7", r"management['’]?s\s+discussion\s+and\s+analysis"),
    ("7A", r"quantitative\s+and\s+qualitative\s+disclosures?\s+about\s+market\s+risk"),
    ("8", r"financial\s+statements\s+and\s+supplementary\s+data"),
]


def split_by_headings(text: str) -> Dict[str, Section]:
    """Chia theo tiêu đề mô tả, dùng khi bản khai không đánh số Item.

    VÌ SAO CẦN HÀM NÀY

    Không phải bản 10-K nào cũng có dòng "Item 1A. Risk Factors" trong thân bài. Intel
    viết báo cáo theo lối tường thuật với tiêu đề mô tả thuần túy, rồi đặt một bảng
    "Form 10-K Cross-Reference Index" ở CUỐI tài liệu để trỏ Item sang số trang. SEC
    chấp nhận cách trình bày này.

    Bộ tách theo Item gặp bản khai như vậy sẽ trả về gần như rỗng, và cả doanh nghiệp bị
    loại khỏi hệ thống trong im lặng — Intel biến mất khỏi một hệ thống phân tích ngành
    bán dẫn là lỗi nghiêm trọng, mà lại không có thông báo lỗi nào.

    Hàm này bám vào tiêu đề mô tả thay vì số hiệu Item. Kém chính xác hơn, nhưng vớt lại
    được đúng những mục quan trọng nhất (Rủi ro, MD&A) thay vì mất trắng.

    GIỚI HẠN ĐÃ BIẾT, cố ý không che giấu: mục ứng với tiêu đề CUỐI CÙNG tìm được sẽ kéo
    dài tới hết tài liệu, vì không còn ranh giới nào để cắt. Với Intel, mục "Cybersecurity"
    vì thế ôm luôn 188k ký tự gồm cả báo cáo tài chính phía sau. Nội dung vẫn tìm kiếm
    được đầy đủ, nhưng NHÃN MỤC ở chế độ dự phòng chỉ là gần đúng — không nên dùng bộ lọc
    theo mục cho những bản khai đi qua nhánh này.
    """
    candidates: Dict[str, List[int]] = {}

    for item, pattern in DESCRIPTIVE_HEADINGS:
        # Tiêu đề phải đứng gần đầu dòng và không nằm giữa câu văn: yêu cầu phía sau là
        # xuống dòng hoặc dấu ngăn ô bảng, để loại các câu kiểu "see Risk Factors below".
        regex = re.compile(rf"^[\s|]*{pattern}\s*[\.\:\|]?\s*$", re.IGNORECASE | re.MULTILINE)
        positions = [m.start() for m in regex.finditer(text)]
        if positions:
            candidates[item] = positions

    if not candidates:
        return {}

    all_positions = sorted({p for ps in candidates.values() for p in ps})
    chosen: Dict[str, int] = {}

    for item, positions in candidates.items():
        best_pos, best_gap = None, -1
        for pos in positions:
            nxt = next((p for p in all_positions if p > pos), len(text))
            if nxt - pos > best_gap:
                best_pos, best_gap = pos, nxt - pos
        if best_pos is not None and best_gap >= 2000:
            chosen[item] = best_pos

    boundaries = sorted(chosen.items(), key=lambda kv: kv[1])
    sections: Dict[str, Section] = {}
    for idx, (item, start) in enumerate(boundaries):
        end = boundaries[idx + 1][1] if idx + 1 < len(boundaries) else len(text)
        sections[item] = Section(
            item=item, title=ITEM_TITLES[item], text=text[start:end].strip(),
            char_start=start, char_end=end,
        )
    return sections


def parse_filing(html_path: Path, content_only: bool = True) -> Dict[str, Section]:
    """Đọc file HTML -> trả về các Section, thử ba chiến lược theo thứ tự tin cậy giảm dần.

        1. Tách theo số hiệu Item     — chính xác nhất, dùng được cho ~95% bản khai
        2. Tách theo tiêu đề mô tả    — cho bản khai kiểu tường thuật (Intel)
        3. Coi cả tài liệu là một mục — không còn cấu trúc, nhưng nội dung vẫn tìm được

    Nguyên tắc: thà mất nhãn mục còn hơn mất cả doanh nghiệp. Chunk ở mức 3 mang item
    "0" nên tầng trên vẫn phân biệt được đâu là dữ liệu có cấu trúc, đâu là dữ liệu vớt.
    """
    text = html_to_text(html_path)
    sections = split_items(text)

    if len([k for k in sections if k in CONTENT_ITEMS]) < 3:
        fallback = split_by_headings(text)
        if len(fallback) > len([k for k in sections if k in CONTENT_ITEMS]):
            sections = fallback

    if not sections and len(text) > 5000:
        sections = {
            "0": Section(item="0", title="Toàn văn (không xác định được cấu trúc mục)",
                         text=text, char_start=0, char_end=len(text))
        }
        return sections

    if content_only:
        sections = {k: v for k, v in sections.items() if k in CONTENT_ITEMS}
        # Áp dụng luật đặc biệt cho Item 15 (xem chú thích ở _FINANCIALS_MARKER)
        item15 = sections.get("15")
        if item15 and not _FINANCIALS_MARKER.search(item15.text):
            sections.pop("15")
    return sections

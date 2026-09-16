"""Đọc chữ từ báo cáo thường niên BẢN SCAN bằng OCR — và từ chối khi chữ đọc ra không đủ tốt.

VÌ SAO PHẢI OCR, VÀ CHỈ CHO ĐÚNG MỘT MÃ

Sau khi vét cả kho tĩnh VietStock (2019–2025, ba thư mục sàn) lẫn trang của chính doanh
nghiệp, 29/30 mã VN30 có báo cáo đọc được. Mã còn lại là DGC: cả bảy năm đều là PDF ảnh.
Kiểm tận cấu trúc file — mỗi trang chứa ĐÚNG MỘT đối tượng loại ảnh, không có lớp chữ ẩn,
không phải lỗi bảng mã phông. Không còn gì để bóc, chỉ còn cách đọc ảnh.

VÌ SAO OCR LÀ THỨ NGUY HIỂM TRONG DỰ ÁN NÀY

Cả kiến trúc này dựng lên để không đưa ra thông tin trông hợp lệ mà sai. OCR đi ngược lại
điều đó theo cách khó thấy nhất: nó không bao giờ báo lỗi. Ảnh mờ thì "Đức Giang" thành
"Đúc Giang", "lợi nhuận" thành "lơi nhuân" — câu vẫn đọc được, trích dẫn vẫn có số trang
thật, người đọc không có dấu hiệu nào để nghi ngờ.

Nên ở đây OCR đi kèm ba ràng buộc, không cái nào là tùy chọn:

1. CÓ CỔNG CHẤT LƯỢNG, VÀ NÓ TỪ CHỐI ĐƯỢC. Văn bản tiếng Việt Unicode bình thường có
   khoảng 27% ký tự mang dấu (con số này đã đo trên năm báo cáo thật ở `vn_annual_report`).
   OCR hỏng dấu thì tỷ lệ đó sụt hẳn. Trang nào không đạt thì bỏ trang đó, cả tài liệu
   không đạt thì không nạp — chứ không nạp kèm lời cảnh báo rồi hy vọng có người đọc.

2. NHÃN ĐI THEO TỚI TẬN CÂU TRẢ LỜI. Mỗi đoạn mang nhãn "chữ do OCR từ bản scan" ngay
   trong tiêu đề trích dẫn, nên nó hiện ra cùng câu trả lời chứ không nằm im trong siêu
   dữ liệu. Người đọc biết mình đang đọc loại nào.

3. KHÔNG ĐỤNG TỚI SỐ. Trang có tỷ lệ chữ số cao vẫn bị `prose_pages` loại như mọi báo cáo
   khác. Số liệu đến từ VCI/XBRL. OCR một bảng số rồi để mô hình đọc là mở đúng cái cửa
   mà dự án này được dựng lên để đóng — và mở nó bằng nguồn chữ kém tin cậy nhất.

CHỌN THAM SỐ THẾ NÀO

    200 DPI   Đo trên DGC 2025: 200 DPI cho tỷ lệ ký tự có dấu 27,1% ở trang tiếng Việt,
              đúng bằng mức của văn bản Unicode thật. 150 DPI bắt đầu rụng dấu, 300 DPI
              không khá hơn mà chậm gấp đôi.
    vie       Gói `tessdata_best` (12 MB) chứ không phải `tessdata_fast`: dấu tiếng Việt
              là chỗ mô hình nhanh sai nhiều nhất, mà dấu sai thì chữ sai nghĩa.
    --psm 3   Tự phân tích bố cục. Báo cáo thường niên nhiều cột, nhiều khung.

Gói ngôn ngữ nằm trong `data/tessdata/` của chính dự án chứ không phải thư mục cài đặt
Tesseract, để không cần quyền quản trị và để dự án tự chứa.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Tỷ lệ ký tự mang dấu của tiếng Việt Unicode bình thường, đo trên báo cáo thật: ~27%.
# Đặt ngưỡng ở 12% để còn chỗ cho trang lẫn nhiều tiếng Anh (phần báo cáo tài chính của
# DGC viết bằng tiếng Anh và tỷ lệ có dấu ở đó gần 0 — đó là trang tiếng Anh thật, không
# phải OCR hỏng). Ngưỡng này lọc ở mức TÀI LIỆU, không ở mức trang, vì lý do đó.
MIN_VI_RATIO = 0.12

# Trang quá ít chữ thì OCR coi như không đọc được gì — bỏ, đừng để lọt chữ rác vào kho.
MIN_PAGE_CHARS = 200

DEFAULT_DPI = 200
DEFAULT_LANG = "vie"

_VI_MARKS = set("ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộ"
                "ờởỡớợùủũúụừửữứựỳỷỹýỵ")

_TESSERACT_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "tesseract",
)


def find_tesseract() -> Optional[str]:
    """Đường dẫn tới tesseract, hoặc None nếu chưa cài."""
    import shutil

    for candidate in _TESSERACT_CANDIDATES:
        if os.path.sep in candidate:
            if Path(candidate).exists():
                return candidate
        elif shutil.which(candidate):
            return shutil.which(candidate)
    return None


def _tessdata_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data" / "tessdata"


def check_ready() -> Tuple[bool, str]:
    """(dùng được chưa, lý do nếu chưa) — gọi TRƯỚC khi chạy cả nghìn trang."""
    exe = find_tesseract()
    if not exe:
        return False, ("chưa cài Tesseract. Cài bằng: "
                       "winget install --id UB-Mannheim.TesseractOCR")
    vie = _tessdata_dir() / "vie.traineddata"
    if not vie.exists():
        return False, (f"thiếu gói tiếng Việt tại {vie}. Tải từ "
                       "github.com/tesseract-ocr/tessdata_best")
    return True, ""


def vi_ratio(text: str) -> float:
    """Tỷ lệ ký tự mang dấu tiếng Việt trên tổng số chữ cái."""
    letters = [ch for ch in text.lower() if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(ch in _VI_MARKS for ch in letters) / len(letters)


def ocr_page(pdf_path: Path, index: int, lang: str = DEFAULT_LANG,
             dpi: int = DEFAULT_DPI) -> str:
    """Đọc chữ của MỘT trang. Trả về chuỗi rỗng nếu không đọc được gì."""
    import pypdfium2 as pdfium

    exe = find_tesseract()
    if not exe:
        raise RuntimeError("chưa cài Tesseract")

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        page = pdf[index]
        image = page.render(scale=dpi / 72).to_pil()
        page.close()
    finally:
        pdf.close()

    env = dict(os.environ, TESSDATA_PREFIX=str(_tessdata_dir()))
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "page.png"
        image.save(png)
        result = subprocess.run(
            [exe, str(png), "stdout", "-l", lang, "--psm", "3"],
            capture_output=True, env=env,
        )
    return result.stdout.decode("utf-8", "replace")


def ocr_document(pdf_path: Path, lang: str = DEFAULT_LANG, dpi: int = DEFAULT_DPI,
                 progress=None) -> List[str]:
    """Chữ của từng trang, cùng định dạng với `extract_pages` để dùng chung mọi hàm sau.

    Trang đọc ra quá ít chữ trả về chuỗi rỗng — `prose_pages` sẽ tự bỏ chúng, y hệt cách
    nó bỏ trang bìa và trang ảnh của báo cáo bình thường.
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    total = len(pdf)
    pdf.close()

    pages: List[str] = []
    for index in range(total):
        text = ocr_page(pdf_path, index, lang=lang, dpi=dpi)
        pages.append(text if len(text.strip()) >= MIN_PAGE_CHARS else "")
        if progress:
            progress(index + 1, total)
    return pages


def quality(pages: List[str]) -> Tuple[bool, str, Dict[str, float]]:
    """(đạt hay không, lý do nếu không, số liệu đo được).

    ⚠️ ĐO TRÊN PHẦN TIẾNG VIỆT, KHÔNG PHẢI TRÊN TOÀN TÀI LIỆU.

    Báo cáo của DGC có hẳn một khối phụ lục viết bằng tiếng Anh. Tỷ lệ ký tự có dấu ở đó
    gần bằng 0 — đúng, vì đó là tiếng Anh thật, không phải OCR hỏng dấu. Lấy trung bình
    toàn tài liệu là kéo tỷ lệ chung xuống và loại nhầm cả tài liệu tốt.

    Nên đo thế này: trang nào có dấu (>2%) thì coi là trang tiếng Việt, và chỉ những trang
    đó mới phải đạt ngưỡng. Nếu không có nổi một trang tiếng Việt nào thì mới là hỏng thật.
    """
    filled = [p for p in pages if p.strip()]
    if not filled:
        return False, "OCR không đọc được chữ nào", {}

    vi_pages = [p for p in filled if vi_ratio(p) > 0.02]
    if not vi_pages:
        return False, "không trang nào có dấu tiếng Việt — nhiều khả năng OCR hỏng", {
            "trang_co_chu": len(filled), "trang_tieng_viet": 0,
        }

    joined = "".join(vi_pages)
    ratio = vi_ratio(joined)
    stats = {
        "trang_co_chu": len(filled),
        "trang_tieng_viet": len(vi_pages),
        "ty_le_co_dau": round(ratio, 4),
        "ky_tu": sum(len(p) for p in filled),
    }
    if ratio < MIN_VI_RATIO:
        return False, (f"tỷ lệ ký tự có dấu chỉ {ratio:.1%} (cần ≥{MIN_VI_RATIO:.0%}) — "
                       f"OCR nhiều khả năng đã làm rụng dấu"), stats
    return True, "", stats

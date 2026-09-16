"""Báo cáo thường niên của doanh nghiệp niêm yết tại Việt Nam — tải, bóc chữ, cắt đoạn.

VÌ SAO TRƯỚC ĐÂY CHO RẰNG KHÔNG LÀM ĐƯỢC

`docs/nguon_du_lieu_viet_nam.md` kết luận: Việt Nam không có thứ tương đương EDGAR. Mọi
cổng công bố thông tin (HOSE, UBCKNN, VietStock, CafeF) đều là ứng dụng dựng bằng
JavaScript, không có API — muốn lấy tài liệu phải điều khiển trình duyệt thật.

Kết luận đó ĐÚNG với giao diện, nhưng sai với kho file phía sau. VietStock lưu tài liệu
ở một máy chủ tĩnh, và đường dẫn có quy luật:

    https://static2.vietstock.vn/data/{SÀN}/{NĂM}/BCTN/VN/{MÃ}_Baocaothuongnien_{NĂM}.pdf

Đo thật: FPT 2025 (12,8 MB), FPT 2024 (10,9 MB), FPT 2023 (14,7 MB) đều trả HTTP 200 kèm
`content-type: application/pdf`. Không cần trình duyệt, không cần Playwright.

BA CÁI BẪY, CẢ BA ĐỀU IM LẶNG

1. ĐƯỜNG DẪN ĐÚNG NHƯNG FILE KHÔNG PHẢI BÁO CÁO.
   `GAS_Baocaothuongnien_2024.pdf` tồn tại, 1,7 MB, HTTP 200 — mở ra là công văn 2 trang
   "REGULAR INFORMATION DISCLOSURE". Bốn mã VN30 khác (SAB, SHB, SSB, TPB) chỉ có file
   2022 nặng 68–466 KB, cùng loại. Nạp chúng vào thì hệ thống tưởng mình có báo cáo
   thường niên của Sabeco, và trả lời "không tìm thấy thông tin về rủi ro" thay vì nói
   thật là chưa có tài liệu. Nên có `is_annual_report()` chặn theo số trang và lượng chữ.

2. PDF KHÔNG CÓ LỚP CHỮ (bản scan) thì bóc ra rỗng, không báo lỗi gì.
   Đo trên 5 báo cáo mã lớn: 230/233, 108/111, 151/158, 104/107, 136/145 trang có chữ —
   nhóm đầu ngành thì ổn. Mã nhỏ chưa đo; hàm này trả về lý do cụ thể để script ghi lại.

3. PHÔNG CHỮ CŨ (TCVN3/VNI) bóc ra thành chữ rác kiểu "B¸o c¸o th­êng niªn".
   Năm báo cáo đã đo đều là Unicode chuẩn (27% ký tự có dấu, đúng tỷ lệ tiếng Việt, ký
   tự lạ dưới 0,5%). `looks_garbled()` bắt phần còn lại.

CẮT ĐOẠN 320 KÝ TỰ, KHÔNG PHẢI 1.200

Kho 10-K cắt 1.200 ký tự vì `bge-small-en` đọc được 512 token. Model đa ngữ dùng cho
tiếng Việt chỉ đọc 128 token, và phần vượt bị cắt IM LẶNG khi nhúng — đoạn vẫn hiện ra
đầy đủ cho người đọc, chỉ là vector chỉ đại diện cho phần đầu. Đo token thật trên báo cáo
FPT: đoạn 300 ký tự vượt ngưỡng 0/67 lần, 350 → 5/58, 450 → 15/45. Chọn 320.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from src.ingest.chunker import Chunk

STATIC_BASE = "https://static2.vietstock.vn/data"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
}

# Tên thư mục sàn trên máy chủ tĩnh. VCI gọi sàn HOSE là "HSX", VietStock dùng "HOSE".
EXCHANGE_FOLDER = {"HSX": "HOSE", "HOSE": "HOSE", "HNX": "HNX", "UPCOM": "UPCOM"}

# Một số mã có hậu tố khi doanh nghiệp nộp lại (POW_..._2023_1.pdf, VJC_..._2023_1.pdf).
NAME_SUFFIXES = ("", "_1", "_2")

# Ngưỡng nhận diện "đây có phải báo cáo thường niên thật không".
#
# Báo cáo thật đo được: 107–233 trang, 214.000–479.000 ký tự văn xuôi. Công văn giả dạng:
# 2 trang, 2.296 ký tự. Khoảng cách giữa hai nhóm rất rộng nên ngưỡng không cần tinh vi.
MIN_PAGES = 30
MIN_PROSE_CHARS = 50_000

# Dưới ngưỡng này thì dù OCR ra chữ cũng vẫn không phải báo cáo thường niên — đó là
# công văn công bố thông tin. Dùng để phân biệt "cần OCR" với "phải tìm nguồn khác".
LETTER_PAGES = 10

# Trang có tỷ lệ chữ số cao là bảng báo cáo tài chính. Bỏ chúng đi vì hai lý do: chúng
# không phải văn xuôi để tìm theo ý nghĩa, và quan trọng hơn — dự án lấy số từ XBRL/VCI
# chứ không cho mô hình đọc số từ văn bản. Đưa bảng số vào kho văn bản là mở đúng cái
# cửa mà cả kiến trúc này được dựng lên để đóng.
MAX_DIGIT_RATIO = 0.15
MIN_PAGE_CHARS = 200

TARGET_CHARS = 320
OVERLAP_CHARS = 64

# Ký tự đặc trưng của phông TCVN3/VNI khi bị bóc bằng bảng mã Unicode.
_LEGACY_MARKS = set("¸µ¶·¹¨©ª«¬®¯°±²³´½¾¿ÇÈÉÊËÌÎÏÒÓÔÕÖØÙÚÛÜ­")
_VI_MARKS = set("ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ")

_last_call = 0.0


def _throttle(min_gap: float = 0.4) -> None:
    """Giãn nhịp. Đây là máy chủ file của một bên thứ ba, không phải dịch vụ trả tiền."""
    global _last_call
    gap = time.time() - _last_call
    if gap < min_gap:
        time.sleep(min_gap - gap)
    _last_call = time.time()


def report_url(symbol: str, exchange: str, year: int, suffix: str = "") -> str:
    folder = EXCHANGE_FOLDER.get((exchange or "HOSE").upper(), "HOSE")
    return _url(symbol, folder, year, suffix)


def _url(symbol: str, folder: str, year: int, suffix: str = "") -> str:
    return (f"{STATIC_BASE}/{folder}/{year}/BCTN/VN/"
            f"{symbol.upper()}_Baocaothuongnien_{year}{suffix}.pdf")


def _folders(exchange: str) -> List[str]:
    """Sàn hiện tại trước, rồi tới các sàn còn lại.

    File nằm ở thư mục của sàn TẠI THỜI ĐIỂM công bố, không phải sàn hôm nay. Nhiều ngân
    hàng niêm yết ở HNX rồi mới chuyển sang HOSE quanh 2020–2021, nên báo cáo 2019–2020
    của họ nằm ở `HNX/` trong khi đồ thị ghi sàn hiện tại là HSX. Đo thật: SHB chỉ có
    đúng một bản báo cáo thật và nó ở `HNX/2020/`, ACB và VIB cũng có bản `HNX/2019/`.
    Chỉ tra thư mục theo sàn hôm nay là bỏ sót cả ba.
    """
    first = EXCHANGE_FOLDER.get((exchange or "HOSE").upper(), "HOSE")
    return [first] + [f for f in ("HOSE", "HNX", "UPCOM") if f != first]


def find_reports(symbol: str, exchange: str, years: Tuple[int, ...]) -> List[Dict]:
    """MỌI bản tìm được, mới nhất trước.

    ⚠️ Vì sao trả về danh sách chứ không chỉ bản mới nhất: bản mới nhất có thể là BẢN
    SCAN, và bản scan thì bóc ra rỗng. Bản đầu chỉ lấy bản mới nhất rồi bỏ cuộc, nên
    ACB, DGC, GAS, POW, VIB đều bị loại — trong khi năm cũ hơn của chính họ có thể là
    PDF có lớp chữ. Chỉ biết được sau khi tải về và thử bóc, nên phải giữ đủ ứng viên.

    Chỉ gửi HEAD nên rẻ: không tải nội dung, chỉ hỏi "file có tồn tại không, nặng bao
    nhiêu". Kiểm cả `content-type` vì máy chủ trả trang HTML lỗi kèm HTTP 200 cho vài
    đường dẫn sai.
    """
    out: List[Dict] = []
    with httpx.Client(timeout=30, follow_redirects=True, headers=_HEADERS) as client:
        for year in years:
            hit = None
            for folder in _folders(exchange):
                for suffix in NAME_SUFFIXES:
                    url = _url(symbol, folder, year, suffix)
                    _throttle()
                    try:
                        resp = client.head(url)
                    except Exception:  # noqa: BLE001 — mạng chập chờn coi như không có
                        continue
                    ctype = resp.headers.get("content-type", "")
                    if resp.status_code == 200 and "pdf" in ctype:
                        hit = {
                            "symbol": symbol.upper(), "year": year, "url": url,
                            "bytes": int(resp.headers.get("content-length") or 0),
                        }
                        break
                if hit:
                    break
            if hit:
                out.append(hit)  # mỗi năm chỉ lấy một bản
    return out


def find_report(symbol: str, exchange: str, years: Tuple[int, ...]) -> Optional[Dict]:
    """Bản mới nhất tìm được, hoặc None."""
    found = find_reports(symbol, exchange, years)
    return found[0] if found else None


def download(url: str, path: Path) -> int:
    """Tải về đĩa theo luồng. Trả về số byte. File có sẵn thì không tải lại."""
    if path.exists() and path.stat().st_size > 0:
        return path.stat().st_size
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    _throttle()
    with httpx.stream("GET", url, headers=_HEADERS, timeout=180, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    # Đổi tên sau khi tải xong: ngắt giữa chừng thì lần sau không nhầm file dở là file đủ.
    tmp.replace(path)
    return path.stat().st_size


def extract_pages(path: Path) -> List[str]:
    """Chữ của từng trang. Trang ảnh (bản scan) trả về chuỗi rỗng, không ném lỗi."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    pages: List[str] = []
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            textpage = page.get_textpage()
            pages.append(textpage.get_text_range() or "")
            textpage.close()
            page.close()
    finally:
        pdf.close()
    return pages


def _digit_ratio(text: str) -> float:
    dense = re.sub(r"\s", "", text)
    return sum(ch.isdigit() for ch in dense) / max(len(dense), 1)


def looks_garbled(pages: List[str]) -> bool:
    """Chữ bóc ra có phải rác do phông TCVN3/VNI không.

    Văn bản tiếng Việt Unicode bình thường có khoảng 27% ký tự mang dấu. Bản phông cũ thì
    tỷ lệ đó gần 0 mà lại đầy ký tự lạ như "¸" và "­".
    """
    letters = [ch for page in pages for ch in page.lower() if ch.isalpha()]
    if len(letters) < 2000:
        return False
    vi_ratio = sum(ch in _VI_MARKS for ch in letters) / len(letters)
    legacy_ratio = sum(ch in _LEGACY_MARKS for page in pages for ch in page) / len(letters)
    return vi_ratio < 0.08 and legacy_ratio > 0.01


def prose_pages(pages: List[str]) -> List[Tuple[int, str]]:
    """[(số trang bắt đầu từ 1, chữ)] — chỉ giữ trang văn xuôi, bỏ trang bảng số."""
    keep = []
    for index, text in enumerate(pages, start=1):
        if len(text.strip()) < MIN_PAGE_CHARS:
            continue
        if _digit_ratio(text) >= MAX_DIGIT_RATIO:
            continue
        keep.append((index, text))
    return keep


def is_annual_report(pages: List[str]) -> Tuple[bool, str]:
    """Đây có thật là báo cáo thường niên không. Trả về (kết luận, lý do nếu không).

    Thứ tự kiểm tra là có chủ đích: HỎI "CÓ CHỮ KHÔNG" TRƯỚC, hỏi "đủ dài không" sau.
    Làm ngược lại thì lý do trả về sai sự thật — DGC 2019 là bản scan 28 trang, nhưng
    vì kiểm số trang trước nên nó bị ghi là "nhiều khả năng là công văn". Lý do sai dẫn
    người đọc đi sai hướng: công văn thì phải tìm nguồn khác, còn bản scan thì OCR là
    xong. Một lý do sai còn tệ hơn không có lý do, vì nó nghe như đã điều tra rồi.
    """
    total_text = sum(len(t.strip()) for t in pages)
    if total_text < 5000:
        # Không có lớp chữ thì không đo được văn xuôi, chỉ còn số trang để đoán. Vài
        # trang thì dù OCR cũng vẫn là công văn; vài chục trang thì OCR có cửa.
        if len(pages) < LETTER_PAGES:
            return False, f"công văn {len(pages)} trang, bản scan — không phải báo cáo"
        return False, f"PDF không có lớp chữ (bản scan, {len(pages)} trang) — cần OCR"
    if looks_garbled(pages):
        return False, "chữ bóc ra là rác (phông TCVN3/VNI)"
    if len(pages) < MIN_PAGES:
        return False, f"chỉ {len(pages)} trang (cần ≥{MIN_PAGES}) — nhiều khả năng là công văn"
    chars = sum(len(t) for _p, t in prose_pages(pages))
    if chars < MIN_PROSE_CHARS:
        return False, f"chỉ {chars:,} ký tự văn xuôi (cần ≥{MIN_PROSE_CHARS:,})"
    return True, ""


def _sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?…;])\s+", text)
    return [p for p in parts if p]


def chunk_report(ticker: str, company: str, year: int, pages: List[str]) -> List[Chunk]:
    """Cắt báo cáo thành đoạn, MỖI ĐOẠN GIỮ SỐ TRANG.

    Số trang là thứ khiến câu trả lời kiểm chứng được: người đọc mở đúng trang đó trong
    file PDF gốc để đối chiếu, y như cách trích dẫn "NVDA FY2026, Item 1A" ở phía Mỹ.
    """
    chunks: List[Chunk] = []
    for page_no, text in prose_pages(pages):
        buffer = ""
        seq = 0
        for sentence in _sentences(text):
            # Câu dài hơn cả đoạn (bảng biểu bị làm phẳng) thì cắt cứng.
            while len(sentence) > TARGET_CHARS:
                head, sentence = sentence[:TARGET_CHARS], sentence[TARGET_CHARS:]
                chunks.append(_make(ticker, company, year, page_no, seq, head))
                seq += 1
            if len(buffer) + len(sentence) + 1 > TARGET_CHARS and buffer:
                chunks.append(_make(ticker, company, year, page_no, seq, buffer))
                seq += 1
                # Phần chồng lấn phải bắt đầu ở RANH GIỚI TỪ. Cắt thẳng theo số ký tự
                # thì đoạn sau mở đầu bằng nửa từ ("ợi, phòng ngừa rủi ro...") — trích dẫn
                # kiểu đó hiện ra trước mặt người đọc và trông như dữ liệu hỏng.
                tail = buffer[-OVERLAP_CHARS:]
                buffer = (tail.split(" ", 1)[1] if " " in tail else "") + " "
            buffer = f"{buffer}{sentence} "
        if len(buffer.strip()) >= 80:
            chunks.append(_make(ticker, company, year, page_no, seq, buffer))
    return chunks


def _make(ticker: str, company: str, year: int, page: int, seq: int, text: str) -> Chunk:
    return Chunk(
        chunk_id=f"{ticker}_AR{year}_p{page:04d}_{seq:02d}",
        doc_id=f"{ticker}_AR_{year}",
        ticker=ticker,
        company=company,
        form="BCTN",
        fiscal_year=str(year),
        item="AR",
        item_title=f"Báo cáo thường niên {year} · trang {page}",
        text=text.strip(),
        seq=seq,
    )

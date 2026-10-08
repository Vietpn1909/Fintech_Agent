"""Báo cáo thường niên lấy TỪ CHÍNH TRANG CỦA DOANH NGHIỆP — nguồn gốc, và mới nhất.

VÌ SAO CẦN NGUỒN THỨ HAI KHI ĐÃ CÓ VIETSTOCK

Kho tĩnh VietStock (`vn_annual_report.py`) phủ được 29/30 mã VN30, nhưng 10 mã trong số
đó mắc kẹt ở báo cáo cũ 2–6 năm. Đo từng mã: hoặc VietStock không có file nào mới hơn,
hoặc bản mới hơn là bản scan. Cạn đường ở đó.

Trang của chính doanh nghiệp thì khác hẳn, và khác theo hướng tốt:

    HPG   VietStock 2023  ->  hoaphat.com.vn 2025   (17,3 MB, 141 trang, có lớp chữ)
    SAB   VietStock 2020  ->  sabeco.com.vn  2025   (47,2 MB, 101 trang, có lớp chữ)
    VJC   VietStock 2023  ->  ir.vietjetair.com 2025 (15,0 MB, 123 trang, có lớp chữ)

Đây cũng là nguồn ĐÁNG TIN NHẤT có thể có: file do chính tổ chức phát hành đăng, không
qua trung gian nào. VietStock vẫn giữ vai trò nguồn phủ rộng; trang doanh nghiệp là nguồn
ưu tiên khi có.

VÌ SAO PHẢI CÓ DANH BẠ CHỨ KHÔNG DÒ TỰ ĐỘNG ĐƯỢC

Đã thử bộ đọc chung: vào trang chủ, tìm link chứa "thuong-nien", rồi gom link PDF. Nó
chạy được với HPG, SAB, VJC nhưng ra 0 kết quả cho cả 7 ngân hàng (ACB, HDB, SHB, SSB,
TPB, VIB, và GAS). Không phải lỗi bộ đọc — kiểm lại bằng cách vào thẳng trang báo cáo
thường niên của từng ngân hàng thì HTML trả về 135–180 KB mà KHÔNG chứa một link .pdf
nào: danh sách tài liệu do JavaScript dựng sau khi tải trang.

Nên phạm vi của mô-đun này được nói rõ ngay từ đầu: nó phục vụ những trang dựng sẵn ở
máy chủ. Trang dựng bằng JavaScript cần trình duyệt thật (Playwright) — việc riêng, chưa
làm. Ghi rõ vào `SITES` để lần sau không phải dò lại từ đầu.

BA THỨ MÔ-ĐUN NÀY CỐ Ý KHÔNG TỰ QUYẾT

1. Không tự đoán năm từ nội dung file. Năm lấy từ tên file/đường dẫn, và nếu không đọc
   được năm thì BỎ QUA thay vì đoán — trích dẫn ghi sai năm còn tệ hơn không có trích dẫn.
2. Không bỏ qua khâu kiểm tra. File tải về vẫn phải qua `is_annual_report()` y như file
   của VietStock: cùng ngưỡng số trang, lượng văn xuôi, và bản scan vẫn bị loại.
3. Không tự ghi đè bản đang có. Script gọi nó mới là nơi quyết định có thay hay không,
   dựa trên năm nào mới hơn.
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, unquote

import httpx

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
}

# Danh bạ trang "báo cáo thường niên" của từng doanh nghiệp.
#
# Chỉ liệt kê những trang ĐÃ ĐO ĐƯỢC là dựng sẵn ở máy chủ. Mỗi dòng kèm ghi chú vì sao
# có mặt hoặc vì sao vắng mặt, để lần mở rộng sau không phải dò lại từ đầu.
SITES: Dict[str, str] = {
    "HPG": "https://www.hoaphat.com.vn/quan-he-co-dong/bao-cao-thuong-nien",
    "SAB": "http://www.sabeco.com.vn/co-dong/bao-cao-thuong-nien/2025-8",
    "VJC": "https://ir.vietjetair.com/Home/Menu/bao-cao-thuong-nien",
}

# Đã đo và KHÔNG dùng được bằng HTTP thường — danh sách tài liệu do JavaScript dựng.
# Bảy mã này nay do `vn_ir_browser.py` lo (Playwright). Giữ danh sách ở đây để ai đọc
# mô-đun này biết vì sao chúng vắng mặt, thay vì tưởng là bỏ sót.
JS_ONLY: Dict[str, str] = {
    "ACB": "danh sách nằm trong API Next.js, chỉ gọi khi bấm tab năm",
    "GAS": "bài viết từng năm; link tải bản 2025 chỉ trả 25 KB rỗng, báo cáo thật là sách lật",
    "HDB": "link thẳng nhưng nạp trễ",
    "SHB": "bài viết từng năm trên WordPress",
    "SSB": "bài viết từng năm, file nằm trên cloud-cdn riêng",
    "TPB": "tab năm, danh sách tải về khi bấm",
    "VIB": "link thẳng nhưng nạp trễ; từ 2023 trở đi đều là BẢN SCAN nên vẫn kẹt ở 2022",
    "DGC": "WordPress khóa REST API (401) — mọi năm đều là bản scan, xử lý bằng OCR ở bước 16",
}

# Link phải TRÔNG NHƯ báo cáo thường niên. Không nhận mọi file .pdf trên trang, vì trang
# quan hệ cổ đông còn chứa báo cáo tài chính quý, nghị quyết, tài liệu đại hội cổ đông.
_LOOKS_ANNUAL = re.compile(
    r"bao[-_ ]?cao[-_ ]?thuong[-_ ]?nien|bctn|annual[-_ ]?report|\bar[-_ ]?20\d\d|20\d\dar",
    re.I,
)

# Những thứ TRÔNG GIỐNG mà không phải. Báo cáo phát triển bền vững hay bị đặt cạnh và có
# tên rất giống ("2025SR_VN" nằm ngay cạnh "2025AR_VN" trên trang Sabeco).
_NOT_ANNUAL = re.compile(
    r"phat[-_ ]?trien[-_ ]?ben[-_ ]?vung|sustainab|\bsr[-_ ]?20\d\d|20\d\dsr|"
    r"tai[-_ ]?chinh|financial[-_ ]?statement|quy[-_ ]?[1-4]\b|dai[-_ ]?hoi|nghi[-_ ]?quyet",
    re.I,
)

_YEAR = re.compile(r"20(1[5-9]|2[0-9])")

# Năm NẰM CẠNH một dấu hiệu "báo cáo thường niên" thì gần như chắc chắn là năm báo cáo.
# Bắt cả hai chiều vì doanh nghiệp viết cả hai kiểu: "AR2025" và "2025AR".
_YEAR_NEAR = re.compile(
    r"(?:ar|bctn|thuong[-_ ]?nien|nam)[-_ ]?(20(?:1[5-9]|2[0-9]))"
    r"|(20(?:1[5-9]|2[0-9]))[-_ ]?(?:ar\b|bctn)",
    re.I,
)

_last_call = 0.0


def _throttle(min_gap: float = 0.5) -> None:
    """Giãn nhịp — đây là máy chủ của doanh nghiệp, không phải dịch vụ ta trả tiền."""
    global _last_call
    gap = time.time() - _last_call
    if gap < min_gap:
        time.sleep(min_gap - gap)
    _last_call = time.time()


def _fold(text: str) -> str:
    """Bỏ dấu để so khớp. Tên file thật có cả "Báo cáo thường niên" lẫn "bao-cao-thuong-nien"."""
    import unicodedata

    text = unquote(text or "").lower().replace("đ", "d")
    return "".join(ch for ch in unicodedata.normalize("NFD", text)
                   if not unicodedata.combining(ch))


def _year_of(url: str) -> Optional[int]:
    """Năm báo cáo đọc từ đường dẫn, hoặc None nếu không chắc.

    ⚠️ BA CÁCH ĐỌC NĂM HIỂN NHIÊN, CẢ BA ĐỀU SAI Ở MỘT CA THẬT.

    Đường dẫn thật hay chứa nhiều năm, và không năm nào tự nhận mình là năm nào:

        .../2025/04/bao-cao-thuong-nien-hpg-2024.pdf     báo cáo 2024, đăng 2025
        .../2026/05/bao-cao-thuong-nien-nam-2025.pdf     báo cáo 2025, đăng 2026
        .../20260417_VJC_AR2025_VN_Final 1.pdf           báo cáo 2025, công bố 17/4/2026

    Năm đầu tiên sai ở ca 1 và 2 (lấy phải năm đăng). Năm lớn nhất trong cả đường dẫn sai
    ở ca 2. Năm lớn nhất trong tên file sai ở ca 3 (tiền tố ngày 2026 lớn hơn 2025).

    Cách đúng là hỏi năm nào NẰM CẠNH một dấu hiệu "báo cáo thường niên" — "AR2025",
    "bctn-2020", "nam-2025" — vì đó là chỗ doanh nghiệp tự ghi năm của báo cáo. Không có
    dấu hiệu nào thì mới rơi về năm lớn nhất trong tên file, và cuối cùng là trả None.

    Trả None chứ không đoán, vì trích dẫn ghi sai năm còn tệ hơn không có trích dẫn: người
    đọc sẽ mở đúng báo cáo của năm đó để đối chiếu rồi không tìm thấy gì.
    """
    name = _fold(urlparse(url).path.rsplit("/", 1)[-1])

    # ⚠️ LẤY NĂM LỚN NHẤT TRONG TÊN FILE CŨNG SAI, chỉ sai ở ca khác.
    #
    # Vietjet đặt tên "20260417_VJC_AR2025_VN_Final 1.pdf": tiền tố 20260417 là NGÀY
    # CÔNG BỐ, còn năm báo cáo nằm ở "AR2025". Lấy năm lớn nhất ra 2026, tức là mọi
    # trích dẫn từ file này sẽ ghi sai một năm — và ghi sai năm thì còn tệ hơn không
    # trích dẫn, vì người đọc mở đúng báo cáo 2026 để đối chiếu rồi không thấy đâu.
    #
    # Nên hỏi trước: có năm nào NẰM CẠNH dấu hiệu "báo cáo thường niên" không. Có thì
    # lấy năm đó. Không có thì mới rơi về năm lớn nhất trong tên file.
    near = _YEAR_NEAR.search(name)
    if near:
        return int(near.group(1) or near.group(2))
    years = [int(f"20{m.group(1)}") for m in _YEAR.finditer(name)]
    if years:
        return max(years)
    # Không có năm trong tên file thì thử cả đường dẫn, nhưng chỉ khi đúng MỘT năm xuất
    # hiện — nhiều năm mà không biết cái nào là năm báo cáo thì thà bỏ qua.
    whole = {int(f"20{m.group(1)}") for m in _YEAR.finditer(_fold(url))}
    return whole.pop() if len(whole) == 1 else None


def find_reports(symbol: str, timeout: float = 30.0) -> List[Dict]:
    """Các bản báo cáo thường niên tìm được trên trang doanh nghiệp, mới nhất trước.

    Trả về [] khi mã không có trong danh bạ — đó là câu trả lời hợp lệ, không phải lỗi.
    """
    page = SITES.get(symbol.upper())
    if not page:
        return []

    _throttle()
    try:
        resp = httpx.get(page, headers=_HEADERS, timeout=timeout,
                         follow_redirects=True, verify=False)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001 — trang doanh nghiệp hay chập chờn, coi như không có
        return []

    seen, out = set(), []
    for href in re.findall(r'href=["\']([^"\']+\.pdf[^"\']*)', resp.text, re.I):
        url = urljoin(str(resp.url), href)
        if url in seen:
            continue
        seen.add(url)
        # ⚠️ LỌC LOẠI TRỪ CHỈ ĐƯỢC SOI TÊN FILE, KHÔNG SOI CẢ ĐƯỜNG DẪN.
        #
        # Vietjet để báo cáo thường niên trong thư mục ".../thong-tin-tai-chinh/...", mà
        # "tai-chinh" nằm trong danh sách loại trừ (để gạt báo cáo tài chính quý). Soi cả
        # đường dẫn là loại nhầm TOÀN BỘ báo cáo của Vietjet — đo thật: 0/9 link lọt qua.
        #
        # Dấu hiệu NHẬN vẫn soi cả đường dẫn, vì tên file nhiều khi chỉ là một mã số và
        # chỉ thư mục mới nói đó là báo cáo thường niên.
        folded = _fold(url)
        name = _fold(urlparse(url).path.rsplit("/", 1)[-1])
        if not _LOOKS_ANNUAL.search(folded) or _NOT_ANNUAL.search(name):
            continue
        year = _year_of(url)
        if year is None:
            continue
        out.append({"symbol": symbol.upper(), "year": year, "url": url, "source": "ir_site"})

    # Mỗi năm giữ một bản; gặp lại năm đã có thì bỏ qua bản sau.
    best: Dict[int, Dict] = {}
    for item in out:
        best.setdefault(item["year"], item)
    return [best[y] for y in sorted(best, reverse=True)]


def download(url: str, path, timeout: float = 240.0) -> int:
    """Tải về đĩa theo luồng. File có sẵn thì không tải lại. Trả về số byte."""
    from pathlib import Path

    path = Path(path)
    if path.exists() and path.stat().st_size > 0:
        return path.stat().st_size
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    _throttle()
    with httpx.stream("GET", url, headers=_HEADERS, timeout=timeout,
                      follow_redirects=True, verify=False) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    # Đổi tên sau khi tải xong: ngắt giữa chừng thì lần sau không nhầm file dở là file đủ.
    tmp.replace(path)
    return path.stat().st_size


def coverage() -> Tuple[List[str], List[str]]:
    """(mã dùng được bằng HTTP thường, mã cần trình duyệt) — để script báo cho người chạy."""
    return sorted(SITES), sorted(JS_ONLY)

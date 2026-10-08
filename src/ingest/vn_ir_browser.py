"""Báo cáo thường niên trên những trang chỉ dựng được bằng JavaScript — đọc bằng trình duyệt thật.

VÌ SAO PHẢI DÙNG TRÌNH DUYỆT Ở ĐÂY

`vn_ir_site.py` lấy báo cáo từ trang doanh nghiệp bằng HTTP thường, và chạy được với HPG,
SAB, VJC. Nhưng 7 mã VN30 còn mắc kẹt ở báo cáo 2019–2022 đều là ngân hàng và doanh
nghiệp nhà nước có trang dựng bằng JavaScript: tải về 135–180 KB HTML mà không chứa nổi
một link `.pdf` nào. Danh sách tài liệu chỉ xuất hiện sau khi trình duyệt chạy script.

BA KIỂU TRANG, ĐO ĐƯỢC CHỨ KHÔNG ĐOÁN

    kiểu            mã          cách tài liệu xuất hiện
    link thẳng      VIB, HDB    có <a href=...pdf> nhưng nạp trễ vài giây
    tab theo năm    ACB, TPB    phải bấm vào tab "2025" thì danh sách mới được tải về
    bài từng năm    SSB, SHB    trang danh sách chỉ có link sang bài viết của từng năm,
                    GAS         file PDF nằm trong bài đó

Một bộ đọc duy nhất phủ cả ba: mở trang, bấm mọi thứ trông như tab năm, gom link PDF; nếu
chưa có gì thì đi tiếp vào các bài viết trông như "báo cáo thường niên <năm>" rồi gom
tiếp. Không cần viết riêng cho từng ngân hàng.

⚠️ NĂM PHẢI LẤY TỪ CHỮ NGƯỜI ĐỌC THẤY, KHÔNG PHẢI TỪ TÊN FILE

Đây là khác biệt lớn nhất so với `vn_ir_site`. Tên file ở đây thường vô nghĩa:

    ACB   acbwebsite/files/LQ2MYuFJyh1NPx9LlG6GQSfpNReANaJzuFyrxf3M.pdf   (mã băm)
    TPB   .../TP+Bank+Annual+Report+.pdf                                  (không có năm)
    SHB   .../2026/04/260420_SHB_BCTN_2025_Web-1.pdf                      (2026 là năm đăng)

Trong khi chữ hiển thị cạnh link thì luôn rõ ràng: "Báo cáo thường niên năm 2025". Nên
thứ tự là: năm trong CHỮ trước, rồi mới tới tên file, và không đọc được thì bỏ qua chứ
không đoán — trích dẫn ghi sai năm còn tệ hơn không có trích dẫn.

⚠️ NHỮNG THỨ TRÌNH DUYỆT LÀM ĐƯỢC MÀ MÔ-ĐUN NÀY CỐ Ý KHÔNG LÀM

Không đăng nhập, không điền biểu mẫu, không bấm nút tải xuống, không vượt captcha. Chỉ
mở trang công khai, bấm tab để xem danh sách, và đọc link. Bấm bằng JavaScript (`el.click()`)
chứ không bấm bằng chuột, vì banner cookie che mất nút và bấm chuột sẽ hỏng.

KẾT QUẢ ĐO ĐƯỢC TRÊN 7 MÃ

    ACB  2021 -> 2025      HDB  2023 -> 2024      SHB  2020 -> 2025
    SSB  2019 -> 2025      TPB  2020 -> 2025
    GAS  kẹt ở 2022 — trang PV GAS có link tải bản 2025 nhưng máy chủ trả về đúng
                       25.662 byte (file rỗng); báo cáo thật chỉ đăng dạng sách lật
                       ở `/ebook/`, không có bản PDF tải được
    VIB  kẹt ở 2022 — trang VIB có đủ bản 2023, 2024, 2025 nhưng CẢ BA đều là bản
                       scan không lớp chữ, y hệt bản trên VietStock

Hai mã cuối không phải lỗi của bộ đọc: đó là giới hạn của thứ doanh nghiệp công bố.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse, unquote

# Trang danh sách báo cáo thường niên. Chỉ những mã mà `vn_ir_site` (HTTP thường) không
# lấy được — mã nào HTTP thường làm được thì không nên mở trình duyệt cho tốn.
SITES: Dict[str, str] = {
    "ACB": "https://acb.com.vn/nha-dau-tu/bao-cao-thuong-nien",
    "HDB": "https://hdbank.com.vn/vi/investor/thong-tin-nha-dau-tu/bao-cao-thuong-nien",
    "SHB": "https://www.shb.com.vn/category/nha-dau-tu/bao-cao-thuong-nien/",
    "SSB": "https://www.seabank.com.vn/nha-dau-tu/bao-cao-thuong-nien",
    "TPB": "https://tpb.vn/nha-dau-tu/bao-cao-thuong-nien",
    "VIB": "https://www.vib.com.vn/vn/nha-dau-tu/bao-cao-thuong-nien",
    "GAS": "https://www.pvgas.com.vn/bai-viet/category/bao-cao-thuong-nien",
}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# ⚠️ Đường dẫn trong JSON CÓ THỂ chứa dấu cách: ACB trả về đúng chuỗi
# "https://acb.com.vn/acbwebsite/files/BCTN 2025.pdf". Mẫu cấm khoảng trắng sẽ cắt cụt
# thành ".../files/BCTN" rồi loại vì không còn đuôi .pdf — mất đúng bản mới nhất của ACB.
# Nên bắt trọn chuỗi nằm giữa hai dấu nháy kép thay vì bắt theo "ký tự không phải trắng".
# Và phải CHO PHÉP dấu gạch chéo ngược, vì JSON escape đường dẫn: ACB trả về đúng chuỗi
# "https:\/\/acb.com.vn\/acbwebsite\/files\/BCTN 2025.pdf". Mẫu cấm "\" cắt mất chính
# chuỗi cần lấy — ACB lại trượt bản 2025, lần thứ hai vì cùng một dòng này.
_PDF_IN_TEXT = re.compile(r'"([^"]*?\.pdf[^"]*?)"', re.I)
_YEAR = re.compile(r"20(1[5-9]|2[0-9])")

# Link tải không phải lúc nào cũng có đuôi .pdf. PV GAS phát file qua
# "DesktopModules/EasyDNNnews/DocumentDownload.ashx?...documentid=2866" — nhìn tên thì
# không biết là gì. Nhận diện theo hình dạng đường dẫn rồi HỎI MÁY CHỦ, chứ không đoán.
_DOWNLOAD_LINK = re.compile(r"documentdownload|/download|download\.ashx|[?&]download=", re.I)

# Chữ hiển thị phải nói đây là báo cáo thường niên. Trang quan hệ cổ đông còn đầy báo cáo
# tài chính quý, nghị quyết, tài liệu đại hội — không được nhận nhầm.
# ⚠️ Dấu phân cách phải là [-_+ ] chứ không chỉ khoảng trắng: trong ĐƯỜNG DẪN thì cụm
# này viết là "bao-cao-thuong-nien", trong CHỮ thì viết là "báo cáo thường niên", và tên
# file WebSphere nối bằng dấu cộng ("Bao+cao+thuong+nien+2025.pdf"). Dùng \s? là trượt
# sạch cả SSB, VIB và GAS — đo thật: 0/3 mã lọt qua.
_SEP = r"[-_+ ]?"
_LOOKS_ANNUAL = re.compile(
    rf"bao{_SEP}cao{_SEP}thuong{_SEP}nien|bctn|annual{_SEP}report|"
    rf"(?<![a-z0-9])ar{_SEP}20\d\d|20\d\d{_SEP}ar(?![a-z0-9])", re.I)
# Ranh giới phải tự viết thay vì dùng \b: gạch dưới LÀ ký tự chữ, nên "\besg\b" không
# khớp "HDB_ESG_Report_2025" — đo thật, báo cáo ESG của HDBank lọt qua và bị nhận nhầm
# thành báo cáo thường niên 2025.
_NOT_ANNUAL = re.compile(
    rf"phat{_SEP}trien{_SEP}ben{_SEP}vung|sustainab|(?<![a-z0-9])esg(?![a-z0-9])|"
    rf"(?<![a-z0-9])sr{_SEP}20\d\d|20\d\d{_SEP}sr(?![a-z0-9])|"
    rf"bao{_SEP}cao{_SEP}tai{_SEP}chinh|financial{_SEP}statement|quy{_SEP}[1-4](?![0-9])|"
    rf"dai{_SEP}hoi|nghi{_SEP}quyet|ban{_SEP}cao{_SEP}bach|prospectus|"
    # ⚠️ Công văn CBTT có tên chứa nguyên cụm "Báo cáo thường niên" nên lọt mọi bộ lọc
    # theo chữ. Đo thật: PV GAS phát "20260323 - GAS - CBTT Bao cao thuong nien" nặng
    # 1,1 MB, mở ra là công văn 2 trang — trong khi báo cáo thật nằm ngay cạnh, cùng
    # trang, tên là "_PVGAS_AR 2025_VN final".
    rf"(?<![a-z0-9])cbtt(?![a-z0-9])|cong{_SEP}bo{_SEP}thong{_SEP}tin", re.I)

# Bản tiếng Anh đặt cạnh bản tiếng Việt ("_PVGAS_AR 2025_EN final"). Kho nhúng bằng model
# đa ngữ nên bản nào cũng tìm được, nhưng người dùng hỏi tiếng Việt thì nên đọc bản gốc
# tiếng Việt — và quan trọng hơn: nạp cả hai là cùng một báo cáo bị đếm hai lần.
_ENGLISH_EDITION = re.compile(r"(?<![a-z0-9])(en|eng|english)(?![a-z0-9])", re.I)

# Bấm mọi phần tử mà chữ của nó CHỈ LÀ một năm, hoặc "Báo cáo thường niên <năm>" — đó là
# hình dạng của tab năm trên cả ACB lẫn TPB. Bấm bằng JS để không bị banner cookie chặn.
#
# ⚠️ KHÔNG BẤM VÀO LINK ĐIỀU HƯỚNG THẬT. Bản đầu bấm cả thẻ <a href="/..."> và tự phá
# trang của mình: VIB nhảy sang URL cổng WebSphere ("/!ut/p/z1/...") và mất sạch danh
# sách vừa dựng (124 link, 0 PDF — trong khi chỉ cần chờ mà không bấm thì có đủ). ACB thì
# mất luôn phản hồi API chứa "BCTN 2025.pdf". Tab năm thật luôn là div/span/button, hoặc
# thẻ <a href="#..."> trỏ trong chính trang đó.
_CLICK_YEAR_TABS = r"""() => { let n = 0;
  for (const e of document.querySelectorAll('div,li,button,span,a[href^="#"],a:not([href])')) {
    const t = (e.innerText || '').trim();
    if (e.children.length <= 1 && /^(Báo cáo thường niên\s*(năm\s*)?)?20(1[5-9]|2[0-9])$/i.test(t)) {
      try { e.click(); n++; } catch (err) {}
    } } return n; }"""

# Mỗi link kèm CHỮ mà người đọc nhìn thấy — chữ mới là chỗ ghi năm đáng tin.
_COLLECT = r"""() => [...document.querySelectorAll('a[href],iframe[src],embed[src]')]
   .map(e => [(e.innerText || e.title || e.getAttribute('aria-label') || '').trim()
                .replace(/\s+/g, ' ').slice(0, 90),
              e.href || e.src || ''])"""


def _fold(text: str) -> str:
    """Bỏ dấu tiếng Việt để so khớp — trang viết cả "thường niên" lẫn "thuong-nien"."""
    import unicodedata

    text = unquote(text or "").lower().replace("đ", "d")
    return "".join(ch for ch in unicodedata.normalize("NFD", text)
                   if not unicodedata.combining(ch))


def _year_from(label: str, url: str) -> Optional[int]:
    """Năm báo cáo: đọc CHỮ trước, tên file sau, không chắc thì None.

    Chữ hiển thị là nơi doanh nghiệp tự nói đây là báo cáo năm nào ("Báo cáo thường niên
    năm 2025"), nên nó đáng tin hơn hẳn tên file — tên file của ACB là mã băm, của TPB
    không có năm, của SHB mang năm ĐĂNG chứ không phải năm báo cáo.
    """
    years = [int(f"20{m.group(1)}") for m in _YEAR.finditer(_fold(label))]
    if len(set(years)) == 1:
        return years[0]
    if years:
        # Chữ có nhiều năm ("Báo cáo thường niên 2025 - công bố 2026") thì lấy năm đứng
        # ngay sau cụm "thường niên", đó là năm của báo cáo.
        near = re.search(r"(?:thuong nien|bctn|annual report)\s*(?:nam\s*)?(20\d\d)", _fold(label))
        if near:
            return int(near.group(1))
    name = _fold(urlparse(url).path.rsplit("/", 1)[-1])
    near = re.search(r"(?:ar|bctn|thuong[-_ ]?nien|nam)[-_ ]?(20(?:1[5-9]|2[0-9]))"
                     r"|(20(?:1[5-9]|2[0-9]))[-_ ]?(?:ar|bctn)", name)
    if near:
        return int(near.group(1) or near.group(2))
    in_name = [int(f"20{m.group(1)}") for m in _YEAR.finditer(name)]
    return max(in_name) if in_name else None


def _is_annual(label: str, url: str) -> bool:
    text = _fold(label)
    name = _fold(urlparse(url).path.rsplit("/", 1)[-1])
    # Dấu hiệu NHẬN xét cả chữ lẫn đường dẫn; dấu hiệu LOẠI chỉ xét chữ và tên file —
    # xét cả đường dẫn là loại nhầm mọi file nằm trong thư mục "thong-tin-tai-chinh".
    if not (_LOOKS_ANNUAL.search(text) or _LOOKS_ANNUAL.search(_fold(url))):
        return False
    return not (_NOT_ANNUAL.search(text) or _NOT_ANNUAL.search(name))


def _looks_like_pdf(url: str, timeout: float = 25.0, min_bytes: int = 3_000_000) -> bool:
    """Đây có thật là một file PDF đủ lớn để là báo cáo không — đọc BYTE ĐẦU, không tin nhãn.

    ⚠️ KIỂU MIME NÓI DỐI. PV GAS phát file qua `DocumentDownload.ashx` và khai
    `application/octet-stream` cho mọi thứ, kể cả PDF. Tin `content-type` là loại nhầm
    toàn bộ báo cáo của họ; tin ngược lại là nhận nhầm file ZIP hay Word.

    Nên hỏi hai câu mà máy chủ không nói dối được: bốn byte đầu có phải "%PDF" không, và
    file có đủ lớn không. Câu thứ hai để loại công văn công bố thông tin — chúng cũng là
    PDF thật, cũng nằm ngay cạnh, nhưng chỉ nặng 25 KB trong khi báo cáo thường niên nhẹ
    nhất đo được là 6,3 MB, còn công văn CBTT của PV GAS chỉ 1,1 MB.
    """
    import httpx

    try:
        with httpx.stream("GET", url, headers={"User-Agent": _UA}, timeout=timeout,
                          follow_redirects=True, verify=False) as resp:
            if resp.status_code != 200:
                return False
            size = int(resp.headers.get("content-length") or 0)
            if size and size < min_bytes:
                return False
            for chunk in resp.iter_bytes():
                return chunk.startswith(b"%PDF")
    except Exception:  # noqa: BLE001 — không hỏi được thì coi như không phải
        return False
    return False


def find_reports(symbol: str, timeout: float = 90.0, follow: int = 4,
                 headless: bool = True) -> List[Dict]:
    """Các bản báo cáo thường niên tìm được, mới nhất trước. [] nếu mã không có trong danh bạ.

    Ba bước, dừng sớm khi đã đủ:
      1. mở trang, chờ script dựng xong, bấm mọi tab năm rồi gom link
      2. nếu chưa có PDF nào: đi vào tối đa `follow` bài viết trông như báo cáo từng năm
      3. lọc theo dấu hiệu, đọc năm, mỗi năm giữ một bản
    """
    page_url = SITES.get(symbol.upper())
    if not page_url:
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError(
            "chưa cài Playwright. Cài bằng: pip install playwright && playwright install chromium"
        ) from exc

    found: List[Dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(user_agent=_UA, locale="vi-VN", ignore_https_errors=True)
        page = ctx.new_page()

        # Danh sách tài liệu nhiều khi về qua mạng rồi mới dựng vào DOM — hoặc không bao
        # giờ dựng thành thẻ <a>. Nghe luôn các phản hồi để không bỏ sót.
        from_net: Dict[str, str] = {}

        def on_response(resp) -> None:
            if resp.request.resource_type not in ("xhr", "fetch", "document"):
                return
            ctype = resp.headers.get("content-type", "")
            if not any(x in ctype for x in ("json", "html", "text")):
                return
            try:
                body = resp.text()
            except Exception:  # noqa: BLE001 — điều hướng đi rồi thì không đọc được nữa
                return
            for match in _PDF_IN_TEXT.findall(body):
                from_net.setdefault(match.replace("\\/", "/"), resp.url)

        page.on("response", on_response)

        def harvest(url: str) -> List[tuple]:
            # Gom HAI LẦN: trước khi bấm và sau khi bấm. Có trang bày sẵn tất cả (VIB,
            # HDB) và một cú bấm nhầm là mất; có trang chỉ hiện sau khi bấm (ACB, TPB).
            # Gom cả hai lần rồi gộp thì không phải chọn bên nào.
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            page.wait_for_timeout(9000)
            for _ in range(3):
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(600)
            out = [(label, urljoin(page.url, href))
                   for label, href in page.evaluate(_COLLECT) if href]
            page.evaluate(_CLICK_YEAR_TABS)
            page.wait_for_timeout(4000)
            out += [(label, urljoin(page.url, href))
                    for label, href in page.evaluate(_COLLECT) if href]
            return out

        try:
            anchors = harvest(page_url)
            pdfs = [(lab, u) for lab, u in anchors if ".pdf" in u.lower()]
            if not pdfs and not from_net:
                # Thử lại một lần. Danh sách của VIB dựng bằng script và có lần về chậm
                # hơn mốc chờ — đo thật: cùng một URL, lần ra 0 bản lần ra 10 bản. Một
                # lần thử lại rẻ hơn nhiều so với việc lặng lẽ tụt về báo cáo 2022.
                anchors += harvest(page_url)
                pdfs = [(lab, u) for lab, u in anchors if ".pdf" in u.lower()]

            if not pdfs:
                # Trang danh sách chỉ trỏ sang bài viết của từng năm (SSB, SHB, GAS).
                # Đi vào bản mới trước, và chỉ đi tối đa `follow` bài.
                articles = [(lab, u) for lab, u in anchors
                            if _is_annual(lab, u) and _year_from(lab, u)
                            and not u.lower().endswith(".pdf")
                            and urlparse(u).netloc == urlparse(page.url).netloc]
                articles.sort(key=lambda x: _year_from(x[0], x[1]) or 0, reverse=True)
                seen = set()
                for label, link in articles:
                    if link in seen or len(pdfs) >= follow:
                        continue
                    seen.add(link)
                    try:
                        inner = harvest(link)
                    except Exception:  # noqa: BLE001 — một bài hỏng không được làm hỏng cả lượt
                        continue
                    for lab2, u2 in inner:
                        if ".pdf" in u2.lower() or _DOWNLOAD_LINK.search(u2):
                            # Bài viết của năm nào thì lấy năm từ TIÊU ĐỀ BÀI, vì tên file
                            # bên trong thường mang năm đăng (SHB: .../2026/04/..._2025_...)
                            pdfs.append((f"{label} {lab2}", u2))
                    if len(seen) >= follow:
                        break

            for url, src in from_net.items():
                if url.lower().startswith("http") and ".pdf" in url.lower():
                    pdfs.append(("", url))

            best: Dict[int, Dict] = {}
            # Bản tiếng Việt trước, để nếu cùng một năm có cả hai thì bản Việt thắng.
            pdfs.sort(key=lambda x: bool(_ENGLISH_EDITION.search(_fold(x[0]) + " " + _fold(x[1]))))
            for label, url in pdfs:
                if not _is_annual(label, url):
                    continue
                # Link không có đuôi .pdf thì phải hỏi máy chủ, đừng tin tên file. Một
                # HEAD rẻ hơn nhiều so với tải nhầm 20 MB HTML rồi mới biết.
                if ".pdf" not in url.lower() and not _looks_like_pdf(url):
                    continue
                year = _year_from(label, url)
                if year is None:
                    continue
                best.setdefault(year, {"symbol": symbol.upper(), "year": year,
                                       "url": url, "source": "ir_browser", "label": label})
            found = [best[y] for y in sorted(best, reverse=True)]
        finally:
            browser.close()
    return found

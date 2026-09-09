# Khảo sát nguồn dữ liệu cho doanh nghiệp Việt Nam

Ngày khảo sát: 2026-09-09. Mọi con số dưới đây là **đo thật**, không phải ước lượng.

## Vì sao cần khảo sát này

Hệ thống hiện có ba tầng dữ liệu. Doanh nghiệp Việt Nam mới có **một** tầng:

| Tầng | Doanh nghiệp Mỹ | Doanh nghiệp Việt Nam |
|---|---|---|
| Số liệu | 6.074 mã · XBRL từ SEC | **30 mã** · VCI |
| Văn bản | 23.869 đoạn từ 10-K | **0** |
| Đồ thị | 95 mã · 2.153 cạnh | **0** |

Kiểm chứng bằng chính công cụ của agent:

```
company_coverage("FPT")  -> tier=metrics, financial_years=8, text_chunks=0
company_coverage("NVDA") -> tier=graph,   financial_years=12, text_chunks=700
```

Nên câu *"FPT nêu rủi ro gì"* hoặc *"FPT có quan hệ với ai"* hiện không trả lời được.

## Kết luận ngắn gọn

**Việt Nam không có thứ tương đương EDGAR.** Đây là phát hiện quan trọng nhất và nó
định hình mọi lựa chọn phía sau.

EDGAR cho phép tải một file JSON chỉ mục rồi lấy thẳng HTML của từng hồ sơ — đó là lý do
tầng văn bản cho 6.074 doanh nghiệp Mỹ dựng được chỉ bằng `httpx`. Phía Việt Nam, **mọi
cổng công bố thông tin chính thức đều là ứng dụng dựng bằng JavaScript**, không có API
công khai. Muốn lấy tài liệu phải điều khiển trình duyệt thật, tức là thêm Playwright,
thêm ~400 MB, và chấp nhận kịch bản vỡ mỗi lần trang đổi giao diện.

## Bảng kết quả dò từng nguồn

| Nguồn | Kết quả | Ghi chú |
|---|---|---|
| **VCI** `/company` (không kèm mã) | ✅ **1,08 MB · 1.905 doanh nghiệp** | Toàn bộ vũ trụ niêm yết |
| **VCI** `/company/{mã}` | ✅ có `enProfile` **tiếng Anh** | 30/30 mã VN30 đều có |
| **VCI** `/company/{mã}/shareholder` | ✅ **1.391 cổ đông trên VN30** | Có cấu trúc sẵn |
| **VCI** `/financial-statement` | ✅ đang dùng | 8 năm, 2018–2025 |
| VCI `/news`, `/officers`, `/subsidiaries`… | ❌ 404 hoặc `data: null` | Không tồn tại |
| **TCBS** `apipubaws` | ❌ HTTP 403 | Cloudflare chặn |
| **FireAnt** `restv2` | ❌ HTTP 401 | Cần khoá API |
| **HOSE** `api.hsx.vn` | ❌ 404 mọi đường dẫn | Không có API công khai |
| **HOSE** `www.hsx.vn` | ❌ chỉ 1.900 byte | Vỏ SPA, nội dung do JS dựng |
| **VietStock** `finance.vietstock.vn` | ❌ không tìm thấy token | Cần phiên trình duyệt thật |
| **UBCKNN** `congbothongtin.ssc.gov.vn` | ❌ 6.798 byte | Ứng dụng JSF, không có API |
| **SSI** `iboard-api` | ⚠️ 200 nhưng `companyProfile: ""` | Trường văn bản rỗng |
| **Simplize** | ❌ 174 KB không chứa tài liệu nào | SPA |
| Trang IR của từng doanh nghiệp | ❌ 404 / 403 | Mỗi nơi một kiểu, không gom được |

## Ba thứ dùng được ngay

### 1. Toàn bộ vũ trụ niêm yết — 1.905 doanh nghiệp

`GET /api/iq-insight-service/v1/company` (không kèm mã) trả về:

```
1.905 bản ghi · HSX 430 · HNX 301 · UPCOM 855 · OTC 313
Trường: ticker, organNameEn, organNameVi, exchange, marketCap,
        marketCapUsd, sectorNameLv1CustomEn, sectorNameLv2En, website
```

Thử 10 mã **ngoài** VN30 (REE, PNJ, DHG, VCI, HSG, NLG, IMP, VGC, BMP, CTD): cả 10 đều
trả về đủ 8 năm số liệu. Nghĩa là con số 30 hiện tại **không phải giới hạn của nguồn**,
chỉ là danh sách VN30 viết cứng trong `scripts/12_load_vietnam_metrics.py`.

Chi phí mở rộng: khoảng 2 lần gọi API mỗi mã. **Không gọi LLM lần nào.**

### 2. Cổ đông — dữ liệu ĐỒ THỊ có cấu trúc sẵn

`GET /company/{mã}/shareholder` trả về `ownerName`, `ownerNameEn`, `ownerType`
(INDIVIDUAL/CORPORATE), `percentage`, `positionName`, `quantity`.

Đo trên VN30:

```
1.391 bản ghi cổ đông · 1.119 node chủ sở hữu riêng biệt
526 tổ chức · 865 cá nhân
85 chủ sở hữu nắm từ 2 doanh nghiệp trở lên  <- đây là các cạnh bắc cầu
```

Ví dụ đường đi nhiều bước có thật:

```
State Capital Investment Corporation -> BID, BVH, FPT, MBB, SAB, VNM
Norges Bank                          -> ACB, DGC, FPT, HPG, MWG, STB, VNM, VPB
Vietnam Enterprise Investments Ltd   -> BID, DGC, FPT, HPG, MWG, STB, TPB, VIC, VPB
```

Để so sánh: đồ thị hiện tại có 2.153 cạnh, dựng bằng cách gọi LLM cho từng đoạn văn.
Nguồn này thêm **1.391 cạnh trong vài giây, không gọi LLM lần nào**.

> ⚠️ **Cạnh sở hữu KHÔNG cùng loại với cạnh quan hệ kinh doanh.** Một quỹ ETF nắm cả FPT
> lẫn VNM không có nghĩa FPT và VNM làm ăn với nhau — đó chỉ là danh mục đầu tư. Nếu nạp,
> phải để riêng loại quan hệ (`OWNED_BY`) và tuyệt đối không để agent suy ra quan hệ kinh
> doanh từ việc chung cổ đông. Nhóm có ý nghĩa thật là cổ đông nhà nước (SCIC), cổ đông
> chiến lược là doanh nghiệp, và người sáng lập.

### 3. `enProfile` — văn bản mô tả TIẾNG ANH

Đây là phát hiện quan trọng nhất cho tầng văn bản. Model nhúng của dự án
(`bge-small-en-v1.5`) **chỉ hiểu tiếng Anh**, nên văn bản tiếng Việt không lập chỉ mục
được nếu không đổi model. Nhưng VCI có sẵn bản tiếng Anh:

```
30/30 mã VN30 đều có enProfile
Ngắn nhất 645 · trung vị 850 · dài nhất 1.501 ký tự
Tổng 25.971 ký tự ≈ 16–30 đoạn
```

Ví dụ (FPT):

> FPT Corporation (FPT), formerly known as Food Technology Company, was established in
> 1988… FPT is a technology and IT services group with a presence in more than 30
> countries… focuses on strategic technology areas such as AI, cloud computing,
> semiconductors, cybersecurity…

**Nhưng phải nói thẳng:** 850 ký tự là *một đoạn*. Nó trả lời được *"FPT làm ngành gì"*,
**không** trả lời được *"FPT nêu rủi ro gì trong báo cáo thường niên"*. So sánh: NVIDIA
có 700 đoạn. Đây là tầng văn bản **hạng nhẹ**, không phải tầng văn bản thật.

## Ba con đường, kèm chi phí thật

| | Việc | Được gì | Chi phí | Rủi ro |
|---|---|---|---|---|
| **A** | Mở rộng số liệu ra 1.905 mã | Sàng lọc toàn thị trường VN | ~2 giờ chạy API | Thấp |
| **B** | Nạp cổ đông làm cạnh `OWNED_BY` | Câu hỏi nhiều bước cho VN | ~10 phút | Trung bình — dễ bị hiểu nhầm thành quan hệ kinh doanh |
| **C** | Tầng văn bản thật (báo cáo thường niên) | Câu hỏi định tính | Playwright + đổi model nhúng + nhúng lại 23.869 đoạn | **Cao** |

### Vì sao đường C đắt đến vậy

Ba việc chồng lên nhau, mỗi việc đều có thể hỏng riêng:

1. **Lấy được file.** Không cổng nào có API. Phải dùng Playwright điều khiển trình duyệt
   thật, và kịch bản sẽ vỡ mỗi lần trang đổi giao diện.
2. **Bóc chữ từ PDF.** Báo cáo thường niên Việt Nam là PDF trình bày đẹp, nhiều cột, nhiều
   ảnh — khác hẳn 10-K vốn là HTML có cấu trúc `Item 1A` cố định. Không có mốc nào để cắt
   đúng phần "rủi ro".
3. **Nhúng được tiếng Việt.** `bge-small-en-v1.5` (384 chiều) không hiểu tiếng Việt. Phải
   đổi sang `bge-m3` (1.024 chiều) — và vì số chiều khác nhau, **phải nhúng lại toàn bộ
   23.869 đoạn tiếng Anh đang có**, chứ không thể trộn hai loại vector trong một collection.

Bước 3 là chỗ nguy hiểm nhất: nó động vào dữ liệu đang chạy đúng. Nếu làm, phải nhúng vào
một collection mới và chỉ đổi tên khi đã đối chiếu xong, chứ không ghi đè.

## Khuyến nghị

Làm **A và B trước**. Cả hai đều là nguồn có cấu trúc sẵn, không cần LLM, không đụng vào
dữ liệu tiếng Anh đang chạy đúng, và mỗi cái đều mở ra một loại câu hỏi mới. Kèm `enProfile`
vào A vì nó gần như miễn phí (đã nằm trong cùng lần gọi API).

Để **C** thành một mục riêng, quyết định sau — nó là dự án riêng chứ không phải một bước.

## Ghi chú kỹ thuật

- Không dùng thư viện `vnstock`: nó kéo theo gói `vnai` gửi `machine_id` ra ngoài, mâu
  thuẫn với cam kết "chạy hoàn toàn trên máy" của dự án. Chi tiết ở đầu `src/ingest/vietnam.py`.
- Mọi endpoint VCI cần header `Referer: https://iq.vietcap.com.vn/`, thiếu thì bị chặn.
- Không gửi thông tin liên hệ cá nhân tới các API Việt Nam. `SEC_USER_AGENT` chỉ dùng cho
  SEC, vì chính sách Fair Access của SEC bắt buộc; VCI không yêu cầu và không được nhận.

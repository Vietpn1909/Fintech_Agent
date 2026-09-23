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

## Đã làm gì sau khảo sát (cập nhật 2026-09-09)

Làm **A** và **B**. **C** vẫn để nguyên là mục riêng.

| | Trước | Sau |
|---|---|---|
| Doanh nghiệp Việt Nam | 30 | **1.532** |
| Bản ghi năm tài chính | 240 | **11.777** |
| Cạnh sở hữu | 0 | **10.701** |
| Tổ chức bắc cầu ≥2 DN | 0 | **515** |

Năng lực mới mở ra: sàng lọc toàn thị trường Việt Nam (*"doanh nghiệp nào doanh thu lớn
nhất 2024"* → PLX 284 nghìn tỷ · VIC 189 · HPG 139), và cấu trúc công ty mẹ/công ty con
(FPT nắm 46,5% FPT Retail, 45,7% FPT Telecom, 23,9% FPT Online).

### Bốn lỗi âm thầm phát hiện được trong lúc làm

Ghi lại vì cả bốn đều thuộc loại "kết quả trông hoàn toàn hợp lệ, không báo lỗi gì".

**1. Trộn khái niệm doanh thu ở nhóm bảo hiểm.** Bảng báo cáo của VCI không có hai khuôn
mà năm khuôn. Nhóm bảo hiểm khớp `isi64` = *"Net sales from insurance business"* qua nhánh
so khớp "bắt đầu bằng", nên BVH 2024 ra 39.823 tỷ **không kèm nhãn nào** — đó là doanh thu
mảng bảo hiểm, không gồm thu nhập đầu tư tài chính. Ở mức 30 mã VN30 chỉ có một doanh
nghiệp bảo hiểm nên lọt lưới; ở mức 1.532 mã có 102 bản ghi như vậy. Đã thay hằng số
`_BANK_REVENUE_TITLE` bằng bảng `_REVENUE_BASIS` phủ cả ba nhóm.

**2. Nhập nhằng mã chứng khoán thành ngõ cụt.** 308 mã Việt Nam trùng mã doanh nghiệp Mỹ
đang có dữ liệu (ABT/Abbott, ADP, AIG…). Hệ thống báo nhập nhằng — đúng — nhưng chỉ có
hậu tố `.VN` để chỉ đích danh bên Việt Nam, **không có gì để nói "ý tôi là bên Mỹ"**. Agent
hỏi lại, người dùng đáp "bên Mỹ", agent không diễn đạt được nên hỏi vòng vo mãi. Đã thêm
hậu tố `.US` đối xứng.

**3. Ghép nhầm người trùng tên.** Gộp node chủ sở hữu theo tên thì với tổ chức là ổn
(515 trường hợp bắc cầu, chỉ 2 ghép nhầm, đều là cách viết khác của cùng một đơn vị) nhưng
với cá nhân thì sai nặng: **121/771** chứng minh được là những người khác nhau —
`Nguyen Van Thanh` gộp làm một từ *Nguyễn Văn Thành / Nguyễn Văn Thạnh / Nguyễn Văn Thanh*,
đứng tên ở 12 doanh nghiệp. Nạp nguyên vậy thì đồ thị dựng ra 770 đường đi bịa. Đã gắn mã
doanh nghiệp vào tên cá nhân (`Truong Gia Binh (FPT)`); script thoát với mã lỗi nếu còn cá
nhân nào bắc cầu.

**4. Node trùng giữa `:Company` và `:Organization`.** Chủ sở hữu là doanh nghiệp niêm yết
thì bị tạo thêm một node `:Organization` cùng tên — **318 trường hợp**. `neighbors()` khớp
theo tên nên gộp cả hai lại và in ra *"FPT Digital Retail nắm 46,54% FPT"*, ngược hoàn toàn
chiều sở hữu. Dữ liệu đúng, hiển thị sai. Đã sửa: chủ sở hữu nào là doanh nghiệp đã có
trong đồ thị thì nối thẳng vào node đó — nhờ vậy mới có cạnh sở hữu giữa hai doanh nghiệp.

### Điều còn phải nói thẳng

Sau bước này `tier='graph'` nhảy từ 95 lên 1.619. **Hai con số đó không cùng nghĩa.** 95
doanh nghiệp có quan hệ trích từ hồ sơ (cạnh thưa, giàu ngữ nghĩa, mỗi cạnh một lần gọi
LLM); 1.524 doanh nghiệp có quan hệ sở hữu (cạnh dày, chỉ nói ai nắm bao nhiêu của ai,
không tốn lần gọi nào). Giao diện đếm riêng hai loại chứ không cộng chung.

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

## Cập nhật 2026-09-15 — ba chỗ hở còn lại sau lần rà soát

### Đường C, phần làm được ngay: mô tả doanh nghiệp bằng tiếng Anh

Tầng văn bản đầy đủ (báo cáo thường niên) vẫn là dự án riêng, lý do như ở trên. Nhưng
`enProfile` của VCI là tiếng Anh, nên nhúng được bằng đúng model hiện tại mà không đụng
tới kho 10-K (`scripts/14_load_vietnam_profiles.py`):

```
1.527 / 1.532 doanh nghiệp có mô tả · ngắn nhất 248 · trung vị 749 · dài nhất 1.501 ký tự
lấy trong 8,9 phút · 0 mã lỗi · kho 10-K giữ nguyên 23.869 đoạn
```

Nó trả lời được *"FPT làm gì"*, **không** trả lời được *"FPT nêu rủi ro gì"*. Ba lớp để
không bị hiểu nhầm thành nội dung báo cáo:

- **Collection riêng** (`sec_filings_vn_profiles`). Tìm kiếm không lọc công ty không bao
  giờ chạm tới nó — đoạn mô tả ngắn và chung chung dễ vượt mặt đoạn 10-K dài về độ tương
  đồng, trộn chung là làm lệch kết quả của những câu hỏi đang trả lời đúng.
- `item = "PROFILE"`, kèm `source_note` trên từng kết quả và `vietnam_note` trên cả lượt tìm.
- Quy tắc 4d trong prompt trả lời: chỉ dùng để nói doanh nghiệp làm gì; hỏi rủi ro hay
  chiến lược thì phải nói thẳng là chưa có báo cáo thường niên.

### Mức phủ `tier` giờ chỉ đo hồ sơ SEC

Sau khi nạp cổ đông, 1.524 doanh nghiệp Việt Nam lên `tier='graph'` trong khi không có một
đoạn văn bản nào. Thang bậc metrics < text < graph hứa *"có đồ thị thì có văn bản"*, còn dữ
liệu nói ngược lại — `company_coverage("FPT")` trả `tier='graph'` kèm `text_chunks=0`.

Giờ cạnh `OWNED_BY` không tính vào mức phủ, và `company_coverage` trả riêng từng con số
(năm số liệu, đoạn 10-K, quan hệ trích từ hồ sơ, quan hệ sở hữu, mô tả) kèm một bản tóm
tắt bằng lời để agent nhắc lại cho người dùng.

```
tier='graph'    1.619 -> 95    đúng bằng số doanh nghiệp có quan hệ trích từ hồ sơ
Việt Nam        1.532 doanh nghiệp về 'metrics'
Deutsche Bank   từng lên 'graph' chỉ vì là cổ đông của IJC và DRC -> về 'metrics'
chạy lại lần 2  nâng 0 · hạ 0 · khai khống 0
```

### Bộ đối chiếu số giờ bắt được sai dấu

Bản đầu so theo độ lớn, bỏ qua dấu, để khỏi báo nhầm câu trả lời đúng "Intel lỗ
-18.756.000.000". Cái giá là mù dấu: "Intel lãi 18,76 tỷ" cũng được coi là khớp nguồn.
Giờ dấu được đọc từ chữ quanh con số — dấu trừ đứng sát, "lỗ"/"âm" đứng trước trong cùng
câu (từ gần nhất thắng, để "chuyển từ lỗ sang lãi 5,2 tỷ" là lãi), "(lỗ)" ngay sau — và chỉ
đem so với số từ dữ liệu có cấu trúc. Số đọc từ văn xuôi 10-K được coi là không rõ dấu,
vì 10-K viết "a net loss of $18.8 billion" với con số dương.

`tests/test_verify.py`: 5 ca phải bắt, 12 ca không được báo.

## Cập nhật 2026-09-16 — đường C làm được, và chỗ khảo sát này đã kết luận sai

### Kết luận cũ sai ở đâu

Phần "Kết luận ngắn gọn" ở trên viết: *mọi cổng công bố thông tin đều là ứng dụng dựng
bằng JavaScript, muốn lấy tài liệu phải điều khiển trình duyệt thật*. Điều đó đúng với
GIAO DIỆN, nhưng tôi đã dừng lại ở đó và không hỏi tiếp: **giao diện đó tải file từ đâu?**

VietStock lưu tài liệu trên một máy chủ tĩnh, đường dẫn có quy luật:

```
https://static2.vietstock.vn/data/{SÀN}/{NĂM}/BCTN/VN/{MÃ}_Baocaothuongnien_{NĂM}.pdf
```

Một lệnh HEAD là biết có file hay không. Không cần Playwright, không thêm 400 MB phụ
thuộc. Bài học: "trang web là SPA" nói về cách hiển thị, không nói gì về cách lưu file.

### Không phải nhúng lại 23.869 đoạn

Lý do thứ ba khiến đường C bị xếp là "đắt" — phải đổi sang model nhúng đa ngữ và nhúng
lại toàn bộ kho 10-K — cũng không đúng. Mỗi collection Qdrant có không gian vector riêng,
nên kho báo cáo tiếng Việt dùng model riêng của nó: tài liệu nhúng bằng model nào thì câu
hỏi nhúng bằng model đó. Kho 10-K không đổi một điểm nào.

Chọn `paraphrase-multilingual-MiniLM-L12-v2` (0,22 GB) thay vì `multilingual-e5-large`
(2,24 GB) vì GPU đã bị LM Studio chiếm 15,6/16,3 GB — nhúng buộc phải chạy CPU.

### Kết quả

```
30/30 mã VN30 · 50.214 đoạn · 23 mã dùng báo cáo 2025, 5 mã 2024, 2 mã 2022
kho 10-K giữ nguyên 23.869 đoạn · kho mô tả giữ nguyên 1.527 đoạn
```

Tìm bằng tiếng Anh vẫn ra đúng đoạn tiếng Việt (model đa ngữ khớp chéo ngôn ngữ):

```
"business risks and risk management" -> FPT BCTN 2025, trang 137 / 132 / 131
"dividend policy"                    -> VNM BCTN 2025, trang 48: "tạm ứng cổ tức đợt 1
                                        với mức 2.500 đồng mỗi cổ phiếu"
```

### 8 mã VN30 không nạp được, và vì sao — BẢNG NÀY ĐÃ SAI, xem mục kế tiếp

| Lý do | Mã |
|---|---|
| Mọi năm đều là **bản scan ảnh**, PDF không có lớp chữ | ACB, DGC, GAS, VIB |
| Không có file nào theo mẫu đường dẫn | SAB, SHB, SSB, TPB |

Bản scan cần OCR tiếng Việt — một dự án riêng nữa, và OCR sai chính tả thì câu trả lời
dẫn nguồn sai mà vẫn trông hợp lệ. Chưa làm.

### Cập nhật — 7 trong 8 mã đó vốn không hỏng, chỉ là tìm chưa tới

Bảng trên nói "mọi năm" và "không có file nào". Cả hai đều không đúng, và sai theo cùng
một kiểu: **kết luận được phát biểu rộng hơn phạm vi đã đo**. Thực tế chỉ thử ba năm
2025/2024/2023, ở đúng một thư mục sàn. Dò lại 2019–2025 trên cả ba thư mục:

| Mã | Bảng cũ nói | Thật ra |
|---|---|---|
| SAB | không có file nào | có, ở **2020** (9,5 MB, 105 trang) |
| SHB | không có file nào | có, ở **HNX/2020** (8,8 MB, 174 trang) |
| SSB | không có file nào | có, ở **2019** (6,3 MB, 92 trang) |
| TPB | không có file nào | có, ở **2020** (10,4 MB, 65 trang) |
| ACB | mọi năm đều scan | 2021 có lớp chữ (12,4 MB, 135 trang) |
| GAS | mọi năm đều scan | 2022 có lớp chữ (9,3 MB, 93 trang) |
| VIB | mọi năm đều scan | 2022 có lớp chữ (12,0 MB, 89 trang) |
| DGC | mọi năm đều scan | **đúng** — cả 7 năm đều scan |

Hai nguyên nhân, cả hai đều đáng ghi lại:

1. **Khoảng năm quá hẹp.** Ba năm gần nhất là phản xạ tự nhiên khi làm dữ liệu tài chính,
   nhưng nó lẫn lộn hai câu hỏi khác nhau: *"số liệu mới nhất là bao nhiêu"* (phải mới)
   và *"doanh nghiệp tự mô tả rủi ro và chiến lược thế nào"* (một báo cáo 2020 vẫn nói
   được rất nhiều). Vì mọi trích dẫn đều in kèm năm, báo cáo cũ không gây hiểu nhầm.
2. **Thư mục sàn là sàn LÚC CÔNG BỐ, không phải sàn hôm nay.** SHB, ACB, VIB đều niêm yết
   ở HNX rồi mới chuyển sang HOSE quanh 2020–2021, nên báo cáo cũ của họ nằm ở `HNX/`
   trong khi đồ thị ghi sàn hiện tại là HSX. Chỉ tra theo sàn hôm nay là bỏ sót cả ba.
   `_folders()` nay thử sàn hiện tại trước rồi tới các sàn còn lại.

Còn một lỗi thứ ba, nhỏ hơn nhưng cùng họ: thứ tự kiểm tra trong `is_annual_report` đặt
"đủ số trang chưa" TRƯỚC "có chữ không", nên DGC 2019 — một bản scan 28 trang — bị ghi lý
do là *"nhiều khả năng là công văn"*. Lý do sai đẩy người đọc đi sai hướng: công văn thì
phải tìm nguồn khác, bản scan thì OCR là xong. Một lý do sai còn tệ hơn không có lý do,
vì nó nghe như đã điều tra rồi. Nay hỏi "có chữ không" trước, và phân biệt công văn scan
(dưới 10 trang, OCR cũng vô ích) với báo cáo scan (đáng OCR).

**Kết quả: 22/30 → 29/30 mã · 40.138 → 48.023 đoạn.** DGC là mã duy nhất thật sự cần OCR.

### Cập nhật — nguồn thứ hai và thứ ba, lên 30/30

Bảng trên nói VietStock đã cạn đường với 10 mã mắc kẹt ở báo cáo cũ 2–6 năm. Đúng với
VietStock, nhưng không đúng với câu hỏi "còn nguồn nào khác không".

**Trang của chính doanh nghiệp là nguồn gốc, và mới hơn hẳn.** Đo thật:

| Mã | VietStock | Trang doanh nghiệp |
|---|---|---|
| HPG | 2023 | **2025** — 17,3 MB, 141 trang, có lớp chữ |
| SAB | 2020 | **2025** — 47,2 MB, 101 trang, có lớp chữ |
| VJC | 2023 | **2025** — 15,0 MB, 123 trang, có lớp chữ |

Nhưng cách này chỉ dùng được với trang dựng sẵn ở máy chủ. Đo trên 7 ngân hàng còn lại
(ACB, GAS, HDB, SHB, SSB, TPB, VIB): vào thẳng trang báo cáo thường niên thì HTML trả về
135–180 KB mà **không chứa một link `.pdf` nào** — danh sách tài liệu do JavaScript dựng
sau khi tải trang. Muốn lấy phải chạy trình duyệt thật (Playwright), chưa làm. Danh sách
này được ghi vào `JS_ONLY` trong `src/ingest/vn_ir_site.py` để lần sau không dò lại.

Các nguồn khác đã thử và không dùng được: VCI không có endpoint tài liệu (404 cho cả 9
đường dẫn thử), Fireant đã đóng API (404), TCBS chặn (403), cổng UBCKNN và HOSE đều trả
vỏ SPA rỗng, danh sách tài liệu của VietStock cần CSRF token.

**DGC thì OCR.** Kiểm tận cấu trúc file: mỗi trang chứa đúng một đối tượng loại ảnh,
không có lớp chữ ẩn, không phải lỗi bảng mã phông. Tesseract 5.4 với gói `tessdata_best`
tiếng Việt ở 200 DPI cho tỷ lệ ký tự có dấu **24,9%** — sát mức 27% của văn bản Unicode
thật, nên cổng chất lượng (ngưỡng 12%) cho qua. 58 trang, 37 trang văn xuôi, 326 đoạn.

Chữ OCR vẫn sai, và sai theo kiểu khó thấy: ở trang tiếng Anh của chính báo cáo đó,
"community" thành "commumity", "risks" thành "rislcs". Nên mỗi đoạn mang nhãn *"chữ do OCR
từ bản scan"* trong tiêu đề trích dẫn, `source_note` cảnh báo, và ANSWER_PROMPT có luật 4e
buộc agent nói rõ với người dùng.

### Bốn cái bẫy, cả bốn đều im lặng

1. **Đường dẫn đúng nhưng file không phải báo cáo.** `GAS_Baocaothuongnien_2024.pdf` có
   thật, 1,7 MB, HTTP 200 — mở ra là công văn 2 trang. Bốn mã khác chỉ có file 2022 nặng
   68–466 KB, cùng loại. Chặn bằng ngưỡng số trang và lượng chữ văn xuôi.
2. **Bản scan bóc ra rỗng** mà không ném lỗi nào.
3. **Phông chữ cũ TCVN3/VNI** bóc ra thành chữ rác. Chặn bằng tỷ lệ ký tự có dấu.
4. **Model đa ngữ chỉ đọc 128 token**, phần vượt bị cắt IM LẶNG khi nhúng — đoạn vẫn hiện
   đủ cho người đọc, chỉ là vector chỉ đại diện phần đầu. Đo token thật trên báo cáo FPT:
   đoạn 300 ký tự vượt ngưỡng 0/67 lần, 350 → 5/58, 450 → 15/45. Nên cắt 320 ký tự, khác
   hẳn 1.200 ký tự của kho 10-K.

Bảng số trong báo cáo bị loại khỏi kho văn bản (tỷ lệ chữ số ≥ 15%): số liệu phải đến từ
XBRL/VCI chứ không phải từ việc mô hình đọc bảng — đúng nguyên tắc của cả dự án.



### Cập nhật — trình duyệt thật cho 7 trang JavaScript

Mục trên kết luận 7 mã (ACB, GAS, HDB, SHB, SSB, TPB, VIB) cần Playwright mới lấy được.
Đã làm, và kết quả đúng như dự đoán một nửa:

| Mã | Trước | Sau | Trang dựng kiểu gì |
|---|---|---|---|
| ACB | 2021 | **2025** | API Next.js, chỉ gọi khi bấm tab năm |
| SHB | 2020 | **2025** | bài viết từng năm trên WordPress |
| SSB | 2019 | **2025** | bài viết từng năm, file trên CDN riêng |
| TPB | 2020 | **2025** | tab năm, danh sách tải về khi bấm |
| HDB | 2023 | **2024** | link thẳng nhưng nạp trễ vài giây |
| GAS | 2022 | 2022 | link tải bản 2025 trả về đúng 25.662 byte — file rỗng |
| VIB | 2022 | 2022 | có đủ bản 2023–2025 nhưng **cả ba đều là bản scan** |

Hai mã cuối không phải giới hạn của công cụ mà là của thứ doanh nghiệp công bố: PV GAS
chỉ đăng báo cáo dạng sách lật `/ebook/`, VIB đăng đúng bản scan giống hệt trên VietStock.

**Năm lỗi đáng nhớ, tất cả đều thuộc loại "im lặng cho kết quả sai":**

1. **Tự bấm vào link điều hướng rồi phá trang của chính mình.** Bản đầu bấm mọi phần tử
   có chữ là một năm, kể cả thẻ `<a href="/...">`. VIB nhảy sang URL cổng WebSphere và
   mất sạch danh sách vừa dựng: 124 link, 0 PDF — trong khi chỉ cần *chờ mà không bấm*
   thì có đủ 10 bản. Nay chỉ bấm `div/span/button` và `<a href="#...">`.
2. **`esg` không khớp `HDB_ESG_Report`** vì gạch dưới là ký tự chữ, nên `` không
   coi đó là ranh giới. Báo cáo ESG của HDBank lọt qua và suýt bị nạp thành báo cáo
   thường niên 2025.
3. **Mẫu tìm PDF cấm khoảng trắng và dấu `\`**, trong khi ACB trả về JSON có escape:
   `"https:\/\/acb.com.vn\/acbwebsite\/files\/BCTN 2025.pdf"`. Cắt cụt thành
   `.../files/BCTN` rồi loại vì không còn đuôi `.pdf`.
4. **Link tải không có đuôi `.pdf`**: PV GAS phát file qua `DocumentDownload.ashx`. Và
   kiểu MIME nói dối — họ khai `application/octet-stream` cho cả PDF. Phải đọc bốn byte
   đầu tìm chữ ký `%PDF`, kèm ngưỡng dung lượng để loại công văn CBTT 1,1 MB nằm ngay
   cạnh và mang tên chứa nguyên cụm "Báo cáo thường niên".
5. **Tên file mang năm CÔNG BỐ.** TPBank đặt tên `BCTN 2026 TV 21.4 VIEW.pdf` cho tài
   liệu mà bìa ghi rõ "BÁO CÁO THƯỜNG NIÊN 2025". Thêm `year_in_text()` đọc năm từ chính
   nội dung tài liệu — đếm năm đứng cạnh cụm "thường niên" trong 25 trang đầu (TPBank:
   2025 xuất hiện 16 lần, 2023 bốn lần, 2026 một lần) — và năm đó ghi đè năm đoán từ tên
   file. Sai một năm là mọi trích dẫn chỉ người đọc sang đúng một tài liệu khác.

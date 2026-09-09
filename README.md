# Agentic GraphRAG — Trợ lý Phân tích Doanh nghiệp & Đầu tư

Hệ thống hỏi đáp trên hồ sơ tài chính SEC, kết hợp ba nguồn tri thức và để một agent tự
chọn nguồn phù hợp với từng câu hỏi.

| Nguồn | Dùng cho | Công nghệ |
|---|---|---|
| **XBRL lookup** | Câu hỏi tra số: *"Doanh thu NVIDIA FY2026?"* | Dữ liệu có cấu trúc của SEC |
| **Vector search** | Câu hỏi tìm nội dung: *"NVIDIA nói gì về kiểm soát xuất khẩu?"* | Qdrant + bge-small |
| **Graph search** | Câu hỏi bắc cầu: *"Rủi ro chuỗi cung ứng của NVIDIA lan sang Microsoft qua đường nào?"* | Neo4j |

## Kiến trúc phủ dữ liệu ba tầng

Ba loại tri thức có chi phí mở rộng chênh nhau hàng nghìn lần, nên **không thể phủ cả
ba ở cùng một quy mô**. Đây là quyết định kiến trúc trung tâm của dự án:

| Tầng | Độ phủ | Chi phí | Vì sao dừng ở đó |
|---|---|---|---|
| **Số liệu** | **6.074 doanh nghiệp** | 1 file bulk 1,41GB · 11 giây xử lý · **0 lần gọi LLM** | Không có nhược điểm nào. Phủ hết. |
| **Văn bản** | ~48–500 doanh nghiệp | ~17 chunk/giây · 20 phút đến 3,5 giờ | Phủ 6.000 công ty làm **giảm** độ chính xác: mục Risk Factors là ngôn ngữ pháp lý sao chép nhau, top-k sẽ đầy small cap nói cùng một câu vô nghĩa. |
| **Đồ thị** | ~48 doanh nghiệp | 1 lần gọi LLM mỗi chunk · hàng giờ GPU | Phủ 6.000 công ty mất **~50 ngày GPU**. Và đồ thị mỏng trải rộng suy luận **kém hơn** đồ thị dày trong một hệ sinh thái — sức mạnh nằm ở cạnh nối, mà 6.000 công ty ngẫu nhiên gần như không nhắc tên nhau. |

### Nạp theo yêu cầu — thứ khiến độ phủ thực tế là toàn bộ

Khi agent bị hỏi về doanh nghiệp chưa có trong index văn bản, nó **tự đi lấy ngay trong
lúc trả lời**: tải 10-K, bóc tách, nhúng vector, rồi trả lời. Đo thật: **36–45 giây**.

Đây cũng là ranh giới giữa "RAG có thêm bộ định tuyến" và "agent thật" — agent nhận ra
mình thiếu thông tin và tự hành động để bù đắp, thay vì trả lời rằng không biết.

Hệ quả: độ phủ thực tế là **toàn bộ 8.001 doanh nghiệp**, chỉ khác nhau ở độ trễ lần
đầu. Mỗi node `Company` mang trường `tier` (`metrics` / `text` / `graph`) để agent biết
mình đang có gì và nói thật với người dùng.

### "Toàn thế giới" tới đâu?

SEC không chỉ có doanh nghiệp Mỹ. Mọi tập đoàn lớn ngoài Mỹ có niêm yết ADR đều nộp hồ
sơ: **TSMC, Toyota, SAP, Alibaba, Shell, Novo Nordisk, ASML, Sony, Unilever, BHP, HSBC,
AstraZeneca, TotalEnergies, Infosys...** — đều tra được.

Nằm ngoài tầm với: doanh nghiệp **không niêm yết trên sàn nào trong hai vũ trụ này**
(Bosch, Huawei, các tập đoàn tư nhân). Đó là giới hạn của nguồn dữ liệu, không phải của
code — muốn phủ thì phải thêm nguồn khác (Companies House, EDINET...).

Doanh nghiệp Việt Nam **đã nằm trong tầm với**: 1.532 mã niêm yết trên HSX/HNX/UPCOM, lấy
số từ VCI. Xem mục *Mở rộng sang doanh nghiệp Việt Nam* bên dưới.

## Chọn lọc chunk trước khi gọi LLM — tối ưu quan trọng nhất của tầng đồ thị

Chạy dàn trải toàn bộ 4.748 chunk mất ~6 giờ GPU. Đo trên 336 bộ ba đầu tiên cho thấy
tiền đang tiêu sai chỗ:

| | Chạy dàn trải | Sau khi lọc |
|---|---|---|
| Số chunk phải chạy | 4.748 | **823** (giảm 83%) |
| Thời gian | ~6 giờ | **~1,1 giờ** |
| Cạnh **Company→Company** | 3,6% | **31%** (10 chunk đầu: 79%) |
| Cạnh `EXPOSED_TO_RISK` | 54% | 16% |
| Bộ ba mỗi chunk | 3,3 | 4,8 |

Đồ thị vừa nhanh hơn 5,5 lần vừa **dày hơn ở đúng chỗ cần**. Không phải đánh đổi — trước
đó phần lớn GPU tiêu vào những đoạn văn không có gì để trích.

**Điều kiện lọc là một CỔNG CHẶN, không phải điểm số.** Bản đầu tiên cộng điểm cho tên tổ
chức rồi so ngưỡng, và một đoạn nói lan man *"competition is intense"*, *"we rely on
suppliers"* mà không nêu tên ai vẫn lọt qua nhờ điểm từ khóa. Nhưng đoạn như vậy **không
thể** sinh ra cạnh giữa hai công ty — không có công ty thứ hai để nối. Không có tên riêng
thì không có cạnh; đó là điều kiện cần, nên phải chặn chứ không phải cộng điểm.

Chunk được **sắp xếp theo điểm giảm dần**. Chạy theo thứ tự chữ cái thì NVIDIA và TSMC nằm
gần cuối — đúng những công ty cần nhất cho câu hỏi bắc cầu lại phải chờ lâu nhất. Sắp xếp
lại nghĩa là dừng ở bất kỳ đâu thì phần giá trị nhất cũng đã xong.

## Vì sao tách riêng XBRL

Điểm yếu chí mạng của mọi hệ RAG tài chính là **con số**. LLM đọc bảng biểu đã bị làm
phẳng thành văn bản rất dễ lấy nhầm cột năm, nhầm đơn vị, hoặc bịa ra số nghe hợp lý.

Ở đây LLM **không bao giờ đọc số từ văn bản**. Mọi con số đến từ file XBRL do chính
doanh nghiệp khai và nộp cho SEC, mỗi con số gắn với mã `us-gaap`, kỳ báo cáo và số hiệu
bản khai truy vết được. Agent tra số bằng tra cứu từ điển, không suy đoán.

### Nhưng đó mới là một nửa, và nửa còn lại từng bị bỏ ngỏ

Lấy số bằng mã lệnh thì đúng. Nhưng **câu trả lời cuối vẫn do mô hình viết ra** — nó nhận
con số đúng rồi tự gõ thành đoạn văn, và trước đây không có gì kiểm lại đoạn văn đó.

Đo trên chính bộ đánh giá 37 câu, có hai lỗi lọt qua:

```
chép sai    công cụ đưa 180.683.000.000 (lợi nhuận gộp Apple FY2024)
            mô hình viết  119.100.000.000 — số nằm sẵn trong ngữ cảnh, chép lại vẫn sai

sai bậc     doanh thu TSMC là 2.894.307.700.000 TWD
            mô hình viết "2.894.307,70 tỷ TWD" — gấp 1.000 lần
```

Ca thứ hai đáng chú ý hơn: bộ đánh giá **chấm ĐẠT**, vì số thô đúng vẫn nằm trong ngoặc
đơn. Nó chỉ dò xem con số kỳ vọng có xuất hiện hay không, chứ không hỏi ngược lại *"những
con số KHÁC trong câu trả lời từ đâu ra?"*

`src/agent/verify.py` hỏi đúng câu đó. Sau khi mô hình viết xong, mọi con số từ 1 triệu
trở lên được đối chiếu với dữ liệu công cụ đã trả về (và với chính câu hỏi, vì ngưỡng lọc
người dùng nêu ra cũng là nguồn hợp lệ). Lệch thì viết lại một lần; vẫn lệch thì gắn cảnh
báo vào câu trả lời chứ **không tự sửa** — không biết phải thay bằng giá trị nào, và đoán
hộ người đọc là việc không bao giờ đúng.

Bước này là mã lệnh thuần, nên nó không hỏng theo cách khâu viết câu hỏng được.

    128 con số được đối chiếu trên 37 câu
      1 câu có số sai ở lần viết đầu -> viết lại -> sạch
      0 cảnh báo gắn nhầm

Kiểm thử ở `tests/test_verify.py` khoá cả hai chiều: ba ca **phải bắt** (chép sai, sai bậc,
bịa thêm dòng) và sáu ca **không được báo** (số làm tròn, số âm, ngưỡng nhắc lại từ câu
hỏi, nguồn tiếng Anh "$17.7 billion" đối chiếu với "17,7 tỷ USD"…). Sáu ca sau đều lấy từ
những lần báo nhầm CÓ THẬT khi chạy trên dữ liệu thật, không phải ca giả định.

---

## Cài đặt

```bash
docker compose up -d
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
cp .env.example .env
```

Mở `.env` và điền `SEC_USER_AGENT` bằng **tên + email thật** — SEC trả về 403 cho mọi
request không khai danh tính (Fair Access Policy).

Bật LM Studio → tab **Developer** → **Start Server**, đặt **Context Length ≥ 16384**
(mặc định 4096 sẽ cắt cụt chunk và làm hỏng bước trích xuất đồ thị).

## Chạy pipeline

```bash
.venv/Scripts/python.exe scripts/00_benchmark_llm.py
```
Đo tốc độ thực tế của model đang nạp rồi điền `LLM_EXTRACTION_MODEL` và
`LLM_REASONING_MODEL` trong `.env` theo số đo. **Đừng bỏ qua** — chênh lệch giữa 8 tok/s
và 40 tok/s là chênh lệch giữa chạy qua đêm và chạy trong một tiếng.

```bash
# --- Tầng số liệu: toàn bộ 6.074 doanh nghiệp, không cần LLM ---
curl -L -H "User-Agent: Ten Ban email@cua.ban" -o data/raw/companyfacts.zip \
     https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
.venv/Scripts/python.exe scripts/05_load_full_universe.py --workers 8

# --- Tầng văn bản ---
.venv/Scripts/python.exe scripts/06_build_text_index.py --tier ecosystem
.venv/Scripts/python.exe scripts/06_build_text_index.py --top-revenue 500   # tùy chọn

# --- Tầng đồ thị (cần LM Studio) ---
# ⚠️ Ba bước này là MỘT khối, phải chạy đủ và đúng thứ tự — xem mục "Thứ tự pipeline".
.venv/Scripts/python.exe scripts/04_build_knowledge_graph.py --limit 10   # chạy thử
.venv/Scripts/python.exe scripts/04_build_knowledge_graph.py --resume     # chạy thật
.venv/Scripts/python.exe scripts/09_resolve_entities.py                   # gộp node tách đôi
.venv/Scripts/python.exe scripts/11_merge_graph_entities.py --apply       # gộp vào bản ghi SEC

# --- Tầng số liệu Việt Nam (không cần LM Studio) ---
.venv/Scripts/python.exe scripts/12_load_vietnam_metrics.py --resume --apply      # 1.532 mã · ~37 phút
.venv/Scripts/python.exe scripts/13_load_vietnam_shareholders.py --resume --apply # cổ đông · ~9 phút

# --- Đánh giá ---
.venv/Scripts/python.exe scripts/07_build_testset.py                 # sinh 34 câu hỏi
.venv/Scripts/python.exe scripts/08_run_eval.py --numeric-only       # chấm xác định
.venv/Scripts/python.exe scripts/08_run_eval.py                      # thêm RAGAS

# --- Giao diện web ---
.venv/Scripts/python.exe run_web.py          # mo http://localhost:8000
.venv/Scripts/python.exe run_web.py --lan    # cho may khac trong mang LAN xem
```

Xem đồ thị: <http://localhost:7474> (neo4j / fintech123) · Qdrant: <http://localhost:6333/dashboard>

---

## Giao diện web

`http://localhost:8000` — một trang duy nhất gồm phần giới thiệu (dùng cho PR, quảng bá,
slide bảo vệ) và khung demo hoạt động thật.

### Vì sao bỏ Streamlit

Bản đầu dùng Streamlit. Nó tiện để dựng nhanh nhưng hỏng ở ba điểm đúng lúc cần nhất:

| | Streamlit | FastAPI + trang tĩnh |
|---|---|---|
| Hiện tiến trình khi agent đang chạy | không — chỉ vẽ sau khi `ask()` trả về, tức là sau 18–78 giây màn hình trắng | có — mỗi bước hiện ngay khi bước đó xong, qua SSE |
| Nhúng phần giới thiệu | không, nó chiếm trọn cửa sổ | có, cùng một trang |
| Deploy | cần WebSocket riêng, phiên bám vào tiến trình, khó đặt sau CDN/reverse-proxy | HTTP thuần, trang tĩnh đẩy lên CDN nào cũng được |

Nguyên nhân gốc là mô hình thực thi: Streamlit chạy lại **toàn bộ file** mỗi lần người
dùng chạm vào bất cứ thứ gì. Với một agent chạy hàng chục giây, mô hình đó không diễn tả
được trạng thái "đang làm dở".

### Hai trang, hai việc

| Trang | Việc duy nhất của nó |
|---|---|
| `/` — trang chủ | Người mới vào hiểu agent biết những gì, trả lời được loại câu hỏi nào, hoạt động ra sao, và vì sao tin được kết quả. Kết thúc bằng một nút dẫn sang trang chat. |
| `/chat` — trò chuyện | Chỉ có hội thoại. Không bảng số liệu, không sơ đồ, không gì để đọc ngoài câu trả lời. |

Gộp cả hai vào một trang là sai ở cả hai chiều: người vào lần đầu phải cuộn qua ô nhập
mới đọc được phần giới thiệu, còn người quay lại lần thứ hai phải cuộn qua phần giới
thiệu mới tới được ô nhập.

Dấu vết suy luận vẫn giữ ở trang chat, nhưng **gấp lại** dưới mỗi câu trả lời
(*"Trợ lý đã làm gì · 3 truy vấn · 42s · 2 vòng"*). Nó là điểm mạnh của hệ thống nên
không bỏ được, nhưng người dùng bình thường không cần nhìn — ai muốn xem thì bấm mở.

### Có gì trong đó

- **Câu trả lời chạy dần ra màn hình.** Tổng thời gian không đổi, nhưng chữ bắt đầu
  hiện từ giây thứ 3-5 thay vì chờ trọn 8-25 giây rồi mới thấy cả khối. Đây là cải thiện
  trải nghiệm rẻ nhất trong dự án — không phải tối ưu gì trong mô hình.
- **Dấu vết suy luận** — định tuyến chọn công cụ nào, mỗi công cụ nhận tham số gì, truy
  vấn nguồn nào, mất bao lâu, khối suy xét kết luận đủ hay phải quay lại. Với hội đồng
  chấm, phần này quan trọng ngang câu trả lời: nó chứng minh hệ thống **thực sự định
  tuyến** chứ không phải truy hồi một lần rồi nhét hết vào prompt.
- **Số liệu độ phủ lấy trực tiếp từ Neo4j và Qdrant**, không phải số viết cứng.
- **Đèn trạng thái** trên thanh điều hướng, kiểm tra ba phụ thuộc **riêng biệt** — thiếu
  cái nào nó nói tên cái đó và lệnh cần chạy, thay vì một chữ "lỗi" chung chung.
- **Tra độ phủ một doanh nghiệp** trước khi hỏi, để biết công ty đó đang ở tầng nào.
- **Xếp hàng tường minh**: model local phục vụ tuần tự, nên câu hỏi thứ hai được báo
  "đang xếp hàng, còn N câu phía trước" thay vì để người dùng nhìn màn hình đứng im.

### API

| Điểm cuối | Việc |
|---|---|
| `GET /api/health` | trạng thái Neo4j / Qdrant / LM Studio, tách riêng từng cái |
| `GET /api/stats` | số liệu độ phủ (cache 60 giây) |
| `GET /api/coverage?q=` | hệ thống đang có gì về một doanh nghiệp |
| `POST /api/ask` | hỏi, nhận luồng SSE từng bước |
| `POST /api/ask-sync` | hỏi, nhận một JSON khi xong — cho tích hợp máy-với-máy |
| `GET /` · `GET /chat` | hai trang giao diện |

Tài liệu tự sinh: `http://localhost:8000/docs`

### Deploy

Ràng buộc quyết định mọi thứ: **mô hình ngôn ngữ chạy trên GPU của máy này**. Máy chủ web
phải nằm ở nơi gọi được LM Studio, nên không thể đẩy toàn bộ lên một dịch vụ đám mây
thông thường.

```bash
# Trong mạng LAN — đủ cho demo trước lớp hoặc hội đồng
.venv/Scripts/python.exe run_web.py --lan

# Mở ra Internet tạm thời, giữ máy chủ ở lại máy có GPU
cloudflared tunnel --url http://localhost:8000
```

Nếu cần một trang giới thiệu **luôn online**: `web/static/` là HTML/CSS/JS thuần, không
có bước build, đẩy thẳng lên GitHub Pages hay bất kỳ CDN nào cũng chạy. Chỉ phần demo cần
máy chủ có GPU — sửa `fetch('/api/...')` trong `app.js` thành URL của tunnel là xong (CORS
đã mở sẵn ở `web/server.py`).

---

## Trạng thái hiện tại

| Bước | Trạng thái | Kết quả đã kiểm chứng |
|---|---|---|
| Tải dữ liệu SEC | ✅ | 8 bản 10-K + bulk XBRL 1,41GB (20.303 doanh nghiệp) |
| Bóc tách theo Item | ✅ | 2,25 triệu ký tự sạch, 8/8 bản khai đúng |
| Trích xuất XBRL | ✅ | **48.025 bản ghi năm · 4.295 doanh nghiệp Mỹ có số liệu** |
| Đồ thị nền | ✅ | **7.772 Company · 59.802 FinancialYear** (6.074 Mỹ + 1.532 Việt Nam) |
| Vector index | ✅ | **23.869 chunk · 47 doanh nghiệp** + nạp theo yêu cầu (36–45 giây/công ty) |
| Phân giải tên công ty | ✅ | Khớp theo ranh giới từ, neo vào CIK, chịu được gõ sai |
| Bộ công cụ agent | ✅ | 7 công cụ, kiểm thử trên dữ liệu thật, không gọi LLM |
| Sơ đồ trạng thái LangGraph | ✅ | Biên dịch chạy được, có vòng lặp suy xét |
| Bộ câu hỏi kiểm thử | ✅ | **34 câu** sinh từ dữ liệu thật (23 chấm xác định + 11 RAGAS) |
| Bộ chấm dò số | ✅ | 12/12 ca kiểm thử, nhận 6 cách viết số khác nhau |
| Giao diện web | ✅ | FastAPI tại `localhost:8000` — trang giới thiệu + demo, stream dấu vết agent theo thời gian thực |
| Đồ thị tri thức | ✅ | **2.567 bộ ba · 265 doanh nghiệp có cạnh · 14/14 loại quan hệ** |
| Gộp thực thể | ✅ | 176 node trùng đã gộp; neo theo CIK nên nạp lại không sinh trùng |
| Agent đầu-cuối | ✅ | **26/26 = 100% độ chính xác số liệu** · recall thực thể 100% ở 4/5 nhóm |
| Đa tiền tệ | ✅ | USD, EUR, JPY, TWD, CNY, DKK... có chặn trộn lẫn khi so sánh |
| Chấm điểm RAGAS | ⏳ | Tùy chọn — thước đo dò số đã đủ mạnh và không cần LLM giám khảo |

---

## Bảy cái bẫy đã gặp thật khi xử lý dữ liệu SEC

Ghi lại vì đây là loại lỗi âm thầm — không báo lỗi, chỉ lặng lẽ cho ra kết quả sai.

### 1. Trường `fy` trong XBRL không phải năm của số liệu

Nó là năm của **bản khai** chứa số liệu đó. Mỗi bản 10-K trình bày 3 năm để so sánh, nên
cùng một `fy=2024` có cả số của FY2022, FY2023 và FY2024:

```
fy=2024  end=2022-09-24  val=394,33B   <- FY2022
fy=2024  end=2023-09-30  val=383,29B   <- FY2023
fy=2024  end=2024-09-28  val=391,04B   <- FY2024 (đúng)
```

### 2. Doanh nghiệp đổi mã khai báo giữa chừng

NVIDIA khai doanh thu bằng `RevenueFromContractWithCustomerExcludingAssessedTax` cho các
năm cũ rồi chuyển sang `Revenues`; Alphabet đi ngược lại. Duyệt danh sách mã ưu tiên rồi
**dừng ở mã đầu tiên có dữ liệu** sẽ khóa vào đúng cái mã đã bị bỏ dùng và mất trắng
những năm gần nhất. Phải **gộp dữ liệu của tất cả các mã theo từng năm**.

### 3. Không có quy tắc ngày tháng nào suy ra được năm tài chính

Cái bẫy này chỉ lộ ra khi mở rộng từ 4 lên 6.000 doanh nghiệp:

```
Walmart     kết thúc 31/01/2025 -> họ gọi là fiscal 2025   (theo năm KẾT THÚC)
NVIDIA      kết thúc 26/01/2025 -> họ gọi là fiscal 2025   (theo năm KẾT THÚC)
Target      kết thúc 01/02/2025 -> họ gọi là fiscal 2024   (theo năm BẮT ĐẦU)
Home Depot  kết thúc 02/02/2025 -> họ gọi là fiscal 2024   (theo năm BẮT ĐẦU)
```

Bốn doanh nghiệp, ngày kết thúc chênh nhau hai ngày, hai cách đặt tên ngược nhau. Lấy
năm của ngày kết thúc khiến Target và Home Depot lệch một năm.

**Cách lấy đúng:** trong mỗi bản khai, kỳ có ngày kết thúc muộn nhất chính là kỳ mà `fy`
đang mô tả. Gán `fy` của bản khai cho kỳ đó, làm vậy cho mọi bản khai của doanh nghiệp →
mỗi năm được gán đúng cái tên mà chính doanh nghiệp dùng.

### 4. Không phải 10-K nào cũng đánh số Item

Intel viết báo cáo theo lối tường thuật với tiêu đề mô tả thuần túy ("Risk Factors"),
rồi đặt một bảng "Form 10-K Cross-Reference Index" ở **cuối** tài liệu trỏ Item sang số
trang. SEC chấp nhận cách này. Bộ tách theo Item gặp bản khai như vậy trả về gần như
rỗng → **Intel biến mất khỏi hệ thống trong im lặng**, không một thông báo lỗi.

Cách xử lý: ba chiến lược theo thứ tự tin cậy giảm dần — tách theo số hiệu Item, rồi
tách theo tiêu đề mô tả, cuối cùng coi cả tài liệu là một mục. Thà mất nhãn mục còn hơn
mất cả doanh nghiệp.

### 5. Model có bước suy nghĩ trả token vào một luồng KHÁC

Gemma 4, Qwen3, DeepSeek-R1 sinh ra hai luồng token riêng: `reasoning_content` (phần tự
lẩm bẩm) và `content` (câu trả lời thật). Code đọc `content` sẽ nhận chuỗi rỗng.

Đo thật với Gemma 4 26B, câu hỏi *"Say hello in 5 words"*:

```
max_tokens=50   ->  47 token suy nghĩ,   0 token nội dung, content RỖNG
max_tokens=800  -> 506 token suy nghĩ,  12 token nội dung
```

Tỷ lệ lãng phí 42:1. Và nếu `max_tokens` quá chặt, model tiêu hết ngân sách cho phần suy
nghĩ rồi bị cắt trước khi kịp trả lời — **trả về rỗng mà không báo lỗi gì**. Benchmark đầu
tiên của tôi vì thế báo *0 tok/s* cho một model đang chạy hoàn toàn bình thường.

Cách xử lý: `reasoning_effort="none"` cho việc máy móc (trích xuất JSON, định tuyến —
nhanh gấp **7,5 lần** và còn trích được nhiều quan hệ hơn), giữ suy nghĩ cho khối viết câu
trả lời cuối. Thêm kiểm tra: `content` rỗng + `finish_reason=length` thì ném lỗi thay vì
im lặng trả về chuỗi rỗng.

### 6. Model dịch tên thực thể sang tiếng Việt

Tìm thấy node rủi ro `"gián đoạn chuỗi cung ứng"` nằm lẫn giữa các tên tiếng Anh. Cùng một
khái niệm thành hai node rời, đồ thị đứt mạch mà không có dấu hiệu gì. Chặn ở tầng kiểm
tra bằng regex ký tự có dấu, kèm luật trong prompt cấm dịch tên.

### 7. Tiêu đề mục bị ngắt dòng giữa từ

Microsoft đặt tiêu đề trong ô bảng và HTML ngắt dòng ngay giữa một từ (`ITEM 1A. RIS` /
`K FACTORS`). So khớp chuỗi thô sẽ trượt và mất trắng mục Risk Factors dài 61k ký tự.
Phải so khớp sau khi **bỏ hết ký tự không phải chữ/số**.

---

## Hai lỗi "im lặng" nặng nhất, tìm ra khi tự rà lại hệ thống

Cả hai đều trả về `status: ok`. Không exception, không cảnh báo, không có gì trong log.
Chúng chỉ lộ ra khi đi kiểm tra thủ công những cái tên nằm ngoài nhóm quen thuộc.

### A. Phân giải tên doanh nghiệp khớp sai một cách tự tin

```
Hỏi "Acer"    ->  trả về MACERICH CO (MAC)       status: ok, doanh thu đầy đủ
Hỏi "Altera"  ->  trả về ALTRIA GROUP (MO)       status: ok, doanh thu đầy đủ
```

Macerich là quỹ bất động sản trung tâm thương mại. Altria là thuốc lá.

Đây là kiểu hỏng tệ nhất trong cả hệ thống, vì nó **vô hiệu hóa chính nguyên tắc trung
tâm của dự án**. Cả kiến trúc được dựng lên để con số không bao giờ đi qua mô hình ngôn
ngữ — nhưng công sức đó thành vô nghĩa nếu con số đúng bị gắn nhầm tên doanh nghiệp.

Có **hai** nhánh cùng sai, không phải một:

| Nhánh | Ví dụ đo được | Vì sao sai |
|---|---|---|
| `substring` | `acer` ⊂ **Ma**cer**ich** · `asco` ⊂ **M**asco · `ey` ⊂ A**ey**e | Trùng ký tự ngẫu nhiên giữa chừng một từ khác |
| `fuzzy` ngưỡng 0,82 | altera~altria `0,833` · *acacia* communications ~ *saga* communications `0,850` | Từ chung ở đuôi ("communications") kéo điểm lên hộ, phần phân biệt thì khác hẳn |

**Sửa ở ba tầng**, vì siết luật khớp thôi là chưa đủ — bảng mã SEC có hơn 10.000 tên,
sớm muộn vẫn sẽ có ca tình cờ giống nhau:

1. **Luật khớp**: `substring` bị hạ xuống hạng không đáng tin; `fuzzy` nâng ngưỡng lên
   0,88 **và** bắt buộc từ đầu tiên cũng phải giống ≥ 0,80, **và** chuỗi phải dài ≥ 5 ký tự.
2. **Nhãn độ tin cậy**: mỗi ứng viên mang `confidence: high | weak`.
3. **Lớp chặn ở tầng gọi** — quan trọng nhất: `resolve_company()` **từ chối** khớp yếu,
   trả về `not_found` kèm gợi ý. Mọi công cụ đều đi qua nó. Dù luật khớp có sai trong
   tương lai, hệ thống sẽ nói "không chắc" chứ không nói sai một cách tự tin.

Ngưỡng 0,88 không phải số chọn bừa: nó nằm **trên** cả ba ca sai đo được (0,833 / 0,848 /
0,850) và **dưới** các ca gõ sai cần giữ (`microsft`→Microsoft 0,941, `teslla`→Tesla 0,909).

Có một chỗ cố ý **không** nới: `amazn` bị từ chối. Nới đủ để nhận `amazn` thì `azure` sẽ
khớp `Azul` (hãng bay Brazil, độ giống 0,889) — đúng loại lỗi đang sửa. Thay vào đó, lời
từ chối kèm gợi ý để người dùng tự chọn.

Nguy hiểm nhất không phải `lookup_financials` mà là `ensure_text_available`: nó **tải về
và ghi vĩnh viễn** 10-K vào vector store. Khớp nhầm ở đó nghĩa là báo cáo của doanh
nghiệp khác nằm lại trong chỉ mục dưới mã sai, và mọi câu hỏi sau đều lấy nhầm nguồn.

Khóa lại bằng `tests/test_resolver.py`: **16 ca phải từ chối · 30 ca phải vẫn nhận đúng**.
Nhóm thứ hai quan trọng ngang nhóm thứ nhất — sửa lỗi mà làm hỏng chức năng đang chạy thì
không phải là sửa.

### B. `graph_neighbors` bị cạnh hạ tầng nhấn chìm

Đo trước khi sửa, với `limit=12`:

| Thực thể | Cạnh trả về |
|---|---|
| NVIDIA | 12/12 hạ tầng · **0 tri thức** |
| Microsoft | 12/12 hạ tầng · **0 tri thức** |
| Apple | 12/12 hạ tầng · **0 tri thức** |

Công cụ duyệt đồ thị tri thức trả về **không một quan hệ tri thức nào** cho ba doanh
nghiệp được phủ tốt nhất — mà vẫn báo `status: ok`, nên agent tin là đã tra xong và kết
luận chúng không có quan hệ nào trong đồ thị.

Hai nguyên nhân chồng lên nhau:

- Truy vấn khớp **mọi** loại cạnh, kể cả 59.802 cạnh `HAS_FINANCIALS` — nhiều gấp 28 lần
  toàn bộ tri thức trích từ hồ sơ cộng lại.
- `ORDER BY r.confidence DESC` — trong Neo4j, `NULL` được xếp **lên đầu** khi sắp giảm
  dần, mà cạnh hạ tầng thì không có thuộc tính `confidence`. Chúng chiếm sạch 12 chỗ.

Sửa: loại `INFRA_RELATIONS` ngay trong mệnh đề `WHERE`, và đổi sang
`ORDER BY coalesce(r.confidence, 0) DESC` để cạnh thiếu điểm không nhảy lên đầu lần nữa.
Sau khi sửa: **0 hạ tầng + 12 tri thức** cho cả sáu doanh nghiệp đã thử.

---

## Thứ tự pipeline bắt buộc: 04 → 09 → 11

⚠️ **Bước nạp của script 04 ghi lại TOÀN BỘ `triples.jsonl` mỗi lần chạy**, dùng tên thực
thể thô mà mô hình đọc được. Nghĩa là mọi việc dọn dẹp làm trực tiếp trên Neo4j đều bị
xóa sổ ở lần trích xuất kế tiếp.

Đo thật, nạp lại đúng cùng một file hai lần liên tiếp:

| | Node Company | Cạnh tri thức |
|---|---|---|
| sau `04 → 09 → 11` | 6.266 | 2.081 |
| chạy lại mỗi `04` | 6.380 | 3.276 |

Không có lỗi nào báo ra — số liệu chỉ phình lên, và tri thức của một doanh nghiệp bị chia
cho hai node mang tên khác nhau.

**Hai lớp xử lý, hai mức độ bền khác nhau:**

- `config/entity_merges.json` được áp dụng **ngay trong bước nạp** (`src/graph/curation.py`),
  nên nó bền qua mọi lần chạy lại. Đây là chỗ nên đưa mọi quyết định đã duyệt vào.
- `09_resolve_entities.py` gộp thêm ~110 cặp bằng so khớp tự động (`Tesla, Inc` với
  `Tesla, Inc.`). Những cặp này **không** nằm trong file cấu hình nào nên phải chạy lại
  sau mỗi lần nạp. Script 04 giờ in cảnh báo nhắc điều đó ở cuối.

### Một hệ quả dây chuyền đáng ghi lại

Script 11 khi gộp có đổi tên node thành tên đẹp trong đồ thị
(`ARM HOLDINGS PLC /UK` → `Arm Holdings`). Nhưng `triples.jsonl` lại ghi tên nguồn theo
**tên chính thức của SEC**. Hậu quả ở lần trích xuất kế tiếp: một node MỚI mang tên SEC
được dựng ra, và toàn bộ tri thức vừa trích treo lên node mới đó thay vì node đã gộp.

Đo được: node `Zoom` (đã gộp, có CIK) chỉ còn **1 cạnh**, trong khi **104 cạnh** vừa trích
nằm ở node `Zoom Communications, Inc` không có CIK — tức là hỏi "Zoom có quan hệ gì" sẽ
gần như không ra gì, dù dữ liệu vừa được nạp xong.

Cách sửa: đưa mười cặp tên-SEC ↔ tên-đã-gộp vào bảng alias, để việc quy về một mối xảy ra
**ngay tại bước nạp**. Sau khi sửa, `Zoom` có 105 cạnh.

---

## Mở rộng sang doanh nghiệp Việt Nam

Giới hạn lớn nhất từng ghi trong tài liệu này là *"không có doanh nghiệp Việt Nam nào
ngoài VinFast"*. Đó là giới hạn của **nguồn dữ liệu**, không phải của kiến trúc — và
`src/ingest/vietnam.py` chứng minh điều đó: **1.532 doanh nghiệp niêm yết · 11.777 bản
ghi năm · 2018–2025**, dùng lại nguyên vẹn lược đồ Neo4j và bộ công cụ của agent.

Con số 30 trong bản đầu là giới hạn của một **danh sách VN30 viết tay**, không phải của
nguồn: VCI có endpoint trả về toàn bộ vũ trụ trong một lần gọi. Bỏ danh sách đó đi thì độ
phủ nhân lên 51 lần mà không phải sửa dòng nào trong agent.

Kèm theo là **10.701 cạnh sở hữu** (`OWNED_BY`) lấy từ bảng cổ đông — tầng đồ thị cho Việt
Nam, dựng xong trong 9 phút và **không tốn một lần gọi LLM nào**. Chi tiết và bốn lỗi âm
thầm phát hiện trong lúc làm nằm ở `docs/nguon_du_lieu_viet_nam.md`.

```
FPT   FPT Corporation                      70,1 nghìn tỷ VND (2025)
HPG   Hoa Phat Group                      156,1 nghìn tỷ VND
VIC   Vingroup                            331,8 nghìn tỷ VND
VCB   Vietcombank                          72,5 nghìn tỷ VND
```

### Vì sao gọi thẳng API thay vì dùng thư viện `vnstock`

Đã thử. Nó chạy, nhưng ba vấn đề:

1. Kéo theo gói **`vnai` — thu thập `machine_id` và gửi ra ngoài.** Dự án bán điểm "chạy
   hoàn toàn trên máy, không gửi dữ liệu đi đâu"; thêm một gói telemetry là tự mâu thuẫn.
2. Kéo theo matplotlib, seaborn, wordcloud và nâng cấp numpy — bốn thứ dự án không dùng.
3. Bản cộng đồng **giới hạn 4 kỳ báo cáo**. Gọi thẳng API lấy được đủ từ 2018.

Dự án vốn đã gọi thẳng API của SEC bằng `httpx`, nên làm y hệt ở đây là nhất quán.

### Ba cái bẫy gặp khi làm

**Ngân hàng có bộ chỉ tiêu hoàn toàn khác.** Báo cáo ngân hàng dùng mã `isb*` thay vì
`isa*` và bắt đầu từ thu nhập lãi thuần — không có dòng "doanh thu bán hàng" nào. Bỏ qua
thì **13/30 mã VN30**, tức toàn bộ nhóm ngân hàng, trống trơn phần doanh thu. Đã ánh xạ
sang "Tổng thu nhập hoạt động" theo quy ước ngành, và gắn thêm trường `revenue_basis` để
nói rõ đây không cùng khái niệm với doanh thu bán hàng.

**Tên doanh nghiệp không nằm trong báo cáo tài chính.** Bản ghi chỉ có `organCode` và
`ticker`; tên thật nằm ở endpoint gốc `/company/{mã}`, trường `enOrganName`. Bản đầu tiên
lấy sai chỗ nên mọi doanh nghiệp vào đồ thị dưới cái tên là chính mã của nó ("ACB",
"BID") — và vì `upsert` dùng `ON CREATE SET`, chạy lại cũng không sửa được.

**Không được MERGE theo `cik`.** Doanh nghiệp Việt Nam không có CIK, mà trong Neo4j
`MERGE (c:Company {cik: null})` khớp với **bất kỳ** node nào có cik null — toàn bộ 30
doanh nghiệp sẽ dồn vào một node, không có lỗi nào báo ra. Ràng buộc duy nhất trên `cik`
cũng không cứu được vì Neo4j bỏ qua null. Phải có `upsert_vn_companies` MERGE theo mã.

### Va chạm mã giữa hai sàn — không được tự chọn bên nào

Đo được **8/30 mã VN30 trùng mã SEC**, trỏ tới những doanh nghiệp hoàn toàn khác nhau:

| Mã | Việt Nam | Mỹ |
|---|---|---|
| `ACB` | Ngân hàng Á Châu | AURORA CANNABIS |
| `MSN` | Tập đoàn Masan | EMERSON RADIO |
| `PLX` | Petrolimex | Protalix BioTherapeutics |
| `MWG` | Thế Giới Di Động | Multi Ways Holdings |

Ưu tiên cứng bên nào cũng tái tạo đúng lỗi vừa mất công sửa: trả về số liệu đầy đủ của
một doanh nghiệp khác mà không báo gì. Nên khi cả hai cùng khớp, hệ thống trả về
`ambiguous` kèm cả hai lựa chọn để agent hỏi lại.

Kiểm thử còn làm lộ ra rằng va chạm **không chỉ ở mã**: `GAS` (PV GAS) đụng
"GAS TRANSPORTER OF THE SOUTH" qua tiền tố tên, `SAB` (Sabeco) đụng "SAB Biotherapeutics".

### Phạm vi: chỉ tầng số liệu

Không làm tầng văn bản và đồ thị cho Việt Nam, có cân nhắc: báo cáo thường niên Việt Nam
là PDF không có cấu trúc Item cố định, và model nhúng đang dùng (`bge-small-en-v1.5`) chỉ
hiểu tiếng Anh — muốn tìm theo ý nghĩa trên tiếng Việt phải đổi sang `bge-m3` (1.024
chiều thay vì 384), tức là nhúng lại toàn bộ 23.869 đoạn vào một collection khác.

```bash
.venv/Scripts/python.exe scripts/12_load_vietnam_metrics.py                  # chạy thử
.venv/Scripts/python.exe scripts/12_load_vietnam_metrics.py --resume --apply # ghi thật

# Tầng đồ thị cho Việt Nam: quan hệ sở hữu, 0 lần gọi LLM
.venv/Scripts/python.exe scripts/13_load_vietnam_shareholders.py                  # chạy thử
.venv/Scripts/python.exe scripts/13_load_vietnam_shareholders.py --resume --apply # ghi thật
.venv/Scripts/python.exe tests/test_vietnam.py                         # 23 ca kiểm thử
```

---

## Nối tầng đồ thị với tầng số liệu

Trước khi dọn, đồ thị tri thức và tầng số liệu là **hai thế giới rời nhau**: node
"Arm Holdings" (38 cạnh tri thức, không CIK) và node ARM (12 năm số liệu) là hai thực thể
khác nhau trong cùng một cơ sở dữ liệu. Hỏi quan hệ thì được, hỏi doanh thu thì không.

Đo được **181 node Company không có CIK**. Bộ phân giải đề xuất 27 cặp có thể gộp — nhưng
đối chiếu với câu văn gốc thì **6 cặp sai hẳn và 2 cặp không đủ chắc**:

| Node | Bộ phân giải đề xuất | Bằng chứng nói gì |
|---|---|---|
| `Celestial` | Hain Celestial (thực phẩm hữu cơ) | Marvell mua lại, sản phẩm "Photonic Fabric" → **Celestial AI** |
| `GF` | New Germany Fund (quỹ đầu tư) | hợp đồng cung ứng wafer với AMD → **GlobalFoundries** |
| `HPI` | John Hancock Preferred Income Fund | nằm trong danh sách "HPE, HPI, IBM, Lenovo" → **HP Inc.** |
| `ESMC` | Escalon Medical | "ESMC, công ty con của chúng tôi ở Đức" → **liên doanh của TSMC** |

**Không có luật hình thức nào tách được đúng khỏi sai ở đây.** `IBM → International
Business Machines` và `GF → New Germany Fund` đều là khớp mã chứng khoán chính xác; khác
biệt nằm ở ngữ cảnh câu văn. Nên việc gộp phải do người duyệt, và script chỉ gộp những gì
có trong `config/entity_merges.json` — mỗi dòng kèm căn cứ.

Kết quả: **11 cặp gộp vào doanh nghiệp SEC · 10 biến thể trùng · 13 node nhiễu bị xóa**
(tổ chức từ thiện trong mục cộng đồng, đại lý chuyển nhượng cổ phiếu, pháp nhân trung
gian) và 2 vòng tự nối.

Phần lớn 170 node còn lại **không phải nhiễu** mà là doanh nghiệp thật không niêm yết ở
Mỹ: Samsung Electronics (13 cạnh), Huawei (6), Lenovo, MediaTek, SMIC, Tokyo Electron,
Bosch, OpenAI. Xóa chúng là phá hủy tri thức thật.

```bash
.venv/Scripts/python.exe scripts/10_review_graph_entities.py     # duyệt, kèm câu văn gốc
.venv/Scripts/python.exe scripts/11_merge_graph_entities.py      # chạy thử
.venv/Scripts/python.exe scripts/11_merge_graph_entities.py --apply
```

---

## Kết quả đánh giá

Bộ 37 câu hỏi, model `gemma-4-26b-a4b-qat` chạy local:

| Nhóm câu hỏi | Số câu | Độ chính xác số | Recall thực thể |
|---|---|---|---|
| Tra số liệu | 24 | **100%** | 96% |
| So sánh doanh nghiệp | 2 | **100%** | 100% |
| Sàng lọc toàn thị trường | 3 | — | 100% |
| Định tính (văn bản) | 5 | — | 100% |
| Bắc cầu (đồ thị) | 3 | — | 100% |

**Độ chính xác số liệu tổng thể: 26/26 = 100%** (sai số cho phép 1%).

Con số này có ý nghĩa vì nó được chấm **không dùng LLM giám khảo** — chỉ dò xem con số
doanh nghiệp khai với SEC có xuất hiện trong câu trả lời hay không.

### Tối ưu độ trễ: từ 275 giây xuống 78 giây

Một câu hỏi bắc cầu ban đầu mất 275 giây. Phân rã ra thì **truy vấn cơ sở dữ liệu chỉ
tốn 0,5 giây** — 99,7% thời gian là gọi LLM.

| Câu hỏi | Trước | Sau | |
|---|---|---|---|
| Tra số liệu | 87s | **29s** | −66% |
| Bắc cầu (đồ thị) | 275s | **78s** | −72% |
| Sàng lọc | 250s | **60s** | −76% |

Ba thay đổi, xếp theo mức đóng góp:

**1. Tắt bước suy nghĩ ở khối trả lời.** Đây là đòn bẩy lớn nhất, và nó lật ngược giả
định ban đầu của tôi. Đo một lần gọi trực tiếp:

```
không gửi tham số      27,1s | 314 token sinh ra, 276 trong đó là token SUY NGHĨ
reasoning_effort=none   4,5s |  41 token, 0 suy nghĩ
```

Model tiêu **88% ngân sách token để tự lẩm bẩm**. Đặt hai câu trả lời cạnh nhau thì bản
không suy nghĩ còn trình bày tốt hơn — tự lập bảng markdown, phép tính y hệt.

Lưu ý quan trọng: `minimal` và `low` **không giảm** suy nghĩ với model này (333 và 312
token, gần bằng mặc định). Chỉ `none` mới thực sự tắt.

Đây cũng là lời giải cho biến động **3,4 lần trên cùng một câu hỏi** (128s/175s/440s/164s):
khi ngữ cảnh lớn, phần suy nghĩ ăn hết `max_tokens` rồi bị cắt trước khi kịp viết, buộc
tầng dưới gọi lại.

**2. Suy xét là đường ngoại lệ, không phải đường mặc định.** Thiết kế ban đầu cho mọi câu
đi qua khối suy xét. Đo được: 11 giây chỉ để kết luận "đủ rồi" cho câu mà công cụ đã trả
`ok` ngay vòng đầu. Giờ chỉ chạy khi có công cụ lỗi hoặc không tìm thấy dữ liệu.

**3. `parallel 1` thay vì 4 trong LM Studio.** Nạp prompt 12.036 ký tự: 50,1s → 33,1s.

### Một chẩn đoán sai đáng ghi lại

Tôi kết luận "model tràn khỏi VRAM". Lấy mẫu GPU trong lúc chạy thật cho thấy ngược lại:

```
VRAM        15.839 / 16.303 MiB   model nằm TRỌN trên GPU
Utilization 98-99%                chạy hết công suất
Power       68,7 W                rất thấp (card ~300W)
```

98% bận nhưng chỉ ăn 69W nghĩa là **nghẽn băng thông bộ nhớ**, không nghẽn tính toán —
đặc trưng cố hữu của kiến trúc MoE, không phải lỗi cấu hình. Bài học: đừng kết luận
nguyên nhân phần cứng khi chưa lấy mẫu đúng lúc tải.

### Năm lần chạy, năm nhóm lỗi

Không lần nào là lãng phí — mỗi lần lộ ra lỗi thật mà đọc code không thấy được:

| Lần | Kết quả | Lỗi phát hiện |
|---|---|---|
| 1 | 91,3% | Bộ chấm mù số âm · **`MAX_ROUNDS` không có tác dụng, agent lặp tới giới hạn đệ quy** · model tiêu hết token cho phần suy nghĩ |
| 2 | 100% | **Bộ lọc `items` sai kiểu (số nguyên vs chuỗi) làm chết toàn bộ 5 câu định tính** |
| 3 | 100% | `find_entity` trả về Product thay vì Company · thiếu form 20-F và chuẩn IFRS |
| 4 | 0% (sàng lọc) | **Trộn đồng tiền: Ecopetrol (peso) xếp trên Walmart** |
| 5 | 100% | — |
| 6 | 96,2% → **100%** | Dấu ngoặc chú thích bị đọc thành số âm (lỗi bộ chấm, agent đúng) |

Điểm chung của cả năm: **không lỗi nào ném ra ngoại lệ**. Hệ thống vẫn chạy, vẫn trả lời,
chỉ là trả lời sai — hoặc trả lời "không tìm thấy" về dữ liệu nằm ngay trong index.

## Đánh giá: hai thước đo, không phải một

Nếu chỉ báo cáo điểm RAGAS, câu hỏi đầu tiên của hội đồng sẽ là *"giám khảo là model
nào?"* — và câu trả lời "chính model đang được đánh giá" làm suy yếu toàn bộ kết luận.
Dùng công cụ đang cần đánh giá để tự chấm mình là một lỗi phương pháp luận.

Vì vậy dự án dùng **hai thước đo độc lập**:

| Thước đo | Số câu | Cần LLM giám khảo? | Đo cái gì |
|---|---|---|---|
| **Dò số** | 23 | **Không** | Câu trả lời có chứa đúng con số doanh nghiệp khai với SEC không (sai số 1%) |
| RAGAS | 11 | Có | faithfulness, answer_relevancy, context_precision/recall |

Câu hỏi tra số được **sinh tự động từ chính dữ liệu XBRL**, nên đáp án chuẩn là con số
chính xác chứ không phải đoạn văn tham chiếu viết tay — muốn bao nhiêu câu cũng có, và
không ai tranh cãi được kết quả. Điểm RAGAS chỉ nên dùng để **so sánh giữa các cấu
hình** (có đồ thị / không đồ thị, top_k khác nhau), không đọc như đánh giá tuyệt đối.

Bộ dò số nhận được sáu cách viết khác nhau của cùng một con số — `215.938.000.000`,
`215,938,000,000`, `215,94 tỷ`, `215.94 billion`, `khoảng 216 tỷ`, và cả dạng thiếu dấu
`215,94 ty` mà model local hay trả về. Bỏ sót một dạng là đánh trượt câu trả lời đúng và
tạo ra điểm số bi quan sai lệch, còn tệ hơn không đo.

## Cấu trúc mã nguồn

```
config/settings.py          Cấu hình tập trung, đọc từ .env
src/ingest/
    edgar.py                Tải 10-K + XBRL, tuân thủ rate limit của SEC
    universe.py             Quản lý vũ trụ doanh nghiệp và ba mức phủ
    parser.py               HTML -> văn bản -> chia theo Item
    chunker.py              Hai cách cắt chunk cho hai mục đích
    xbrl.py                 Trích xuất số liệu chính xác + xử lý ba cái bẫy ở trên
    on_demand.py            Phân giải tên công ty + nạp dữ liệu ngay khi cần
    pipeline.py             Ghép các bước trên thành một đường đi chung
src/vector/store.py         Qdrant + fastembed (CPU đa nhân, không tranh VRAM)
src/graph/
    schema.py               Ontology đóng + chuẩn hóa thực thể
    selector.py             Lọc & xếp hạng chunk trước khi gọi LLM (giảm 83% chi phí)
    extractor.py            Prompt trích xuất + kiểm tra kết quả
    store.py                Neo4j: nạp dữ liệu và các truy vấn cho agent
src/agent/
    tools.py                7 công cụ, Cypher viết sẵn và tham số hóa (không để LLM sinh)
    graph_agent.py          Sơ đồ trạng thái LangGraph: định tuyến -> thực thi -> suy xét
src/eval/
    testset.py              Sinh câu hỏi từ dữ liệu thật + câu hỏi định tính viết tay
    grader.py               Chấm dò số, xác định, không dùng LLM
    ragas_runner.py         RAGAS với model local (embedding chạy CPU, một luồng)
src/llm/client.py           LM Studio qua API tương thích OpenAI, ép JSON schema
web/server.py               FastAPI: API JSON + phục vụ trang tĩnh + streaming SSE
web/static/index.html       Trang chủ: agent biết gì, hỏi được gì, hoạt động ra sao
web/static/chat.html        Trang trò chuyện, không có gì ngoài hội thoại
web/static/styles.css       Hệ thiết kế dùng chung: màu, nút, chuyển động
web/static/chat.css         Riêng cho trang trò chuyện
web/static/home.js          Hiện dần khi cuộn, đếm số, đổ số liệu thật vào trang
web/static/chat.js          Đọc SSE, dựng Markdown, gấp dấu vết agent lại
src/graph/curation.py       Áp bảng dọn thực thể NGAY TẠI bước nạp (bền qua chạy lại)
src/ingest/vietnam.py       Tầng số liệu doanh nghiệp Việt Nam, gọi thẳng API VCI
config/entity_merges.json   Danh sách gộp/xóa node đồ thị — DUYỆT BẰNG TAY
tests/test_resolver.py      Hồi quy bộ phân giải tên: 16 ca từ chối, 30 ca nhận đúng
tests/test_vietnam.py       Tầng Việt Nam: nhận đúng, báo nhập nhằng, không lẫn tiền tệ
run_web.py                  Kiểm tra phụ thuộc rồi khởi động máy chủ
app/streamlit_app.py        (cũ) Giao diện Streamlit — giữ lại để gỡ lỗi, xem mục Giao diện web
scripts/                    Các bước chạy, đánh số theo thứ tự
```

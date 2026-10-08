# Agentic GraphRAG cho Phân tích Doanh nghiệp & Đầu tư

**Tài liệu chuẩn bị buổi gặp thầy hướng dẫn — ĐATN 1**
Cập nhật: 02/10/2026 · Mã nguồn: ~20.500 dòng, 29 script pipeline

---

## 0. Cách dùng tài liệu này

Đây **không phải** bản thuyết trình để đọc nguyên văn. Nó là thứ để bạn nắm chắc dự án
trước khi vào phòng, gồm ba phần:

- **Phần 1–4**: nói gì trong 5 phút đầu. Đọc thuộc ý, đừng thuộc câu.
- **Phần 5–7**: số liệu và kỹ thuật, dùng khi thầy hỏi sâu.
- **Phần 8**: *mười câu thầy gần như chắc chắn sẽ hỏi*, kèm câu trả lời. Phần này quan
  trọng nhất — buổi đầu thường thành hay không nằm ở đây.

> **Một điều phải nói thật ngay từ buổi đầu:** dự án đã có bản chạy được, không phải ý
> tưởng trên giấy. Đừng che chỗ này, nhưng cũng đừng để nó thành "em làm xong rồi" —
> xem [§8, câu 1](#câu-1-em-làm-gần-xong-rồi-thì-còn-gì-để-làm-đatn).

---

## 1. Vấn đề — nói trong 60 giây

Người phân tích đầu tư phải trả lời những câu hỏi mà **không công cụ nào trả lời được
trọn vẹn**:

| Câu hỏi | Công cụ hiện có làm được không? |
|---|---|
| "Doanh thu FPT 2025 là bao nhiêu?" | ✅ Website tài chính nào cũng có |
| "FPT nêu rủi ro gì trong báo cáo thường niên?" | ⚠️ Phải tự mở PDF 200 trang đọc |
| "FPT mạnh hay yếu so với ngành?" | ⚠️ Phải tự tải số của 24 doanh nghiệp rồi tính |
| "**Ai thực sự kiểm soát Mộc Châu Milk?**" | ❌ Không công cụ nào |
| "Nếu TSMC gián đoạn thì Microsoft bị ảnh hưởng qua đâu?" | ❌ Không công cụ nào |

Hai câu cuối là loại cần **lần theo chuỗi quan hệ nhiều tầng** — việc mà bảng tính và
tìm kiếm toàn văn đều không làm được.

Và khi dùng chatbot AI thông thường để hỏi những câu này thì gặp vấn đề nặng hơn:
**nó bịa số, mà bịa rất thuyết phục.** Một con số sai trong báo cáo đầu tư không phải
lỗi nhỏ — nó là cơ sở cho một quyết định tiền thật.

---

## 2. Ý tưởng — nói trong 90 giây

Xây một **agent** (không phải chatbot) trả lời câu hỏi phân tích doanh nghiệp, dựa trên
**ba tầng dữ liệu** và có **lớp kiểm chứng từng con số**.

### 2.1. Ba tầng dữ liệu, ba loại câu hỏi

```
┌─────────────────────────────────────────────────────────────┐
│  TẦNG SỐ LIỆU   Neo4j · 59.802 bản ghi năm tài chính        │
│                 → "doanh thu bao nhiêu"                     │
├─────────────────────────────────────────────────────────────┤
│  TẦNG VĂN BẢN   Qdrant · 75.610 đoạn đã nhúng vector        │
│                 → "doanh nghiệp tự nói gì về rủi ro"        │
├─────────────────────────────────────────────────────────────┤
│  TẦNG ĐỒ THỊ    Neo4j · 15 loại quan hệ, 10.701 cạnh sở hữu │
│                 → "ai liên quan tới ai, qua mấy tầng"       │
└─────────────────────────────────────────────────────────────┘
```

**Đây là chỗ "GraphRAG" khác "RAG".** RAG thuần chỉ có tầng giữa: nó tìm đoạn văn giống
câu hỏi. Nếu không đoạn nào nhắc cả TSMC lẫn Microsoft cùng lúc thì nó bó tay. Đồ thị
thì đi qua các mắt xích trung gian để dựng lại chuỗi liên kết.

### 2.2. Agent, không phải chatbot

Khác biệt nằm ở chỗ **nó tự quyết định dùng công cụ nào**:

```
Câu hỏi → [Định tuyến] chọn công cụ → [Thực thi] gọi Cypher/vector
                ↑                              ↓
                └──── [Suy xét] đủ chưa? ──────┘
                                               ↓
                                        [Trả lời] + KIỂM SỐ
```

Hiện có **12 công cụ**, mỗi công cụ là một truy vấn viết sẵn và tham số hóa. Mô hình
**chỉ chọn công cụ và điền tham số**, không bao giờ tự sinh câu truy vấn — nếu để nó
sinh Cypher thì vừa có nguy cơ sai logic, vừa mở đường cho injection.

### 2.3. Lớp kiểm chứng số — điểm cốt lõi của đề tài

Sau khi mô hình viết xong câu trả lời, hệ thống **bóc từng con số ra và đối chiếu với dữ
liệu công cụ vừa trả về**. Số nào không truy được về nguồn thì bị đánh dấu.

Con số này đo được: **60/60 = 100% độ chính xác số liệu** trên 80 câu hỏi.

---

## 3. Năm chức năng vượt ra ngoài hỏi–đáp

Hỏi–đáp đặt toàn bộ gánh nặng lên người dùng: họ phải **biết trước cần hỏi gì**. Năm
chức năng sau đảo lại — agent tự chạy một chuỗi bước đã định sẵn.

| Chức năng | Làm gì | Cái khó thật sự |
|---|---|---|
| **Hồ sơ phân tích tự động** | Đưa một tên → 7 bước thu thập → hồ sơ 7 mục | Mục cuối *"Điều hệ thống KHÔNG biết"* sinh từ chính dữ liệu thiếu |
| **Theo dõi & cảnh báo** | Chụp ảnh nền dữ liệu, báo cái gì đã đổi | Phát hiện **số cũ bị sửa** — doanh nghiệp khai lại, không gì khác báo ra |
| **Mạng lưới sở hữu** | Lần chuỗi sở hữu nhiều tầng | Phân biệt **quyền lợi kinh tế** vs **quyền kiểm soát** (xem §4) |
| **So sánh ngành tự động** | Tự tìm nhóm cùng loại rồi xếp hạng | Không trộn đồng tiền, mẫu số riêng từng chỉ tiêu |
| **Gợi ý đầu tư** (chỉ DN Việt Nam) | Nên mua / Theo dõi / Tránh từ bảng điểm 7 nhóm, gồm cả DN Mỹ cùng ngành và vĩ mô thế giới | "Không biết" không được giả làm "trung tính"; cảnh báo do mã lệnh chèn, không do AI viết |

Nguyên tắc chung cho cả năm, và **đây là câu nên nói với thầy**:

> **Mã lệnh quyết định chạy gì và tính mọi con số. LLM chỉ viết lời — và lời đó vẫn phải
> qua lớp kiểm chứng.**

Vì sao: ở hỏi–đáp, câu hỏi có thể là bất cứ thứ gì nên phải để mô hình chọn. Ở đây câu
hỏi luôn cố định ("doanh nghiệp này thế nào", "ai đứng sau nó"), nên danh sách việc làm
viết sẵn được. Để mô hình tự chọn chỉ thêm một chỗ hỏng mà không thêm khả năng nào.

---

## 4. Ví dụ đắt giá nhất để kể cho thầy

Nếu chỉ được kể **một** ví dụ, kể cái này. Nó cho thấy đề tài có nội dung tài chính thật,
không chỉ là ghép công nghệ.

### Câu hỏi: "Vinamilk kiểm soát Mộc Châu Milk ở mức nào?"

Dữ liệu trong đồ thị:

```
Vinamilk ──68,94%──> Vilico ──59,3%──> Mộc Châu Milk
Vinamilk ──────────8,85%────────────> Mộc Châu Milk
```

**Quyền lợi kinh tế** = tích các tỷ lệ = 68,94% × 59,3% = **40,88%**, cộng 8,85% trực
tiếp → **49,73%**. Đây là phần lãi Vinamilk thực nhận.

**Quyền kiểm soát** thì khác hẳn: Vinamilk nắm đa số ở Vilico, Vilico nắm đa số ở Mộc
Châu → **Vinamilk kiểm soát Mộc Châu hoàn toàn**, dù quyền lợi kinh tế chưa tới 50%.

> Nhân phần trăm rồi gọi kết quả là "mức độ kiểm soát" là sai lầm kinh điển, và nó sai
> theo hướng nguy hiểm: **làm một quan hệ chi phối tuyệt đối trông như khoản đầu tư nhỏ.**

Cùng qua Vilico nhưng sang Lâm Đồng Foodstuffs (38,3%) thì 26,4% và **không** kiểm soát.
Con số đơn thuần không phân biệt được hai trường hợp này.

### Và đây là phần có giá trị học thuật

Lần đầu hỏi, agent trả lời **"Vinamilk không kiểm soát"** — sai hoàn toàn. Nó đọc cờ
`false` của chuỗi trực tiếp 8,85% trong **14 chuỗi** trả về, bỏ qua chuỗi qua Vilico có
`true`. Kết quả đã mang sẵn một dòng nhắc đúng việc đó, và mô hình vẫn bỏ qua.

Phải sửa **ba bậc** mới đúng:

1. Thêm truy vấn tìm chuỗi giữa hai doanh nghiệp (trước đó chỉ tìm cổ đông chung)
2. Đổi tên trường cho rõ phạm vi + thêm câu kết luận viết sẵn
3. **Tách hẳn một danh sách chỉ chứa chuỗi kiểm soát**

Chỉ sau bậc 3 mới đúng. Kết luận rút ra:

> **Lời nhắc trong prompt là thứ mô hình có thể bỏ qua; một trường dữ liệu chỉ chứa kết
> luận thì không.** Khi 12/14 chuỗi là nhiễu, việc lọc phải do mã lệnh làm — không phải
> giao cho mô hình rồi dặn nó cẩn thận.

Đây là một **phát hiện có thể viết thành một mục trong báo cáo**, không phải chỉ là một
lần sửa lỗi.

---

## 5. Số liệu để trả lời "em đã làm được gì"

### 5.1. Quy mô dữ liệu (đếm thật, không ước)

| | Số lượng |
|---|---:|
| Doanh nghiệp trong đồ thị | **7.606** (6.074 Mỹ + 1.532 Việt Nam) |
| Bản ghi năm tài chính | **59.802** |
| Cạnh quan hệ sở hữu | **10.701** |
| Đoạn văn bản đã nhúng vector | **75.610** |
| → 10-K (tiếng Anh) | 23.869 |
| → Báo cáo thường niên Việt Nam | 50.214 |
| → Mô tả doanh nghiệp Việt Nam | 1.527 |
| Loại thực thể / loại quan hệ | 8 / 15 |

### 5.2. Kết quả đánh giá

**80 câu hỏi** (37 Mỹ + 43 Việt Nam), chạy hết **10 phút 13 giây**:

| Nhóm | Số câu | Chính xác số | Recall thực thể |
|---|---:|---:|---:|
| Tra số liệu (Mỹ) | 24 | **100%** | 75% |
| Tra số liệu (Việt Nam) | 30 | **100%** | 100% |
| So sánh doanh nghiệp | 2 | **100%** | 100% |
| Phải từ chối trả lời | 3 | **100%** | — |
| Phải cảnh báo khác đồng tiền | 1 | **100%** | — |
| Định tính / sàng lọc / bắc cầu / sở hữu | 20 | — | 100% |
| **Tổng độ chính xác số liệu** | **60/60** | **100%** | |

**235 ca kiểm thử** tự động. Sao lưu đã **kiểm chứng khôi phục** 10/10 chỉ tiêu.

### 5.3. Chạy trên máy cá nhân, không gọi API trả tiền

Toàn bộ mô hình chạy **local**: `gemma-4-26b` qua LM Studio trên GPU 16GB, nhúng vector
chạy CPU đa nhân (ONNX) để không tranh VRAM. Hai cơ sở dữ liệu chạy Docker.

Điểm này đáng nhấn: **chi phí vận hành bằng 0** và **dữ liệu không ra khỏi máy** — quan
trọng với bài toán tài chính.

---

## 6. Kiến trúc và công nghệ

```
Nguồn dữ liệu            Xử lý                    Lưu trữ           Truy vấn
─────────────────────────────────────────────────────────────────────────────
SEC EDGAR (XBRL, 10-K) ─┐
VCI API (số liệu VN)   ─┤                      ┌─ Neo4j ──────┐
VietStock (BCTN PDF)   ─┼─ pypdfium2 ──┐       │  số liệu     │   12 công cụ
Trang IR (Playwright)  ─┤   Tesseract  ├──────>│  đồ thị      ├─> LangGraph
                        └─ LLM trích ──┘       └──────────────┘    state machine
                           quan hệ             ┌─ Qdrant ─────┐         │
                                               │  vector      │   lớp kiểm số
                                               └──────────────┘         │
                                                                   FastAPI + web
```

| Thành phần | Chọn gì | Vì sao |
|---|---|---|
| Đồ thị | **Neo4j 5.26** | Cypher diễn đạt truy vấn nhiều tầng tự nhiên |
| Vector | **Qdrant 1.12** | Lọc theo metadata tốt, chạy Docker nhẹ |
| Agent | **LangGraph** | Cần vòng lặp suy xét, không chỉ chuỗi tuyến tính |
| LLM | **Gemma 4 26B** (local) | Vừa GPU 16GB, miễn phí, dữ liệu không ra ngoài |
| Nhúng EN / VN | `bge-small-en` / `paraphrase-multilingual-MiniLM` | Kho tiếng Việt cần model đa ngữ riêng |
| OCR | **Tesseract** `tessdata_best` | Có báo cáo chỉ là bản scan |
| Web | **FastAPI** | Stream được từng bước agent (Streamlit không làm được) |

---

## 7. Đề xuất phạm vi: ĐATN 1 và ĐATN 2

Đây là phần nên **để thầy chốt**, nhưng vào phòng có sẵn đề xuất thì luôn tốt hơn.

### ĐATN 1 — Nền tảng và kiểm chứng *(phần lớn đã có bản chạy được)*

1. Khảo sát: RAG, GraphRAG, agent tool-calling, các công cụ phân tích hiện có
2. Thiết kế kiến trúc ba tầng + ontology (8 thực thể, 15 quan hệ)
3. Pipeline thu thập đa nguồn (SEC, VCI, VietStock, OCR)
4. Agent LangGraph + 12 công cụ
5. **Lớp kiểm chứng số và bộ đánh giá** — phần đóng góp chính
6. Báo cáo: kiến trúc, phương pháp, kết quả đo

### ĐATN 2 — Mở rộng và đào sâu *(hướng đề xuất)*

| Hướng | Nội dung | Vì sao đáng làm |
|---|---|---|
| **Đánh giá nghiêm hơn** | Mở rộng bộ câu hỏi, so sánh RAG thuần vs GraphRAG trên cùng bộ đo | Hiện chưa có đối chứng định lượng — đây là lỗ hổng lớn nhất |
| **Phân tích mạng lưới sâu** | Phát hiện cụm sở hữu chéo, truy vết chủ sở hữu cuối | Khai thác đúng thế mạnh của đồ thị |
| **Chuỗi suy luận nhiều bước** | Câu hỏi cần 4–5 bước, có kiểm chứng từng bước | Hiện tối đa 3 công cụ/vòng |
| **Triển khai & đo độ trễ** | Deploy, cache, đo p95 | Chưa làm gì phần này |

---

## 8. Mười câu thầy sẽ hỏi

### Câu 1: "Em làm gần xong rồi thì còn gì để làm ĐATN?"

**Đây là câu khó nhất và gần như chắc chắn sẽ bị hỏi.** Trả lời thật:

> "Em đã có bản chạy được, nhưng phần *hệ thống* xong không có nghĩa phần *đồ án* xong.
> Ba thứ còn thiếu hẳn:
>
> 1. **Chưa có đối chứng định lượng.** Em đo được agent đạt 100% độ chính xác số liệu,
>    nhưng chưa đo RAG thuần trên cùng bộ câu hỏi. Nên hiện em *chưa chứng minh được*
>    tầng đồ thị đóng góp bao nhiêu — đó là luận điểm trung tâm mà chưa có số.
> 2. **Chưa khảo sát công trình liên quan.** Em xây từ vấn đề thực tế, chưa đặt vào bối
>    cảnh GraphRAG của Microsoft hay các hệ thống QA tài chính đã công bố.
> 3. **Chưa triển khai và chưa đo độ trễ có hệ thống.**
>
> Em nghĩ việc đã làm là *bằng chứng khả thi*, giúp ĐATN không phải mất nửa thời gian
> dựng hạ tầng mà dùng được cho phần đo đạc và phân tích."

Câu trả lời này mạnh vì nó **tự nêu điểm yếu trước khi thầy nêu**.

### Câu 2: "Khác gì ChatGPT hỏi về doanh nghiệp?"

Ba điểm, nói theo thứ tự này:

1. **Mọi con số đều truy được về nguồn.** Lớp kiểm chứng bóc từng số ra đối chiếu với
   dữ liệu công cụ vừa trả về. ChatGPT không có nguồn để đối chiếu.
2. **Trả lời được câu hỏi nhiều tầng.** "Ai kiểm soát Mộc Châu Milk" cần lần chuỗi sở
   hữu, không phải nhớ một đoạn văn.
3. **Nói thật khi không biết.** Hệ thống phân biệt ba trạng thái mà bình thường bị gộp
   thành một: *không có dữ liệu* / *tên mơ hồ, phải hỏi lại* / *cơ sở dữ liệu đang chết*.

### Câu 3: "Khác gì GraphRAG của Microsoft?"

> "GraphRAG của Microsoft xây đồ thị **từ chính văn bản** rồi tóm tắt theo cụm — mạnh với
> câu hỏi tổng quát trên một tập tài liệu. Hệ của em khác ở hai chỗ:
>
> - **Đồ thị của em có cả phần dữ liệu CÓ CẤU TRÚC** (10.701 cạnh sở hữu từ nguồn chính
>   thức, 59.802 bản ghi số liệu), không chỉ quan hệ do LLM trích. Nhờ vậy nó trả lời
>   được câu hỏi về con số chính xác, việc GraphRAG thuần văn bản không làm được.
> - **Em có lớp kiểm chứng số sau khi sinh câu trả lời.** Đây là phần em chưa thấy trong
>   các công bố đã đọc, và cũng là phần em muốn làm trọng tâm."

*(Nếu thầy hỏi kỹ hơn và bạn chưa đọc bài gốc — nói thật là chưa đọc kỹ, sẽ khảo sát
trong ĐATN. Đừng bịa.)*

### Câu 4: "Dữ liệu lấy ở đâu, có vi phạm bản quyền không?"

- **SEC EDGAR**: dữ liệu công cộng, Mỹ. Có chính sách Fair Access yêu cầu khai thông tin
  liên hệ trong `User-Agent` — đã tuân thủ và có giới hạn tần suất.
- **VCI**: API công khai của công ty chứng khoán.
- **VietStock / trang quan hệ cổ đông**: báo cáo thường niên là tài liệu **doanh nghiệp
  bắt buộc công bố**.
- Mã nguồn để giấy phép **MIT**, kèm mục nói rõ **giấy phép chỉ phủ mã, không phủ dữ
  liệu**.

### Câu 5: "100% thì có đáng tin không? Nghe như đo dễ quá."

**Đừng bảo vệ con số — hãy nói rõ nó đo gì và không đo gì.** Đây là câu trả lời trung thực:

> "100% này là **độ chính xác số liệu**, chấm bằng cách dò xem con số doanh nghiệp đã khai
> có xuất hiện trong câu trả lời không — **không dùng LLM làm giám khảo**. Nó đo đúng một
> việc: hệ thống có bịa số hay không.
>
> Nó **không** đo chất lượng diễn giải, không đo câu trả lời có hữu ích hay không. Và câu
> hỏi do em sinh ra từ chính dữ liệu, nên nó thiên về loại câu hệ thống làm tốt.
>
> Một điều nữa: lần chấm đầu ra **98,3%**, và em phát hiện câu 'sai' là **lỗi của thước
> đo**, không phải của agent — thước đo coi mọi con số trong một câu từ chối là số bịa,
> kể cả số lịch sử gắn đúng năm và đúng nguồn. Em đã sửa thước đo và thêm ca kiểm thử để
> nó không nới lỏng."

Rồi chốt bằng bài học:

> **"Một thước đo phạt hành vi ĐÚNG thì tệ hơn không có thước đo nào — rồi sẽ có người
> tắt nó đi, và mất luôn cả phần nó đo đúng."**

Đây **là** câu trả lời mạnh: nó cho thấy bạn hiểu giới hạn của chính phép đo mình dùng.
Và đó là lỗi lặp lần thứ hai — lần đầu thước đo chấm 0 cho câu trả lời viết "SeABank"
thay vì mã "SSB".

### Câu 6: "Phần Việt Nam có gì riêng, hay chỉ dịch lại?"

Bốn thứ **chỉ phát sinh khi làm dữ liệu Việt Nam**:

1. **Mã trùng giữa hai sàn.** `ACB` vừa là Aurora Cannabis (Mỹ) vừa là Ngân hàng Á Châu.
   Hệ thống **từ chối tự chọn** và hỏi lại — không đoán.
2. **Không trộn đồng tiền.** Doanh thu VND đặt cạnh USD trong một bảng xếp hạng cho ra
   thứ tự hoàn toàn bịa. Hệ thống từ chối so sánh và nói rõ vì sao.
3. **Báo cáo bản scan.** Có báo cáo không có lớp chữ → phải OCR, và đoạn nào do OCR thì
   **được đánh dấu trong câu trả lời**.
4. **Model nhúng riêng.** Kho tiếng Việt dùng model đa ngữ, giới hạn 128 token nên phải
   cắt đoạn 320 ký tự. Dùng nhầm model thì Qdrant **vẫn trả kết quả** (cùng 384 chiều) —
   chỉ là kết quả vô nghĩa, không lỗi nào báo ra.

Điểm 4 là ví dụ tốt cho câu "lỗi im lặng" nếu thầy muốn nghe chi tiết kỹ thuật.

### Câu 7: "Em dùng mô hình gì? Có cần GPU xịn không?"

> "`gemma-4-26b` lượng tử hóa, chạy local trên GPU 16GB qua LM Studio. Nhúng vector chạy
> CPU bằng ONNX để không tranh VRAM với LLM. Chi phí vận hành bằng 0 và dữ liệu không ra
> khỏi máy."

Nếu thầy hỏi độ trễ: câu tra số liệu **4–6 giây**, câu định tính 10–18 giây, hồ sơ đầy đủ
~20 giây. Đã tối ưu từ 275 giây xuống 78 giây cho câu bắc cầu — và **99,7% thời gian là
gọi LLM, truy vấn cơ sở dữ liệu chỉ 0,5 giây**.

### Câu 8: "Hệ thống có đưa ra khuyến nghị đầu tư không?"

**Có, cho doanh nghiệp Việt Nam — ba mức Nên mua / Theo dõi / Tránh — nhưng theo một
phương pháp công bố đầy đủ, và luôn kèm cảnh báo.** Nói theo thứ tự này:

1. **Mức gợi ý do bảng chấm điểm quyết định, không do AI "cảm thấy".** Bảy nhóm yếu tố
   với trọng số viết sẵn: tăng trưởng 20, sinh lời (kèm phân vị trong ngành) 20, sức khỏe
   tài chính 15, chất lượng lợi nhuận 10, ổn định 10, **doanh nghiệp Mỹ cùng ngành 10**,
   **vĩ mô thế giới (FRED) 15**. Ngưỡng ≥70 Nên mua, 45–70 Theo dõi, <45 Tránh.
2. **Câu cảnh báo do mã lệnh chèn**, không giao cho AI tự viết: *"Đây là gợi ý do AI đưa
   ra, cần cân nhắc kỹ trước khi thực hiện theo"* — kèm vế nói rõ mức gợi ý **không dựa
   trên giá cổ phiếu**.
3. **Biết nói "không đủ dữ liệu".** Nhóm thiếu dữ liệu bị loại chứ không cho 50 điểm
   trung tính; tính được dưới 70% trọng số thì không xếp mức nào.
4. **Hai chốt chặn cứng:** đang lỗ hoặc vốn chủ âm thì không bao giờ "Nên mua", dù vĩ mô
   thuận lợi tới đâu.

Nếu thầy hỏi tiếp về **giấy phép tư vấn đầu tư**: đây là công cụ nghiên cứu cho đồ án,
phương pháp minh bạch để kiểm chứng và phản biện, không phục vụ khách hàng thật.

Nếu thầy hỏi **"mô hình đúng không?"** — trả lời thật: *chưa kiểm chứng được tính đúng
của khuyến nghị*, vì muốn biết phải theo dõi nhiều năm và tách được may mắn. Thứ đã kiểm
chứng được là **tính nhất quán** (một doanh nghiệp được chấm cùng một điểm dù hỏi riêng
hay nằm trong danh sách) và **các chốt chặn** (có ca kiểm thử tự động cho từng chốt).
Một hướng cho ĐATN 2 là backtest: chấm điểm bằng dữ liệu các năm trước rồi so với kết
quả kinh doanh năm sau.

### Câu 9: "Điểm yếu lớn nhất hiện tại là gì?"

Nói thật, ba điểm, theo thứ tự này:

1. **Chưa có đối chứng RAG thuần vs GraphRAG** → chưa chứng minh được tầng đồ thị đóng 
   góp bao nhiêu. Đây là lỗ hổng học thuật lớn nhất.
2. **Độ phủ không đều.** Tầng số liệu phủ 7.606 doanh nghiệp, nhưng tầng đồ thị quan hệ
   chỉ phủ ~265 doanh nghiệp trọng tâm (vì trích quan hệ cần gọi LLM, rất tốn thời gian).
3. **Không có giá cổ phiếu** → không tính được P/E, P/B hay bất kỳ chỉ số định giá nào.

### Câu 10: "Em định bảo vệ luận điểm gì?"

Một câu, học thuộc:

> **"Với bài toán phân tích doanh nghiệp, kết hợp đồ thị tri thức với RAG và thêm một
> lớp kiểm chứng số sau sinh thì trả lời được lớp câu hỏi nhiều tầng mà RAG thuần không
> làm được, đồng thời loại bỏ được việc bịa số — và điều này đo được."**

---

## 9. Ba câu chốt nếu chỉ còn 30 giây

1. **"Em xây agent phân tích doanh nghiệp trên đồ thị tri thức, điểm khác biệt là mọi con
   số trong câu trả lời đều được đối chiếu lại với dữ liệu nguồn trước khi hiển thị."**

2. **"Nó trả lời được câu mà công cụ khác không trả lời được — ví dụ Vinamilk kiểm soát
   Mộc Châu Milk qua Vilico, dù quyền lợi kinh tế chỉ 40,88%."**

3. **"Hiện đã có bản chạy được, 7.606 doanh nghiệp, 100% độ chính xác số liệu trên 80 câu
   hỏi. Phần em muốn làm trong đồ án là đo cho chặt: so RAG thuần với GraphRAG để chứng
   minh tầng đồ thị thật sự đóng góp."**

---

## 10. Chuẩn bị thực tế cho buổi gặp

**Nên mang theo:**
- Laptop có Docker + LM Studio đã chạy sẵn (khởi động mất vài phút, đừng để thầy chờ)
- Mở sẵn `http://localhost:8000` và hỏi thử 2 câu
- In hoặc mở sẵn một hồ sơ phân tích tự động (`scripts/19_company_brief.py FPT --out ho-so.md`)

**Hai câu nên hỏi lại thầy** — buổi đầu nên có câu hỏi, không chỉ trả lời:
1. *"Thầy thấy phạm vi ĐATN 1 nên dừng ở đâu? Em đề xuất dừng ở phần kiến trúc, pipeline
   và bộ đánh giá, để ĐATN 2 làm phần đối chứng và phân tích sâu."*
2. *"Em nên đọc những công trình nào để đặt đề tài vào đúng bối cảnh? Em biết GraphRAG của
   Microsoft nhưng chưa đọc kỹ."*

**Đừng nói:**
- "Em làm xong rồi" → thầy sẽ hỏi vậy làm gì nữa
- "Độ chính xác 100%" mà không nói ngay nó đo gì → nghe như không hiểu phép đo của mình
- Bịa về công trình liên quan → thầy biết ngay, và mất tin cậy cho cả buổi

---

## Phụ lục: thử nhanh trước khi đi

```bash
docker compose up -d                                          # 2 CSDL
.venv/Scripts/python.exe run_web.py                           # mở localhost:8000

# Hoặc dòng lệnh, không cần web
.venv/Scripts/python.exe scripts/19_company_brief.py FPT      # hồ sơ tự động
.venv/Scripts/python.exe scripts/21_ownership.py Vinamilk     # mạng lưới sở hữu
.venv/Scripts/python.exe scripts/22_peers.py FPT              # so sánh ngành
```

**Ba câu hỏi nên demo** (chọn đúng loại để thấy cả ba tầng):

| Câu | Cho thấy |
|---|---|
| *"Doanh thu thuần của TSMC năm 2024?"* | tầng số liệu + ghi rõ đồng tiền TWD |
| *"Ai thực sự đứng sau Vinamilk, kể cả qua công ty trung gian?"* | tầng đồ thị nhiều tầng |
| *"Phân tích giúp tôi doanh nghiệp Hòa Phát"* | agent tự chạy 7 bước |

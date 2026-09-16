"""Vector store trên Qdrant + sinh embedding bằng fastembed.

VÌ SAO DÙNG fastembed CHỨ KHÔNG DÙNG MODEL EMBEDDING TRONG LM STUDIO

GPU chỉ có 16GB và LLM sinh câu trả lời đã ăn gần hết. Nếu nạp thêm model embedding
lên GPU, hai model sẽ tranh VRAM và cả hai cùng chậm. fastembed chạy ONNX trên CPU:
bge-small chỉ 133MB, nhúng khoảng 100-200 chunk/giây trên CPU thường — đủ nhanh và
KHÔNG chạm vào GPU. Đây là lựa chọn kiến trúc có chủ đích, không phải giải pháp tạm.

VÌ SAO LƯU NHIỀU TRƯỜNG METADATA ĐẾN VẬY

Mỗi chunk mang theo ticker, năm tài chính, mã Item. Nhờ vậy agent lọc TRƯỚC khi tìm
kiếm ngữ nghĩa: câu hỏi "rủi ro của NVIDIA năm 2026" sẽ chỉ so vector trong phạm vi
ticker=NVDA, fiscal_year=2026, item=1A. Lọc trước như vậy vừa nhanh hơn, vừa loại
sạch nhiễu chéo giữa các công ty — vấn đề kinh điển khi bốn báo cáo cùng ngành nói
những điều na ná nhau và vector search trả về nhầm công ty.
"""

from __future__ import annotations

import uuid
from typing import Dict, Iterable, List, Optional, Sequence

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from config.settings import settings
from src.ingest.chunker import Chunk

_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00cf4fc964ff")

# Câu dẫn BẮT BUỘC của họ model BGE, đặt trước CÂU HỎI (không đặt trước tài liệu).
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder:
    """Bọc fastembed. Nạp model một lần rồi dùng lại (lazy, tránh tốn thời gian khởi động)."""

    def __init__(self, model_name: Optional[str] = None, query_prefix: Optional[str] = None):
        self.model_name = model_name or settings.embedding_model
        # ⚠️ CÂU DẪN CHỈ ĐÚNG VỚI HỌ BGE. Model đa ngữ dùng cho báo cáo thường niên Việt
        # Nam không được huấn luyện với câu dẫn nào; thêm vào là bịa thêm nhiễu tiếng Anh
        # trước mỗi câu hỏi tiếng Việt. Nên câu dẫn đi theo MODEL, không nằm cứng trong mã.
        self.query_prefix = (
            BGE_QUERY_PREFIX if query_prefix is None and "bge" in self.model_name.lower()
            else (query_prefix or "")
        )
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed_documents(self, texts: Sequence[str], batch_size: int = 64) -> List[List[float]]:
        """Nhúng một loạt văn bản.

        `parallel=0` bảo fastembed dùng TẤT CẢ nhân CPU thông qua đa tiến trình. Đo trên
        máy 24 nhân: 3,8 chunk/giây khi để mặc định, 16,9 chunk/giây khi bật — nhanh gấp
        4,4 lần. Với 2.500 chunk đó là chênh lệch giữa 11 phút và 2,5 phút.

        Nhưng đa tiến trình có chi phí khởi động cố định mỗi lần gọi, nên chỉ bật khi lô
        đủ lớn để bù lại. Lô nhỏ (ví dụ nhúng một câu hỏi) chạy tuần tự sẽ nhanh hơn.
        """
        texts = list(texts)
        parallel = 0 if len(texts) >= 200 else None
        return [v.tolist() for v in self.model.embed(texts, batch_size=batch_size, parallel=parallel)]

    def embed_query(self, text: str) -> List[float]:
        """Nhúng câu hỏi.

        Họ model BGE được huấn luyện với một câu dẫn đặt trước CÂU HỎI (không đặt trước
        tài liệu). Bỏ câu dẫn này đi thì điểm truy hồi tụt thấy rõ — đây là chi tiết rất
        hay bị bỏ sót khi dùng BGE.
        """
        return next(iter(self.model.embed([self.query_prefix + text]))).tolist()


# Collection RIÊNG cho mô tả doanh nghiệp Việt Nam, không trộn vào kho 10-K.
#
# Vì sao tách: tìm kiếm không lọc theo công ty ("doanh nghiệp nào đầu tư vào hạ tầng AI")
# chạy trên toàn bộ kho. Đoạn mô tả VCI ngắn và chung chung ("X là tập đoàn công nghệ tập
# trung vào AI, điện toán đám mây…"), nên về độ tương đồng nó dễ vượt mặt một đoạn 10-K
# dài và cụ thể — tức là 1.532 đoạn mô tả sẽ chen vào kết quả của những câu hỏi đang trả
# lời đúng. Để riêng thì kho 10-K không đổi một điểm nào, và mô tả chỉ được đọc khi câu
# hỏi nêu đích danh một doanh nghiệp Việt Nam.
VN_PROFILE_COLLECTION = f"{settings.qdrant_collection}_vn_profiles"


class VectorStore:
    def __init__(
        self,
        collection: Optional[str] = None,
        model_name: Optional[str] = None,
        dim: Optional[int] = None,
    ):
        self.collection = collection or settings.qdrant_collection
        self.client = QdrantClient(url=settings.qdrant_url, timeout=120)
        self.embedder = Embedder(model_name)
        # Số chiều đi theo model, không lấy cứng từ settings: kho báo cáo thường niên
        # Việt Nam dùng model khác kho 10-K. Tạo collection sai số chiều thì Qdrant báo
        # lỗi lúc ghi, nhưng nếu hai model TRÙNG số chiều thì nó im lặng nhận vào và mọi
        # kết quả tìm kiếm sau đó đều vô nghĩa — đúng kiểu hỏng không ai thấy.
        self.dim = dim or settings.embedding_dim

    # ------------------------------------------------------------------ ghi

    def recreate(self) -> None:
        """Tạo lại collection từ đầu và đánh index cho các trường sẽ dùng để lọc."""
        self.client.recreate_collection(
            collection_name=self.collection,
            vectors_config=qm.VectorParams(
                size=self.dim, distance=qm.Distance.COSINE
            ),
        )
        # Không có payload index thì Qdrant phải quét tuần tự khi lọc -> chậm dần theo dữ liệu
        for field, schema in (
            ("ticker", qm.PayloadSchemaType.KEYWORD),
            ("item", qm.PayloadSchemaType.KEYWORD),
            ("doc_id", qm.PayloadSchemaType.KEYWORD),
            ("fiscal_year", qm.PayloadSchemaType.INTEGER),
        ):
            self.client.create_payload_index(
                collection_name=self.collection, field_name=field, field_schema=schema
            )

    def ensure_collection(self) -> None:
        """Tạo collection nếu CHƯA có. Không bao giờ xóa dữ liệu đang có.

        `recreate()` xóa sạch rồi tạo lại. Gọi nhầm nó lên collection chính là mất 23.869
        đoạn 10-K đã nhúng — vài giờ CPU. Collection phụ (mô tả doanh nghiệp Việt Nam) cần
        được tạo lần đầu mà không có rủi ro đó.
        """
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection not in existing:
            self.recreate()

    def count_for(self, ticker: str) -> int:
        """Số điểm của một mã trong collection này. Collection chưa tồn tại thì trả 0."""
        try:
            return self.client.count(
                collection_name=self.collection,
                count_filter=qm.Filter(
                    must=[qm.FieldCondition(key="ticker", match=qm.MatchValue(value=ticker))]
                ),
                exact=True,
            ).count
        except Exception:  # noqa: BLE001 — collection chưa tạo nghĩa là 0 điểm
            return 0

    def delete_by_tickers(self, tickers: Iterable[str]) -> None:
        """Xóa mọi điểm của các mã này. Dùng TRƯỚC khi nạp lại.

        ⚠️ NẠP LẠI MÀ KHÔNG XÓA THÌ ĐỂ LẠI ĐIỂM MỒ CÔI.

        `chunk_id` mang số thứ tự đoạn trong trang, nên chỉ cần đổi cách cắt đoạn là số
        đoạn mỗi trang đổi theo: FPT từ 2.083 xuống 2.078 đoạn. Năm điểm cũ mang số thứ
        tự cao hơn không bị ghi đè — chúng nằm lại trong kho với nội dung của lần cắt
        TRƯỚC, và thỉnh thoảng lọt vào kết quả tìm kiếm mà không ai biết vì sao.
        Đo thật một lần nạp lại: 40.352 điểm trong kho nhưng chỉ nạp 40.138 đoạn.
        """
        tickers = [t for t in tickers if t]
        if not tickers:
            return
        self.client.delete(
            collection_name=self.collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[qm.FieldCondition(key="ticker", match=qm.MatchAny(any=list(tickers)))]
                )
            ),
            wait=True,
        )

    def upsert_chunks(self, chunks: List[Chunk], batch_size: int = 256) -> int:
        """Nhúng và ghi các chunk vào Qdrant.

        Nhúng TOÀN BỘ trong một lần gọi rồi mới ghi theo lô. Nếu nhúng theo từng lô nhỏ,
        mỗi lô sẽ phải khởi động lại nhóm tiến trình song song của fastembed và chi phí
        đó ăn hết phần tăng tốc.
        """
        if not chunks:
            return 0

        all_vectors = self.embedder.embed_documents([c.text for c in chunks])

        total = 0
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            vectors = all_vectors[i : i + batch_size]
            points = [
                qm.PointStruct(
                    # Qdrant chỉ nhận id kiểu UUID hoặc số nguyên, không nhận chuỗi tự do.
                    # Dùng uuid5 để cùng một chunk_id luôn cho ra cùng một id -> chạy lại
                    # script sẽ ghi đè đúng bản ghi cũ thay vì tạo bản trùng.
                    id=str(uuid.uuid5(_NAMESPACE, c.chunk_id)),
                    vector=vec,
                    payload={
                        "chunk_id": c.chunk_id,
                        "doc_id": c.doc_id,
                        "ticker": c.ticker,
                        "company": c.company,
                        "form": c.form,
                        "fiscal_year": int(c.fiscal_year),
                        "item": c.item,
                        "item_title": c.item_title,
                        "seq": c.seq,
                        "text": c.text,
                    },
                )
                for c, vec in zip(batch, vectors)
            ]
            self.client.upsert(collection_name=self.collection, points=points)
            total += len(points)
        return total

    # ------------------------------------------------------------------ đọc

    def search(
        self,
        query: str,
        top_k: int = 6,
        tickers: Optional[Iterable[str]] = None,
        items: Optional[Iterable[str]] = None,
        years: Optional[Iterable[int]] = None,
    ) -> List[Dict]:
        """Tìm kiếm ngữ nghĩa, có thể lọc trước theo công ty / mục / năm."""
        must: List[qm.FieldCondition] = []
        if tickers:
            must.append(qm.FieldCondition(key="ticker", match=qm.MatchAny(any=list(tickers))))
        if items:
            must.append(qm.FieldCondition(key="item", match=qm.MatchAny(any=list(items))))
        if years:
            must.append(
                qm.FieldCondition(key="fiscal_year", match=qm.MatchAny(any=[int(y) for y in years]))
            )

        hits = self.client.search(
            collection_name=self.collection,
            query_vector=self.embedder.embed_query(query),
            query_filter=qm.Filter(must=must) if must else None,
            limit=top_k,
            with_payload=True,
        )
        return [{**h.payload, "score": h.score} for h in hits]

    def count(self) -> int:
        return self.client.count(collection_name=self.collection, exact=True).count

# Kho BÁO CÁO THƯỜNG NIÊN Việt Nam — collection riêng VÀ model nhúng riêng.
#
# ⚠️ ĐÂY LÀ LÝ DO KHÔNG PHẢI NHÚNG LẠI 23.869 ĐOẠN 10-K.
#
# Báo cáo thường niên Việt Nam viết bằng tiếng Việt, mà `bge-small-en-v1.5` chỉ hiểu
# tiếng Anh. Cách hiển nhiên là đổi sang model đa ngữ cho TOÀN hệ thống — và phải nhúng
# lại toàn bộ kho 10-K, vài giờ CPU, trong lúc đó hệ thống trả lời sai.
#
# Không cần. Mỗi collection Qdrant có không gian vector riêng, nên kho này dùng model
# riêng: tài liệu nhúng bằng nó, câu hỏi cũng nhúng bằng nó. Kho 10-K không đổi một điểm.
#
# Chọn MiniLM đa ngữ (0,22 GB) thay vì multilingual-e5-large (2,24 GB) vì GPU đã bị LM
# Studio chiếm gần hết (15,6/16,3 GB), nhúng buộc phải chạy trên CPU. Đo thật: model này
# cho "rủi ro tỷ giá" và "foreign exchange risk" độ tương đồng 0,70, còn hai câu tiếng
# Việt khác chủ đề chỉ 0,40 — nên agent vẫn hỏi bằng tiếng Anh như hiện nay vẫn ra đúng
# đoạn tiếng Việt.
VN_REPORT_COLLECTION = f"{settings.qdrant_collection}_vn_annual_reports"
VN_REPORT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
VN_REPORT_DIM = 384

# ⚠️ Model này chỉ đọc 128 token đầu; phần sau bị cắt IM LẶNG khi nhúng. Đo trên báo cáo
# FPT 2025: đoạn 300 ký tự -> 0/67 đoạn vượt ngưỡng, 350 -> 5/58, 450 -> 15/45. Vì vậy
# `src/ingest/vn_annual_report.py` cắt đoạn ở 320 ký tự chứ không phải 1.200 như kho 10-K.
VN_REPORT_MAX_TOKENS = 128

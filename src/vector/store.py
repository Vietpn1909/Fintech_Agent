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


class Embedder:
    """Bọc fastembed. Nạp model một lần rồi dùng lại (lazy, tránh tốn thời gian khởi động)."""

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or settings.embedding_model
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
        prefixed = f"Represent this sentence for searching relevant passages: {text}"
        return next(iter(self.model.embed([prefixed]))).tolist()


class VectorStore:
    def __init__(self, collection: Optional[str] = None):
        self.collection = collection or settings.qdrant_collection
        self.client = QdrantClient(url=settings.qdrant_url, timeout=120)
        self.embedder = Embedder()

    # ------------------------------------------------------------------ ghi

    def recreate(self) -> None:
        """Tạo lại collection từ đầu và đánh index cho các trường sẽ dùng để lọc."""
        self.client.recreate_collection(
            collection_name=self.collection,
            vectors_config=qm.VectorParams(
                size=settings.embedding_dim, distance=qm.Distance.COSINE
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

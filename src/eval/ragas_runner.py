"""Chấm điểm bằng RAGAS với model chạy local.

BỐN CHỈ SỐ RAGAS DÙNG Ở ĐÂY

    faithfulness       Câu trả lời có bám vào ngữ cảnh lấy được không, hay tự bịa thêm.
                       Đây là chỉ số quan trọng nhất với một hệ thống tài chính.
    answer_relevancy   Câu trả lời có đúng trọng tâm câu hỏi không.
    context_precision  Trong các đoạn lấy về, bao nhiêu phần thực sự liên quan.
    context_recall     Đã lấy về đủ những gì cần để trả lời chưa.

HAI ĐIỀU CHỈNH BẮT BUỘC KHI CHẠY VỚI MODEL LOCAL

1. EMBEDDING KHÔNG DÙNG LM STUDIO.
   RAGAS cần embedding để tính answer_relevancy. Mặc định nó gọi API embedding của
   OpenAI. Trỏ sang LM Studio thì phải nạp thêm một model embedding lên GPU, tranh VRAM
   với model đang sinh câu trả lời. Ở đây ta bọc lại chính fastembed đang dùng cho vector
   store — chạy CPU, không đụng GPU, và cũng nhất quán với hệ thống thật.

2. CHẠY TUẦN TỰ, MỘT LUỒNG.
   RAGAS mặc định gọi song song 16 luồng. LM Studio phục vụ một model trên một GPU nên
   sẽ xếp hàng lại hết, còn ta thì mất khả năng theo dõi và dễ chạm timeout. Đặt
   max_workers=1 và timeout rộng.

CẢNH BÁO VỀ CÁCH ĐỌC KẾT QUẢ

Giám khảo ở đây là model local — cũng chính là model sinh ra câu trả lời đang bị chấm.
Điểm số vì thế KHÔNG phải một đánh giá tuyệt đối. Nó chỉ có ý nghĩa khi dùng để SO SÁNH
giữa các cấu hình (ví dụ: có đồ thị và không có đồ thị, top_k=3 và top_k=8). Kết luận
tuyệt đối về độ chính xác nên dựa vào thước đo dò số trong src/eval/grader.py.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from config.settings import settings


class FastEmbedEmbeddings:
    """Bọc fastembed theo giao diện Embeddings của LangChain để RAGAS dùng được."""

    def __init__(self):
        from src.vector.store import Embedder

        self._embedder = Embedder()

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        return self._embedder.embed_documents(list(texts))

    def embed_query(self, text: str) -> List[float]:
        return self._embedder.embed_query(text)

    # RAGAS gọi bản bất đồng bộ ở một số nhánh; fastembed chạy đồng bộ nên chỉ cần bọc lại
    async def aembed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> List[float]:
        return self.embed_query(text)


def build_judges():
    """Dựng LLM giám khảo và embedding cho RAGAS."""
    from langchain_openai import ChatOpenAI
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    judge = ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_reasoning_model,
        temperature=0.0,
        timeout=float(settings.llm_timeout),
        max_retries=1,
    )
    return LangchainLLMWrapper(judge), LangchainEmbeddingsWrapper(FastEmbedEmbeddings())


def run_ragas(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Chấm RAGAS trên các bản ghi đã có câu trả lời và ngữ cảnh.

    records: [{question, answer, contexts: [str], ground_truth}]
    """
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )
    from ragas.run_config import RunConfig

    usable = [
        r for r in records
        if r.get("answer") and r.get("contexts") and r.get("ground_truth")
    ]
    if not usable:
        return {"error": "không có bản ghi nào đủ dữ liệu để chấm"}

    dataset = Dataset.from_dict({
        "question": [r["question"] for r in usable],
        "answer": [r["answer"] for r in usable],
        "contexts": [r["contexts"] for r in usable],
        "ground_truth": [r["ground_truth"] for r in usable],
    })

    llm, embeddings = build_judges()

    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=llm,
        embeddings=embeddings,
        # Một luồng: LM Studio phục vụ một model trên một GPU, song song không giúp gì
        run_config=RunConfig(max_workers=1, timeout=settings.llm_timeout, max_retries=2),
        raise_exceptions=False,   # một câu lỗi không nên làm hỏng cả mẻ chấm
    )

    scores: Dict[str, Any] = {}
    try:
        frame = result.to_pandas()
        for column in ("faithfulness", "answer_relevancy", "context_precision", "context_recall"):
            if column in frame.columns:
                scores[column] = float(frame[column].mean(skipna=True))
        scores["_per_sample"] = frame.to_dict(orient="records")
        scores["n_samples"] = len(frame)
    except Exception:  # noqa: BLE001 - phiên bản RAGAS khác nhau trả về kiểu khác nhau
        scores = {k: v for k, v in dict(result).items() if isinstance(v, (int, float))}

    return scores

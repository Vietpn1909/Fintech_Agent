"""Tầng giao tiếp với LLM chạy local qua LM Studio.

LM Studio expose một API tương thích chuẩn OpenAI, nên ta dùng thẳng thư viện `openai`
và chỉ đổi base_url. Nhờ vậy nếu sau này bạn muốn chuyển sang OpenAI thật, Anthropic,
hay Ollama, chỉ cần đổi biến trong .env chứ không phải sửa code.

HAI TẦNG MODEL

    extraction  — gọi hàng trăm lần khi xây đồ thị. Việc của nó là đọc một đoạn văn và
                  xuất ra JSON đúng khuôn. Đây là việc máy móc, model 12-14B làm tốt và
                  quan trọng là phải NẰM TRỌN trong VRAM để chạy nhanh.
    reasoning   — gọi vài lần cho mỗi câu hỏi. Việc của nó là suy luận và viết câu trả
                  lời. Đây là lúc cần model to.

Đo bằng scripts/00_benchmark_llm.py rồi hãy quyết định, đừng đoán.

ÉP ĐỊNH DẠNG JSON

LM Studio hỗ trợ response_format kiểu json_schema: nó ràng buộc quá trình sinh token
sao cho kết quả BẮT BUỘC hợp lệ theo schema. Đây là khác biệt lớn so với việc chỉ viết
"hãy trả lời bằng JSON" trong prompt — model nhỏ rất hay kèm thêm lời dẫn hoặc bọc
```json quanh kết quả. Code dưới đây thử ép schema trước; nếu server không hỗ trợ thì
tự lùi về chế độ json_object rồi cuối cùng là bóc JSON từ văn bản thô.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings

_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=float(settings.llm_timeout),
            max_retries=0,  # tự quản lý retry bằng tenacity để kiểm soát nhịp
        )
    return _client


def list_models() -> List[str]:
    """Các model LM Studio đang nạp sẵn. Dùng để kiểm tra kết nối."""
    return [m.id for m in get_client().models.list().data]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
def chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    json_schema: Optional[Dict[str, Any]] = None,
    reasoning_effort: Optional[str] = None,
) -> str:
    """Gọi LLM, trả về nội dung văn bản.

    ⚠️ MODEL CÓ BƯỚC SUY NGHĨ (Gemma 4, Qwen3, DeepSeek-R1...) — cái bẫy lớn.

    Những model này sinh ra hai luồng token riêng biệt:

        reasoning_content   phần tự lẩm bẩm suy nghĩ, KHÔNG phải câu trả lời
        content             câu trả lời thật

    Đo thật với Gemma 4 26B, câu hỏi "Say hello in 5 words":
        max_tokens=50   -> 47 token suy nghĩ, 0 token nội dung, content RỖNG
        max_tokens=800  -> 506 token suy nghĩ, 12 token nội dung

    Tức là tỷ lệ lãng phí 42:1, và nếu max_tokens quá chặt thì model dùng hết ngân sách
    cho phần suy nghĩ rồi bị cắt trước khi kịp trả lời — trả về chuỗi rỗng mà KHÔNG báo
    lỗi gì. Đây là kiểu hỏng tệ nhất: im lặng.

    `reasoning_effort="none"` tắt hẳn phần suy nghĩ. Dùng cho việc máy móc như trích xuất
    JSON (đã có schema ràng buộc, không hưởng lợi từ chain-of-thought). Giữ suy nghĩ cho
    bước viết câu trả lời cuối, nơi nó thực sự cải thiện chất lượng.
    """
    kwargs: Dict[str, Any] = {
        "model": model or settings.llm_reasoning_model,
        "messages": messages,
        "temperature": settings.llm_temperature if temperature is None else temperature,
        "max_tokens": max_tokens or settings.llm_max_tokens,
    }

    effort = reasoning_effort if reasoning_effort is not None else settings.llm_reasoning_effort
    if effort and effort != "default":
        kwargs["reasoning_effort"] = effort

    if json_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "result", "strict": True, "schema": json_schema},
        }

    try:
        resp = get_client().chat.completions.create(**kwargs)
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        # Một số bản LM Studio / model chưa hỗ trợ json_schema -> lùi về json_object
        if json_schema is not None and "json_schema" in message:
            kwargs["response_format"] = {"type": "json_object"}
            resp = get_client().chat.completions.create(**kwargs)
        # Model không phải loại có reasoning thì tham số này gây lỗi -> bỏ đi rồi thử lại
        elif "reasoning_effort" in message:
            kwargs.pop("reasoning_effort", None)
            resp = get_client().chat.completions.create(**kwargs)
        else:
            raise

    choice = resp.choices[0]
    content = choice.message.content or ""

    # Nội dung rỗng mà lý do dừng là "length" -> model đã tiêu hết ngân sách token cho
    # phần suy nghĩ và bị cắt trước khi kịp trả lời.
    #
    # Thử lại y hệt là vô nghĩa: lần sau model cũng sẽ suy nghĩ dài như thế. Cách cứu
    # đúng là TẮT SUY NGHĨ rồi gọi lại — mất một phần chất lượng lập luận, nhưng có câu
    # trả lời còn hơn trả về rỗng. Đo thật: lỗi này làm chết câu hỏi sàng lọc có ngữ cảnh
    # dài, nơi model tiêu sạch 4096 token để suy nghĩ.
    if not content.strip() and choice.finish_reason == "length":
        if kwargs.get("reasoning_effort") != "none":
            kwargs["reasoning_effort"] = "none"
            retry_resp = get_client().chat.completions.create(**kwargs)
            content = retry_resp.choices[0].message.content or ""
            if content.strip():
                return content
        raise RuntimeError(
            "Model dùng hết token cho phần suy nghĩ mà chưa kịp trả lời, "
            "kể cả khi đã tắt suy nghĩ. Cần tăng max_tokens."
        )

    return content


def _strip_fences(text: str) -> str:
    """Bóc phần ```json ... ``` mà model nhỏ hay tự thêm vào."""
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    return fenced.group(1) if fenced else text


def chat_json(
    messages: List[Dict[str, str]],
    json_schema: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    **kwargs: Any,
) -> Optional[Any]:
    """Gọi LLM và parse kết quả thành đối tượng Python. Trả về None nếu không parse nổi.

    Trả None thay vì ném lỗi là có chủ đích: khi xây đồ thị ta gọi hàm này hàng trăm
    lần, và một chunk lỗi định dạng không đáng làm sập cả mẻ chạy 2 tiếng.
    """
    raw = chat(messages, model=model, json_schema=json_schema,
               reasoning_effort=reasoning_effort, **kwargs)
    if not raw.strip():
        return None

    candidate = _strip_fences(raw).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Cứu vãn: lấy khối {...} hoặc [...] dài nhất trong văn bản
    for pattern in (r"\{.*\}", r"\[.*\]"):
        match = re.search(pattern, candidate, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                continue

    return None

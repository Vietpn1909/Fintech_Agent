"""Bước 0: Đo tốc độ thực tế của model đang nạp trong LM Studio.

Mục đích: quyết định model bằng SỐ ĐO chứ không bằng cảm giác.

Với 544 chunk cần trích xuất, chênh lệch giữa 8 tok/s và 40 tok/s là chênh lệch giữa
"chạy qua đêm" và "chạy trong lúc đi ăn trưa". Script này đo:

    1. Tốc độ sinh token (tok/s) — yếu tố chi phối tổng thời gian.
    2. Độ trễ đến token đầu tiên — cho biết model có bị tràn khỏi VRAM hay không.
       Model nằm trọn trong VRAM thường phản hồi dưới 1 giây; nếu con số này lên
       tới vài giây thì gần như chắc chắn trọng số đang bị đẩy một phần sang RAM.
    3. Có tuân thủ JSON schema không — điều kiện tiên quyết để trích xuất đồ thị.
    4. Ngoại suy ra tổng thời gian xây đồ thị.

Chạy:  .venv/Scripts/python.exe scripts/00_benchmark_llm.py
Yêu cầu: LM Studio -> tab Developer -> Start Server (mặc định cổng 1234)
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

from config.settings import settings
from src.llm.client import chat, chat_json, get_client, list_models

console = Console()

GRAPH_CHUNKS_TOTAL = 544  # đo được từ scripts/_check_chunker.py

# Đoạn văn mẫu lấy đúng phong cách Item 1A của một bản 10-K thật
SAMPLE = """
We are dependent on third-party foundries to manufacture our semiconductor products.
We currently rely on Taiwan Semiconductor Manufacturing Company for the production of
substantially all of our GPUs. Our data center customers include major cloud service
providers such as Microsoft Azure and Amazon Web Services. Competition in the accelerated
computing market is intense, and we compete with Advanced Micro Devices and Intel, as
well as with internally developed accelerators from our own cloud customers.
"""

TRIPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "relation": {"type": "string"},
                    "target": {"type": "string"},
                },
                "required": ["source", "relation", "target"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["relations"],
    "additionalProperties": False,
}


def benchmark(model: str, effort: str) -> dict:
    """Đo một model ở một mức reasoning. Trả về dict có khóa 'error' nếu thất bại.

    ⚠️ PHẢI ĐẾM RIÊNG HAI LUỒNG TOKEN.

    Model có bước suy nghĩ (Gemma 4, Qwen3, DeepSeek-R1) sinh token vào `reasoning_content`
    chứ không phải `content`. Bản đo đầu tiên của tôi chỉ đếm `content` và báo về 0 tok/s
    cho một model đang chạy hoàn toàn bình thường — kết luận sai hoàn toàn.

    Đo riêng còn cho biết TỶ LỆ LÃNG PHÍ: bao nhiêu token dùng để suy nghĩ so với bao
    nhiêu token thật sự là câu trả lời. Với Gemma 4, tỷ lệ đó là 42:1 cho việc đơn giản.
    """
    result = {"model": model, "effort": effort}
    extra = {} if effort == "default" else {"reasoning_effort": effort}

    # --- Phép đo 1: tốc độ sinh + độ trễ token đầu, dùng streaming ---
    started = time.time()
    first_token_at = None
    content_tokens = reasoning_tokens = 0
    try:
        stream = get_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Count from 1 to 120, separated by spaces."}],
            temperature=0.0,
            max_tokens=600,
            stream=True,
            **extra,
        )
        for part in stream:
            if not part.choices:
                continue
            delta = part.choices[0].delta
            if getattr(delta, "reasoning_content", None):
                reasoning_tokens += 1
            elif delta.content:
                content_tokens += 1
            else:
                continue
            if first_token_at is None:
                first_token_at = time.time() - started
    except Exception as exc:  # noqa: BLE001
        return {**result, "error": str(exc)[:160]}

    total = time.time() - started
    gen_time = max(total - (first_token_at or 0), 1e-6)
    result["ttft"] = first_token_at or 0.0
    result["tok_per_s"] = (content_tokens + reasoning_tokens) / gen_time
    result["content_tokens"] = content_tokens
    result["reasoning_tokens"] = reasoning_tokens

    # --- Phép đo 2: có tuân thủ JSON schema không, và mất bao lâu ---
    started = time.time()
    try:
        parsed = chat_json(
            [
                {"role": "system", "content": "Extract entity relationships. Respond only with JSON."},
                {"role": "user", "content": SAMPLE},
            ],
            json_schema=TRIPLE_SCHEMA,
            model=model,
            max_tokens=1500,
            reasoning_effort=effort,
        )
    except Exception as exc:  # noqa: BLE001
        return {**result, "json_time": time.time() - started, "json_ok": False,
                "n_relations": 0, "sample": [], "json_error": str(exc)[:120]}

    result["json_time"] = time.time() - started
    result["json_ok"] = isinstance(parsed, dict) and isinstance(parsed.get("relations"), list)
    result["n_relations"] = len(parsed.get("relations", [])) if result["json_ok"] else 0
    result["sample"] = parsed.get("relations", [])[:3] if result["json_ok"] else []

    return result


def main() -> None:
    console.print(f"[cyan]Kết nối tới {settings.llm_base_url} ...[/]")
    try:
        available = list_models()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Không kết nối được LM Studio:[/] {exc}")
        console.print("\nKiểm tra: mở LM Studio → tab [bold]Developer[/] → [bold]Start Server[/].")
        console.print("Nhớ nạp sẵn model và đặt Context Length ít nhất 16384.")
        return

    console.print(f"[green]Đã kết nối.[/] Model đang có: {', '.join(available)}\n")

    # Bỏ model embedding — chúng không phục vụ endpoint chat, đo sẽ chỉ ra lỗi
    chat_models = [m for m in available if "embed" not in m.lower()]
    targets = [m for m in dict.fromkeys(
        chat_models + [settings.llm_extraction_model, settings.llm_reasoning_model]
    ) if m in chat_models]

    if not targets:
        console.print("[red]Không có model chat nào đang nạp trong LM Studio.[/]")
        return

    # Đo cả hai chế độ: tắt suy nghĩ (cho trích xuất) và mặc định (cho trả lời)
    results = []
    for model in targets:
        for effort in ("none", "default"):
            console.print(f"[yellow]Đang đo[/] {model} · reasoning={effort} ...")
            results.append(benchmark(model, effort))

    table = Table(title="Kết quả đo — dùng số này để chọn model")
    table.add_column("Model", overflow="fold")
    table.add_column("Suy nghĩ", justify="center")
    table.add_column("tok/s", justify="right")
    table.add_column("Trễ token đầu", justify="right")
    table.add_column("Token suy nghĩ", justify="right")
    table.add_column("JSON", justify="center")
    table.add_column("Quan hệ", justify="right")
    table.add_column("544 chunk", justify="right")

    for r in results:
        if "error" in r:
            table.add_row(r["model"][:22], r["effort"], "-", "-", "-", "[red]lỗi[/]", "-",
                          r["error"][:28])
            continue
        est_hours = GRAPH_CHUNKS_TOTAL * r["json_time"] / 3600
        vram_warn = "[red]" if r["ttft"] > 2.0 else "[green]"
        table.add_row(
            r["model"][:22],
            r["effort"],
            f"{r['tok_per_s']:.1f}",
            f"{vram_warn}{r['ttft']:.2f}s[/]",
            f"{r['reasoning_tokens']}",
            "[green]có[/]" if r.get("json_ok") else "[red]không[/]",
            str(r.get("n_relations", 0)),
            f"{est_hours:.1f}h",
        )

    console.print()
    console.print(table)

    for r in results:
        if r.get("sample"):
            console.print(f"\n[bold]{r['model']}[/] (suy nghĩ={r['effort']}) trích ra ví dụ:")
            for tri in r["sample"]:
                # markup=False: rich hiểu "[SUPPLIED_BY]" là thẻ định dạng và nuốt mất
                # tên quan hệ, khiến kết quả trông như thể model không trích được gì.
                console.print(
                    f"   {tri.get('source')} --[{tri.get('relation')}]--> {tri.get('target')}",
                    markup=False,
                )

    console.print(
        "\n[dim]Cách đọc:\n"
        "· Trễ token đầu > 2s là dấu hiệu model tràn khỏi VRAM.\n"
        "· Cột 'Token suy nghĩ' cho biết model tiêu bao nhiêu token để tự lẩm bẩm trước\n"
        "  khi trả lời. Với bước trích xuất JSON, con số này là lãng phí thuần túy.\n"
        "· Chọn cấu hình nhanh nhất mà vẫn tuân thủ JSON schema cho LLM_EXTRACTION_MODEL.[/]"
    )


if __name__ == "__main__":
    main()

"""Chẩn đoán vì sao agent trả lời chậm.

CÂU HỎI NÀY QUAN TRỌNG HƠN VẺ NGOÀI

Một câu hỏi bắc cầu đo được 275 giây. Phân rã ra:

    Truy vấn Neo4j (3 lần)      0,5 giây   —  0,25%
    Gọi LLM (4 lần)           200,5 giây   — 99,75%

Cơ sở dữ liệu không phải vấn đề. Và trong phần LLM, phần lớn KHÔNG phải thời gian sinh
chữ mà là thời gian NẠP PROMPT (prefill).

Đây là điều dễ chẩn đoán sai nhất: nhìn thì tưởng model sinh chữ chậm, nên người ta đi
tối ưu prompt cho ngắn lại hoặc đổi model nhỏ hơn. Nhưng nếu prefill mới là nút thắt thì
nguyên nhân nằm ở CẤU HÌNH BỘ NHỚ, và sửa cấu hình cho kết quả tốt hơn nhiều lần so với
mọi tối ưu trong code.

CÁCH ĐỌC KẾT QUẢ

Prefill (nạp prompt) là phép nhân ma trận song song trên toàn bộ token đầu vào cùng lúc,
nên nó PHẢI nhanh hơn sinh chữ rất nhiều — thường hàng nghìn token/giây trên GPU rời.

    prefill >> sinh chữ   ->  bình thường, model nằm gọn trong VRAM
    prefill << sinh chữ   ->  BẤT THƯỜNG, gần như chắc chắn model hoặc KV cache
                              đang tràn sang RAM hệ thống

Nếu rơi vào trường hợp thứ hai, thử theo thứ tự:
  1. LM Studio -> Server Settings -> đặt Parallel = 1
     (hệ thống này gọi LLM tuần tự; mỗi slot song song nhân đôi KV cache lên một lần)
  2. Giảm Context Length xuống 16384 nếu đang đặt cao hơn
  3. Bật Flash Attention nếu model hỗ trợ
  4. Dùng bản lượng tử hóa nhỏ hơn để chừa chỗ cho KV cache

Chạy:  .venv/Scripts/python.exe scripts/00b_diagnose_latency.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

from config.settings import settings
from src.llm.client import get_client

console = Console()

# Đoạn văn nền để bơm ngữ cảnh lên các mức độ dài khác nhau
FILLER = (
    "The company reported revenue growth driven by data center demand. "
    "Management noted supply constraints and elevated capital expenditure. "
)


def measure(prompt: str, max_tokens: int = 120) -> dict:
    """Đo riêng thời gian nạp prompt và thời gian sinh chữ.

    Token đầu tiên chỉ xuất hiện SAU KHI toàn bộ prompt đã được nạp xong, nên độ trễ tới
    token đầu tiên chính là thời gian prefill. Phần còn lại là thời gian sinh chữ.
    """
    started = time.time()
    first_at = None
    tokens = 0

    stream = get_client().chat.completions.create(
        model=settings.llm_reasoning_model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.0,
        stream=True,
        reasoning_effort="none",
    )
    for part in stream:
        if not part.choices:
            continue
        delta = part.choices[0].delta
        if delta.content or getattr(delta, "reasoning_content", None):
            if first_at is None:
                first_at = time.time() - started
            tokens += 1

    total = time.time() - started
    prefill_time = first_at or 0.0
    gen_time = max(total - prefill_time, 1e-6)

    # Ước lượng token đầu vào: tiếng Anh trung bình ~4 ký tự một token
    prompt_tokens = max(len(prompt) // 4, 1)

    return {
        "prompt_chars": len(prompt),
        "prompt_tokens": prompt_tokens,
        "prefill_s": prefill_time,
        "prefill_tps": prompt_tokens / max(prefill_time, 1e-6),
        "gen_tps": tokens / gen_time,
        "total_s": total,
    }


def main() -> None:
    console.print(f"[cyan]Model:[/] {settings.llm_reasoning_model}")
    console.print("[dim]Mỗi phép đo dùng một nội dung khác nhau để không trúng cache.[/]\n")

    sizes = [200, 2000, 6000, 12000]
    results = []
    for i, size in enumerate(sizes):
        # Thêm số thứ tự vào đầu để mỗi prompt là duy nhất, tránh KV cache trả lời thay
        prompt = f"[case {i}] " + (FILLER * (size // len(FILLER) + 1))[:size] + "\nSummarize in one sentence."
        console.print(f"[yellow]Đang đo[/] prompt ~{size} ký tự ...")
        results.append(measure(prompt))

    table = Table(title="Nạp prompt so với sinh chữ")
    table.add_column("Prompt", justify="right")
    table.add_column("~Token vào", justify="right")
    table.add_column("Thời gian nạp", justify="right")
    table.add_column("Tốc độ nạp", justify="right")
    table.add_column("Tốc độ sinh", justify="right")

    for r in results:
        healthy = r["prefill_tps"] > r["gen_tps"] * 3
        colour = "green" if healthy else "red"
        table.add_row(
            f"{r['prompt_chars']:,}",
            f"{r['prompt_tokens']:,}",
            f"{r['prefill_s']:.1f}s",
            f"[{colour}]{r['prefill_tps']:.0f} tok/s[/]",
            f"{r['gen_tps']:.0f} tok/s",
        )

    console.print()
    console.print(table)

    worst = max(results, key=lambda r: r["prefill_s"])
    ratio = worst["prefill_tps"] / max(results[0]["gen_tps"], 1e-6)

    console.print()
    if ratio < 3:
        console.print(
            "[bold red]BẤT THƯỜNG:[/] nạp prompt không nhanh hơn sinh chữ ít nhất 3 lần.\n"
            "Đây là dấu hiệu model hoặc KV cache đang tràn sang RAM hệ thống.\n\n"
            "Thử theo thứ tự:\n"
            "  1. LM Studio → Server Settings → [bold]Parallel = 1[/]\n"
            "  2. Context Length về 16384\n"
            "  3. Bật Flash Attention\n"
            "  4. Dùng bản lượng tử hóa nhỏ hơn\n\n"
            f"Hiện tại một prompt {worst['prompt_chars']:,} ký tự mất "
            f"[bold]{worst['prefill_s']:.0f} giây[/] chỉ để nạp — đó chính là phần lớn "
            "thời gian bạn phải chờ mỗi câu trả lời."
        )
    else:
        console.print(
            "[bold green]BÌNH THƯỜNG:[/] nạp prompt nhanh hơn sinh chữ nhiều lần, "
            "model đang nằm gọn trong VRAM.\n"
            "Nếu vẫn thấy chậm thì nút thắt nằm ở số lượt gọi LLM, không phải ở phần cứng."
        )


if __name__ == "__main__":
    main()

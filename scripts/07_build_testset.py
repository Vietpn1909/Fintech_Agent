"""Bước 7: Sinh bộ câu hỏi kiểm thử từ dữ liệu thật.

Chạy:  .venv/Scripts/python.exe scripts/07_build_testset.py
Không cần LLM.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

from src.eval.testset import EVAL_PATH, build_testset, save_testset

console = Console()


def main() -> None:
    questions = build_testset()
    save_testset(questions)

    counts = Counter(q.category for q in questions)
    grading = Counter(q.grading for q in questions)

    table = Table(title=f"Bộ câu hỏi kiểm thử — {len(questions)} câu")
    table.add_column("Loại câu hỏi")
    table.add_column("Số lượng", justify="right")
    table.add_column("Cách chấm")
    labels = {
        "numeric": ("Tra số liệu", "dò số — xác định, không cần LLM"),
        "comparison": ("So sánh doanh nghiệp", "dò số — xác định, không cần LLM"),
        "screening": ("Sàng lọc toàn thị trường", "RAGAS"),
        "qualitative": ("Định tính (văn bản)", "RAGAS"),
        "multihop": ("Bắc cầu (đồ thị)", "RAGAS"),
    }
    for cat, n in counts.most_common():
        name, how = labels.get(cat, (cat, "-"))
        table.add_row(name, str(n), how)
    console.print(table)

    console.print(
        f"\n[bold]{grading['numeric']} câu chấm bằng dò số[/] (xác định, lặp lại được) · "
        f"[bold]{grading['ragas']} câu chấm bằng RAGAS[/] (cần LLM giám khảo)"
    )

    console.print("\n[cyan]Ví dụ câu hỏi sinh tự động, đáp án lấy thẳng từ XBRL:[/]")
    for q in [x for x in questions if x.category == "numeric"][:3]:
        console.print(f"   [bold]{q.question}[/]")
        console.print(f"      đáp án: {q.expected_value:,.0f} {q.expected_unit}")

    console.print("\n[cyan]Ví dụ câu hỏi bắc cầu (chỉ đồ thị mới trả lời được):[/]")
    for q in [x for x in questions if x.category == "multihop"][:2]:
        console.print(f"   [bold]{q.question}[/]")

    console.print(f"\nĐã lưu: {EVAL_PATH}")


if __name__ == "__main__":
    main()

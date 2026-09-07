"""Bước 8: Chạy đánh giá — agent trả lời toàn bộ bộ câu hỏi rồi chấm điểm.

HAI THƯỚC ĐO, CHẠY ĐỘC LẬP NHAU

    --numeric-only   Chỉ chấm dò số. KHÔNG cần LLM giám khảo, chạy nhanh, kết quả xác
                     định. Đây là thước đo mạnh nhất của dự án.
    (mặc định)       Chấm cả dò số lẫn RAGAS.

Nếu chỉ báo cáo điểm RAGAS, hội đồng chấm sẽ hỏi ngay: "giám khảo là model nào?" — và
câu trả lời "chính model đang được đánh giá" làm suy yếu toàn bộ kết luận. Thước đo dò
số không có điểm yếu đó, nên nó nên được trình bày TRƯỚC.

Chạy:  .venv/Scripts/python.exe scripts/08_run_eval.py --numeric-only
       .venv/Scripts/python.exe scripts/08_run_eval.py
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from config.settings import PROCESSED_DIR, settings
from src.eval.grader import grade_entities, grade_numeric
from src.eval.testset import load_testset

console = Console()
RESULTS_PATH = PROCESSED_DIR / "eval_results.json"


def collect_contexts(observations) -> list:
    """Gom các đoạn văn bản mà agent thực sự đã dùng — RAGAS cần chúng làm 'context'."""
    contexts = []
    for obs in observations or []:
        result = obs.get("result")
        if not isinstance(result, dict):
            continue
        for hit in result.get("results", []) or []:
            if hit.get("text"):
                contexts.append(hit["text"][:1500])
        for rel in result.get("relations", []) or []:
            if rel.get("evidence"):
                contexts.append(f"{rel['source']} -[{rel['relation']}]-> {rel['target']}: {rel['evidence']}")
        # Số liệu tài chính cũng là ngữ cảnh — nếu không đưa vào, RAGAS sẽ chấm mọi câu
        # trả lời tra số là "không có căn cứ", một kết luận sai hoàn toàn.
        for year in result.get("years", []) or []:
            contexts.append(json.dumps(year, ensure_ascii=False, default=str))
        for row in result.get("rows", []) or []:
            contexts.append(json.dumps(row, ensure_ascii=False, default=str))
    return contexts[:12]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--numeric-only", action="store_true", help="bỏ qua RAGAS")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tolerance", type=float, default=0.01, help="sai số cho phép khi dò số")
    args = ap.parse_args()

    questions = load_testset()
    if args.limit:
        questions = questions[: args.limit]

    console.print(
        f"[cyan]Chạy đánh giá {len(questions)} câu hỏi[/] · "
        f"model suy luận: {settings.llm_reasoning_model}"
    )

    from src.agent.graph_agent import ask  # nạp muộn để --help không cần Neo4j

    records = []
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(), TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(), console=console,
    ) as progress:
        task = progress.add_task("Đang hỏi agent", total=len(questions))
        for q in questions:
            started = time.time()
            try:
                out = ask(q.question, verbose=True)
                answer = out["answer"]
                observations = out["observations"]
                tools_used = [t.get("tool") for t in out["trace"] if t["step"] == "thực thi"]
                error = None
            except Exception as exc:  # noqa: BLE001
                answer, observations, tools_used = "", [], []
                error = str(exc)[:200]

            record = {
                "qid": q.qid, "category": q.category, "question": q.question,
                "answer": answer, "ground_truth": q.ground_truth,
                "contexts": collect_contexts(observations),
                "tools_used": tools_used, "seconds": round(time.time() - started, 1),
                "error": error,
            }

            if q.grading == "numeric" and q.expected_value is not None:
                g = grade_numeric(answer, q.expected_value, q.expected_entities, args.tolerance)
                record.update({
                    "correct": g.correct, "expected_value": g.expected,
                    "matched_value": g.matched_value, "relative_error": g.relative_error,
                })
            if q.expected_entities:
                record["entity_recall"] = grade_entities(answer, q.expected_entities)

            records.append(record)
            progress.advance(task)

    # ---------------- Thước đo xác định ----------------
    numeric = [r for r in records if "correct" in r]
    by_category = defaultdict(list)
    for r in records:
        by_category[r["category"]].append(r)

    table = Table(title="Thước đo XÁC ĐỊNH (không dùng LLM giám khảo)")
    table.add_column("Nhóm câu hỏi")
    table.add_column("Số câu", justify="right")
    table.add_column("Độ chính xác số", justify="right")
    table.add_column("Recall thực thể", justify="right")
    table.add_column("Thời gian TB", justify="right")

    for cat, rows in by_category.items():
        graded = [r for r in rows if "correct" in r]
        acc = f"{sum(r['correct'] for r in graded) / len(graded) * 100:.0f}%" if graded else "—"
        ents = [r["entity_recall"] for r in rows if "entity_recall" in r]
        ent = f"{sum(ents) / len(ents) * 100:.0f}%" if ents else "—"
        avg = sum(r["seconds"] for r in rows) / len(rows)
        table.add_row(cat, str(len(rows)), acc, ent, f"{avg:.1f}s")

    console.print()
    console.print(table)

    if numeric:
        n_ok = sum(r["correct"] for r in numeric)
        console.print(
            f"\n[bold green]Độ chính xác số liệu tổng thể: {n_ok}/{len(numeric)} "
            f"= {n_ok / len(numeric) * 100:.1f}%[/] (sai số cho phép {args.tolerance * 100:.0f}%)"
        )
        wrong = [r for r in numeric if not r["correct"]]
        if wrong:
            console.print(f"\n[yellow]{len(wrong)} câu sai:[/]")
            for r in wrong[:8]:
                got = f"{r['matched_value']:,.0f}" if r["matched_value"] else "không có số"
                console.print(f"   [dim]{r['qid']}[/] cần {r['expected_value']:,.0f} · nhận {got}")

    # ---------------- RAGAS ----------------
    if not args.numeric_only:
        try:
            from src.eval.ragas_runner import run_ragas
            console.print("\n[cyan]Đang chấm RAGAS...[/]")
            ragas_scores = run_ragas([r for r in records if r["contexts"] and r["ground_truth"]])
            rt = Table(title="RAGAS (LLM giám khảo — đọc kèm cảnh báo bên dưới)")
            rt.add_column("Chỉ số")
            rt.add_column("Điểm", justify="right")
            for k, v in ragas_scores.items():
                rt.add_row(k, f"{v:.3f}" if isinstance(v, float) else str(v))
            console.print(rt)
            console.print(
                "[dim]Lưu ý: giám khảo là model local, cũng chính là model sinh câu trả lời.\n"
                "Điểm RAGAS ở đây chỉ nên dùng để SO SÁNH giữa các cấu hình, không nên đọc\n"
                "như một đánh giá tuyệt đối.[/]"
            )
            for r, s in zip(records, ragas_scores.get("_per_sample", [])):
                r["ragas"] = s
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Bỏ qua RAGAS:[/] {str(exc)[:200]}")

    RESULTS_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    console.print(f"\nĐã lưu chi tiết: {RESULTS_PATH}")


if __name__ == "__main__":
    main()

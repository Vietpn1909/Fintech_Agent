"""Gộp node đồ thị vào bản ghi doanh nghiệp SEC — CHỈ theo danh sách đã duyệt bằng tay.

Nối tầng đồ thị với tầng số liệu. Trước khi gộp, node "Arm Holdings" (38 cạnh tri thức,
không CIK) và node ARM (12 năm số liệu tài chính) là hai thực thể tách rời trong cùng
một cơ sở dữ liệu. Sau khi gộp, hỏi "Arm Holdings có quan hệ gì" và "doanh thu Arm bao
nhiêu" cùng trỏ về một node.

⚠️ SCRIPT NÀY GHI VĨNH VIỄN VÀO CƠ SỞ DỮ LIỆU

Nên nó có ba lớp bảo vệ:

  1. CHỈ đọc từ config/entity_merges.json. Không tự suy ra cặp nào. Khớp tên tự động sai
     khoảng 30% ở đây (xem phần đầu file JSON đó), và sai kiểu không luật nào bắt được.
  2. Mặc định là CHẠY THỬ. Muốn ghi thật phải gõ thêm --apply.
  3. Đếm trước và sau, rồi đối chiếu với con số mong đợi. Lệch là báo ngay.

Chạy thử:  .venv/Scripts/python.exe scripts/11_merge_graph_entities.py
Ghi thật:  .venv/Scripts/python.exe scripts/11_merge_graph_entities.py --apply
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.table import Table

from config.settings import settings  # noqa: F401 — chỉnh stdout sang UTF-8 cho Windows
from src.agent.tools import graph
from src.graph.schema import INFRA_RELATIONS

console = Console()
MERGE_FILE = ROOT / "config" / "entity_merges.json"


def counts(store) -> dict:
    """Ba con số dùng để đối chiếu trước và sau."""
    companies = store.run("MATCH (c:Company) RETURN count(*) AS n")[0]["n"]
    no_cik = store.run("MATCH (c:Company) WHERE c.cik IS NULL RETURN count(*) AS n")[0]["n"]
    edges = store.run(
        "MATCH ()-[r]->() WHERE NOT type(r) IN $infra RETURN count(r) AS n",
        infra=INFRA_RELATIONS,
    )[0]["n"]
    return {"companies": companies, "no_cik": no_cik, "knowledge_edges": edges}


def plan(store, merges: list) -> tuple:
    """Đối chiếu từng mục với cơ sở dữ liệu thật. Trả về (làm được, bỏ qua)."""
    ready, skipped = [], []
    for item in merges:
        node_name, ticker = item["node"], item["ticker"]

        src = store.run(
            "MATCH (c:Company {name:$n}) RETURN c.cik AS cik, elementId(c) AS eid", n=node_name
        )
        dst = store.run(
            "MATCH (c:Company {ticker:$t}) WHERE c.cik IS NOT NULL "
            "RETURN c.name AS name, c.cik AS cik, elementId(c) AS eid LIMIT 1", t=ticker
        )

        if not src:
            skipped.append((node_name, ticker, "đã gộp rồi hoặc không còn node này"))
            continue
        if src[0]["cik"] is not None:
            skipped.append((node_name, ticker, "node này đã có CIK"))
            continue
        if not dst:
            skipped.append((node_name, ticker, f"không tìm thấy doanh nghiệp SEC mã {ticker}"))
            continue
        if src[0]["eid"] == dst[0]["eid"]:
            skipped.append((node_name, ticker, "hai bên là cùng một node"))
            continue

        deg = store.run(
            "MATCH (c:Company {name:$n})-[r]-() WHERE NOT type(r) IN $infra "
            "RETURN count(r) AS n", n=node_name, infra=INFRA_RELATIONS
        )[0]["n"]

        # Có cạnh nối thẳng giữa hai node cần gộp không? Gộp xong nó thành vòng tự nối,
        # vô nghĩa về mặt ngữ nghĩa ("Arm cạnh tranh với Arm") nên phải xóa sau khi gộp.
        between = store.run(
            "MATCH (a:Company {name:$n})-[r]-(b:Company {cik:$cik}) RETURN count(r) AS n",
            n=node_name, cik=dst[0]["cik"],
        )[0]["n"]

        ready.append({
            "node": node_name, "ticker": ticker, "sec_name": dst[0]["name"],
            "cik": dst[0]["cik"], "edges": deg, "between": between,
            "why": item.get("why", ""),
        })
    return ready, skipped


def backup(store, ready: list) -> Path:
    """Ghi lại toàn bộ cạnh của các node sắp bị gộp, TRƯỚC KHI động vào chúng.

    apoc.refactor.mergeNodes xóa node nguồn — không có lệnh hoàn tác. File này đủ để dựng
    lại thủ công nếu phát hiện gộp nhầm: nó giữ tên hai đầu, loại quan hệ, câu bằng chứng
    và mã bản khai của từng cạnh.
    """
    dump = []
    for item in ready:
        rels = store.run(
            """
            MATCH (c:Company {name: $n})-[r]-(o)
            RETURN type(r) AS rel, startNode(r).name AS source, endNode(r).name AS target,
                   properties(r) AS props
            """,
            n=item["node"],
        )
        dump.append({"node": item["node"], "merged_into": item["ticker"],
                     "sec_name": item["sec_name"], "relationships": rels})

    out_dir = ROOT / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"entity_merge_backup_{int(time.time())}.json"
    path.write_text(json.dumps(dump, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def apply_merge(store, item: dict) -> None:
    """Gộp một cặp.

    Node có CIK đứng TRƯỚC và dùng chế độ `discard`, nên mọi thuộc tính định danh
    (cik, ticker, tier) giữ nguyên của nó. Riêng `name` thì ghi đè lại bằng tên trong đồ
    thị sau khi gộp: "Arm Holdings" dễ đọc hơn "ARM HOLDINGS PLC /UK", và
    `upsert_companies` dùng `ON CREATE SET c.name` nên lần nạp lại vũ trụ sau sẽ không
    ghi đè ngược.
    """
    store.run(
        """
        MATCH (keep:Company {cik: $cik})
        MATCH (drop:Company {name: $node})
        WITH keep, drop WHERE elementId(keep) <> elementId(drop)
        CALL apoc.refactor.mergeNodes([keep, drop],
             {properties: 'discard', mergeRels: true}) YIELD node
        SET node.name = $node
        RETURN node
        """,
        cik=item["cik"], node=item["node"],
    )
    # Vòng tự nối sinh ra khi trước đó hai node có cạnh nối thẳng với nhau.
    store.run(
        "MATCH (c:Company {cik:$cik})-[r]->(c) WHERE NOT type(r) IN $infra DELETE r",
        cik=item["cik"], infra=INFRA_RELATIONS,
    )


def plan_aliases(store, aliases: list) -> tuple:
    """Gộp hai node CÙNG không có CIK — dọn trùng trong nội bộ đồ thị."""
    ready, skipped = [], []
    for item in aliases:
        src_name, dst_name = item["from"], item["into"]
        src = store.run("MATCH (c:Company {name:$n}) RETURN elementId(c) AS eid", n=src_name)
        dst = store.run("MATCH (c:Company {name:$n}) RETURN elementId(c) AS eid", n=dst_name)
        if not src:
            skipped.append((src_name, dst_name, "không còn node nguồn (có thể đã gộp)"))
            continue
        if not dst:
            skipped.append((src_name, dst_name, "không thấy node đích"))
            continue
        deg = store.run(
            "MATCH (c:Company {name:$n})-[r]-() WHERE NOT type(r) IN $infra RETURN count(r) AS n",
            n=src_name, infra=INFRA_RELATIONS,
        )[0]["n"]
        ready.append({"from": src_name, "into": dst_name, "edges": deg, "why": item.get("why", "")})
    return ready, skipped


def apply_alias(store, item: dict) -> None:
    store.run(
        """
        MATCH (keep:Company {name: $into})
        MATCH (drop:Company {name: $from})
        WITH keep, drop WHERE elementId(keep) <> elementId(drop)
        CALL apoc.refactor.mergeNodes([keep, drop],
             {properties: 'discard', mergeRels: true}) YIELD node
        RETURN node
        """,
        into=item["into"], **{"from": item["from"]},
    )


def drop_self_loops(store) -> int:
    """Xóa cạnh nối một node với chính nó.

    "Dell Technologies là công ty con của Dell Technologies" không mang thông tin nào, và
    nó chiếm một suất trong 12 quan hệ mà graph_neighbors trả về. Hai vòng như vậy có sẵn
    từ bước gộp thực thể trước đây, chứ không phải do script này sinh ra.
    """
    return store.run(
        "MATCH (c)-[r]->(c) WHERE NOT type(r) IN $infra DELETE r RETURN count(r) AS n",
        infra=INFRA_RELATIONS,
    )[0]["n"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Gop node do thi vao doanh nghiep SEC")
    ap.add_argument("--apply", action="store_true", help="ghi that (mac dinh chi chay thu)")
    args = ap.parse_args()

    if not MERGE_FILE.exists():
        console.print(f"[red]Không thấy {MERGE_FILE}[/]")
        raise SystemExit(1)

    config = json.loads(MERGE_FILE.read_text(encoding="utf-8"))
    merges = config.get("merges", [])
    aliases = config.get("aliases", [])
    removals = config.get("remove", [])
    store = graph()

    before = counts(store)
    ready, skipped = plan(store, merges)
    alias_ready, alias_skipped = plan_aliases(store, aliases)
    remove_names = [r["node"] for r in removals]
    remove_found = [
        r["name"] for r in store.run(
            "MATCH (c:Company) WHERE c.name IN $names RETURN c.name AS name", names=remove_names
        )
    ]

    table = Table(title=f"Kế hoạch gộp — {len(ready)} cặp làm được / {len(merges)} trong danh sách")
    table.add_column("Node trong đồ thị"); table.add_column("Cạnh", justify="right")
    table.add_column("→ Doanh nghiệp SEC"); table.add_column("Mã"); table.add_column("Căn cứ")
    for item in ready:
        table.add_row(item["node"][:30], str(item["edges"]),
                      item["sec_name"][:32], item["ticker"], item["why"][:44])
    console.print(); console.print(table)

    if skipped:
        console.print("\n[yellow]Bỏ qua:[/]")
        for node, ticker, why in skipped:
            console.print(f"  {node[:34]:36} ({ticker})  {why}")

    if alias_ready:
        console.print("\n[bold]Gộp biến thể trùng (cả hai đều chưa có CIK):[/]")
        for item in alias_ready:
            console.print(f"  {item['from'][:36]:38} → {item['into'][:34]:36} "
                          f"({item['edges']} cạnh) [dim]{item['why']}[/]")
    if alias_skipped:
        for a, b, why in alias_skipped:
            console.print(f"  [yellow]bỏ qua[/] {a[:30]:32} → {b[:26]:28} {why}")

    if remove_found:
        console.print(f"\n[bold]Xóa nhiễu:[/] {len(remove_found)}/{len(remove_names)} node có trong đồ thị")
        for name in remove_found:
            console.print(f"  [dim]{name[:60]}[/]")

    console.print(f"\n[bold]Trước:[/] {before}")

    if not args.apply:
        console.print("\n[yellow]CHẠY THỬ — chưa ghi gì.[/] Thêm [cyan]--apply[/] để ghi thật.")
        return

    saved = backup(store, ready + [{"node": a["from"], "ticker": a["into"],
                                    "sec_name": a["into"]} for a in alias_ready]
                   + [{"node": n, "ticker": "(xóa)", "sec_name": "(xóa)"} for n in remove_found])
    console.print(f"\n[dim]Đã sao lưu cạnh của các node sắp thay đổi: {saved}[/]")

    for item in ready:
        apply_merge(store, item)
        console.print(f"  đã gộp  {item['node'][:32]:34} → {item['ticker']}")

    for item in alias_ready:
        apply_alias(store, item)
        console.print(f"  đã gộp  {item['from'][:32]:34} → {item['into'][:30]}")

    if remove_found:
        store.run("MATCH (c:Company) WHERE c.name IN $names DETACH DELETE c", names=remove_found)
        console.print(f"  đã xóa  {len(remove_found)} node nhiễu")

    loops = drop_self_loops(store)
    if loops:
        console.print(f"  đã xóa  {loops} vòng tự nối")

    after = counts(store)
    console.print(f"\n[bold]Sau:[/]   {after}")

    # ĐỐI CHIẾU. Số Company phải giảm ĐÚNG bằng tổng số node bị gộp hoặc bị xóa —
    # không hơn không kém. Lệch một node cũng là dấu hiệu có gì đó ngoài dự tính.
    removed_total = len(ready) + len(alias_ready) + len(remove_found)
    expected = before["companies"] - removed_total
    ok = after["companies"] == expected
    console.print(
        f"\nSố doanh nghiệp: {before['companies']} − {len(ready)} gộp vào SEC "
        f"− {len(alias_ready)} biến thể trùng − {len(remove_found)} nhiễu "
        f"= {expected} · thực tế {after['companies']} → "
        + ("[green]khớp[/]" if ok else "[red]LỆCH[/]")
    )
    lost = before["knowledge_edges"] - after["knowledge_edges"]
    console.print(
        f"Cạnh tri thức: {before['knowledge_edges']} → {after['knowledge_edges']} "
        f"(giảm {lost}). Giảm nhẹ là bình thường: hai node trùng nhau có thể cùng nối tới "
        "một đích, gộp xong thì hai cạnh đó nhập làm một."
    )
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

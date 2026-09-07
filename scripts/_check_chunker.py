import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.settings import RAW_DIR
from src.ingest.parser import parse_filing
from src.ingest.chunker import make_vector_chunks, make_graph_chunks

tv = tg = 0
for html in sorted(RAW_DIR.rglob("*.html")):
    meta = json.loads(html.with_suffix("").with_suffix(".meta.json").read_text(encoding="utf-8"))
    dm = {"doc_id": html.stem, "ticker": meta["ticker"], "company": meta["company_name"],
          "form": meta["form"], "fiscal_year": (meta["period_end"] or meta["filing_date"])[:4]}
    secs = parse_filing(html)
    v, g = make_vector_chunks(secs, dm), make_graph_chunks(secs, dm)
    tv += len(v); tg += len(g)
    print(f"{html.stem:>16}: vector={len(v):>4}  graph={len(g):>3}")

print(f"\nTONG: {tv} chunk vector | {tg} chunk graph (= {tg} lan goi LLM)")
print(f"Uoc tinh thoi gian trich xuat:")
for tps in (5, 15, 40):
    secs_total = tg * (350 / tps + 1.5)
    print(f"   o {tps:>2} tok/s -> {secs_total/3600:.1f} gio")

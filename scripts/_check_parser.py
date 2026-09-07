import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.settings import RAW_DIR
from src.ingest.parser import parse_filing

total = 0
for html in sorted(RAW_DIR.rglob("*.html")):
    secs = parse_filing(html)
    chars = sum(s.length for s in secs.values())
    total += chars
    ordered = sorted(secs.values(), key=lambda s: s.char_start)
    print(f"{html.stem:>16}: {chars//1000:>4}k giu lai | " + " ".join(f"{s.item}={s.length//1000}k" for s in ordered))
print(f"\nTONG VAN BAN DUA VAO PIPELINE: {total:,} ky tu (~{total//4:,} tokens)")

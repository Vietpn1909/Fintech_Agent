import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.settings import RAW_DIR
from src.ingest.parser import html_to_text

html = next(RAW_DIR.rglob("NVDA_10K_2026.html"))
text = html_to_text(html)
print("=== NVDA: cac vi tri 'Item 8' ===")
for m in re.finditer(r"item\s*8(?![0-9A-Za-z])", text, re.IGNORECASE):
    s = max(0, m.start() - 70)
    print(f"  @{m.start():>7} ...{text[s:m.start()+90]!r}...")
print("\n=== 'Item 15' ===")
for m in re.finditer(r"item\s*15(?![0-9A-Za-z])", text, re.IGNORECASE):
    s = max(0, m.start() - 70)
    print(f"  @{m.start():>7} ...{text[s:m.start()+90]!r}...")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import RAW_DIR, settings
from src.ingest.xbrl import METRIC_LABELS, extract_facts, facts_to_lookup, format_value, growth_pct

SHOW = ["revenue", "gross_profit", "operating_income", "net_income", "rnd_expense", "total_assets", "eps_diluted"]

for ticker in settings.tickers:
    facts = extract_facts(RAW_DIR / ticker / "companyfacts.json", ticker)
    lut = facts_to_lookup(facts)
    years = sorted({f.fiscal_year for f in facts})[-3:]
    print(f"\n===== {ticker} ({facts[0].company}) - {len(facts)} so lieu =====")
    for metric in SHOW:
        row = lut.get(metric, {})
        cells = []
        for y in years:
            f = row.get(y)
            if f:
                g = growth_pct(lut, metric, y)
                gs = f" ({g:+.0f}%)" if g is not None else ""
                cells.append(f"{y}: {format_value(f)}{gs}")
            else:
                cells.append(f"{y}: -")
        print(f"  {METRIC_LABELS[metric]:<34} " + " | ".join(f"{c:<28}" for c in cells))

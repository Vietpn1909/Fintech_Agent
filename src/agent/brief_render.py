"""Dựng hồ sơ phân tích thành văn bản đọc được.

PHÂN CÔNG RÕ RÀNG GIỮA MÃ LỆNH VÀ LLM

    mã lệnh   mọi con số, mọi bảng, mọi trích dẫn nguồn
    LLM       chỉ ba đoạn văn: nhận xét xu hướng, tóm rủi ro, tóm chiến lược

Cách chia này không phải để tiết kiệm lượt gọi. Nó để lớp đối chiếu ở `verify.py` còn
việc mà làm: mỗi đoạn LLM viết ra đều được soi lại từng con số so với ĐÚNG phần dữ liệu
đã đưa cho nó. Nếu để LLM tự tính tăng trưởng hay biên lợi nhuận thì nó sẽ sinh ra con số
không có trong nguồn, lớp đối chiếu sẽ kêu — và kêu đúng, chỉ là kêu về lỗi ta tự tạo ra.

Bảng số và phần cổ đông thì hoàn toàn không cần LLM: chúng là dữ liệu có cấu trúc, dựng
thẳng ra markdown vừa nhanh vừa không bao giờ sai.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from config.settings import settings
from src.agent.verify import check_answer, warning_block
from src.ingest.xbrl import METRIC_LABELS
from src.llm.client import chat

NARRATE_PROMPT = """Bạn viết một đoạn ngắn cho hồ sơ phân tích doanh nghiệp.

LUẬT:
1. CHỈ dùng những con số có trong dữ liệu được đưa. Không tự tính thêm bất kỳ con số nào.
2. Không suy đoán nguyên nhân nếu dữ liệu không nói. Không dự báo tương lai.
3. Không đưa khuyến nghị mua/bán.
4. Viết 2–4 câu, tiếng Việt, văn xuôi liền mạch, không gạch đầu dòng.
5. Khi nhắc nội dung trích từ báo cáo, ghi kèm năm và số trang đúng như dữ liệu ghi.
"""


def _fmt_money(value: Optional[float], currency: str = "") -> str:
    """Số tiền theo cách người Việt đọc, KHÔNG làm tròn mất dấu vết.

    Giữ hai chữ số thập phân ở bậc nghìn tỷ/tỷ để con số vẫn đối chiếu được với nguồn —
    làm tròn tới hàng đơn vị nghe gọn hơn nhưng lệch quá ngưỡng 1% của lớp kiểm.
    """
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    v = abs(float(value))
    if v >= 1e12:
        return f"{sign}{v / 1e12:,.2f} nghìn tỷ {currency}".strip()
    if v >= 1e9:
        return f"{sign}{v / 1e9:,.2f} tỷ {currency}".strip()
    if v >= 1e6:
        return f"{sign}{v / 1e6:,.2f} triệu {currency}".strip()
    return f"{sign}{v:,.0f} {currency}".strip()


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:+.1f}%"


def _narrate(section: str, data: Dict[str, Any]) -> str:
    """Một đoạn văn, viết từ dữ liệu, rồi ĐỐI CHIẾU LẠI từng con số.

    Không sửa câu văn khi phát hiện lệch — dán cảnh báo vào cuối. Không biết phải thay
    bằng giá trị nào, và đoán hộ người đọc là việc không bao giờ đúng.
    """
    import json

    prose = chat(
        [
            {"role": "system", "content": NARRATE_PROMPT},
            {"role": "user", "content": f"Mục: {section}\n\nDữ liệu:\n"
                                        f"{json.dumps(data, ensure_ascii=False, default=str)[:4000]}"},
        ],
        model=settings.llm_reasoning_model,
        max_tokens=700,
        temperature=0.2,
        reasoning_effort="none",
    )
    prose = (prose or "").strip()
    check = check_answer(prose, data)
    if not check["ok"]:
        prose += "\n\n" + warning_block(check)
    return prose


def render(brief: Dict[str, Any], narrate: bool = True) -> str:
    """Hồ sơ dạng markdown. `narrate=False` cho bản thuần dữ liệu, không gọi LLM."""
    if brief.get("status") != "ok":
        return _render_failure(brief)

    fin = brief.get("financials") or {}
    currency = fin.get("currency") or ""
    lines: List[str] = []

    lines.append(f"# Hồ sơ phân tích — {brief['company']} ({brief['ticker']})")
    lines.append("")
    lines.append(f"*Sinh tự động · {brief.get('tool_calls', 0)} lần gọi công cụ · "
                 f"{brief.get('seconds', 0)} giây thu thập dữ liệu*")
    lines.append("")

    # --- 1. Quy mô và xu hướng ---
    lines.append("## 1. Quy mô và xu hướng")
    lines.append("")
    if fin.get("status") == "ok":
        years = fin["years_covered"]
        lines.append(f"| Chỉ tiêu | {years[0]} | {years[-1]} | Thay đổi | CAGR |")
        lines.append("|---|---|---|---|---|")
        for metric, t in fin["trend"].items():
            lines.append(
                f"| {METRIC_LABELS.get(metric, metric)} "
                f"| {_fmt_money(t['first_value'], currency)} "
                f"| {_fmt_money(t['last_value'], currency)} "
                f"| {_pct(t['change_pct'])} | {_pct(t['cagr_pct'])} |")
        lines.append("")
        if fin.get("margins_latest"):
            m = fin["margins_latest"]
            parts = [f"{name}: {value:.1f}%" for name, value in (
                ("biên lợi nhuận gộp", m.get("gross_margin_pct")),
                ("biên lợi nhuận hoạt động", m.get("operating_margin_pct")),
                ("biên lợi nhuận ròng", m.get("net_margin_pct")),
            ) if value is not None]
            lines.append(f"**Năm {years[-1]}** — " + " · ".join(parts))
            lines.append("")
        lines.append(f"*Nguồn: {fin.get('source', 'XBRL/VCI')}*")
        lines.append("")
        if narrate:
            # ⚠️ ĐƯA CẢ DẠNG ĐÃ ĐỊNH DẠNG, không chỉ số thô.
            #
            # Đưa mỗi số thô thì mô hình chép nguyên xi: "doanh thu tăng từ
            # 35.657.262.545.027,0 lên 70.112.825.100.710,0". Đúng nhưng không ai đọc
            # kiểu đó, và đuôi ",0" của số thực còn làm câu văn trông như lỗi.
            #
            # Vẫn giữ số thô bên cạnh để lớp đối chiếu có cái mà so — nếu chỉ đưa chuỗi
            # đã định dạng thì "35,66 nghìn tỷ" vẫn khớp được (bộ đọc hiểu từ chỉ bậc),
            # nhưng đưa cả hai thì mô hình có quyền chọn, mà cách nào cũng đối chiếu được.
            readable = {
                metric: {
                    **t,
                    "first_display": _fmt_money(t["first_value"], currency),
                    "last_display": _fmt_money(t["last_value"], currency),
                }
                for metric, t in fin["trend"].items()
            }
            lines.append(_narrate("Quy mô và xu hướng tài chính", {
                "company": brief["company"], "currency": currency,
                "huong_dan": "Dùng các trường *_display khi nhắc số tiền trong câu văn, "
                             "đừng chép số thô đầy đủ chữ số.",
                "trend": readable, "margins_latest": fin.get("margins_latest"),
            }))
            lines.append("")
    else:
        lines.append(f"*Không có số liệu tài chính ({fin.get('status')}).*")
        lines.append("")

    # --- 2. Rủi ro doanh nghiệp tự nêu ---
    lines.extend(_text_section("2. Rủi ro doanh nghiệp tự nêu", brief.get("risks"),
                               "Rủi ro doanh nghiệp tự nêu trong báo cáo", narrate))

    # --- 3. Chiến lược ---
    lines.extend(_text_section("3. Chiến lược và định hướng", brief.get("strategy"),
                               "Chiến lược và định hướng phát triển", narrate))

    # --- 4. Cơ cấu sở hữu ---
    lines.append("## 4. Cơ cấu sở hữu")
    lines.append("")
    own = brief.get("ownership") or {}
    if own.get("status") == "ok" and (own.get("holders") or own.get("holdings")):
        if own.get("holders"):
            lines.append("**Cổ đông nắm giữ doanh nghiệp này**")
            lines.append("")
            lines.append("| Cổ đông | Tỷ lệ | Tại ngày |")
            lines.append("|---|---|---|")
            for h in own["holders"]:
                pct = f"{h['percent']:.2f}%" if h.get("percent") is not None else "—"
                lines.append(f"| {h['name']} | {pct} | {h.get('as_of') or '—'} |")
            lines.append("")
        if own.get("holdings"):
            lines.append("**Doanh nghiệp này nắm cổ phần tại**")
            lines.append("")
            lines.append("| Đơn vị | Tỷ lệ | Tại ngày |")
            lines.append("|---|---|---|")
            for h in own["holdings"]:
                pct = f"{h['percent']:.2f}%" if h.get("percent") is not None else "—"
                lines.append(f"| {h['name']} | {pct} | {h.get('as_of') or '—'} |")
            lines.append("")
        # Câu này KHÔNG phải trang trí: lỗi đọc ngược chiều sở hữu đã xảy ra thật.
        lines.append("> ⚠️ Sở hữu cổ phần **không phải** quan hệ kinh doanh. Hai doanh "
                     "nghiệp chung cổ đông không có nghĩa là họ làm ăn với nhau.")
        lines.append("")
    else:
        lines.append("*Không có dữ liệu cổ đông.*")
        lines.append("")

    # --- 5. Quan hệ kinh doanh ---
    rel = brief.get("relations") or {}
    if rel.get("by_type"):
        lines.append("## 5. Quan hệ kinh doanh (trích từ hồ sơ)")
        lines.append("")
        for rtype, items in rel["by_type"].items():
            names = ", ".join(i["neighbor"] for i in items[:6])
            lines.append(f"- **{rtype}**: {names}")
        lines.append("")

    # --- 6. Điều hệ thống không biết ---
    lines.append("## 6. Điều hệ thống KHÔNG biết")
    lines.append("")
    lines.append("Mục này có mặt vì một hồ sơ trông đầy đủ khiến người đọc mặc định phần "
                 "không được nhắc tới là không đáng kể.")
    lines.append("")
    for gap in brief.get("gaps", []):
        lines.append(f"- {gap}")
    lines.append("")
    lines.append("---")
    lines.append("*Hồ sơ này trình bày dữ kiện, không phải khuyến nghị đầu tư.*")
    return "\n".join(lines)


def _text_section(title: str, data: Optional[Dict[str, Any]], topic: str,
                  narrate: bool) -> List[str]:
    lines = [f"## {title}", ""]
    if not data or data.get("status") != "ok" or not data.get("hits"):
        lines.append("*Hệ thống chưa có văn bản báo cáo thường niên của doanh nghiệp này.*")
        lines.append("")
        return lines

    hits = data["hits"]
    if narrate:
        lines.append(_narrate(topic, {"excerpts": hits}))
        lines.append("")
    lines.append("<details><summary>Các đoạn trích làm căn cứ</summary>")
    lines.append("")
    for h in hits:
        note = f" — {h['note']}" if h.get("note") else ""
        lines.append(f"- **{h.get('title')}**{note}")
        lines.append(f"  > {' '.join((h.get('text') or '').split())[:300]}")
    lines.append("")
    lines.append("</details>")
    lines.append("")
    return lines


def _render_failure(brief: Dict[str, Any]) -> str:
    """Hồ sơ không dựng được — nói rõ VÌ SAO, vì mỗi lý do đòi một hành động khác nhau."""
    status = brief.get("status")
    detail = brief.get("detail") or {}
    head = f"# Không dựng được hồ sơ — {brief.get('company')}\n"
    if status == "ambiguous":
        opts = ", ".join(f"{o['ticker']} ({o['company']})" for o in detail.get("options", []))
        return head + f"\nTên này khớp nhiều doanh nghiệp: {opts}.\n\nHãy gọi lại với mã đầy đủ."
    if status == "backend_unavailable":
        return head + ("\nCơ sở dữ liệu tạm thời không truy cập được. Đây là sự cố kỹ "
                       "thuật, **không phải** thiếu dữ liệu — hãy thử lại sau.")
    return head + "\nHệ thống không có dữ liệu về doanh nghiệp này."

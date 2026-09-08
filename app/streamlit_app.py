"""Giao diện Streamlit — BẢN CŨ, đã được thay bằng web/ (FastAPI).

⚠️ Dùng `run_web.py` cho mọi việc trình diễn và triển khai. File này giữ lại vì nó vẫn
tiện để gỡ lỗi nhanh một thay đổi trong agent mà không cần mở trình duyệt qua HTTP.

Streamlit chạy lại toàn bộ script mỗi lần tương tác nên không thể hiện từng bước của
agent trong lúc nó chạy — người dùng nhìn màn hình trắng 18-78 giây. Nó cũng cần
WebSocket riêng nên khó đặt sau reverse-proxy/CDN. Xem mục "Giao diện web" trong README.


Điểm khác biệt so với một khung chat thông thường: giao diện này PHƠI BÀY quá trình suy
luận của agent — nó đã gọi công cụ nào, với tham số gì, mất bao lâu, và có phải tự đi
lấy thêm dữ liệu giữa chừng hay không.

Với một đồ án, phần hiển thị dấu vết này quan trọng ngang câu trả lời: nó cho người chấm
thấy hệ thống thực sự định tuyến chứ không phải chỉ gọi một lần truy hồi rồi nhét vào
prompt. Nó cũng là công cụ gỡ lỗi hữu ích nhất khi agent trả lời sai.

Chạy:  .venv/Scripts/python.exe -m streamlit run app/streamlit_app.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from config.settings import settings

st.set_page_config(page_title="Trợ lý Phân tích Đầu tư", page_icon="📊", layout="wide")

TOOL_LABELS = {
    "lookup_financials": "📈 Tra số liệu XBRL",
    "compare_financials": "⚖️ So sánh doanh nghiệp",
    "screen_companies": "🔍 Sàng lọc toàn thị trường",
    "search_filings": "📄 Tìm trong báo cáo",
    "graph_neighbors": "🕸️ Duyệt đồ thị tri thức",
    "graph_path": "🔗 Tìm chuỗi liên kết",
    "company_coverage": "ℹ️ Kiểm tra độ phủ",
}


@st.cache_resource(show_spinner=False)
def get_backend():
    """Nạp một lần rồi dùng lại — tránh mở lại kết nối Neo4j/Qdrant mỗi lần rerun."""
    from src.agent.graph_agent import ask
    from src.agent.tools import graph, vectors

    return ask, graph(), vectors()


@st.cache_data(ttl=120, show_spinner=False)
def load_stats():
    _, graph_store, vector_store = get_backend()
    nodes = {r["label"]: r["n"] for r in graph_store.stats()["nodes"]}
    rels = {r["type"]: r["n"] for r in graph_store.stats()["relationships"]}
    tiers = {
        r["tier"]: r["n"]
        for r in graph_store.run(
            "MATCH (c:Company) RETURN coalesce(c.tier,'metrics') AS tier, count(*) AS n"
        )
    }
    return nodes, rels, tiers, vector_store.count()


# ------------------------------------------------------------------ thanh bên

with st.sidebar:
    st.title("📊 Trợ lý Phân tích")
    st.caption("Agentic GraphRAG trên hồ sơ SEC")

    try:
        nodes, rels, tiers, n_points = load_stats()

        st.subheader("Độ phủ dữ liệu")
        col1, col2 = st.columns(2)
        col1.metric("Doanh nghiệp", f"{nodes.get('Company', 0):,}")
        col2.metric("Bản ghi năm TC", f"{nodes.get('FinancialYear', 0):,}")
        col1.metric("Chunk văn bản", f"{n_points:,}")
        col2.metric("Quan hệ đồ thị", f"{sum(v for k, v in rels.items() if k not in ('HAS_FINANCIALS', 'FILED')):,}")

        st.caption(
            f"**{tiers.get('metrics', 0):,}** chỉ có số liệu · "
            f"**{tiers.get('text', 0):,}** có văn bản · "
            f"**{tiers.get('graph', 0):,}** có đồ thị"
        )
        st.caption(
            "Doanh nghiệp chưa có văn bản sẽ được agent tự tải và lập chỉ mục "
            "khi cần (~40 giây)."
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"Không kết nối được cơ sở dữ liệu: {str(exc)[:150]}")
        st.caption("Kiểm tra: `docker compose up -d`")

    st.divider()
    st.subheader("Kiểm tra một doanh nghiệp")
    probe = st.text_input("Tên hoặc mã", placeholder="Tesla, NVDA, Coca Cola...")
    if probe:
        from src.agent.tools import company_coverage

        info = company_coverage(probe)
        if info.get("status") == "ok":
            st.success(f"**{info['company']}** ({info['ticker']})")
            st.write(
                f"- Mức phủ: `{info['tier']}`\n"
                f"- Số liệu: {info['financial_years']} năm "
                f"({info['year_range'][0]}–{info['year_range'][1]})\n"
                f"- Văn bản: {info['text_chunks']:,} chunk"
            )
            if info.get("alternatives"):
                st.caption("Có thể bạn muốn tìm: " + ", ".join(a["ticker"] for a in info["alternatives"]))
        else:
            st.warning("Không tìm thấy doanh nghiệp này trong hệ thống SEC.")

    st.divider()
    st.caption(f"Model: `{settings.llm_reasoning_model}`")
    st.caption(f"LM Studio: `{settings.llm_base_url}`")
    if st.button("Xóa hội thoại"):
        st.session_state.messages = []
        st.rerun()

# ------------------------------------------------------------------ khung chat

st.title("Trợ lý Phân tích Doanh nghiệp & Đầu tư")

if "messages" not in st.session_state:
    st.session_state.messages = []

if not st.session_state.messages:
    st.markdown(
        "Hỏi về **số liệu tài chính**, **nội dung báo cáo**, hoặc **quan hệ giữa các "
        "doanh nghiệp**. Agent tự chọn nguồn dữ liệu phù hợp."
    )
    st.caption("Thử một trong các câu sau:")
    samples = [
        "Doanh thu và lợi nhuận của NVIDIA năm tài chính 2026 là bao nhiêu?",
        "So sánh chi phí R&D của Apple, Microsoft và Alphabet năm 2025",
        "Nếu TSMC gián đoạn sản xuất thì ảnh hưởng tới Microsoft qua những mắt xích nào?",
        "NVIDIA nêu rủi ro gì về kiểm soát xuất khẩu sang Trung Quốc?",
        "Doanh nghiệp nào có doanh thu trên 200 tỷ USD năm 2024?",
    ]
    cols = st.columns(len(samples))
    for col, sample in zip(cols, samples):
        if col.button(sample[:38] + "...", key=sample, use_container_width=True):
            st.session_state.pending = sample
            st.rerun()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("trace"):
            with st.expander(
                f"🔎 Agent đã làm gì · {message['rounds']} vòng · {message['seconds']}s"
            ):
                for step in message["trace"]:
                    if step["step"] == "định tuyến":
                        st.markdown(
                            f"**1. Định tuyến** ({step['seconds']}s) → chọn: "
                            + ", ".join(TOOL_LABELS.get(t, t) for t in step["calls"])
                        )
                        if step.get("reasoning"):
                            st.caption(step["reasoning"][:400])
                    elif step["step"] == "thực thi":
                        st.markdown(
                            f"**{TOOL_LABELS.get(step['tool'], step['tool'])}** "
                            f"({step['seconds']}s · `{step.get('status')}`)"
                        )
                        st.code(str(step["args"])[:300], language="python")
                    elif step["step"] == "suy xét":
                        verdict = "đủ dữ liệu" if step["sufficient"] else "cần lấy thêm"
                        st.markdown(f"**Suy xét** ({step['seconds']}s) → {verdict}")
                        if step.get("missing"):
                            st.caption(step["missing"][:300])
                    elif step["step"] == "trả lời":
                        st.markdown(f"**Viết câu trả lời** ({step['seconds']}s)")

prompt = st.chat_input("Đặt câu hỏi về doanh nghiệp...")
if "pending" in st.session_state:
    prompt = st.session_state.pop("pending")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        placeholder.markdown("_Đang định tuyến và truy vấn dữ liệu..._")
        started = time.time()

        try:
            ask, _, _ = get_backend()
            result = ask(prompt)
            answer = result["answer"] or "_Không tạo được câu trả lời._"
            placeholder.markdown(answer)

            st.session_state.messages.append({
                "role": "assistant", "content": answer,
                "trace": result["trace"], "rounds": result["rounds"],
                "seconds": result["seconds"],
            })
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            placeholder.error(
                f"Lỗi sau {time.time() - started:.0f}s: {str(exc)[:300]}\n\n"
                "Kiểm tra LM Studio đã bật server chưa (tab Developer → Start Server)."
            )

/* FinGraph — trang chủ.
 *
 * Chỉ hai việc: đổ số liệu thật vào trang, và hiện đèn trạng thái hệ thống.
 *
 * KHÔNG có hiệu ứng nào ở đây. Phần chuyển động của trang nằm hết trong các hình minh
 * họa SVG (đồ thị tri thức, ba làn dữ liệu, đường ống bốn khối) và chạy bằng chính SVG
 * — không cần JavaScript. Nội dung chữ và các khối đứng yên để đọc được ngay.
 */

'use strict';

const $ = (s) => document.querySelector(s);
const nf = new Intl.NumberFormat('vi-VN');

// ══════════════════════════════════════════════ số liệu thật

async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    if (!r.ok) throw new Error('stats ' + r.status);
    const s = await r.json();

    const values = {
      companies: s.companies,
      financial_years: s.financial_years,
      text_chunks: s.text_chunks,
      knowledge_edges: s.knowledge_edges,
      relation_types: s.relation_types,
      tier_graph: s.tiers.graph,
    };
    // Trang HTML có sẵn số dự phòng để vẫn đọc được khi backend chưa chạy — ở đây chỉ
    // ghi đè bằng số THẬT lấy từ Neo4j và Qdrant.
    for (const [key, val] of Object.entries(values)) {
      if (val == null) continue;
      document.querySelectorAll(`[data-stat="${key}"]`)
        .forEach((el) => { el.textContent = nf.format(val); });
    }
    if (s.model) $('#modelName').textContent = s.model;
  } catch (_) { /* trang giới thiệu vẫn phải đọc được khi Docker chưa bật */ }
}

// ══════════════════════════════════════════════ đèn trạng thái

async function loadHealth() {
  const box = $('#status');
  const paint = (cls, text, title) => {
    box.innerHTML = `<span class="dot ${cls}"></span><span>${text}</span>`;
    box.title = title || '';
  };
  try {
    const h = await (await fetch('/api/health')).json();
    if (h.ready) { paint('dot-ok', 'sẵn sàng', 'Neo4j, Qdrant và LM Studio đều phản hồi'); return; }

    const down = [];
    if (!h.neo4j) down.push('Neo4j');
    if (!h.qdrant) down.push('Qdrant');
    if (!h.llm) down.push('LM Studio');
    paint('dot-bad', 'thiếu: ' + down.join(', '),
      down.includes('LM Studio')
        ? 'Bật LM Studio → tab Developer → Start Server'
        : 'Chạy: docker compose up -d');
  } catch (_) {
    paint('dot-bad', 'máy chủ không phản hồi');
  }
}

// ══════════════════════════════════════════════ thanh điều hướng

/* Đổ bóng chỉ xuất hiện khi trang đã cuộn — ở đỉnh trang thanh nav phải hòa vào nền.
 * passive:true báo cho trình duyệt biết listener này không gọi preventDefault, nhờ đó
 * nó không phải chờ ta xử lý xong mới cuộn tiếp. */
const nav = $('#nav');
const onScroll = () => nav.classList.toggle('scrolled', window.scrollY > 8);
window.addEventListener('scroll', onScroll, { passive: true });
onScroll();

loadStats();
loadHealth();
setInterval(loadHealth, 30000);

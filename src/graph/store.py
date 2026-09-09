"""Lớp truy cập Neo4j: tạo lược đồ, ghi node/cạnh, và các truy vấn Cypher cho agent.

CẤU TRÚC ĐỒ THỊ

    (:Company {ticker, name, cik})
        -[:FILED]->        (:Filing {doc_id, form, fiscal_year, filing_date, url})
        -[:REPORTED]->     (:Metric {key, metric, fiscal_year, value, unit, us_gaap_tag})
        -[:COMPETES_WITH]-> (:Company)
        -[:SUPPLIED_BY]->  (:Company)
        -[:OPERATES_SEGMENT]-> (:Segment)
        ... (xem src/graph/schema.py để biết bộ quan hệ đầy đủ)

    Mọi cạnh do LLM trích xuất đều mang thuộc tính truy vết:
        evidence   - câu văn gốc làm căn cứ
        chunk_id   - chunk nào sinh ra cạnh này
        doc_id     - bản khai nào
        confidence - độ tin cậy LLM tự đánh giá

    Nhờ đó agent luôn trích dẫn được nguồn cho từng mắt xích trong chuỗi suy luận, thay
    vì đưa ra một khẳng định không ai kiểm chứng được. Đây chính là thứ nâng điểm
    faithfulness khi chấm bằng RAGAS.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from neo4j import GraphDatabase

from config.settings import settings
from src.graph.schema import INFRA_RELATIONS, QUERYABLE_RELATIONS, RELATION_NAMES


class GraphStore:
    def __init__(self) -> None:
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        self.database = settings.neo4j_database

    def close(self) -> None:
        self.driver.close()

    def run(self, cypher: str, **params: Any) -> List[Dict]:
        with self.driver.session(database=self.database) as session:
            return [record.data() for record in session.run(cypher, **params)]

    # ------------------------------------------------------------ lược đồ

    def init_schema(self) -> None:
        """Tạo ràng buộc duy nhất và index. Chạy được nhiều lần, không gây lỗi.

        Ràng buộc duy nhất không chỉ để đảm bảo tính đúng đắn — nó còn tạo index, giúp
        lệnh MERGE khi nạp dữ liệu chạy nhanh gấp nhiều lần.
        """
        constraints = [
            "CREATE CONSTRAINT company_name IF NOT EXISTS FOR (c:Company) REQUIRE c.name IS UNIQUE",
            # CIK là khóa định danh thật của doanh nghiệp; tên chỉ là nhãn hiển thị
            "CREATE CONSTRAINT company_cik IF NOT EXISTS FOR (c:Company) REQUIRE c.cik IS UNIQUE",
            "CREATE CONSTRAINT filing_id IF NOT EXISTS FOR (f:Filing) REQUIRE f.doc_id IS UNIQUE",
            "CREATE CONSTRAINT finyear_key IF NOT EXISTS FOR (fy:FinancialYear) REQUIRE fy.key IS UNIQUE",
            "CREATE CONSTRAINT segment_name IF NOT EXISTS FOR (s:Segment) REQUIRE s.name IS UNIQUE",
            "CREATE CONSTRAINT product_name IF NOT EXISTS FOR (p:Product) REQUIRE p.name IS UNIQUE",
            "CREATE CONSTRAINT tech_name IF NOT EXISTS FOR (t:Technology) REQUIRE t.name IS UNIQUE",
            "CREATE CONSTRAINT risk_name IF NOT EXISTS FOR (r:RiskFactor) REQUIRE r.name IS UNIQUE",
            "CREATE CONSTRAINT person_name IF NOT EXISTS FOR (p:Person) REQUIRE p.name IS UNIQUE",
            "CREATE CONSTRAINT regulator_name IF NOT EXISTS FOR (r:Regulator) REQUIRE r.name IS UNIQUE",
            "CREATE CONSTRAINT geo_name IF NOT EXISTS FOR (g:Geography) REQUIRE g.name IS UNIQUE",
            # Chủ sở hữu là tổ chức (quỹ đầu tư, tổng công ty nhà nước, ngân hàng nước
            # ngoài). Nhãn riêng chứ không dùng Company: họ không niêm yết, không có số
            # liệu, và đếm họ vào "doanh nghiệp" sẽ thổi phồng con số trên trang chủ.
            "CREATE CONSTRAINT org_name IF NOT EXISTS FOR (o:Organization) REQUIRE o.name IS UNIQUE",
            "CREATE INDEX company_ticker IF NOT EXISTS FOR (c:Company) ON (c.ticker)",
            "CREATE INDEX finyear_lookup IF NOT EXISTS FOR (fy:FinancialYear) ON (fy.ticker, fy.fiscal_year)",
            # Index cho câu hỏi sàng lọc kiểu "công ty nào doanh thu lớn nhất năm 2024"
            "CREATE INDEX finyear_revenue IF NOT EXISTS FOR (fy:FinancialYear) ON (fy.fiscal_year, fy.revenue)",
        ]
        for stmt in constraints:
            self.run(stmt)

    def wipe(self) -> None:
        """Xóa sạch đồ thị. Dùng khi muốn xây lại từ đầu."""
        self.run("MATCH (n) DETACH DELETE n")

    # ------------------------------------------------------------ nạp dữ liệu

    def upsert_companies(self, companies: List[Dict[str, str]], batch_size: int = 2000) -> int:
        """companies: [{ticker, name, cik, exchange, tier}]

        `tier` cho biết công ty được phủ tới mức nào:
            "metrics" — chỉ có số liệu tài chính (mặc định cho toàn bộ 8.001)
            "text"    — có thêm văn bản 10-K trong vector store
            "graph"   — có thêm quan hệ trong đồ thị tri thức
        Agent đọc trường này để biết nên trả lời bằng công cụ nào, và để nói thật với
        người dùng khi một công ty chưa được lập chỉ mục sâu.
        """
        written = 0
        for i in range(0, len(companies), batch_size):
            batch = companies[i : i + batch_size]
            # ⚠️ MERGE THEO `cik`, KHÔNG THEO `name`.
            #
            # CIK là mã định danh duy nhất SEC cấp cho mỗi doanh nghiệp và không bao giờ
            # đổi. Tên thì đổi liên tục: SEC ghi "AMAZON COM INC", LLM trích ra
            # "Amazon.com, Inc.", và bước gộp thực thể đổi lại thành tên đẹp.
            #
            # MERGE theo tên khiến mỗi lần nạp lại vũ trụ TẠO LẠI node theo tên SEC, hủy
            # sạch kết quả gộp của lần trước. Đo thật: sau một lần nạp lại, AMZN xuất hiện
            # hai lần trong bảng xếp hạng doanh thu với cùng một con số.
            #
            # ON CREATE SET giữ nguyên tên đẹp đã gộp, chỉ đặt tên SEC cho node hoàn toàn
            # mới. coalesce giữ mức phủ đã nâng, không hạ ngược về 'metrics'.
            self.run(
                """
                UNWIND $rows AS row
                MERGE (c:Company {cik: row.cik})
                ON CREATE SET c.name = row.name
                SET c.ticker = row.ticker,
                    c.exchange = coalesce(row.exchange, ''),
                    c.tier = coalesce(c.tier, row.tier, 'metrics')
                """,
                rows=batch,
            )
            written += len(batch)
        return written

    def upsert_vn_companies(self, companies: List[Dict[str, str]]) -> int:
        """Doanh nghiệp niêm yết tại Việt Nam.

        ⚠️ PHẢI MERGE THEO `ticker`, TUYỆT ĐỐI KHÔNG DÙNG `upsert_companies`.

        `upsert_companies` MERGE theo `cik` — mã định danh do SEC cấp. Doanh nghiệp Việt
        Nam không có CIK, và trong Neo4j `MERGE (c:Company {cik: null})` khớp với BẤT KỲ
        node nào có cik null. Hậu quả: toàn bộ doanh nghiệp Việt Nam dồn vào đúng một
        node, mỗi lần nạp lại ghi đè lên nhau, và không có lỗi nào báo ra.
        (Ràng buộc duy nhất trên `cik` cũng không cứu được: Neo4j bỏ qua giá trị null.)

        Mã được gắn hậu tố `.VN` để không đụng mã Mỹ — sàn HOSE có mã "FPT", NYSE cũng
        có "FPT" của một quỹ đóng khác hẳn.
        """
        self.run(
            """
            UNWIND $rows AS row
            MERGE (c:Company {ticker: row.ticker})
            SET c.market = 'VN',
                c.exchange = coalesce(row.exchange, 'HOSE'),
                c.symbol = row.symbol,
                // Ngành do VCI phân loại. Giữ lại vì khi vũ trụ Việt Nam lên gần 1.600 mã
                // thì "doanh nghiệp thép nào lãi nhất" mới là câu hỏi trả lời được.
                c.sector = coalesce(row.sector, c.sector),
                c.tier = coalesce(c.tier, 'metrics'),
                // Khác với doanh nghiệp SEC (dùng ON CREATE SET để giữ tên đẹp đã gộp
                // bằng tay), tên doanh nghiệp Việt Nam luôn ghi đè từ nguồn: VCI là
                // nguồn duy nhất cho nhóm này và không có bước gộp thủ công nào.
                //
                // Cần thiết chứ không chỉ cho gọn: bản đầu lấy tên sai chỗ nên mọi doanh
                // nghiệp vào đồ thị dưới cái tên là chính mã của nó ("ACB", "BID"). Nếu
                // giữ ON CREATE SET thì chạy lại cũng không sửa được, phải xóa node đi.
                c.name = row.name
            """,
            rows=companies,
        )
        return len(companies)

    # Thứ bậc mức phủ. Số càng lớn càng phủ sâu.
    TIER_RANK = {"metrics": 0, "text": 1, "graph": 2}

    def set_tier(self, tickers: List[str], tier: str) -> None:
        """Nâng mức phủ của một nhóm công ty. CHỈ NÂNG LÊN, không bao giờ hạ xuống.

        Vì sao phải chặn hạ cấp: bước lập chỉ mục văn bản gọi set_tier(..., "text") cho
        mọi công ty nó xử lý, trong đó có cả những công ty ĐÃ có quan hệ trong đồ thị.
        Nếu ghi đè thẳng, một công ty đang ở mức "graph" sẽ bị tụt xuống "text", và agent
        sẽ nói với người dùng rằng nó không có dữ liệu đồ thị về công ty đó — trong khi
        nó có. Nói thiếu về năng lực của chính mình cũng là một dạng trả lời sai.
        """
        rank = self.TIER_RANK.get(tier, 0)
        self.run(
            """
            UNWIND $tickers AS t
            MATCH (c:Company {ticker: t})
            WITH c, CASE coalesce(c.tier, 'metrics')
                        WHEN 'graph' THEN 2 WHEN 'text' THEN 1 ELSE 0 END AS current
            WHERE current < $rank
            SET c.tier = $tier
            """,
            tickers=tickers, tier=tier, rank=rank,
        )

    def upsert_shareholders(self, rows: List[Dict[str, Any]]) -> int:
        """Cạnh sở hữu (Company)-[:OWNED_BY]->(Person|Organization).

        ⚠️ CHỦ SỞ HỮU KHÔNG ĐƯỢC MANG NHÃN `Company`.

        Phần lớn là quỹ đầu tư và cá nhân — không niêm yết, không có số liệu tài chính,
        không nằm trong vũ trụ tra cứu. Gắn nhãn Company cho họ sẽ làm mọi con số "bao
        nhiêu doanh nghiệp" phồng lên hàng nghìn, đúng kiểu đếm nhầm mà `collect_stats`
        vừa phải tách ra để sửa. Nên: INDIVIDUAL -> :Person, CORPORATE -> :Organization.

        MERGE theo tên vì nguồn không cấp mã định danh nào cho chủ sở hữu. Đây là điểm yếu
        đã biết: "Norges Bank" và "NORGES BANK" sẽ thành hai node. Chấp nhận được vì tên
        tiếng Anh của VCI khá nhất quán, và bước gộp thực thể (script 09) xử lý được phần
        còn lại — nhưng phải nói ra chứ không giấu.

        Cạnh mang theo `percent` và `as_of`: tỷ lệ sở hữu thay đổi liên tục, một con số
        không kèm ngày công bố là con số không kiểm chứng được.
        """
        if not rows:
            return 0

        # ⚠️ CHỦ SỞ HỮU LÀ DOANH NGHIỆP ĐÃ CÓ TRONG ĐỒ THỊ THÌ PHẢI NỐI VÀO CHÍNH NODE ĐÓ.
        #
        # Bản đầu tạo node :Organization cho mọi chủ sở hữu là tổ chức. Kết quả: "FPT
        # Corporation" tồn tại HAI node — một :Company mã FPT.VN, một :Organization sinh ra
        # vì FPT đứng tên cổ đông của FPT Retail. Đo được 318 trường hợp trùng như vậy.
        #
        # Hậu quả không chỉ là dư node. `neighbors()` khớp theo TÊN, nên nó gộp cả hai node
        # lại và in ra "FPT Digital Retail nắm 46,54% FPT Corporation" — đọc ngược hoàn
        # toàn chiều sở hữu. Dữ liệu đúng, hiển thị sai, và không có gì báo lỗi.
        #
        # Nối vào node Company có sẵn vừa xóa được node trùng, vừa sinh ra thứ giá trị nhất
        # của cả bước này: cạnh sở hữu GIỮA HAI DOANH NGHIỆP — tức cấu trúc công ty mẹ/công
        # ty con, thứ mà tầng số liệu không thể hiện được.
        #
        # Khớp CHÍNH XÁC theo tên đã hạ chữ thường, không khớp gần đúng. Khớp gần đúng ở
        # đây sẽ tái tạo đúng lỗi Acer→Macerich, chỉ khác là trên quan hệ sở hữu.
        known = {
            (r["name"] or "").strip().lower(): r["ticker"]
            for r in self.run(
                "MATCH (c:Company) WHERE c.name IS NOT NULL AND c.ticker IS NOT NULL "
                "RETURN c.name AS name, c.ticker AS ticker"
            )
        }
        rows = [
            {**r, "owner_ticker": known.get((r.get("owner_raw") or r.get("owner") or "").strip().lower())}
            for r in rows
        ]

        # Doanh nghiệp không tự sở hữu chính mình. Cạnh tự nối không mang thông tin và làm
        # hỏng mọi truy vấn đường đi.
        linked = [r for r in rows if r.get("owner_ticker") and r["owner_ticker"] != r["ticker"]]
        if linked:
            self.run(
                """
                UNWIND $rows AS row
                MATCH (c:Company {ticker: row.ticker})
                MATCH (o:Company {ticker: row.owner_ticker})
                MERGE (c)-[r:OWNED_BY]->(o)
                SET r.percent = row.percent,
                    r.shares = row.shares,
                    r.position = row.position,
                    r.as_of = row.as_of,
                    r.ticker = row.ticker,
                    r.source = 'VCI',
                    r.evidence = coalesce(row.owner_raw, row.owner) + ' nắm ' +
                                 toString(round(row.percent * 1000) / 10.0) + '% ' +
                                 row.symbol + coalesce(' (công bố ' + row.as_of + ')', ''),
                    r.confidence = row.percent
                """,
                rows=linked,
            )

        rest = [r for r in rows if not r.get("owner_ticker")]
        for label in ("Person", "Organization"):
            batch = [r for r in rest if r.get("owner_label") == label]
            if not batch:
                continue
            self.run(
                f"""
                UNWIND $rows AS row
                MATCH (c:Company {{ticker: row.ticker}})
                MERGE (o:{label} {{name: row.owner}})
                  ON CREATE SET o.source = 'VCI', o.kind = row.owner_kind
                SET o.name_vi = coalesce(row.owner_vi, o.name_vi)
                MERGE (c)-[r:OWNED_BY]->(o)
                SET r.percent = row.percent,
                    r.shares = row.shares,
                    r.position = row.position,
                    r.as_of = row.as_of,
                    r.ticker = row.ticker,
                    r.source = 'VCI',
                    // `evidence` để `neighbors()` có gì hiển thị cho người đọc kiểm chứng,
                    // giống câu văn bằng chứng của cạnh do LLM trích.
                    // Câu bằng chứng dùng tên GỐC, không phải tên hiển thị đã gắn mã.
                    // "Trương Gia Bình nắm 6.9% FPT" đọc được; "Trương Gia Bình (FPT) nắm
                    // 6.9% FPT" thì lặp và khó đọc.
                    r.evidence = coalesce(row.owner_raw, row.owner) + ' nắm ' +
                                 toString(round(row.percent * 1000) / 10.0) + '% ' +
                                 row.symbol + coalesce(' (công bố ' + row.as_of + ')', ''),
                    // Tỷ lệ sở hữu làm độ tin cậy: `neighbors()` sắp xếp giảm dần theo
                    // trường này, nên cổ đông lớn hiện trước cổ đông nhỏ.
                    r.confidence = row.percent
                """,
                rows=batch,
            )
        return len(rows)

    def sync_graph_tier(self) -> Tuple[int, int]:
        """Đặt mức phủ 'graph' theo ĐÚNG những gì đồ thị đang có. Trả về (nâng, khai khống).

        ⚠️ Hàm này tồn tại vì mức 'graph' trước đây KHÔNG AI GÁN CẢ.

        Script 05 ghi rõ trong chú thích rằng "script 04 nâng lên graph", nhưng script 04
        chưa bao giờ gọi `set_tier`. Hậu quả đo được: 95 doanh nghiệp trong vũ trụ thực sự
        có cạnh tri thức, nhưng chỉ 84 doanh nghiệp mang nhãn 'graph'. Zoom có 105 cạnh mà
        vẫn bị xếp mức 'text' — nghĩa là agent tự báo mình KHÔNG có dữ liệu đồ thị về Zoom
        trong khi nó có nhiều nhất. Nói thiếu về năng lực của chính mình cũng là trả lời sai,
        đúng như cảnh báo trong `set_tier`.

        Vì sao SUY RA thay vì gán tay: gán tay là thứ đã hỏng. Mức phủ phải là hệ quả của
        dữ liệu, nên chạy lại bao nhiêu lần cũng ra cùng kết quả, và không lệ thuộc vào việc
        có ai nhớ gọi hàm ở đúng chỗ hay không.

        Chỉ xét node CÓ ticker. Bước trích xuất đẻ ra hàng trăm node như "Samsung
        Electronics" hay "OpenAI" — chúng có cạnh tri thức nhưng không nằm trong vũ trụ SEC,
        không có số liệu, nên đếm chúng vào mức phủ là thổi phồng con số.
        """
        upgraded = self.run(
            """
            MATCH (c:Company)
            WHERE c.ticker IS NOT NULL AND coalesce(c.tier, 'metrics') <> 'graph'
            OPTIONAL MATCH (c)-[r]-() WHERE NOT type(r) IN $infra
            WITH c, count(r) AS n
            WHERE n > 0
            SET c.tier = 'graph'
            RETURN count(c) AS n
            """,
            infra=INFRA_RELATIONS,
        )

        # Chiều ngược lại: mang nhãn 'graph' mà không còn cạnh tri thức nào. Xảy ra khi
        # bảng dọn ở config/entity_merges.json bỏ hết cạnh của một doanh nghiệp. Ở đây chỉ
        # BÁO chứ không tự hạ cấp: hạ xuống mức nào là câu hỏi không trả lời được từ Neo4j
        # (còn văn bản trong Qdrant hay không thì đồ thị không biết), mà đoán sai lại tạo ra
        # đúng cái lỗi báo thiếu năng lực mà hàm này đang đi sửa.
        stale = self.run(
            """
            MATCH (c:Company) WHERE c.tier = 'graph'
            OPTIONAL MATCH (c)-[r]-() WHERE NOT type(r) IN $infra
            WITH c, count(r) AS n
            WHERE n = 0
            RETURN count(c) AS n
            """,
            infra=INFRA_RELATIONS,
        )

        return (
            upgraded[0]["n"] if upgraded else 0,
            stale[0]["n"] if stale else 0,
        )

    def upsert_filings(self, filings: List[Dict[str, Any]]) -> None:
        self.run(
            """
            UNWIND $rows AS row
            MERGE (f:Filing {doc_id: row.doc_id})
            SET f.form = row.form,
                f.fiscal_year = toInteger(row.fiscal_year),
                f.filing_date = row.filing_date,
                f.accession = row.accession,
                f.url = row.source_url
            WITH f, row
            MATCH (c:Company {ticker: row.ticker})
            MERGE (c)-[:FILED]->(f)
            """,
            rows=filings,
        )

    def upsert_financial_years(self, rows: List[Dict[str, Any]], batch_size: int = 2000) -> int:
        """Ghi số liệu tài chính: một node cho mỗi (công ty, năm tài chính).

        Ghi theo lô vì ở quy mô 8.001 doanh nghiệp có khoảng 80.000 bản ghi. Gửi tất cả
        trong một giao dịch sẽ khiến Neo4j phồng bộ nhớ giao dịch và có thể thất bại;
        chia lô 2.000 giữ mỗi giao dịch ở kích thước lành mạnh và cho phép theo dõi
        tiến độ.

        Lưu ý về `derived`: Neo4j không lưu được danh sách rỗng lẫn với danh sách chuỗi
        một cách nhất quán khi thuộc tính chưa tồn tại, nên ta luôn ghi một danh sách
        chuỗi (có thể rỗng) — đồng nhất kiểu dữ liệu.
        """
        written = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            self.run(
                """
                UNWIND $rows AS row
                MATCH (c:Company {ticker: row.ticker})
                MERGE (fy:FinancialYear {key: row.key})
                SET fy += row
                MERGE (c)-[:HAS_FINANCIALS]->(fy)
                """,
                rows=batch,
            )
            written += len(batch)
        return written

    def upsert_relations(self, triples: List[Dict[str, Any]]) -> int:
        """Ghi các bộ ba do LLM trích xuất.

        Loại quan hệ nằm trong tên kiểu cạnh Cypher, mà Cypher không cho truyền tên kiểu
        cạnh bằng tham số. Vì vậy ta nhóm theo loại quan hệ rồi sinh một câu lệnh cho mỗi
        loại. Tên quan hệ đã được kiểm tra nằm trong bộ đóng RELATION_NAMES trước khi
        ghép chuỗi, nên không có nguy cơ chèn Cypher độc hại.
        """
        by_relation: Dict[str, List[Dict]] = {}
        for t in triples:
            if t["relation"] not in RELATION_NAMES:
                continue
            by_relation.setdefault(t["relation"], []).append(t)

        written = 0
        for relation, rows in by_relation.items():
            source_label = rows[0]["source_type"]
            target_label = rows[0]["target_type"]
            # Nhóm thêm theo cặp nhãn vì cùng một quan hệ chỉ có một cặp nhãn hợp lệ
            self.run(
                f"""
                UNWIND $rows AS row
                MERGE (s:{source_label} {{name: row.source}})
                MERGE (t:{target_label} {{name: row.target}})
                MERGE (s)-[r:{relation}]->(t)
                SET r.evidence = row.evidence,
                    r.chunk_id = row.chunk_id,
                    r.doc_id = row.doc_id,
                    r.ticker = row.ticker,
                    r.fiscal_year = toInteger(row.fiscal_year),
                    r.confidence = row.confidence,
                    r.mentions = coalesce(r.mentions, 0) + 1
                """,
                rows=rows,
            )
            written += len(rows)
        return written

    # ------------------------------------------------------------ truy vấn

    def stats(self) -> Dict[str, Any]:
        nodes = self.run(
            "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n ORDER BY n DESC"
        )
        rels = self.run(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n ORDER BY n DESC"
        )
        return {"nodes": nodes, "relationships": rels}

    def get_metric(self, ticker: str, metric: str, fiscal_year: int) -> Optional[Dict]:
        rows = self.run(
            """
            MATCH (c:Company {ticker: $ticker})-[:REPORTED]->(m:Metric)
            WHERE m.metric = $metric AND m.fiscal_year = $year
            RETURN m
            """,
            ticker=ticker, metric=metric, year=int(fiscal_year),
        )
        return rows[0]["m"] if rows else None

    def neighbors(
        self, name: str, relations: Optional[Iterable[str]] = None, limit: int = 40
    ) -> List[Dict]:
        """Các thực thể nối trực tiếp với một thực thể, kèm bằng chứng."""
        rel_filter = ""
        if relations:
            allowed = [r for r in relations if r in QUERYABLE_RELATIONS]
            if allowed:
                rel_filter = ":" + "|".join(allowed)
        # ⚠️ PHẢI TRẢ VỀ ĐÚNG CHIỀU THẬT CỦA CẠNH.
        #
        # Mẫu MATCH (a)-[r]-(b) là vô hướng, nên nếu trả về a làm "source" và b làm
        # "target" thì mọi cạnh ĐI VÀO sẽ bị đảo ngược. Với quan hệ chuỗi cung ứng, đảo
        # chiều là sai nghiêm trọng về mặt sự kiện: "TSMC được NVIDIA cung cấp" thay vì
        # "NVIDIA được TSMC cung cấp" — và agent sẽ trình bày cả chuỗi cung ứng ngược.
        #
        # Dùng startNode(r) / endNode(r) để lấy chiều thật, kèm trường `direction` cho
        # biết thực thể đang hỏi đứng ở đầu nào.
        # ⚠️ PHẢI LOẠI CẠNH HẠ TẦNG, NẾU KHÔNG CÔNG CỤ NÀY VÔ DỤNG.
        #
        # Mẫu MATCH ở trên khớp MỌI loại cạnh, kể cả 48.025 cạnh HAS_FINANCIALS nối công
        # ty với từng năm tài chính. Tệ hơn: `ORDER BY r.confidence DESC` xếp NULL lên
        # ĐẦU trong Neo4j, mà cạnh hạ tầng thì không có thuộc tính confidence.
        #
        # Hậu quả đo được trước khi sửa, với limit=12:
        #     NVIDIA    -> 12/12 cạnh hạ tầng,  0 quan hệ tri thức
        #     Microsoft -> 12/12 cạnh hạ tầng,  0 quan hệ tri thức
        #     Apple     -> 12/12 cạnh hạ tầng,  0 quan hệ tri thức
        # Công cụ trả về `status: ok` nên agent tin là đã tra xong và kết luận ba doanh
        # nghiệp này không có quan hệ nào trong đồ thị — sai, và không có lỗi nào báo ra.
        #
        # coalesce ở phần sắp xếp để cạnh thiếu confidence không lại nhảy lên đầu lần nữa.
        return self.run(
            f"""
            MATCH (a)-[r{rel_filter}]-(b)
            WHERE toLower(a.name) = toLower($name)
              AND NOT type(r) IN $infra
            RETURN startNode(r).name AS source,
                   type(r) AS relation,
                   endNode(r).name AS target,
                   labels(endNode(r))[0] AS target_type,
                   CASE WHEN startNode(r).name = a.name THEN 'outgoing' ELSE 'incoming' END
                       AS direction,
                   b.name AS neighbor,
                   labels(b)[0] AS neighbor_type,
                   r.evidence AS evidence, r.doc_id AS doc_id,
                   r.ticker AS ticker, r.confidence AS confidence
            ORDER BY coalesce(r.confidence, 0) DESC
            LIMIT $limit
            """,
            name=name, limit=limit, infra=INFRA_RELATIONS,
        )

    def path_between(self, source: str, target: str, max_hops: int = 3) -> List[Dict]:
        """Đường đi ngắn nhất giữa hai thực thể — đây là thứ RAG thuần không làm được.

        Ví dụ: "NVIDIA liên quan gì tới Long Châu / tới Azure?" Vector search không trả
        lời nổi vì không có đoạn văn nào nhắc cả hai cùng lúc. Đồ thị thì đi qua các mắt
        xích trung gian để dựng lại chuỗi liên kết.
        """
        return self.run(
            f"""
            MATCH (a), (b)
            WHERE toLower(a.name) = toLower($source) AND toLower(b.name) = toLower($target)
            MATCH p = shortestPath((a)-[*1..{int(max_hops)}]-(b))
            RETURN [n IN nodes(p) | n.name] AS nodes,
                   [r IN relationships(p) | type(r)] AS relations,
                   [r IN relationships(p) | r.evidence] AS evidence
            LIMIT 5
            """,
            source=source, target=target,
        )

    def find_entity(
        self, fragment: str, limit: int = 10, prefer_type: Optional[str] = "Company"
    ) -> List[Dict]:
        """Tìm thực thể theo tên gần đúng — dùng để nối câu hỏi của người dùng với node.

        ⚠️ XẾP HẠNG "CHỨA CHUỖI + TÊN NGẮN NHẤT THẮNG" LÀ SAI MỘT CÁCH CÓ HỆ THỐNG.

        Bản đầu tiên khớp mọi nhãn node rồi sắp xếp theo độ dài tên. Kết quả đo thật:

            "NVIDIA"    -> "NVIDIA DGX Cloud"         (Product, không phải Company)
            "Microsoft" -> "Microsoft Cloud"          (Product)
            "Intel"     -> "Intel 18A"                (Technology)
            "AMD"       -> "Amdocs"                   (một công ty HOÀN TOÀN KHÁC)
            "TSMC"      -> "TSMC Arizona Corporation" (công ty con)

        Hậu quả: graph_path đi tìm đường từ một SẢN PHẨM thay vì từ DOANH NGHIỆP, không
        thấy đường nào, rồi báo `no_path` — trong khi đường đi giữa hai doanh nghiệp nằm
        sẵn trong đồ thị. Lỗi này khiến toàn bộ năng lực suy luận bắc cầu vô hiệu mà
        không có một thông báo lỗi nào.

        Cách xếp hạng đúng, theo thứ tự ưu tiên giảm dần:
          0. Trùng khít tên
          1. Tên bắt đầu bằng cụm tìm kiếm, tiếp theo là ranh giới từ ("NVIDIA Corporation")
          2. Cụm tìm kiếm xuất hiện như một TỪ trọn vẹn trong tên
          3. Chỉ là chuỗi con ("Amdocs" chứa "amd") — hạng thấp nhất, gần như luôn sai
        Trong cùng hạng: ưu tiên nhãn `prefer_type`, rồi tới tên ngắn hơn.
        """
        rows = self.run(
            """
            MATCH (n)
            WHERE n.name IS NOT NULL AND toLower(n.name) CONTAINS toLower($fragment)
            RETURN n.name AS name, labels(n)[0] AS type, n.ticker AS ticker
            LIMIT 200
            """,
            fragment=fragment,
        )
        if not rows:
            return []

        import re as _re

        needle = (fragment or "").strip().lower()
        word_re = _re.compile(rf"(?<![a-z0-9]){_re.escape(needle)}(?![a-z0-9])")

        def tier_of(row: Dict) -> int:
            name = (row["name"] or "").lower()
            if name == needle:
                return 0
            if word_re.match(name):
                return 1
            if word_re.search(name):
                return 2
            return 3

        def rank(row: Dict) -> tuple:
            return (tier_of(row), row["type"] != prefer_type, len(row["name"] or ""))

        # Hạng 3 = chỉ chứa chuỗi, không theo ranh giới từ. Đây đúng là nhánh đã sinh ra
        # "AMD" -> "Amdocs". Vẫn trả về, nhưng đánh dấu `weak` để phía gọi từ chối dùng
        # nó làm điểm xuất phát cho truy vấn đồ thị.
        out = []
        for row in sorted(rows, key=rank)[:limit]:
            out.append({**row, "confidence": "weak" if tier_of(row) == 3 else "high"})
        return out

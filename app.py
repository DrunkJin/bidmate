from __future__ import annotations

import json
import mimetypes
import os
import sqlite3
import uuid
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse


ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
DB_PATH = ROOT / "bidmate.db"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8787"))


def load_local_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip().lstrip("\ufeff"), value.strip().strip('"').strip("'"))


load_local_env()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connection() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def demo_notices() -> list[dict]:
    today = date.today()
    rows = [
        ("2026-001", "2026년 도시브랜드 홍보영상 제작 용역", "서울특별시", "서울", "영상제작", 48_000_000, 5, ["영상", "홍보", "콘텐츠"], ["사업자등록증", "제안서", "실적증명서"]),
        ("2026-002", "청년정책 SNS 콘텐츠 기획 및 운영", "한국청소년정책연구원", "전국", "홍보·마케팅", 72_000_000, 8, ["SNS", "콘텐츠", "마케팅"], ["제안서", "가격제안서", "신용평가등급확인서"]),
        ("2026-003", "지역문화축제 공식 홈페이지 개편", "서울문화재단", "서울", "소프트웨어", 38_500_000, 3, ["웹사이트", "개발", "디자인"], ["소프트웨어사업자 신고확인서", "제안서"]),
        ("2026-004", "공공데이터 활용사례 카드뉴스 디자인", "한국지능정보사회진흥원", "전국", "디자인", 18_000_000, 2, ["디자인", "카드뉴스", "공공데이터"], ["견적서", "포트폴리오"]),
        ("2026-005", "관광 숏폼 영상 콘텐츠 제작", "부산관광공사", "부산", "영상제작", 35_000_000, 11, ["영상", "숏폼", "관광"], ["제안서", "실적증명서", "지역제한 확인"]),
        ("2026-006", "디지털 전환 교육 프로그램 운영", "중소벤처기업진흥공단", "전국", "교육·컨설팅", 96_000_000, 14, ["교육", "디지털", "컨설팅"], ["제안서", "강사이력", "실적증명서"]),
        ("2026-007", "박물관 전시 안내 그래픽 제작", "국립민속박물관", "서울", "디자인", 27_000_000, 6, ["디자인", "전시", "그래픽"], ["제안서", "포트폴리오", "사업자등록증"]),
        ("2026-008", "지역상권 홍보 콘텐츠 제작 및 배포", "경기도시장상권진흥원", "경기", "홍보·마케팅", 44_000_000, 9, ["콘텐츠", "홍보", "상권"], ["제안서", "수행계획서", "실적증명서"]),
    ]
    return [
        {
            "id": item[0], "title": item[1], "agency": item[2], "region": item[3],
            "category": item[4], "budget": item[5],
            "deadline": (today + timedelta(days=item[6])).isoformat(),
            "keywords": item[7], "documents": item[8],
            "source_url": "https://www.g2b.go.kr/",
        }
        for item in rows
    ]


def infer_category(title: str) -> str:
    rules = [
        ("영상제작", ("영상", "유튜브", "숏폼", "촬영", "미디어")),
        ("디자인", ("디자인", "그래픽", "브랜드", "편집", "전시")),
        ("홍보·마케팅", ("홍보", "마케팅", "SNS", "광고", "콘텐츠")),
        ("소프트웨어", ("시스템", "홈페이지", "소프트웨어", "정보화", "플랫폼", "개발")),
        ("교육·컨설팅", ("교육", "컨설팅", "연구", "운영")),
    ]
    for category, terms in rules:
        if any(term.lower() in title.lower() for term in terms):
            return category
    return "기타용역"


def infer_keywords(title: str) -> list[str]:
    candidates = ("영상", "콘텐츠", "홍보", "디자인", "SNS", "마케팅", "웹사이트", "개발", "교육", "컨설팅", "연구", "행사", "관광", "공공데이터")
    hits = [term for term in candidates if term.lower() in title.lower()]
    return hits or [infer_category(title)]


def parse_api_deadline(value: object) -> str | None:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if len(digits) < 8:
        return None
    try:
        return datetime.strptime(digits[:8], "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def sync_public_notices() -> int:
    service_key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not service_key:
        raise RuntimeError("공공데이터 서비스 키가 설정되지 않았습니다.")
    end = datetime.now()
    begin = end - timedelta(days=7)
    params = {
        "pageNo": 1, "numOfRows": 100, "type": "json", "inqryDiv": 1,
        "inqryBgnDt": begin.strftime("%Y%m%d0000"),
        "inqryEndDt": end.strftime("%Y%m%d%H%M"),
    }
    endpoint = (
        "https://apis.data.go.kr/1230000/ad/BidPublicInfoService/"
        "getBidPblancListInfoServc?serviceKey=" + service_key + "&" + urlencode(params)
    )
    request = urllib.request.Request(endpoint, headers={"Accept": "application/json", "User-Agent": "Bidmate/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"공공데이터 API가 HTTP {exc.code}를 반환했습니다.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"공공데이터 API 연결 실패: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("공공데이터 API 응답이 JSON 형식이 아닙니다.") from exc

    api_response = payload.get("response", {})
    header = api_response.get("header", {})
    if str(header.get("resultCode", "00")) not in {"00", "0"}:
        raise RuntimeError(f"공공데이터 API 오류: {header.get('resultMsg', '알 수 없는 오류')}")
    items = api_response.get("body", {}).get("items", []) or []
    if isinstance(items, dict):
        items = items.get("item", []) or []
    if isinstance(items, dict):
        items = [items]

    normalized = []
    for item in items:
        deadline = parse_api_deadline(item.get("bidClseDt") or item.get("bidClseDate"))
        if not deadline or date.fromisoformat(deadline) < date.today():
            continue
        notice_no = str(item.get("bidNtceNo") or "").strip()
        order = str(item.get("bidNtceOrd") or "00").strip()
        title = str(item.get("bidNtceNm") or "").strip()
        if not notice_no or not title:
            continue
        budget_raw = item.get("asignBdgtAmt") or item.get("presmptPrce") or 0
        try:
            budget = int(float(str(budget_raw).replace(",", "")))
        except ValueError:
            budget = 0
        normalized.append({
            "id": f"{notice_no}-{order}", "title": title,
            "agency": str(item.get("dminsttNm") or item.get("ntceInsttNm") or "발주기관 미상"),
            "region": str(item.get("prtcptPsblRgnNm") or "전국"),
            "category": infer_category(title), "budget": budget, "deadline": deadline,
            "keywords": infer_keywords(title),
            "documents": ["입찰참가자격 확인", "공고문 및 첨부파일 확인"],
            "source_url": str(item.get("bidNtceDtlUrl") or "https://www.g2b.go.kr/"),
        })

    if not normalized:
        raise RuntimeError("최근 7일간 진행 중인 용역 공고를 찾지 못했습니다.")
    with connection() as db:
        db.execute("DELETE FROM notices")
        for notice in normalized:
            db.execute(
                """INSERT INTO notices
                (id,title,agency,region,category,budget,deadline,keywords,documents,source_url,fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (notice["id"], notice["title"], notice["agency"], notice["region"], notice["category"],
                 notice["budget"], notice["deadline"], json.dumps(notice["keywords"], ensure_ascii=False),
                 json.dumps(notice["documents"], ensure_ascii=False), notice["source_url"], now_iso()),
            )
        db.execute("DELETE FROM saved_notices WHERE notice_id NOT IN (SELECT id FROM notices)")
    return len(normalized)


def call_public_api(operation: str, params: dict) -> list[dict]:
    service_key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not service_key:
        raise RuntimeError("공공데이터 서비스 키가 설정되지 않았습니다.")
    query = {"pageNo": 1, "numOfRows": 100, "type": "json", **params}
    url = (
        "https://apis.data.go.kr/1230000/ad/BidPublicInfoService/" + operation
        + "?serviceKey=" + service_key + "&" + urlencode(query)
    )
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Bidmate/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise RuntimeError("나라장터 상세조건을 불러오지 못했습니다.") from exc
    api_response = payload.get("response", {})
    header = api_response.get("header", {})
    if str(header.get("resultCode", "00")) not in {"00", "0"}:
        raise RuntimeError(f"나라장터 상세조건 오류: {header.get('resultMsg', '알 수 없는 오류')}")
    items = api_response.get("body", {}).get("items", []) or []
    if isinstance(items, dict):
        items = items.get("item", []) or []
    if isinstance(items, dict):
        items = [items]
    return items


def fetch_notice_eligibility(notice_id: str) -> dict:
    try:
        bid_no, order = notice_id.rsplit("-", 1)
    except ValueError as exc:
        raise RuntimeError("올바르지 않은 공고번호입니다.") from exc
    params = {"inqryDiv": 2, "bidNtceNo": bid_no, "bidNtceOrd": order}
    operations = (
        "getBidPblancListInfoLicenseLimit",
        "getBidPblancListInfoPrtcptPsblRgn",
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        license_future = pool.submit(call_public_api, operations[0], params)
        region_future = pool.submit(call_public_api, operations[1], params)
        licenses = license_future.result()
        regions = region_future.result()
    result = {"licenses": licenses, "regions": regions, "fetched_at": now_iso()}
    with connection() as db:
        db.execute(
            """INSERT OR REPLACE INTO notice_eligibility(notice_id, licenses, regions, fetched_at)
            VALUES (?, ?, ?, ?)""",
            (notice_id, json.dumps(licenses, ensure_ascii=False),
             json.dumps(regions, ensure_ascii=False), result["fetched_at"]),
        )
    return result


def initialize_database() -> None:
    with connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                user_id TEXT PRIMARY KEY, company_name TEXT NOT NULL, region TEXT NOT NULL,
                categories TEXT NOT NULL, keywords TEXT NOT NULL, max_budget INTEGER NOT NULL,
                capabilities TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notices (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, agency TEXT NOT NULL, region TEXT NOT NULL,
                category TEXT NOT NULL, budget INTEGER NOT NULL, deadline TEXT NOT NULL,
                keywords TEXT NOT NULL, documents TEXT NOT NULL, source_url TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS saved_notices (
                user_id TEXT NOT NULL, notice_id TEXT NOT NULL, saved_at TEXT NOT NULL,
                PRIMARY KEY(user_id, notice_id)
            );
            CREATE TABLE IF NOT EXISTS notice_eligibility (
                notice_id TEXT PRIMARY KEY, licenses TEXT NOT NULL, regions TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );
            """
        )
        db.execute(
            """INSERT OR IGNORE INTO profiles VALUES
            ('demo', '모먼트 스튜디오', '서울', ?, ?, 50000000, ?, ?)""",
            (
                json.dumps(["영상제작", "디자인"], ensure_ascii=False),
                json.dumps(["영상", "콘텐츠", "홍보", "디자인"], ensure_ascii=False),
                json.dumps(["사업자등록증", "실적증명서"], ensure_ascii=False),
                now_iso(),
            ),
        )
        existing_title = db.execute("SELECT title FROM notices LIMIT 1").fetchone()
        if existing_title and "?" in existing_title[0]:
            db.execute("DELETE FROM saved_notices")
            db.execute("DELETE FROM notices")
            db.execute("UPDATE profiles SET company_name='모먼트 스튜디오', region='서울', categories=?, keywords=?, capabilities=?, updated_at=? WHERE user_id='demo'", (json.dumps(["영상제작", "디자인"], ensure_ascii=False), json.dumps(["영상", "콘텐츠", "홍보", "디자인"], ensure_ascii=False), json.dumps(["사업자등록증", "실적증명서"], ensure_ascii=False), now_iso()))
        if db.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 0:
            for notice in demo_notices():
                db.execute(
                    """INSERT INTO notices
                    (id,title,agency,region,category,budget,deadline,keywords,documents,source_url,fetched_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        notice["id"], notice["title"], notice["agency"], notice["region"],
                        notice["category"], notice["budget"], notice["deadline"],
                        json.dumps(notice["keywords"], ensure_ascii=False),
                        json.dumps(notice["documents"], ensure_ascii=False), notice["source_url"], now_iso(),
                    ),
                )


def profile_dict(row: sqlite3.Row) -> dict:
    return {
        "company_name": row["company_name"], "region": row["region"],
        "categories": json.loads(row["categories"]), "keywords": json.loads(row["keywords"]),
        "max_budget": row["max_budget"], "capabilities": json.loads(row["capabilities"]),
    }


def score_notice(notice: sqlite3.Row, profile: dict) -> tuple[int, list[str], list[str]]:
    notice_keywords = json.loads(notice["keywords"])
    documents = json.loads(notice["documents"])
    score, reasons = 0, []
    keyword_hits = sorted(set(notice_keywords) & set(profile["keywords"]))
    if keyword_hits:
        points = min(45, 15 * len(keyword_hits))
        score += points
        reasons.append(f"관심 키워드 {', '.join(keyword_hits)} 일치")
    if notice["category"] in profile["categories"]:
        score += 25
        reasons.append(f"주력 업종 {notice['category']} 일치")
    if notice["region"] in ("전국", profile["region"]):
        score += 15
        reasons.append("참가 지역 조건 적합")
    if notice["budget"] <= profile["max_budget"]:
        score += 15
        reasons.append("희망 사업 규모 이내")
    missing = [doc for doc in documents if doc.endswith("확인서") and doc not in profile["capabilities"]]
    return min(score, 100), reasons, missing


def serialize_notice(row: sqlite3.Row, profile: dict, saved: bool) -> dict:
    score, reasons, missing = score_notice(row, profile)
    deadline = date.fromisoformat(row["deadline"])
    return {
        "id": row["id"], "title": row["title"], "agency": row["agency"],
        "region": row["region"], "category": row["category"], "budget": row["budget"],
        "deadline": row["deadline"], "days_left": max(0, (deadline - date.today()).days),
        "keywords": json.loads(row["keywords"]), "documents": json.loads(row["documents"]),
        "source_url": row["source_url"], "match_score": score, "match_reasons": reasons,
        "missing_requirements": missing, "saved": saved,
    }


# Clean Korean demo fixtures and scoring copy. These definitions intentionally
# replace the early MVP fixtures, whose source encoding was damaged.
def demo_notices() -> list[dict]:
    today = date.today()
    rows = [
        ("DEMO-001", "2026 도시브랜드 홍보영상 제작 용역", "서울특별시", "서울", "영상제작", 48_000_000, 5, ["영상", "홍보", "콘텐츠"], ["사업자등록증", "제안서", "실적증명서"]),
        ("DEMO-002", "청년정책 SNS 콘텐츠 기획 및 운영", "한국청소년정책연구원", "전국", "홍보·마케팅", 72_000_000, 8, ["SNS", "콘텐츠", "마케팅"], ["제안서", "가격제안서", "신용평가등급확인서"]),
        ("DEMO-003", "지역문화축제 공식 홈페이지 개편", "서울문화재단", "서울", "소프트웨어", 38_500_000, 3, ["웹사이트", "개발", "디자인"], ["소프트웨어사업자 신고확인서", "제안서"]),
        ("DEMO-004", "공공데이터 활용사례 카드뉴스 디자인", "한국지능정보사회진흥원", "전국", "디자인", 18_000_000, 2, ["디자인", "카드뉴스", "공공데이터"], ["견적서", "포트폴리오"]),
        ("DEMO-005", "관광 숏폼 영상 콘텐츠 제작", "부산관광공사", "부산", "영상제작", 35_000_000, 11, ["영상", "숏폼", "관광"], ["제안서", "실적증명서", "지역제한 확인"]),
        ("DEMO-006", "디지털 전환 교육 프로그램 운영", "중소벤처기업진흥공단", "전국", "교육·컨설팅", 96_000_000, 14, ["교육", "디지털", "컨설팅"], ["제안서", "강사이력", "실적증명서"]),
        ("DEMO-007", "박물관 전시 안내 그래픽 제작", "국립민속박물관", "서울", "디자인", 27_000_000, 6, ["디자인", "전시", "그래픽"], ["제안서", "포트폴리오", "사업자등록증"]),
        ("DEMO-008", "지역상권 홍보 콘텐츠 제작 및 배포", "경기도시장상권진흥원", "경기", "홍보·마케팅", 44_000_000, 9, ["콘텐츠", "홍보", "상권"], ["제안서", "수행계획서", "실적증명서"]),
    ]
    return [{"id": r[0], "title": r[1], "agency": r[2], "region": r[3], "category": r[4], "budget": r[5], "deadline": (today + timedelta(days=r[6])).isoformat(), "keywords": r[7], "documents": r[8], "source_url": "https://www.g2b.go.kr/"} for r in rows]


def infer_category(title: str) -> str:
    rules = [("영상제작", ("영상", "유튜브", "숏폼", "촬영")), ("디자인", ("디자인", "그래픽", "브랜드", "편집", "전시")), ("홍보·마케팅", ("홍보", "마케팅", "SNS", "광고", "콘텐츠")), ("소프트웨어", ("시스템", "홈페이지", "소프트웨어", "정보화", "플랫폼", "개발")), ("교육·컨설팅", ("교육", "컨설팅", "연구", "운영"))]
    return next((category for category, terms in rules if any(term.lower() in title.lower() for term in terms)), "기타 용역")


def infer_keywords(title: str) -> list[str]:
    candidates = ("영상", "콘텐츠", "홍보", "디자인", "SNS", "마케팅", "웹사이트", "개발", "교육", "컨설팅", "연구", "행사", "관광", "공공데이터")
    return [term for term in candidates if term.lower() in title.lower()] or [infer_category(title)]


def score_notice(notice: sqlite3.Row, profile: dict) -> tuple[int, list[str], list[str]]:
    notice_keywords, documents = json.loads(notice["keywords"]), json.loads(notice["documents"])
    score, reasons = 0, []
    keyword_hits = sorted(set(notice_keywords) & set(profile["keywords"]))
    if keyword_hits:
        score += min(45, 15 * len(keyword_hits)); reasons.append(f"관심 키워드 {', '.join(keyword_hits)} 일치")
    if notice["category"] in profile["categories"]:
        score += 25; reasons.append(f"주력 업종 {notice['category']} 일치")
    if notice["region"] in ("전국", profile["region"]) or profile["region"] == "전국":
        score += 15; reasons.append("참가 지역 조건 적합")
    if not notice["budget"] or notice["budget"] <= profile["max_budget"]:
        score += 15; reasons.append("수행 가능 사업 규모")
    missing = [doc for doc in documents if "확인" in doc and doc not in profile["capabilities"]]
    return min(score, 100), reasons, missing


class Handler(BaseHTTPRequestHandler):
    server_version = "Bidmate/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, payload: dict | list, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "pending_cookie", None):
            self.send_header("Set-Cookie", self.pending_cookie)
        self.end_headers()
        self.wfile.write(body)

    def user_id(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            candidate = cookie.get("bidmate_user")
            user_id = candidate.value if candidate else ""
            uuid.UUID(user_id)
        except (ValueError, AttributeError):
            user_id = str(uuid.uuid4())
            self.pending_cookie = f"bidmate_user={user_id}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"
        with connection() as db:
            exists = db.execute("SELECT 1 FROM profiles WHERE user_id=?", (user_id,)).fetchone()
            if not exists:
                demo = db.execute("SELECT * FROM profiles WHERE user_id='demo'").fetchone()
                db.execute(
                    """INSERT INTO profiles(user_id,company_name,region,categories,keywords,max_budget,capabilities,updated_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (user_id, "새 회사", demo["region"], demo["categories"], demo["keywords"],
                     demo["max_budget"], demo["capabilities"], now_iso()),
                )
        return user_id

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length > 50_000:
            raise ValueError("요청이 너무 큽니다.")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        if path == "/api/session":
            self.send_json({"ok": True, "user_id": self.user_id()})
            return
        if path == "/api/health":
            with connection() as db:
                fetched = db.execute("SELECT MAX(fetched_at) FROM notices").fetchone()[0]
            self.send_json({
                "ok": True,
                "data_mode": "live-ready" if os.environ.get("DATA_GO_KR_SERVICE_KEY") else "demo",
                "source": "나라장터 입찰공고정보서비스",
                "last_fetched_at": fetched,
            })
            return
        if path == "/api/profile":
            user_id = self.user_id()
            with connection() as db:
                row = db.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
                self.send_json(profile_dict(row))
            return
        if path == "/api/notices":
            self.list_notices(query)
            return
        if path == "/api/stats":
            user_id = self.user_id()
            with connection() as db:
                profile = profile_dict(db.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone())
                rows = db.execute("SELECT * FROM notices").fetchall()
                scores = [score_notice(row, profile)[0] for row in rows]
                saved = db.execute("SELECT COUNT(*) FROM saved_notices WHERE user_id=?", (user_id,)).fetchone()[0]
                self.send_json({
                    "total": len(rows), "strong_matches": sum(score >= 70 for score in scores),
                    "closing_soon": sum((date.fromisoformat(row["deadline"]) - date.today()).days <= 3 for row in rows),
                    "saved": saved,
                })
            return
        self.serve_static(path)

    def list_notices(self, query: dict) -> None:
        keyword = query.get("q", [""])[0].strip().lower()
        category = query.get("category", [""])[0]
        saved_only = query.get("saved", ["false"])[0] == "true"
        user_id = self.user_id()
        with connection() as db:
            profile = profile_dict(db.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone())
            saved_ids = {row[0] for row in db.execute("SELECT notice_id FROM saved_notices WHERE user_id=?", (user_id,))}
            rows = db.execute("SELECT * FROM notices").fetchall()
            notices = [serialize_notice(row, profile, row["id"] in saved_ids) for row in rows]
        if keyword:
            notices = [n for n in notices if keyword in (n["title"] + n["agency"] + " ".join(n["keywords"])).lower()]
        if category:
            notices = [n for n in notices if n["category"] == category]
        if saved_only:
            notices = [n for n in notices if n["saved"]]
        sort = query.get("sort", ["match"])[0]
        if sort == "deadline":
            notices.sort(key=lambda item: (item["days_left"], -item["match_score"]))
        elif sort == "budget":
            notices.sort(key=lambda item: (-item["budget"], -item["match_score"]))
        else:
            notices.sort(key=lambda item: (-item["match_score"], item["deadline"]))
        self.send_json(notices)

    def do_PUT(self) -> None:
        if urlparse(self.path).path != "/api/profile":
            self.send_json({"error": "지원하지 않는 경로입니다."}, HTTPStatus.NOT_FOUND)
            return
        try:
            user_id = self.user_id()
            body = self.read_json()
            company_name = str(body.get("company_name", "")).strip()
            region = str(body.get("region", "")).strip()
            categories = [str(v).strip() for v in body.get("categories", []) if str(v).strip()]
            keywords = [str(v).strip() for v in body.get("keywords", []) if str(v).strip()]
            max_budget = int(body.get("max_budget", 0))
            if not company_name or not region or not categories or not keywords or max_budget <= 0:
                raise ValueError("회사명, 지역, 업종, 키워드와 사업 규모를 모두 입력해주세요.")
            with connection() as db:
                db.execute(
                    """UPDATE profiles SET company_name=?,region=?,categories=?,keywords=?,max_budget=?,updated_at=?
                    WHERE user_id=?""",
                    (company_name, region, json.dumps(categories, ensure_ascii=False),
                     json.dumps(keywords, ensure_ascii=False), max_budget, now_iso(), user_id),
                )
            self.send_json({"ok": True})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/sync":
            try:
                count = sync_public_notices()
                self.send_json({"ok": True, "count": count})
            except RuntimeError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return
        if path.startswith("/api/notices/") and path.endswith("/save"):
            notice_id = path.split("/")[3]
            user_id = self.user_id()
            with connection() as db:
                exists = db.execute(
                    "SELECT 1 FROM saved_notices WHERE user_id=? AND notice_id=?", (user_id, notice_id)
                ).fetchone()
                if exists:
                    db.execute("DELETE FROM saved_notices WHERE user_id=? AND notice_id=?", (user_id, notice_id))
                    saved = False
                else:
                    if not db.execute("SELECT 1 FROM notices WHERE id=?", (notice_id,)).fetchone():
                        self.send_json({"error": "공고를 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
                        return
                    db.execute("INSERT INTO saved_notices VALUES (?, ?, ?)", (user_id, notice_id, now_iso()))
                    saved = True
            self.send_json({"saved": saved})
            return
        self.send_json({"error": "지원하지 않는 경로입니다."}, HTTPStatus.NOT_FOUND)

    def serve_static(self, path: str) -> None:
        requested = "index.html" if path == "/" else path.lstrip("/")
        file_path = (PUBLIC_DIR / requested).resolve()
        try:
            file_path.relative_to(PUBLIC_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not file_path.is_file():
            file_path = PUBLIC_DIR / "index.html"
        body = file_path.read_bytes()
        kind = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    initialize_database()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Bidmate가 http://{HOST}:{PORT} 에서 실행 중입니다.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버를 종료합니다.")
    finally:
        server.server_close()

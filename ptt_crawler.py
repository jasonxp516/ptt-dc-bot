import asyncio
import copy
import heapq
import logging
import os
import random
import re
import time
import urllib.parse
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiohttp
from bs4 import BeautifulSoup

from boards import (
    BOARD_ALIASES_MAP,
    COMMON_BOARDS_MAP,
    PTT_BOARD_DIRECTORY,
    infer_board_from_query,
    resolve_board_alias,
)
from sold_rules import (
    AUTHOR_PUSH_SOLD_REGEX,
    AVAILABLE,
    LIST_ITEM_MARKER_REGEX,
    NO_MAIL,
    RESERVED,
    SOLD,
    STRONG_SOLD_REGEX,
    TITLE_SOLD_REGEX,
    classify_author_pushes,
    classify_text_state,
    classify_title_state,
    is_title_sold,
    line_matches_item_token,
    normalize_nfkc,
    pick_item_token,
)
from timeutil import now_tpe, parse_ptt_month_day

logger = logging.getLogger("ptt_crawler")

CRAWL_P0_INTERACTIVE = 0
CRAWL_P1_POLL = 1
CRAWL_P2_SOLD_CHECK = 2
CRAWL_P3_SCAN = 3

DEFAULT_PTT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

USER_AGENTS = [
    DEFAULT_PTT_USER_AGENT,
]

VALID_BOARD_NAME_REGEX = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
VALID_PTT_ARTICLE_URL_REGEX = re.compile(
    r"^https://www\.ptt\.cc/bbs/[A-Za-z0-9_\-]{1,32}/M\.\d+\.A\.[A-Za-z0-9]{1,8}\.html$"
)


def is_valid_board_name(board: str) -> bool:
    return bool(board and VALID_BOARD_NAME_REGEX.match(board.strip()))


def is_valid_ptt_article_url(url: str) -> bool:
    return bool(url and VALID_PTT_ARTICLE_URL_REGEX.match(url.strip()))


def _token_matches_title_safe(token: str, title_lower: str, title_compact: str) -> bool:
    if token.isdigit():
        return bool(re.search(rf"(?<!\d){re.escape(token)}(?!\d)", title_lower))
    return (token in title_lower) or (token in title_compact)


def apply_keyword_view(base_detail: Dict, target_keyword: str = "") -> Dict:
    """
    Fix P1-1 & Phase 1.3: Derive a keyword-specific view from a keyword-agnostic `base_detail` dict
    without mutating the cached object (deep copy of list fields)!
    - Selects the primary item token via `pick_item_token(pos_tokens)` independent of token order.
    - Respects digit boundaries and `-exclusion` tokens (`neg_tokens`) when matching item/price lines.
    - Normalizes fullwidth list markers (`１．`, `①`) via NFKC and uses unified `LIST_ITEM_MARKER_REGEX`.
    """
    view = copy.deepcopy(base_detail)
    if "sold_state" not in view:
        view["sold_state"] = SOLD if view.get("is_sold") else AVAILABLE

    raw_price_lines = list(view.get("raw_price_lines") or [])
    body_lines = (view.get("body_only") or view.get("full_body") or "").splitlines()

    raw_tokens = [t for t in normalize_nfkc(target_keyword or "").lower().split() if t]
    pos_tokens = [t for t in raw_tokens if not t.startswith("-")]
    neg_tokens = [t[1:] for t in raw_tokens if t.startswith("-") and len(t) > 1]
    item_token = pick_item_token(pos_tokens)

    if raw_price_lines:
        if item_token and len(raw_price_lines) > 1:
            matched_p_lines = [
                pl for pl in raw_price_lines if line_matches_item_token(pl, item_token, neg_tokens)
            ]
            if matched_p_lines:
                view["extracted_price"] = " / ".join(matched_p_lines)[:100]
            else:
                view["extracted_price"] = " / ".join(raw_price_lines)[:100]
        else:
            view["extracted_price"] = " / ".join(raw_price_lines)[:100]

    if view.get("is_sold"):
        return view

    # Check head/tail lines: if a numbered/bulleted item line is for a DIFFERENT keyword (or we are building the base view), skip it!
    if body_lines:
        head_tail = body_lines[:3] + body_lines[-4:]
        for ln in head_tail:
            ln_nfkc = normalize_nfkc(ln)
            is_list_item = bool(LIST_ITEM_MARKER_REGEX.match(ln_nfkc))
            if is_list_item:
                if not item_token:
                    continue
                if not line_matches_item_token(ln, item_token, neg_tokens):
                    continue
                st, _ = classify_text_state(ln, require_short_or_boundary=False)
            else:
                st, _ = classify_text_state(ln, require_short_or_boundary=True)

            if st != AVAILABLE:
                view["is_sold"] = True
                view["sold_state"] = st
                view["sold_reason"] = f"內文標示：{ln[:45]}"
                return view

    if not item_token:
        if view.get("extracted_price"):
            # Only mark global sold if there is a single price line or all price lines are sold
            if len(raw_price_lines) <= 1:
                st, _ = classify_text_state(view["extracted_price"], require_short_or_boundary=False)
                if st != AVAILABLE:
                    view["is_sold"] = True
                    view["sold_state"] = st
                    view["sold_reason"] = f"價格欄標示：{view['extracted_price'][:45]}"
            else:
                line_states = [
                    classify_text_state(pl, require_short_or_boundary=False)[0]
                    for pl in raw_price_lines
                ]
                if line_states and all(s != AVAILABLE for s in line_states):
                    view["is_sold"] = True
                    view["sold_state"] = line_states[0]
                    view["sold_reason"] = f"價格欄標示：{view['extracted_price'][:45]}"
        return view

    # Also check matched price lines for item-specific sold markers
    if raw_price_lines:
        for pl in raw_price_lines:
            if line_matches_item_token(pl, item_token, neg_tokens):
                st, _ = classify_text_state(pl, require_short_or_boundary=False)
                if st != AVAILABLE:
                    view["is_sold"] = True
                    view["sold_state"] = st
                    view["sold_reason"] = f"品項價格標示：{pl[:50]}"
                    return view

    # Keyword-specific line window check in body
    for idx, ln in enumerate(body_lines):
        if line_matches_item_token(ln, item_token, neg_tokens):
            # If this is a numbered list item, only inspect this line (and non-numbered continuation lines)
            window_lines = [ln]
            for next_ln in body_lines[idx + 1 : idx + 3]:
                if LIST_ITEM_MARKER_REGEX.match(normalize_nfkc(next_ln)):
                    break
                window_lines.append(next_ln)
            window = " | ".join(window_lines)
            st, _ = classify_text_state(window, require_short_or_boundary=False)
            if st != AVAILABLE:
                view["is_sold"] = True
                view["sold_state"] = st
                view["sold_reason"] = f"品項旁標示：{window[:50]}"
                return view

    return view


def is_article_date_too_old(date_str: str, max_age_days: int = 21, ref_dt: Optional[datetime] = None) -> bool:
    """
    Fix B7 & Phase 3.4: Parse PTT list date 'MM/DD' in Asia/Taipei timezone with cross-year
    and leap-year ('02/29') safety, and check if older than `max_age_days`.
    """
    if not date_str:
        return False
    ref = ref_dt if ref_dt is not None else now_tpe()
    parsed = parse_ptt_month_day(date_str, ref_dt=ref)
    if parsed is None:
        return False
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=parsed.tzinfo)
    age_days = (ref - parsed).days
    return age_days > max_age_days


class PTTCrawler:
    """
    Asynchronous PTT Web Crawler with built-in Anti-Ban protections,
    priority scheduling (P0 interactive > P1 poll > P2 sold check > P3 batch scan),
    article detail caching, two-tier sold detection, and native PTT board search.
    """

    BASE_URL = "https://www.ptt.cc"
    apply_keyword_view = staticmethod(apply_keyword_view)

    def __init__(
        self,
        min_request_delay: float = 2.0,
        max_request_delay: float = 4.2,
        storage: Optional[Any] = None,
        user_agent: Optional[str] = None,
    ):
        self.min_request_delay = min_request_delay
        self.max_request_delay = max_request_delay
        self.storage = storage
        self.user_agent = (
            user_agent
            or os.getenv("PTT_USER_AGENT", "").strip()
            or DEFAULT_PTT_USER_AGENT
        )
        self._session: Optional[aiohttp.ClientSession] = None
        self._req_lock = asyncio.Lock()
        self._active_req: bool = False
        self._wait_heap: List[Tuple[int, int, asyncio.Event]] = []
        self._wait_seq: int = 0
        self._last_request_ts: float = 0.0
        self.consecutive_errors: int = 0
        self.cooldown_until: float = 0.0
        self.total_requests: int = 0

    async def _acquire_priority_slot(self, priority: int) -> None:
        if not self._active_req and not self._wait_heap:
            self._active_req = True
            return
        ev = asyncio.Event()
        self._wait_seq += 1
        heapq.heappush(self._wait_heap, (int(priority), self._wait_seq, ev))
        await ev.wait()

    def _release_priority_slot(self) -> None:
        while self._wait_heap:
            _, _, next_ev = heapq.heappop(self._wait_heap)
            if not next_ev.is_set():
                next_ev.set()
                return
        self._active_req = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15, connect=8)
            connector = aiohttp.TCPConnector(limit=4, ttl_dns_cache=300)
            self._session = aiohttp.ClientSession(
                cookies={"over18": "1"},
                timeout=timeout,
                connector=connector,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def is_in_cooldown(self) -> Tuple[bool, int]:
        now = time.time()
        if now < self.cooldown_until:
            return True, int(self.cooldown_until - now)
        return False, 0

    def _build_headers(self, referer: str = "https://www.ptt.cc/bbs/index.html") -> Dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": referer,
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    async def _safe_get(
        self,
        url: str,
        referer: Optional[str] = None,
        priority: int = CRAWL_P1_POLL,
    ) -> Tuple[int, Optional[str]]:
        if not url or not url.startswith("https://www.ptt.cc/bbs/"):
            logger.warning(f"安全防護攔截非 PTT 網域請求: {url}")
            return 400, None

        in_cd, remain = self.is_in_cooldown()
        if in_cd:
            logger.warning(f"防封鎖保護冷卻中，尚餘 {remain} 秒，跳過請求: {url}")
            return -1, None

        await self._acquire_priority_slot(priority)
        try:
            async with self._req_lock:
                in_cd_inner, remain_inner = self.is_in_cooldown()
                if in_cd_inner:
                    logger.warning(f"防封鎖保護冷卻中，尚餘 {remain_inner} 秒，跳過請求: {url}")
                    return -1, None

                now = time.time()
                elapsed = now - self._last_request_ts
                target_delay = random.uniform(self.min_request_delay, self.max_request_delay)
                if elapsed < target_delay and self._last_request_ts > 0:
                    await asyncio.sleep(target_delay - elapsed)

                session = await self._get_session()
                headers = self._build_headers(referer=referer or "https://www.ptt.cc/bbs/index.html")

                try:
                    self._last_request_ts = time.time()
                    self.total_requests += 1
                    curr_url = url
                    hops = 0
                    max_redirects = 2

                    while True:
                        async with session.get(curr_url, headers=headers, allow_redirects=False) as resp:
                            status = resp.status
                            if status == 200:
                                text = await resp.text(errors="replace")
                                self.consecutive_errors = 0
                                return 200, text
                            elif status in (301, 302, 303, 307, 308):
                                hops += 1
                                if hops > max_redirects:
                                    logger.warning(
                                        f"安全防護阻斷過多重定向 (>{max_redirects} hops): {curr_url}"
                                    )
                                    return 400, None
                                loc = resp.headers.get("Location", "")
                                if not loc:
                                    return 400, None
                                resolved_loc = urllib.parse.urljoin(curr_url, loc)
                                if not resolved_loc.startswith("https://www.ptt.cc/bbs/"):
                                    logger.warning(
                                        f"安全防護阻斷不安全之重定向 (SSRF 防護): {resolved_loc}"
                                    )
                                    return 400, None
                                curr_url = resolved_loc
                                continue
                            elif status == 404:
                                return 404, None
                            elif status in (429, 403, 503):
                                self.consecutive_errors += 1
                                backoff = min(
                                    1800, 120 * (2 ** min(self.consecutive_errors - 1, 4))
                                ) + random.randint(15, 45)
                                self.cooldown_until = time.time() + backoff
                                logger.warning(
                                    f"收到 HTTP {status} ({curr_url})，啟動防封鎖指數退避冷卻 {backoff} 秒！"
                                )
                                return status, None
                            else:
                                self.consecutive_errors += 1
                                logger.warning(f"非預期 HTTP 狀態碼 {status}: {curr_url}")
                                return status, None
                except Exception as e:
                    self.consecutive_errors += 1
                    if self.consecutive_errors >= 4:
                        backoff = min(900, 60 * self.consecutive_errors)
                        self.cooldown_until = time.time() + backoff
                        logger.warning(f"連續連線異常 ({e})，暫停請求 {backoff} 秒")
                    else:
                        logger.warning(f"請求發生例外 ({url}): {e}")
                    return 0, None
        finally:
            self._release_priority_slot()

    async def verify_board(self, board_input: str) -> Tuple[bool, str, str]:
        board_input = resolve_board_alias(board_input.strip())
        if not is_valid_board_name(board_input):
            return (
                False,
                board_input,
                f"看板名稱 `{board_input}` 格式不合法（僅允許英數字、底線與連字號，或輸入常見中文別名如「相機」、「筆電」、「蘋果」）",
            )
        # Fast-path: if board is in known directory/aliases, return immediately without network probes
        if board_input.lower() in COMMON_BOARDS_MAP:
            return True, COMMON_BOARDS_MAP[board_input.lower()], "驗證成功"

        candidates = []
        for variant in [board_input, board_input.capitalize(), board_input.upper(), board_input.lower()]:
            if variant not in candidates:
                candidates.append(variant)

        # Probe at most 2 candidate casings over network to avoid amplifying requests on typos
        for candidate in candidates[:2]:
            url = f"{self.BASE_URL}/bbs/{candidate}/index.html"
            status, html = await self._safe_get(url, priority=CRAWL_P0_INTERACTIVE)
            if status == 200 and html:
                soup = BeautifulSoup(html, "lxml")
                board_link = soup.select_one("#topbar a.board")
                if board_link and board_link.get("href"):
                    m = re.search(r"/bbs/([^/]+)/index\.html", board_link["href"])
                    if m:
                        found_board = m.group(1)
                        return True, COMMON_BOARDS_MAP.get(found_board.lower(), found_board), "驗證成功"
                return True, COMMON_BOARDS_MAP.get(candidate.lower(), candidate), "驗證成功"
            elif status in (-1, 429, 403, 503):
                return False, board_input, f"PTT 伺服器忙碌或觸發防封鎖保護 (HTTP {status})，請稍後再試"

        return False, board_input, f"找不到看板 `{board_input}`，請確認 PTT 看板英文名稱是否正確"

    async def find_boards_by_query(self, query: str) -> List[Dict[str, str]]:
        q_lower = query.strip().lower()
        tokens = [t for t in re.split(r"[\s,，、/]+", q_lower) if t]

        results = []
        seen_names = set()

        for item in PTT_BOARD_DIRECTORY:
            haystack = f"{item['name']} {item['zh_name']} {item['tags']}".lower()
            score = 0
            if q_lower in haystack:
                score += 10
            for tok in tokens:
                if tok in haystack:
                    score += 3
                else:
                    # Only apply 2-character bigram fuzzy matching for CJK characters
                    for i in range(len(tok) - 1):
                        bg = tok[i : i + 2]
                        if re.match(r"^[\u4e00-\u9fff]{2}$", bg) and bg in haystack:
                            score += 1
            if score > 0:
                results.append((score, item))
                seen_names.add(item["name"].lower())

        results.sort(key=lambda x: x[0], reverse=True)
        matched_list = [r[1] for r in results[:8]]

        if len(matched_list) < 3:
            status, html = await self._safe_get(
                f"{self.BASE_URL}/bbs/hotboards.html", priority=CRAWL_P0_INTERACTIVE
            )
            if status == 200 and html:
                soup = BeautifulSoup(html, "lxml")
                for b_ent in soup.select("div.b-ent"):
                    b_name_el = b_ent.select_one("div.board-name")
                    b_class_el = b_ent.select_one("div.board-class")
                    b_title_el = b_ent.select_one("div.board-title")
                    if not b_name_el:
                        continue
                    b_name = b_name_el.get_text(strip=True)
                    if b_name.lower() in seen_names:
                        continue
                    b_class = b_class_el.get_text(strip=True) if b_class_el else ""
                    b_title = b_title_el.get_text(strip=True) if b_title_el else ""
                    haystack = f"{b_name} {b_class} {b_title}".lower()
                    if any(tok in haystack for tok in tokens) or q_lower in haystack:
                        matched_list.append(
                            {
                                "name": b_name,
                                "zh_name": f"[{b_class}] {b_title}",
                                "tags": b_title,
                            }
                        )
                        seen_names.add(b_name.lower())

        return matched_list[:10]

    def _parse_list_page(self, html: str, board: str) -> Tuple[List[Dict], Optional[str]]:
        soup = BeautifulSoup(html, "lxml")

        prev_url = None
        for btn in soup.select("div.btn-group-paging a.btn.wide"):
            if "上頁" in btn.get_text():
                href = btn.get("href")
                if href and href.startswith("/bbs/"):
                    prev_url = f"{self.BASE_URL}{href}"
                break

        container = soup.select_one("div.r-list-container")
        if not container:
            return [], prev_url

        articles = []
        for child in container.find_all("div", recursive=False):
            classes = child.get("class") or []
            if "r-list-sep" in classes:
                break
            if "r-ent" not in classes:
                continue

            title_a = child.select_one("div.title > a")
            if not title_a:
                continue

            title = title_a.get_text(strip=True)
            href = title_a.get("href", "").strip()
            if not href:
                continue

            if re.match(r"^\s*\[公告\]", title):
                continue

            author_el = child.select_one("div.meta > div.author")
            date_el = child.select_one("div.meta > div.date")
            nrec_el = child.select_one("div.nrec")

            author = author_el.get_text(strip=True) if author_el else "未知"
            date_str = date_el.get_text(strip=True) if date_el else ""
            nrec = nrec_el.get_text(strip=True) if nrec_el else "0"

            bracket_m = re.match(r"^\s*(?:Re:\s*|Fw:\s*)?\[([^\]]+)\]", title)
            category = bracket_m.group(1).strip() if bracket_m else "一般"
            # Fix B13: Generalize malformed bracket detection across 賣/售/徵/換
            if not re.search(r"[賣售徵換]", category):
                m_head = re.search(r"(賣|售|徵|換)/", title[:18])
                if m_head:
                    category = m_head.group(1)

            articles.append(
                {
                    "board": board,
                    "title": title,
                    "url": f"{self.BASE_URL}{href}",
                    "author": author,
                    "date": date_str,
                    "nrec": nrec or "0",
                    "category": category,
                }
            )

        return articles, prev_url

    async def fetch_board_articles(
        self, board: str, fetch_prev_if_few: bool = True, max_pages: int = 1
    ) -> Tuple[bool, List[Dict], str]:
        index_url = f"{self.BASE_URL}/bbs/{board}/index.html"
        status, html = await self._safe_get(index_url)
        if status != 200 or not html:
            return False, [], f"HTTP {status}" if status else "連線逾時或錯誤"

        current_articles, prev_url = self._parse_list_page(html, board)
        all_articles = list(current_articles)

        pages_to_fetch = max_pages - 1
        if max_pages == 1 and fetch_prev_if_few and len(current_articles) < 8 and prev_url:
            pages_to_fetch = 1

        curr_prev = prev_url
        referer = index_url
        while pages_to_fetch > 0 and curr_prev:
            p_status, p_html = await self._safe_get(curr_prev, referer=referer)
            if p_status != 200 or not p_html:
                break
            older_articles, next_prev = self._parse_list_page(p_html, board)
            all_articles = older_articles + all_articles
            referer = curr_prev
            curr_prev = next_prev
            pages_to_fetch -= 1

        seen_urls = set()
        deduped = []
        for art in all_articles:
            if art["url"] not in seen_urls:
                seen_urls.add(art["url"])
                deduped.append(art)

        return True, deduped, f"正常 ({len(deduped)} 篇)"

    async def search_board_by_ptt_query(
        self,
        board: str,
        query: str,
        max_pages: int = 1,
        priority: int = CRAWL_P0_INTERACTIVE,
    ) -> Tuple[bool, List[Dict], str]:
        if board.lower() in COMMON_BOARDS_MAP:
            board = COMMON_BOARDS_MAP[board.lower()]

        # Strip exclusion tokens (-ti) from PTT server query so PTT search finds the base keyword
        pos_tokens = [t for t in query.strip().split() if not t.startswith("-")]
        clean_query = " ".join(pos_tokens) if pos_tokens else query.strip()

        queries_to_run = [clean_query]
        spaced_variant = re.sub(
            r"(\d{3,4})\s*(ti|xt|xtx|super|gre)\b", r"\1 \2", clean_query, flags=re.IGNORECASE
        )
        unspaced_variant = re.sub(
            r"(\d{3,4})\s+(ti|xt|xtx|super|gre)\b", r"\1\2", clean_query, flags=re.IGNORECASE
        )
        for v in (spaced_variant, unspaced_variant):
            if v.lower() not in [q.lower() for q in queries_to_run]:
                queries_to_run.append(v)

        collected: List[Dict] = []
        seen_urls = set()
        any_ok = False
        last_err_status = 0

        for q in queries_to_run:
            encoded_q = urllib.parse.quote(q)
            for page in range(1, max_pages + 1):
                if page == 1:
                    url = f"{self.BASE_URL}/bbs/{board}/search?q={encoded_q}"
                else:
                    url = f"{self.BASE_URL}/bbs/{board}/search?page={page}&q={encoded_q}"

                status, html = await self._safe_get(url, priority=priority)
                if status != 200 or not html:
                    last_err_status = status
                    break

                any_ok = True
                page_arts, _ = self._parse_list_page(html, board)
                if not page_arts:
                    break
                for art in page_arts:
                    if art["url"] not in seen_urls:
                        seen_urls.add(art["url"])
                        collected.append(art)

        if not any_ok and last_err_status != 0:
            err_desc = "防封鎖冷卻中" if last_err_status == -1 else f"HTTP {last_err_status}"
            return False, [], err_desc

        return True, collected, f"搜尋完成 (共 {len(collected)} 篇)"

    @staticmethod
    def _empty_detail(
        sold_reason: Optional[str] = None,
        fetch_status: int = 0,
        deleted: bool = False,
    ) -> Dict:
        return {
            "snippet": None,
            "full_body": "",
            "body_only": "",
            "raw_price_lines": [],
            "image_url": None,
            "post_time": None,
            "extracted_price": None,
            "extracted_location": None,
            "is_sold": False,
            "sold_state": AVAILABLE,
            "sold_reason": sold_reason,
            "buyer_queue_count": 0,
            "author_notes": [],
            "recent_pushes": [],
            "ai_verified_by": None,
            "fetch_status": fetch_status,
            "deleted": deleted,
        }

    async def fetch_article_full_detail(
        self,
        article_url: str,
        article_title: str = "",
        article_author: str = "",
        target_keyword: str = "",
        use_cache: bool = True,
        force_refresh: bool = False,
        priority: int = CRAWL_P1_POLL,
    ) -> Dict:
        """
        Fetch and parse a PTT article with 10-minute caching (Fix C3) and two-tier sold detection (Fix B5).
        """
        if force_refresh:
            use_cache = False

        if not is_valid_ptt_article_url(article_url):
            return self._empty_detail(
                sold_reason="無效的 PTT 文章網址",
                fetch_status=0,
                deleted=False,
            )

        if use_cache and self.storage is not None:
            cached = self.storage.get_cached_article(article_url)
            if cached is not None:
                return apply_keyword_view(cached, target_keyword)

        status, html = await self._safe_get(article_url, priority=priority)
        if status == 404:
            return self._empty_detail(
                sold_reason="原文已刪除或不存在 (404)",
                fetch_status=404,
                deleted=True,
            )
        if status != 200 or not html:
            return self._empty_detail(
                sold_reason=None,
                fetch_status=status,
                deleted=False,
            )

        if len(html) > 32768:
            parsed = await asyncio.to_thread(
                self._parse_article_detail_sync,
                html,
                article_title,
                article_author,
                target_keyword,
                article_url,
            )
        else:
            parsed = self._parse_article_detail_sync(
                html,
                article_title=article_title,
                article_author=article_author,
                target_keyword=target_keyword,
                article_url=article_url,
            )
        parsed["fetch_status"] = 200
        parsed["deleted"] = False
        return parsed

    def _parse_article_detail_sync(
        self,
        html: str,
        article_title: str = "",
        article_author: str = "",
        target_keyword: str = "",
        article_url: str = "",
    ) -> Dict:
        result = self._empty_detail(fetch_status=200, deleted=False)

        if article_title:
            t_state, t_reason = classify_title_state(article_title)
            if t_state != AVAILABLE:
                result["is_sold"] = True
                result["sold_state"] = t_state
                result["sold_reason"] = t_reason

        try:
            soup = BeautifulSoup(html, "lxml")
            main_content = soup.select_one("#main-content")
            if not main_content:
                return result

            # 1. Extract metadata
            for meta in main_content.select("div.article-metaline, div.article-metaline-right"):
                tag = meta.select_one("span.article-meta-tag")
                val = meta.select_one("span.article-meta-value")
                if tag and val:
                    t_txt = tag.get_text(strip=True)
                    v_txt = val.get_text(strip=True)
                    if "時間" in t_txt:
                        result["post_time"] = v_txt
                    elif "標題" in t_txt and not article_title:
                        article_title = v_txt
                        t_state, t_reason = classify_title_state(article_title)
                        if t_state != AVAILABLE:
                            result["is_sold"] = True
                            result["sold_state"] = t_state
                            result["sold_reason"] = t_reason
                    elif "作者" in t_txt and not article_author:
                        article_author = v_txt.split("(")[0].strip()
                meta.decompose()

            # 2. Inspect structured div.push elements before decomposing (in chronological DOM order)
            author_id_clean = article_author.split("(")[0].strip().lower() if article_author else ""
            buyer_queue_users = set()
            dom_author_pushes: List[str] = []

            for push in main_content.select("div.push"):
                uid_el = push.select_one("span.push-userid")
                cnt_el = push.select_one("span.push-content")
                if not uid_el or not cnt_el:
                    push.decompose()
                    continue

                uid = uid_el.get_text(strip=True)
                content = cnt_el.get_text(strip=True).lstrip(": ").strip()

                if author_id_clean and uid.lower() == author_id_clean:
                    dom_author_pushes.append(content)
                else:
                    result["recent_pushes"].append(f"{uid}: {content}")
                    if re.search(r"(排|收|已私|站內|私訊|已信|寄信|全收|已匯款)", content):
                        buyer_queue_users.add(uid.lower())

                push.decompose()

            for extra in main_content.select("div.richcontent"):
                extra.decompose()

            raw_all_text = main_content.get_text("\n")

            sig_parts = re.split(r"\n\s*--\s*\n", raw_all_text, maxsplit=1)
            if len(sig_parts) == 2:
                raw_body_text = sig_parts[0]
                post_sig_text = sig_parts[1]
            else:
                raw_body_text = raw_all_text
                post_sig_text = ""

            if post_sig_text:
                for sig_line in post_sig_text.splitlines():
                    s_ln = sig_line.strip()
                    if (
                        not s_ln
                        or s_ln.startswith("※ 發信站:")
                        or s_ln.startswith("※ 文章網址:")
                        or s_ln.startswith("※ 編輯:")
                    ):
                        continue
                    m_inline_push = re.match(r"^[推噓→]\s+([A-Za-z0-9_]+)\s*:\s*(.+)$", s_ln)
                    if m_inline_push:
                        p_uid = m_inline_push.group(1).strip()
                        p_cnt = m_inline_push.group(2).strip()
                        p_cnt_clean = re.sub(
                            r"\s*\d+\.\d+\.\d+\.\d+\s+\d{2}/\d{2}\s+\d{2}:\d{2}\s*$", "", p_cnt
                        ).strip()
                        if author_id_clean and p_uid.lower() == author_id_clean:
                            if p_cnt_clean not in result["author_notes"] and p_cnt_clean not in dom_author_pushes:
                                result["author_notes"].append(p_cnt_clean)
                        else:
                            entry = f"{p_uid}: {p_cnt_clean}"
                            if entry not in result["recent_pushes"]:
                                result["recent_pushes"].append(entry)
                            if re.search(r"(排|收|已私|站內|私訊|已信|寄信|全收|已匯款)", p_cnt_clean):
                                buyer_queue_users.add(p_uid.lower())
                    else:
                        result["author_notes"].append(s_ln)

            # Append structured DOM pushes after post-signature supplements so chronological order is preserved
            result["author_notes"].extend(dom_author_pushes)

            # Evaluate all author notes/pushes chronologically (supports reopen override)
            if result["author_notes"]:
                push_state, push_reason = classify_author_pushes(result["author_notes"])
                if push_state != AVAILABLE:
                    result["is_sold"] = True
                    result["sold_state"] = push_state
                    result["sold_reason"] = push_reason

            result["buyer_queue_count"] = len(buyer_queue_users)
            result["recent_pushes"] = result["recent_pushes"][-10:]

            img_match = re.search(
                r"(https?://(?:i\.imgur\.com|imgur\.com|meee\.com\.tw)/[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|webp|gif))",
                raw_body_text,
                re.IGNORECASE,
            )
            if img_match:
                result["image_url"] = img_match.group(1)

            lines = [line.strip() for line in raw_body_text.splitlines() if line.strip()]
            cleaned_body = "\n".join(lines)
            result["body_only"] = cleaned_body[:2200]
            if result["author_notes"]:
                notes_block = "\n【作者補充/推文】： " + " / ".join(result["author_notes"])
                cleaned_body_with_notes = cleaned_body + notes_block
            else:
                cleaned_body_with_notes = cleaned_body

            result["full_body"] = cleaned_body_with_notes[:2500]
            result["snippet"] = (
                cleaned_body_with_notes[:350].rstrip() + " ..."
                if len(cleaned_body_with_notes) > 350
                else cleaned_body_with_notes
            )

            # Extract ALL raw price lines (keyword-agnostic for cache purity, Fix P1-1; supports HardwareSale, DC_Sale, MacShop, Gamesale, etc.)
            for idx, ln in enumerate(lines):
                if re.search(
                    r"(?:◎?\s*[\[\[【]?\s*(?:欲售價格|物品售價|商品售價|商品價格|欲售金額|售\s*價|價\s*格)\s*[\]】]?)",
                    ln,
                    re.IGNORECASE,
                ):
                    after_colon = re.sub(
                        r"^.*?(?:欲售價格|物品售價|商品售價|商品價格|欲售金額|售\s*價|價\s*格)\s*[\]】]?\s*[:：]?\s*",
                        "",
                        ln,
                    ).strip()
                    price_lines = []
                    if after_colon and "沒有明確價格" not in after_colon:
                        price_lines.append(after_colon)
                    for sub_ln in lines[idx + 1 : idx + 6]:
                        if (
                            sub_ln.startswith(("◎", "★", "【"))
                            or re.match(
                                r"^[\[\[【]?(?:品樣狀況|實物照片|保固證明|詳細說明|交易方式|交易地區|交易地點|連絡方式|聯絡方式|商品照|照片|拍賣連結|其他說明)",
                                sub_ln,
                            )
                        ):
                            break
                        if "沒有明確價格" in sub_ln or "水桶" in sub_ln:
                            continue
                        price_lines.append(sub_ln)
                    if price_lines:
                        result["raw_price_lines"] = price_lines
                        result["extracted_price"] = " / ".join(price_lines)[:100]
                    break

            # Extract Location / Trading method
            for idx, ln in enumerate(lines):
                if re.search(
                    r"(?:◎?\s*[\[\[【]?\s*(?:交易方式|交易地點|交易地區|面交地點)\s*[\]】]?)",
                    ln,
                    re.IGNORECASE,
                ):
                    after_colon = re.sub(
                        r"^.*?(?:交易方式(?:、地區)?|交易地點|交易地區|面交地點)\s*[\]】]?\s*[:：]?\s*",
                        "",
                        ln,
                    ).strip()
                    if after_colon and not after_colon.startswith("("):
                        result["extracted_location"] = after_colon[:80]
                    elif (
                        idx + 1 < len(lines)
                        and not lines[idx + 1].startswith(("◎", "★", "【", "["))
                    ):
                        result["extracted_location"] = lines[idx + 1][:80]
                    break

            # Global (keyword-agnostic) body sold marker check: only standalone full-post sold lines
            if not result["is_sold"]:
                for ln in lines:
                    if LIST_ITEM_MARKER_REGEX.match(normalize_nfkc(ln)):
                        continue
                    if re.match(
                        r"^(?:已售出|售出|暫售|全售出|皆售出|皆已售出|已全部售出|撤售|已洽談|洽談中|"
                        r"洽中(?![壢山和正區路街站醫央興原華信安港投園清友科工學])|"
                        r"不用再來信|不用來信|暫勿來信)\s*(?:[，,。!！\-/｜|].*)?$",
                        ln,
                        re.IGNORECASE,
                    ):
                        st, _ = classify_text_state(ln, require_short_or_boundary=True)
                        if st != AVAILABLE:
                            result["is_sold"] = True
                            result["sold_state"] = st
                            result["sold_reason"] = f"內文標示：{ln[:45]}"
                            break

            # Cache the pure keyword-agnostic result BEFORE applying any target_keyword view (Fix P1-1)
            if self.storage is not None and article_url:
                self.storage.set_cached_article(article_url, result)

            return apply_keyword_view(result, target_keyword)
        except Exception as e:
            logger.debug(f"解析文章詳情失敗 ({article_url}): {e}")
            return result

    async def scan_unsold_items(
        self,
        board: str,
        keywords: List[str],
        max_per_keyword: int = 5,
        max_checked_per_keyword: int = 10,
        max_age_days: int = 21,
        category_filter: str = "賣",
        ai_semantic_verifier: Optional[Callable[[Dict, Dict, str], Any]] = None,
    ) -> List[Dict]:
        """
        Scan past matching articles on `board` for each keyword in `keywords`:
        - Fix B6: Separate `max_per_keyword` (desired available results) from `max_checked_per_keyword` (max articles inspected).
        - Fix B7: Skip stale articles older than `max_age_days` (default 21 days).
        - Fix B8 & P2-5: Support `-exclusion` tokens in keywords and pass clean positive keyword to AI.
        """
        if board.lower() in COMMON_BOARDS_MAP:
            board = COMMON_BOARDS_MAP[board.lower()]

        unsold_items = []
        seen_urls = set()

        for kw in keywords:
            ok, articles, _ = await self.search_board_by_ptt_query(
                board, kw, max_pages=1, priority=CRAWL_P3_SCAN
            )
            if not ok or not articles:
                continue

            checked_for_kw = 0
            found_for_kw = 0
            raw_tokens = [t for t in kw.lower().split() if t]
            pos_tokens = [t for t in raw_tokens if not t.startswith("-")]
            neg_tokens = [t[1:] for t in raw_tokens if t.startswith("-") and len(t) > 1]
            clean_pos_kw = " ".join(pos_tokens) if pos_tokens else kw

            for art in articles:
                if art["url"] in seen_urls:
                    continue

                if is_article_date_too_old(art.get("date", ""), max_age_days=max_age_days):
                    continue

                title = art["title"]
                if category_filter and category_filter.upper() != "ALL":
                    if category_filter.lower() not in art.get("category", "").lower():
                        continue

                if is_title_sold(title):
                    continue

                title_lower = title.lower()
                title_compact = re.sub(r"[\s\-_]+", "", title_lower)

                if any(_token_matches_title_safe(nt, title_lower, title_compact) for nt in neg_tokens):
                    continue
                if not all(_token_matches_title_safe(pt, title_lower, title_compact) for pt in pos_tokens):
                    continue

                seen_urls.add(art["url"])
                checked_for_kw += 1

                detail = await self.fetch_article_full_detail(
                    art["url"],
                    article_title=title,
                    article_author=art.get("author", ""),
                    target_keyword=pos_tokens[-1] if pos_tokens else kw,
                    priority=CRAWL_P3_SCAN,
                )

                if detail["is_sold"]:
                    logger.info(f"[Heuristic 排除已售/洽中] {title} -> {detail['sold_reason']}")
                    if checked_for_kw >= max_checked_per_keyword:
                        break
                    continue

                if ai_semantic_verifier is not None:
                    ai_check = await ai_semantic_verifier(art, detail, clean_pos_kw)
                    if not ai_check.get("is_available", True):
                        logger.info(
                            f"[AI 語意排除 ({ai_check.get('model')})] {title} -> {ai_check.get('reason')}"
                        )
                        if checked_for_kw >= max_checked_per_keyword:
                            break
                        continue
                    if ai_check.get("target_price"):
                        detail["extracted_price"] = ai_check["target_price"]
                    if ai_check.get("model"):
                        detail["ai_verified_by"] = ai_check["model"]
                    if ai_check.get("summary_note"):
                        detail["ai_summary_note"] = ai_check["summary_note"]

                art_enriched = dict(art)
                art_enriched["matched_keyword"] = kw
                art_enriched["detail"] = detail
                unsold_items.append(art_enriched)
                found_for_kw += 1

                if found_for_kw >= max_per_keyword or checked_for_kw >= max_checked_per_keyword:
                    break

        return unsold_items

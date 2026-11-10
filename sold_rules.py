import re
import unicodedata
from typing import List, Optional, Tuple

SOLD = "SOLD"
RESERVED = "RESERVED"
NO_MAIL = "NO_MAIL"
AVAILABLE = "AVAILABLE"

# Unified list item marker regex (supports halfwidth/fullwidth numbers, circled digits, bullets, letters)
LIST_ITEM_MARKER_REGEX = re.compile(
    r"^\s*(?:\d+[\.\)、．]|\(\d+\)|【\d+】|[①-⑩]|◎|●|▲|■|[A-Za-z][\.\)])"
)

# Phrases that look like status words but are actually location names, product info, or trading policies
_FALSE_POSITIVE_CLEANUP_REGEX = re.compile(
    r"(?:"
    r"可?洽\s*(?:中壢|中山|中和|中正|台中|中央|中興|中原|中華|中信|中港|中醫|中科|中友|中園|中清|中工|中投|中學)"
    r"|中和\s*(?:區|站|面交|自取)"
    r"|(?:原廠|官方|早就|已經|已)?\s*停售(?:款|品|型號|版|多年|已久)"
    r"|原廠\s*已?\s*停售"
    r"|結標\s*(?:時間|日期|日|前|後|價|金額)"
    r"|(?:請勿|勿|不用|別)\s*來信\s*(?:詢問|問|殺價|議價|芭樂|砍價|大刀|刀|交換|換物|排隊)"
    r")",
    re.IGNORECASE,
)

# Negation prefixes within 3 chars before a status phrase
_NEGATION_PREFIX_REGEX = re.compile(
    r"(?:尚未|還沒|還未|並未|沒有|不是|未|無|非|別)\s*$"
)

# Reopen / override phrases in author pushes or body updates
_REOPEN_REGEX = re.compile(
    r"(?:恢復販售|恢復開賣|重新開賣|重新販售|重開|還在|尚在|繼續賣|繼續販售|再次開放|可購買|交易取消|買家取消|跑單|放鳥|爽約)",
    re.IGNORECASE,
)

# Unambiguous strong status markers
_STRONG_SOLD_MARKERS = re.compile(
    r"(?<!未)(?<!尚未)(?<!還沒)(?<!還未)(?<!沒有)"
    r"(?:已售出|已賣出|售出\s*感謝|全售出|皆售出|皆已售出|已全部售出|已售|撤售|取消販售|已結標|已交換|已徵得)",
    re.IGNORECASE,
)

_STRONG_RESERVED_MARKERS = re.compile(
    r"(?<!未)(?<!尚未)(?<!還沒)(?<!還未)(?<!沒有)"
    r"(?:已洽談|洽談中|已預定|已預訂|預定中|預訂中|暫售|"
    r"(?<!可)洽中(?![壢山和正區路街站醫央興原華信安港投園清友科工學]))",
    re.IGNORECASE,
)

_STRONG_NO_MAIL_MARKERS = re.compile(
    r"(?<!未)(?<!尚未)(?<!還沒)(?<!還未)(?<!沒有)"
    r"(?:不用再來信|不用來信|暫勿來信|停止來信|暫不回信|恕不一一回信|"
    r"(?:請勿來信|勿來信)(?!\s*(?:詢問|問|殺價|議價|芭樂|砍價|大刀|刀|交換|換物|排隊)))",
    re.IGNORECASE,
)

# Short status words only valid in explicit status positions (brackets, start after category, end of line/title)
_POSITIONAL_SOLD_WORDS = r"(?:已售出|已賣出|全售出|皆售出|皆已售出|已全部售出|售出|賣出|已售|撤售|取消販售|已結標|結標|已交換|已徵得)"
_POSITIONAL_RESERVED_WORDS = r"(?:已洽談|洽談中|洽中(?![壢山和正區路街站醫央興原華信安港投園清友科工學])|已預定|已預訂|預定中|預訂中|預定|預訂|保留|暫售)"
_POSITIONAL_NO_MAIL_WORDS = r"(?:不用再來信|不用來信|暫勿來信|請勿來信|勿來信|停止來信|暫不回信|恕不一一回信)"

# Generalized thanks in author push comments
_AUTHOR_THANKS_SOLD_REGEX = re.compile(
    r"(?:感謝.{0,16}[版板]|謝謝\s*(?:PTT|PPT|ptt|大家|板友|[A-Za-z0-9_\-]+(?:\s*[版板])?))",
    re.IGNORECASE,
)

# Backward-compatible compiled regex exports for existing callers
STRONG_SOLD_REGEX = re.compile(
    r"(?<!未)(?<!尚未)(?<!還沒)("
    r"已售出|已賣出|售出\s*感謝|全售出|皆售出|皆已售出|已全部售出|已售|"
    r"暫售|撤售|已結標|取消販售|已交換|已徵得|"
    r"已洽談|洽談中|(?<!可)洽中(?![壢山和正區路街站醫央興原華信安港投園清友科工學])|"
    r"已預定|已預訂|預定中|預訂中|"
    r"不用再來信|不用來信|暫勿來信|停止來信|暫不回信|恕不一一回信|"
    r"(?:請勿來信|勿來信)(?!\s*(?:詢問|問|殺價|議價|芭樂|砍價|大刀|刀|交換|換物|排隊))"
    r")",
    re.IGNORECASE,
)

TITLE_SOLD_REGEX = re.compile(
    rf"(?:[（(\[【]\s*(?:{_POSITIONAL_SOLD_WORDS}|{_POSITIONAL_RESERVED_WORDS}|{_POSITIONAL_NO_MAIL_WORDS})\s*[）)\]】]"
    rf"|^\s*(?:Re:\s*|Fw:\s*)?(?:\[[^\]]+\]\s*)+(?:{_POSITIONAL_SOLD_WORDS}|{_POSITIONAL_RESERVED_WORDS}|{_POSITIONAL_NO_MAIL_WORDS})(?=\s|$|[，,。!！/｜|])"
    rf"|(?:^|[\s/｜|，,。\-])(?:{_POSITIONAL_SOLD_WORDS}|{_POSITIONAL_RESERVED_WORDS}|{_POSITIONAL_NO_MAIL_WORDS})\s*$)",
    re.IGNORECASE,
)

AUTHOR_PUSH_SOLD_REGEX = re.compile(
    r"(?<!未)(?<!尚未)(?<!還沒)("
    r"已售出|售出|已售|已賣出|賣出|全售出|皆售出|皆已售出|已全部售出|"
    r"暫售|撤售|已結標|取消販售|已交換|已徵得|"
    r"已洽談|洽談中|(?<!可)洽中(?![壢山和正區路街站醫央興原華信安港投園清友科工學])|"
    r"已預定|已預訂|預定中|預訂中|"
    r"不用再來信|不用來信|暫勿來信|停止來信|暫不回信|恕不一一回信|"
    r"(?:請勿來信|勿來信)(?!\s*(?:詢問|問|殺價|議價|芭樂|砍價|大刀|刀|交換|換物|排隊))|"
    r"感謝.{0,16}[版板]|謝謝\s*(?:PTT|PPT|ptt|大家|板友|[A-Za-z0-9_\-]+(?:\s*[版板])?)"
    r")",
    re.IGNORECASE,
)


def normalize_nfkc(text: str) -> str:
    if not text:
        return ""
    return unicodedata.normalize("NFKC", str(text))


def pick_item_token(pos_tokens: List[str]) -> str:
    """
    Phase 1.3: Deterministically select the primary item token from positive search tokens
    regardless of token order (e.g. ['3060ti', '台北'] and ['台北', '3060ti'] both return '3060ti').
    Priority:
    1. Token containing both ASCII letters and digits (e.g. '3060ti', 'rx6700xt', 'i512400', 'ddr5')
    2. Token containing digits (e.g. '3060', '1060', '4090')
    3. Token containing ASCII letters (e.g. 'macbook', 'airpods')
    4. Longest token overall
    """
    cleaned: List[str] = []
    for raw in pos_tokens or []:
        t = re.sub(r"[\s\-_]+", "", normalize_nfkc(raw).lower())
        if t:
            cleaned.append(t)
    if not cleaned:
        return ""

    def has_ascii_alpha(s: str) -> bool:
        return bool(re.search(r"[a-z]", s))

    def has_digit(s: str) -> bool:
        return bool(re.search(r"\d", s))

    tier1 = [t for t in cleaned if has_ascii_alpha(t) and has_digit(t)]
    if tier1:
        return sorted(tier1, key=lambda x: (-len(x), x))[0]

    tier2 = [t for t in cleaned if has_digit(t)]
    if tier2:
        return sorted(tier2, key=lambda x: (-len(x), x))[0]

    tier3 = [t for t in cleaned if has_ascii_alpha(t)]
    if tier3:
        return sorted(tier3, key=lambda x: (-len(x), x))[0]

    return sorted(cleaned, key=lambda x: (-len(x), x))[0]


def line_matches_item_token(
    line: str,
    item_token: str,
    neg_tokens: Optional[List[str]] = None,
) -> bool:
    """
    Phase 1.3: Check if a line in a multi-item post matches `item_token` (respecting digit
    and model suffix boundaries so '3060' does not match '3060Ti' or '30600') and does NOT
    contain any negative exclusion token in `neg_tokens`.
    """
    if not line or not item_token:
        return False
    ln_norm = normalize_nfkc(line).lower()
    ln_compact = re.sub(r"[\s\-_]+", "", ln_norm)

    # Check exclusion tokens first
    for nt in neg_tokens or []:
        nt_clean = re.sub(r"[\s\-_]+", "", normalize_nfkc(nt).lstrip("-").lower())
        if not nt_clean:
            continue
        if nt_clean.isdigit():
            if re.search(rf"(?<!\d){re.escape(nt_clean)}(?!\d)", ln_norm):
                return False
        else:
            if nt_clean in ln_norm or nt_clean in ln_compact:
                return False

    tok_clean = re.sub(r"[\s\-_]+", "", normalize_nfkc(item_token).lower())
    if not tok_clean:
        return False

    if tok_clean.isdigit():
        # Pure number token (e.g. '3060', '1060'): must not be part of a longer number ('30600')
        # and must not be immediately followed by 'ti' / 'xt' / 'xtx' / 'super' model variant suffix
        return bool(
            re.search(
                rf"(?<!\d){re.escape(tok_clean)}(?!\d|\s*(?:ti|xt|xtx|super|gre)\b)",
                ln_norm,
                re.IGNORECASE,
            )
        )

    return (tok_clean in ln_norm) or (tok_clean in ln_compact)


def _is_negated_or_question(text: str, match_start: int) -> bool:
    stripped = text.strip()
    if stripped.endswith(("?", "？")) or re.search(r"(?:嗎|呢|還在嗎|售出了嗎)\s*[?？]?$", stripped):
        return True
    prefix = text[max(0, match_start - 4) : match_start]
    if _NEGATION_PREFIX_REGEX.search(prefix):
        return True
    return False


def classify_title_state(title: str) -> Tuple[str, Optional[str]]:
    """
    Phase 1.1: Classify an article title into (SOLD | RESERVED | NO_MAIL | AVAILABLE, reason).
    Only matches status words in valid status positions and filters out location/product false positives.
    """
    if not title:
        return AVAILABLE, None

    t_raw = title.strip()
    if t_raw.endswith(("?", "？")):
        return AVAILABLE, None

    # Scrub known false-positive phrases (locations like 洽中壢, product descriptions like 原廠已停售款, 結標時間)
    t_cleaned = _FALSE_POSITIVE_CLEANUP_REGEX.sub(" ", t_raw)

    # 1. Check bracketed status blocks in original/cleaned title: (已售出), [洽中], 【售出】
    for m_br in re.finditer(r"[（(\[【]\s*([^）)\]】]{1,14})\s*[）)\]】]", t_cleaned):
        inner = m_br.group(1).strip()
        if re.search(r"(?:原廠|停售款|停產|時間|日期)", inner):
            continue
        if _is_negated_or_question(inner, 0):
            continue
        if re.match(rf"^(?:{_POSITIONAL_SOLD_WORDS})(?:\s*感謝.*)?$", inner, re.IGNORECASE):
            return SOLD, f"標題標示：{t_raw[:45]}"
        if re.match(rf"^(?:{_POSITIONAL_RESERVED_WORDS})$", inner, re.IGNORECASE):
            return RESERVED, f"標題標示：{t_raw[:45]}"
        if re.match(rf"^(?:{_POSITIONAL_NO_MAIL_WORDS})$", inner, re.IGNORECASE):
            return NO_MAIL, f"標題標示：{t_raw[:45]}"

    # 2. Check start of title right after category brackets, e.g. '[賣/台北] 售出 RTX 3060'
    m_after_cat = re.match(
        r"^\s*(?:Re:\s*|Fw:\s*)?(?:\[[^\]]+\]\s*)+",
        t_cleaned,
        re.IGNORECASE,
    )
    rest_after_cat = t_cleaned[m_after_cat.end() :].strip() if m_after_cat else t_cleaned.strip()

    for state, pat in (
        (SOLD, _POSITIONAL_SOLD_WORDS),
        (RESERVED, _POSITIONAL_RESERVED_WORDS),
        (NO_MAIL, _POSITIONAL_NO_MAIL_WORDS),
    ):
        m_start = re.match(rf"^({pat})(?=\s|$|[，,。!！/｜|（(\[【\-])", rest_after_cat, re.IGNORECASE)
        if m_start:
            return state, f"標題標示：{t_raw[:45]}"

        m_end = re.search(
            rf"(?:^|[\s/｜|，,。\-（(\[【])({pat})\s*(?:感謝[^\s]*)?$",
            t_cleaned,
            re.IGNORECASE,
        )
        if m_end and not _is_negated_or_question(t_cleaned, m_end.start(1)):
            return state, f"標題標示：{t_raw[:45]}"

    # 3. Check unambiguous compound markers anywhere in cleaned title (if not negated)
    for state, regex in (
        (SOLD, _STRONG_SOLD_MARKERS),
        (RESERVED, _STRONG_RESERVED_MARKERS),
        (NO_MAIL, _STRONG_NO_MAIL_MARKERS),
    ):
        for m in regex.finditer(t_cleaned):
            if not _is_negated_or_question(t_cleaned, m.start()):
                return state, f"標題標示：{t_raw[:45]}"

    return AVAILABLE, None


def is_title_sold(title: str) -> bool:
    state, _ = classify_title_state(title)
    return state != AVAILABLE


def is_title_marked_sold(title: str) -> bool:
    state, _ = classify_title_state(title)
    return state != AVAILABLE


def classify_text_state(
    text: str,
    require_short_or_boundary: bool = False,
) -> Tuple[str, Optional[str]]:
    """
    Phase 1.1: Classify a body line, price line, or item window into (SOLD | RESERVED | NO_MAIL | AVAILABLE, reason).
    When `require_short_or_boundary=True` (used for head/tail standalone lines), long descriptive sentences
    (> 22 chars after stripping punctuation) that do not begin with a status marker are ignored.
    """
    if not text:
        return AVAILABLE, None

    raw = text.strip()
    if not raw or raw.endswith(("?", "？")):
        return AVAILABLE, None

    cleaned = _FALSE_POSITIVE_CLEANUP_REGEX.sub(" ", raw)
    cleaned_compact = re.sub(r"[\s\-_:*★◎●▲■※！!。，,、/｜|]+", "", cleaned)

    if require_short_or_boundary and len(cleaned_compact) > 22:
        # Only allow if the line explicitly starts with a status word or bracketed status word
        m_head = re.match(
            rf"^\s*[（(\[【★◎●※]*\s*(?:{_POSITIONAL_SOLD_WORDS}|{_POSITIONAL_RESERVED_WORDS}|{_POSITIONAL_NO_MAIL_WORDS})",
            cleaned,
            re.IGNORECASE,
        )
        if not m_head:
            return AVAILABLE, None

    # Check if line has a reopen override phrase at the end
    m_reopen = _REOPEN_REGEX.search(cleaned)
    if m_reopen and not _is_negated_or_question(cleaned, m_reopen.start()):
        # If the reopen phrase appears after any sold/reserved phrase on the same line, it's available
        last_reopen_pos = m_reopen.end()
        later_status = False
        for rx in (_STRONG_SOLD_MARKERS, _STRONG_RESERVED_MARKERS, _STRONG_NO_MAIL_MARKERS):
            for ms in rx.finditer(cleaned[last_reopen_pos:]):
                later_status = True
        if not later_status:
            return AVAILABLE, None

    # Check bracketed status or positional sold/reserved/no_mail words
    for state, regex in (
        (SOLD, _STRONG_SOLD_MARKERS),
        (RESERVED, _STRONG_RESERVED_MARKERS),
        (NO_MAIL, _STRONG_NO_MAIL_MARKERS),
    ):
        for m in regex.finditer(cleaned):
            if not _is_negated_or_question(cleaned, m.start()):
                return state, raw[:50]

    # Also check bare '售出' / '賣出' inside brackets or at line start/end
    m_bare_sold = re.search(
        r"(?:[（(\[【]\s*(?:售出|賣出|已售)\s*[）)\]】]"
        r"|^\s*(?:售出|賣出|已售)(?=\s|$|[，,。!！:：\-])"
        r"|(?:^|[\s:：，,。\-/｜|])(?:售出|賣出|已售)\s*$)",
        cleaned,
        re.IGNORECASE,
    )
    if m_bare_sold and not _is_negated_or_question(cleaned, m_bare_sold.start()):
        return SOLD, raw[:50]

    return AVAILABLE, None


def classify_author_pushes(pushes: List[str]) -> Tuple[str, Optional[str]]:
    """
    Phase 1.1: Evaluate author push comments in chronological order.
    Supports reopen/override phrases ('交易取消，恢復販售', '跑單重開', '還在') that reset earlier
    RESERVED / NO_MAIL / SOLD states back to AVAILABLE.
    """
    state = AVAILABLE
    reason: Optional[str] = None

    for raw_push in pushes or []:
        if not raw_push:
            continue
        # Strip trailing PTT push IP/timestamp if present
        p_clean = re.sub(
            r"\s*(?:\d+\.\d+\.\d+\.\d+\s+)?\d{2}/\d{2}\s+\d{2}:\d{2}\s*$",
            "",
            str(raw_push),
        ).strip()
        if not p_clean or p_clean.endswith(("?", "？")):
            continue

        p_scrubbed = _FALSE_POSITIVE_CLEANUP_REGEX.sub(" ", p_clean)

        # 1. Check for reopen / override phrases
        m_reopen = _REOPEN_REGEX.search(p_scrubbed)
        if m_reopen and not _is_negated_or_question(p_scrubbed, m_reopen.start()):
            state = AVAILABLE
            reason = None
            # Check if there is still a status word AFTER the reopen phrase in the same push
            tail = p_scrubbed[m_reopen.end() :]
            sub_state, _ = classify_text_state(tail, require_short_or_boundary=False)
            if sub_state != AVAILABLE:
                state = sub_state
                reason = f"作者於推文標示：{p_clean}"
            continue

        # 2. Check status markers in the author push
        # Restrict bare '售出' in long pushes unless near start/end or accompanied by unambiguous markers
        sub_state, _ = classify_text_state(p_scrubbed, require_short_or_boundary=False)
        if sub_state != AVAILABLE:
            state = sub_state
            reason = f"作者於推文標示：{p_clean}"
            continue

        # 3. Check bare '售出'/'賣出' near start (first 8 chars) or end of push, or generalized thanks
        m_bare = re.search(r"(?<!未)(?<!尚未)(?<!還沒)(?:售出|賣出)", p_scrubbed)
        if m_bare and not _is_negated_or_question(p_scrubbed, m_bare.start()):
            if m_bare.start() <= 8 or len(p_scrubbed) <= 20 or m_bare.end() >= len(p_scrubbed) - 6:
                state = SOLD
                reason = f"作者於推文標示：{p_clean}"
                continue

        m_thanks = _AUTHOR_THANKS_SOLD_REGEX.search(p_scrubbed)
        if m_thanks and not _is_negated_or_question(p_scrubbed, m_thanks.start()):
            if len(p_scrubbed) <= 25:
                state = SOLD
                reason = f"作者於推文標示：{p_clean}"

    return state, reason

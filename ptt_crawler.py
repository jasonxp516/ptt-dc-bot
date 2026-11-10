import asyncio
import copy
import logging
import random
import re
import time
import urllib.parse
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiohttp
from bs4 import BeautifulSoup

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

logger = logging.getLogger("ptt_crawler")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
]

PTT_BOARD_DIRECTORY = [
    {
        "name": "HardwareSale",
        "zh_name": "電腦硬體二手交易板",
        "tags": "顯卡 顯示卡 GPU CPU 處理器 主機板 MB 記憶體 RAM 硬碟 SSD 電供 PSU 機殼 散熱 螢幕 電腦零組件 二手硬體買賣",
    },
    {
        "name": "PC_Shopping",
        "zh_name": "電腦組裝與硬體採購討論板 (電蝦板)",
        "tags": "電腦菜單 組電腦 新品情報 顯示卡討論 硬體開箱 電蝦",
    },
    {
        "name": "nb-shopping",
        "zh_name": "筆記型電腦二手買賣與討論板",
        "tags": "筆電 二手筆電 電競筆電 文書筆電 Laptop Notebook ThinkPad ROG Zephyrus ZenBook VivoBook TUF Legion Gram Surface 買賣",
    },
    {
        "name": "MacShop",
        "zh_name": "蘋果二手產品交易板",
        "tags": "Apple Mac MacBook Pro Air iPhone iPad AppleWatch AirPods Mac mini Mac Studio HomePod Apple Pencil 二手買賣 蘋果",
    },
    {
        "name": "mobilesales",
        "zh_name": "手機二手交易板",
        "tags": "手機 二手手機 iPhone Samsung Galaxy S23 S24 Pixel 小米 RedMi POCO vivo OPPO Sony Xperia ROG Phone Zenfone Android 智慧型手機買賣",
    },
    {
        "name": "Gamesale",
        "zh_name": "電玩遊戲二手交易板",
        "tags": "PS5 PS4 Switch NSwitch Xbox Steam Deck ROG Ally 遊戲片 卡帶 主機 手把 Joy-Con 薩爾達 瑪利歐 寶可夢 魔物獵人 電玩二手買賣",
    },
    {
        "name": "DC_Sale",
        "zh_name": "數位相機與攝影器材二手買賣板",
        "tags": "相機 單眼 微單 鏡頭 Sony Canon Nikon 富士 Fujifilm Ricoh GR3 GRIII Leica Sigma Tamron GoPro Insta360 DJI Neo Mavic Mini Air Avata Osmo Pocket Action 空拍機 無人機 航拍機 運動相機 全景相機 穩定器 三軸 雲台 閃光燈 攝影器材二手買賣",
    },
    {
        "name": "Headphone",
        "zh_name": "耳機與隨身音訊討論/交易板",
        "tags": "耳機 耳道 耳罩 真無線 DAC 耳擴 一體機 藍牙耳機 音響 音訊 Sony WH-1000XM5 WF-1000XM5 Sennheiser 鐵三角 Bose",
    },
    {
        "name": "Audiophile",
        "zh_name": "發燒音響板",
        "tags": "喇叭 擴大機 音響 HiFi 被動喇叭 DAC",
    },
    {
        "name": "Key_Mou_Pad",
        "zh_name": "鍵盤滑鼠鼠墊板 (鍵鼠板)",
        "tags": "機械鍵盤 滑鼠 電競滑鼠 鍵帽 鼠墊 軸體",
    },
    {
        "name": "Storage_Zone",
        "zh_name": "硬碟與儲存設備板",
        "tags": "SSD HDD 外接硬碟 NAS 隨身碟 記憶卡 儲存",
    },
    {
        "name": "VideoCard",
        "zh_name": "顯示卡討論專板",
        "tags": "NVIDIA AMD RTX Radeon 顯示卡 驅動 跑分",
    },
    {
        "name": "LCD",
        "zh_name": "液晶螢幕板",
        "tags": "電腦螢幕 顯示器 4K 2K 電競螢幕 OLED 面板",
    },
    {
        "name": "Broad_Band",
        "zh_name": "寬頻網路與路由器板",
        "tags": "路由器 WiFi 分享器 網路 光世代 網卡 Mesh",
    },
    {
        "name": "forsale",
        "zh_name": "綜合二手跳蚤市場板",
        "tags": "二手 雜物 票券 家電 生活用品 買賣 跳蚤市場",
    },
    {
        "name": "Lifeismoney",
        "zh_name": "省錢板",
        "tags": "特價 優惠 折扣 情報 省錢 便宜 折價券",
    },
    {
        "name": "E-appliance",
        "zh_name": "家電討論板",
        "tags": "家電 冷氣 冰箱 洗衣機 除濕機 吸塵器 電視",
    },
    {
        "name": "Digitalhome",
        "zh_name": "數位家庭與電視盒板",
        "tags": "電視 投影機 劇院 AppleTV 電視盒 聲霸",
    },
    {
        "name": "MobileComm",
        "zh_name": "手機通訊討論板",
        "tags": "手機 資費 電信 5G Android iOS 討論",
    },
    {
        "name": "iOS",
        "zh_name": "Apple iOS 討論板",
        "tags": "iPhone iPad iOS App 蘋果討論",
    },
    {
        "name": "PlayStation",
        "zh_name": "PlayStation 討論板",
        "tags": "PS5 PS4 PSN 索尼 家機遊戲",
    },
    {
        "name": "NSwitch",
        "zh_name": "Nintendo Switch 討論板",
        "tags": "Switch 任天堂 薩爾達 瑪利歐",
    },
    {
        "name": "Steam",
        "zh_name": "Steam 與 PC 遊戲板",
        "tags": "Steam PC遊戲 特價 電腦遊戲 SteamDeck",
    },
    {
        "name": "C_Chat",
        "zh_name": "希洽板 (ACG 動漫遊戲討論)",
        "tags": "動漫 動畫 漫畫 遊戲 ACG 二次元",
    },
    {
        "name": "Gossiping",
        "zh_name": "八卦板",
        "tags": "新聞 時事 八卦 問卦 閒聊",
    },
    {
        "name": "Stock",
        "zh_name": "股票板 (股板)",
        "tags": "台股 美股 股票 投資 ETF 財經",
    },
    {
        "name": "Tech_Job",
        "zh_name": "科技業求職與職場板",
        "tags": "科技業 工程師 半導體 台積電 面試 薪水",
    },
    {
        "name": "Soft_Job",
        "zh_name": "軟體工程師職場板",
        "tags": "軟體 寫程式 前後端 AI 工程師 面試",
    },
    {
        "name": "car",
        "zh_name": "汽車板",
        "tags": "汽車 買車 電動車 保養 行車紀錄器",
    },
    {
        "name": "biker",
        "zh_name": "機車板",
        "tags": "機車 速克達 安全帽 維修 買車",
    },
    {
        "name": "CarShop",
        "zh_name": "二手汽車買賣板",
        "tags": "二手車 中古車 買車 賣車 汽車交易",
    },
    {
        "name": "BikerShop",
        "zh_name": "二手機車與人身部品買賣板",
        "tags": "二手機車 中古機車 安全帽 防摔衣 零件買賣",
    },
    {
        "name": "home-sale",
        "zh_name": "房屋買賣與房地產板",
        "tags": "買房 賣房 房價 預售屋 中古屋 不動產",
    },
    {
        "name": "give",
        "zh_name": "贈送板",
        "tags": "免費 贈送 索取 斷捨離",
    },
]

COMMON_BOARDS_MAP = {item["name"].lower(): item["name"] for item in PTT_BOARD_DIRECTORY}

BOARD_ALIASES_MAP: Dict[str, str] = {
    # HardwareSale
    "電蝦": "HardwareSale",
    "電蝦二手": "HardwareSale",
    "硬體": "HardwareSale",
    "硬體買賣": "HardwareSale",
    "電腦硬體": "HardwareSale",
    "電腦零件": "HardwareSale",
    "顯卡": "HardwareSale",
    "顯示卡": "HardwareSale",
    "hs": "HardwareSale",
    "hardwaresale": "HardwareSale",
    # DC_Sale
    "相機": "DC_Sale",
    "二手相機": "DC_Sale",
    "數位相機": "DC_Sale",
    "攝影": "DC_Sale",
    "攝影器材": "DC_Sale",
    "鏡頭": "DC_Sale",
    "單眼": "DC_Sale",
    "微單": "DC_Sale",
    "空拍機": "DC_Sale",
    "無人機": "DC_Sale",
    "航拍機": "DC_Sale",
    "運動相機": "DC_Sale",
    "dji": "DC_Sale",
    "gopro": "DC_Sale",
    "insta360": "DC_Sale",
    "dc": "DC_Sale",
    "dcsale": "DC_Sale",
    "dc_sale": "DC_Sale",
    "dc-sale": "DC_Sale",
    # nb-shopping
    "筆電": "nb-shopping",
    "二手筆電": "nb-shopping",
    "筆記型電腦": "nb-shopping",
    "筆電買賣": "nb-shopping",
    "筆電交易": "nb-shopping",
    "nb": "nb-shopping",
    "nbshopping": "nb-shopping",
    "nb_shopping": "nb-shopping",
    "nb-shopping": "nb-shopping",
    # MacShop
    "蘋果": "MacShop",
    "蘋果二手": "MacShop",
    "二手蘋果": "MacShop",
    "apple": "MacShop",
    "mac": "MacShop",
    "macbook": "MacShop",
    "ipad": "MacShop",
    "macshop": "MacShop",
    "mac_shop": "MacShop",
    "mac-shop": "MacShop",
    # Gamesale
    "遊戲": "Gamesale",
    "電玩": "Gamesale",
    "二手遊戲": "Gamesale",
    "遊戲買賣": "Gamesale",
    "遊戲交易": "Gamesale",
    "遊戲片": "Gamesale",
    "switch": "Gamesale",
    "ps5": "Gamesale",
    "gs": "Gamesale",
    "gamesale": "Gamesale",
    "game_sale": "Gamesale",
    "game-sale": "Gamesale",
    # mobilesales
    "手機": "mobilesales",
    "二手手機": "mobilesales",
    "手機買賣": "mobilesales",
    "手機交易": "mobilesales",
    "ms": "mobilesales",
    "mobilesale": "mobilesales",
    "mobilesales": "mobilesales",
    "mobile_sales": "mobilesales",
    # Headphone
    "耳機": "Headphone",
    "二手耳機": "Headphone",
    "耳機買賣": "Headphone",
    "耳擴": "Headphone",
    "headphone": "Headphone",
    "headphones": "Headphone",
    # Other common boards
    "省錢": "Lifeismoney",
    "省錢板": "Lifeismoney",
    "跳蚤": "forsale",
    "跳蚤市場": "forsale",
    "鍵鼠": "Key_Mou_Pad",
    "鍵盤": "Key_Mou_Pad",
    "螢幕": "LCD",
    "音響": "Audiophile",
    "八卦": "Gossiping",
    "股板": "Stock",
    "股票": "Stock",
    "希洽": "C_Chat",
}

COMMON_BOARDS_MAP.update(BOARD_ALIASES_MAP)


def resolve_board_alias(board_input: str) -> str:
    """
    Resolve user-supplied board name, Chinese alias, or autocomplete display string
    (e.g. 'DC_Sale — 數位相機與攝影器材二手買賣板' or '相機') into a canonical PTT board name.
    """
    if not board_input:
        return "HardwareSale"
    raw = board_input.strip()
    if not raw:
        return "HardwareSale"
    if raw.upper() == "ALL":
        return "ALL"
    # If the user selected or pasted an autocomplete label like "DC_Sale — 數位相機..." or "DC_Sale (數位相機...)"
    for sep in (" — ", " - ", " (", "（", " ["):
        if sep in raw:
            head = raw.split(sep, 1)[0].strip()
            if head:
                raw = head
                break
    lower = raw.lower()
    if lower in COMMON_BOARDS_MAP:
        return COMMON_BOARDS_MAP[lower]
    # Also check if input matches any zh_name substring in PTT_BOARD_DIRECTORY
    for item in PTT_BOARD_DIRECTORY:
        if lower == item["zh_name"].lower():
            return item["name"]
    return raw


_CATEGORY_INFERENCE_RULES: List[Tuple[str, re.Pattern]] = [
    (
        "DC_Sale",
        re.compile(
            r"(?:\bdji\b|\bneo\b|\bmavic\b|\bavata\b|\bosmo\b|\bpocket\s*[123]?\b|\baction\s*[2345]?\b"
            r"|\binsta\s*360\b|\bgopro\b|空拍機|無人機|航拍機|運動相機|全景相機|穩定器|三軸雲台|手持雲台"
            r"|數位相機|單眼|微單|無反|鏡頭|變焦鏡|定焦鏡|閃光燈|防潮箱|記憶卡\s*cf|攝影器材"
            r"|\ba7c2?\b|\ba7m[234]\b|\ba7r[2345]\b|\ba7s3?\b|\ba6[0-9]{3}\b|\bzv[\-\s]?e?10\b|\bzv[\-\s]?1\b"
            r"|\brx100\b|\bfx30?\b|\bx100v?i?\b|\bx[\-\s]?t[345]0?\b|\bx[\-\s]?s[12]0\b"
            r"|\bgr3x?\b|\bgriii?x?\b|\bleica\b|徠卡|哈蘇|\bsigma\b|適馬|\btamron\b|騰龍"
            r"|\bcanon\s*(?:rf|ef|eos|r[5678]|r10|r50)\b|\bnikon\s*(?:z[fc56789]|z30|z50|z6ii|z7ii|z8|z9)\b"
            r"|\blumix\b|\bolympus\b|\bom[\-\s]?[15]\b|相機)",
            re.IGNORECASE,
        ),
    ),
    (
        "MacShop",
        re.compile(
            r"(?:\bmacbook\b|\bmac\s*mini\b|\bmac\s*studio\b|\bmac\s*pro\b|\bimac\b"
            r"|\bipad\b|\bapple\s*watch\b|\bairpods\b|\bapple\s*pencil\b|\bhomepod\b"
            r"|\bstudio\s*display\b|巧控鍵盤|巧控板|巧控滑鼠|\bm[1234]\s*(?:pro|max|ultra)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "Gamesale",
        re.compile(
            r"(?:\bps5\b|\bps4\b|\bswitch\b|\bnswitch\b|\bxbox\b|\bsteam\s*deck\b|\bsteamdeck\b"
            r"|\brog\s*ally\b|\blegion\s*go\b|遊戲片|遊戲卡帶|實體片|二手片|joy[\-\s]?con|pro\s*手把"
            r"|薩爾達|瑪利歐|寶可夢|魔物獵人|艾爾登法環|黑神話|劍星|斯普拉遁|動森|動物森友會|電玩主機)",
            re.IGNORECASE,
        ),
    ),
    (
        "nb-shopping",
        re.compile(
            r"(?:二手筆電|電競筆電|文書筆電|筆記型電腦|筆電|\blaptop\b|\bnotebook\b"
            r"|\bthinkpad\b|\bzenbook\b|\bvivobook\b|\bzephyrus\b|西風之神|\btuf\s*gaming\b"
            r"|\bideapad\b|\blegion\s*[57]\b|\blg\s*gram\b|\bsurface\s*(?:pro|laptop|go)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "mobilesales",
        re.compile(
            r"(?:二手手機|智慧型手機|安卓手機|\bgalaxy\s*s2[1234]\b|\bs2[234]\s*ultra\b"
            r"|\bz\s*fold\b|\bz\s*flip\b|\bpixel\s*[6789]\b|\bxperia\s*[15]\b|\brog\s*phone\b"
            r"|\bzenfone\b|紅米|\bredmi\b|\bpoco\b|\bvivo\s*x[0-9]+\b|\bfind\s*x[0-9]+\b|\biphone\b|手機)",
            re.IGNORECASE,
        ),
    ),
    (
        "Headphone",
        re.compile(
            r"(?:耳機|耳道|耳罩|真無線|藍牙耳機|耳擴|隨身耳擴|\bdac\b|隨身聽|\bdap\b"
            r"|\bwh[\-\s]?1000xm[345]\b|\bwf[\-\s]?1000xm[345]\b|\bsennheiser\b|森海塞爾"
            r"|鐵三角|\baudio[\-\s]?technica\b|\bbose\s*qc\b|\bie[2369]00\b|\bhd[68][056]0s?\b)",
            re.IGNORECASE,
        ),
    ),
]


def infer_board_from_query(text: str, default_board: str = "HardwareSale") -> str:
    """
    Infer the most appropriate PTT second-hand trading board from a keyword or natural language prompt.
    Ignores negative exclusion tokens (e.g. '-筆電' in '3060 -ti -筆電') so PC hardware exclusions
    do not falsely route to nb-shopping.
    """
    if not text:
        return default_board

    # Remove negative exclusion tokens (starting with '-') before matching
    tokens = [t for t in text.strip().split() if not t.startswith("-")]
    clean_text = " ".join(tokens) if tokens else text.strip()
    clean_lower = clean_text.lower()

    # 1. Check if an explicit board name from PTT_BOARD_DIRECTORY is mentioned in clean_text
    for item in PTT_BOARD_DIRECTORY:
        b_lower = item["name"].lower()
        if re.search(rf"(?<![a-z0-9]){re.escape(b_lower)}(?![a-z0-9])", clean_lower):
            return item["name"]

    # 2. Check if clean_text is an exact alias
    if clean_lower in BOARD_ALIASES_MAP:
        return BOARD_ALIASES_MAP[clean_lower]

    # 3. Check PC Hardware indicators first if both GPU/CPU and something else appear
    if re.search(
        r"(?:\brtx\s*\d{4}|\bgtx\s*\d{3,4}|\brx\s*\d{4}|\b\d{4}\s*ti\b|\b\d{4}\s*xt\b"
        r"|顯卡|顯示卡|主機板|處理器|\bcpu\b|\bgpu\b|\bddr[45]\b|電供|電源供應器|水冷|塔散)",
        clean_lower,
        re.IGNORECASE,
    ):
        return "HardwareSale"

    # 4. Evaluate category inference patterns in priority order
    for target_board, pattern in _CATEGORY_INFERENCE_RULES:
        if pattern.search(clean_text):
            return target_board

    return default_board

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


def is_article_date_too_old(date_str: str, max_age_days: int = 21) -> bool:
    """
    Fix B7: Parse PTT list date 'MM/DD' with cross-year handling and check if older than `max_age_days`.
    """
    if not date_str:
        return False
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})\s*$", date_str.strip())
    if not m:
        return False
    try:
        month, day = int(m.group(1)), int(m.group(2))
        now = datetime.now()
        year = now.year
        candidate = datetime(year, month, day)
        if (candidate - now).days > 2:
            candidate = datetime(year - 1, month, day)
        age_days = (now - candidate).days
        return age_days > max_age_days
    except Exception:
        return False


class PTTCrawler:
    """
    Asynchronous PTT Web Crawler with built-in Anti-Ban protections,
    article detail caching, two-tier sold detection, and native PTT board search.
    """

    BASE_URL = "https://www.ptt.cc"
    apply_keyword_view = staticmethod(apply_keyword_view)

    def __init__(
        self,
        min_request_delay: float = 2.0,
        max_request_delay: float = 4.2,
        storage: Optional[Any] = None,
    ):
        self.min_request_delay = min_request_delay
        self.max_request_delay = max_request_delay
        self.storage = storage
        self._session: Optional[aiohttp.ClientSession] = None
        self._req_lock = asyncio.Lock()
        self._last_request_ts: float = 0.0
        self.consecutive_errors: int = 0
        self.cooldown_until: float = 0.0
        self.total_requests: int = 0

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
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": referer,
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    async def _safe_get(self, url: str, referer: Optional[str] = None) -> Tuple[int, Optional[str]]:
        if not url or not url.startswith("https://www.ptt.cc/bbs/"):
            logger.warning(f"安全防護攔截非 PTT 網域請求: {url}")
            return 400, None

        in_cd, remain = self.is_in_cooldown()
        if in_cd:
            logger.warning(f"防封鎖保護冷卻中，尚餘 {remain} 秒，跳過請求: {url}")
            return 429, None

        async with self._req_lock:
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
                async with session.get(url, headers=headers, allow_redirects=False) as resp:
                    status = resp.status
                    if status == 200:
                        text = await resp.text(errors="replace")
                        self.consecutive_errors = 0
                        return 200, text
                    elif status in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("Location", "")
                        if not loc or not loc.startswith("https://www.ptt.cc/bbs/"):
                            logger.warning(f"安全防護阻斷不安全之重定向 (SSRF 防護): {loc}")
                            return 400, None
                        async with session.get(loc, headers=headers, allow_redirects=False) as red_resp:
                            if red_resp.status == 200:
                                text = await red_resp.text(errors="replace")
                                self.consecutive_errors = 0
                                return 200, text
                            return red_resp.status, None
                    elif status == 404:
                        return 404, None
                    elif status in (429, 403, 503):
                        self.consecutive_errors += 1
                        backoff = min(1800, 120 * (2 ** min(self.consecutive_errors - 1, 4))) + random.randint(15, 45)
                        self.cooldown_until = time.time() + backoff
                        logger.warning(
                            f"收到 HTTP {status} ({url})，啟動防封鎖指數退避冷卻 {backoff} 秒！"
                        )
                        return status, None
                    else:
                        self.consecutive_errors += 1
                        logger.warning(f"非預期 HTTP 狀態碼 {status}: {url}")
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

    async def verify_board(self, board_input: str) -> Tuple[bool, str, str]:
        board_input = resolve_board_alias(board_input.strip())
        if not is_valid_board_name(board_input):
            return False, board_input, f"看板名稱 `{board_input}` 格式不合法（僅允許英數字、底線與連字號，或輸入常見中文別名如「相機」、「筆電」、「蘋果」）"
        candidates = []
        if board_input.lower() in COMMON_BOARDS_MAP:
            candidates.append(COMMON_BOARDS_MAP[board_input.lower()])
        for variant in [board_input, board_input.capitalize(), board_input.upper(), board_input.lower()]:
            if variant not in candidates:
                candidates.append(variant)

        for candidate in candidates:
            url = f"{self.BASE_URL}/bbs/{candidate}/index.html"
            status, html = await self._safe_get(url)
            if status == 200 and html:
                soup = BeautifulSoup(html, "lxml")
                board_link = soup.select_one("#topbar a.board")
                if board_link and board_link.get("href"):
                    m = re.search(r"/bbs/([^/]+)/index\.html", board_link["href"])
                    if m:
                        found_board = m.group(1)
                        return True, COMMON_BOARDS_MAP.get(found_board.lower(), found_board), "驗證成功"
                return True, COMMON_BOARDS_MAP.get(candidate.lower(), candidate), "驗證成功"
            elif status in (429, 403, 503):
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
                    for i in range(len(tok) - 1):
                        bg = tok[i : i + 2]
                        if bg in haystack:
                            score += 1
            if score > 0:
                results.append((score, item))
                seen_names.add(item["name"].lower())

        results.sort(key=lambda x: x[0], reverse=True)
        matched_list = [r[1] for r in results[:8]]

        if len(matched_list) < 3:
            status, html = await self._safe_get(f"{self.BASE_URL}/bbs/hotboards.html")
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
        self, board: str, query: str, max_pages: int = 1
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

        for q in queries_to_run:
            encoded_q = urllib.parse.quote(q)
            for page in range(1, max_pages + 1):
                if page == 1:
                    url = f"{self.BASE_URL}/bbs/{board}/search?q={encoded_q}"
                else:
                    url = f"{self.BASE_URL}/bbs/{board}/search?page={page}&q={encoded_q}"

                status, html = await self._safe_get(url)
                if status != 200 or not html:
                    break

                page_arts, _ = self._parse_list_page(html, board)
                if not page_arts:
                    break
                for art in page_arts:
                    if art["url"] not in seen_urls:
                        seen_urls.add(art["url"])
                        collected.append(art)

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

        status, html = await self._safe_get(article_url)
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
            ok, articles, _ = await self.search_board_by_ptt_query(board, kw, max_pages=1)
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

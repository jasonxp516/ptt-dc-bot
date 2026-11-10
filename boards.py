"""
Single Source of Truth (SSOT) for PTT board directory, aliases, and category inference rules.
Loads board definitions from `boards.json` so `ptt_crawler.py`, `groq_agent.py`, and `bot.py`
share identical board routing rules without duplication.
"""

import json
import os
import re
from typing import Dict, List, Tuple

_BOARDS_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boards.json")

with open(_BOARDS_JSON_PATH, "r", encoding="utf-8") as _f:
    _BOARDS_DATA = json.load(_f)

PTT_BOARD_DIRECTORY: List[Dict[str, str]] = _BOARDS_DATA["directory"]

BOARD_ALIASES_MAP: Dict[str, str] = _BOARDS_DATA["aliases"]

COMMON_BOARDS_MAP: Dict[str, str] = {
    item["name"].lower(): item["name"] for item in PTT_BOARD_DIRECTORY
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
    for sep in (" — ", " - ", " (", "（", " ["):
        if sep in raw:
            head = raw.split(sep, 1)[0].strip()
            if head:
                raw = head
                break
    lower = raw.lower()
    if lower in COMMON_BOARDS_MAP:
        return COMMON_BOARDS_MAP[lower]
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

    tokens = [t for t in text.strip().split() if not t.startswith("-")]
    clean_text = " ".join(tokens) if tokens else text.strip()
    clean_lower = clean_text.lower()

    for item in PTT_BOARD_DIRECTORY:
        b_lower = item["name"].lower()
        if re.search(rf"(?<![a-z0-9]){re.escape(b_lower)}(?![a-z0-9])", clean_lower):
            return item["name"]

    if clean_lower in BOARD_ALIASES_MAP:
        return BOARD_ALIASES_MAP[clean_lower]

    if re.search(
        r"(?:\brtx\s*\d{4}|\bgtx\s*\d{3,4}|\brx\s*\d{4}|\b\d{4}\s*ti\b|\b\d{4}\s*xt\b"
        r"|顯卡|顯示卡|主機板|處理器|\bcpu\b|\bgpu\b|\bddr[45]\b|電供|電源供應器|水冷|塔散)",
        clean_lower,
        re.IGNORECASE,
    ):
        return "HardwareSale"

    for target_board, pattern in _CATEGORY_INFERENCE_RULES:
        if pattern.search(clean_text):
            return target_board

    return default_board


BOARD_ROUTING_SHORT_DESCRIPTION = (
    "目標看板英文名稱（或中文別名）。請依商品類別自動選擇正確二手看板："
    "相機/鏡頭/空拍機/DJI/GoPro/Insta360 為 'DC_Sale'、"
    "Apple/MacBook/iPad/AirPods 為 'MacShop'、"
    "筆電/Notebook 為 'nb-shopping'、"
    "PS5/Switch/遊戲片/電玩 為 'Gamesale'、"
    "手機/Android/iPhone 為 'mobilesales'（手機通訊/資費討論為 'MobileComm'）、"
    "耳機/DAC/耳擴 為 'Headphone'、"
    "電腦零組件/顯卡/CPU/RAM/SSD 二手買賣預設為 'HardwareSale'（組裝菜單討論為 'PC_Shopping'）"
)

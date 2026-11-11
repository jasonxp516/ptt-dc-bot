"""
Generate the README illustrations in assets/ from the bot's real UI strings.
Re-run after changing UI text in bot.py / embeds.py:  python tools/gen_readme_assets.py
"""
import os
import sys
from xml.sax.saxutils import escape as E

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans TC', 'Noto Sans CJK TC', 'PingFang TC', 'Microsoft JhengHei', sans-serif"
MONO = "Consolas, 'SF Mono', Menlo, 'Courier New', 'Noto Sans Mono CJK TC', monospace"

def tw(s, size, mono=False):
    w = 0.0
    for ch in s:
        o = ord(ch)
        if o > 0x2E7F:
            w += size
        elif mono:
            w += size * 0.6
        elif ch in "ilI.,:;'|! ":
            w += size * 0.3
        elif ch.isupper() or ch in "mwMW@":
            w += size * 0.68
        else:
            w += size * 0.54
    return w

class Svg:
    def __init__(self, w, h, title):
        self.w, self.h, self.b = w, h, []
        self.title = title
    def rect(self, x, y, w, h, fill, rx=4, stroke=None):
        s = f' stroke="{stroke}" stroke-width="1"' if stroke else ""
        self.b.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="{rx}" fill="{fill}"{s}/>')
    def text(self, x, y, s, size=13, fill="#DBDEE1", weight=400, mono=False, anchor="start", strike=False):
        fam = MONO if mono else SANS
        deco = ' text-decoration="line-through"' if strike else ""
        self.b.append(f'<text x="{x:.0f}" y="{y:.0f}" font-family="{fam}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{deco}>{E(s)}</text>')
    def code(self, x, y, s, size=12, fill="#DBDEE1"):
        """Inline code chip; returns its width."""
        w = tw(s, size, mono=True) + 10
        self.rect(x, y - size - 2, w, size + 8, "#1E1F22", rx=3)
        self.text(x + 5, y, s, size, fill, mono=True)
        return w
    def header(self, time_s):
        self.b.append('<circle cx="36" cy="38" r="20" fill="#5865F2"/>')
        self.text(36, 43, "PTT", 12, "#FFFFFF", 700, anchor="middle")
        self.text(70, 34, "PTT-DC-Bot", 15, "#F2F3F5", 600)
        self.rect(160, 21, 34, 17, "#5865F2", rx=4)
        self.text(177, 33.5, "APP", 10, "#FFFFFF", 700, anchor="middle")
        self.text(204, 34, time_s, 12, "#949BA4")
    def embed(self, y, accent):
        self._emb = (len(self.b), y, accent)
        return 86
    def embed_end(self, bottom):
        idx, y, accent = self._emb
        h = bottom - y
        w = self.w - 70 - 24
        self.b[idx:idx] = [
            f'<rect x="70" y="{y}" width="{w}" height="{h:.0f}" rx="4" fill="#2B2D31"/>',
            f'<path d="M70 {y+4} a4 4 0 0 1 4 -4 v{h:.0f} a4 4 0 0 1 -4 -4 z" fill="{accent}"/>',
        ]
        return bottom
    def runs(self, x, y, parts, size=13):
        spans = "".join(
            f'<tspan font-weight="{700 if bold else 400}" fill="{"#F2F3F5" if bold else "#DBDEE1"}">{E(t)}</tspan>'
            for t, bold in parts if t)
        self.b.append(f'<text x="{x:.0f}" y="{y:.0f}" font-family="{SANS}" font-size="{size}" xml:space="preserve">{spans}</text>')
    def button(self, x, y, label, fill, w=None, link=False):
        w = w or tw(label, 14) + 34 + (18 if link else 0)
        self.rect(x, y, w, 34, fill, rx=4)
        self.text(x + (w - (18 if link else 0)) / 2, y + 22, label, 14, "#FFFFFF", 500, anchor="middle")
        if link:
            ax = x + w - 24
            self.b.append(f'<path d="M{ax} {y+12} h8 v8 M{ax+8} {y+12} l-9 9" stroke="#FFFFFF" stroke-width="1.6" fill="none"/>')
        return w
    def save(self, name):
        body = "\n  ".join(self.b)
        open(f"{OUT}/{name}", "w", encoding="utf-8").write(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}" role="img" aria-label="{E(self.title)}">\n'
            f'  <title>{E(self.title)}</title>\n'
            f'  <rect width="{self.w}" height="{self.h}" rx="8" fill="#313338"/>\n  {body}\n</svg>\n')

LABEL = dict(size=13, fill="#F2F3F5", weight=600)
MUTED = "#949BA4"

# --------------------------------------------------------------------- setup panel
def setup_panel(en):
    s = Svg(680, 0, "PTT-DC-Bot /setup panel" if en else "PTT-DC-Bot /setup 設定面板")
    s.header("Today at 9:20 PM" if en else "今天 21:20")
    x = s.embed(52, "#3498DB")
    y = 80
    s.text(x, y, "PTT Tracking Dispatch Control Center" if en else "PTT-DC-Bot 視覺化互動設定面板", 16, "#FFFFFF", 700)
    y += 24
    lab = "Scope: " if en else "歸屬範圍："
    s.text(x, y, lab, 13, "#F2F3F5", 700)
    s.code(x + tw(lab, 13) + 2, y, "[Server-wide Tracking]" if en else "[伺服器追蹤]")
    y += 22
    for line in (["Welcome to PTT Smart Tracker! Use this panel to bind notification channels,",
                  "select monitored trading boards, add tracking keywords, and test alerts."] if en else
                 ["歡迎使用 PTT 智慧追蹤機器人！透過本面板，您可以一鍵完成推播頻道綁定、勾選",
                  "熱門二手交易看板、新增追蹤關鍵字，並發送測試推播驗證。"]):
        s.text(x, y, line, 13); y += 19
    y += 8
    s.text(x, y, "Current Alert Target" if en else "目前推播目標", **LABEL); y += 20
    chip = "# ptt-alerts" if en else "# 二手通知"
    w = tw(chip, 13) + 10
    s.rect(x, y - 14, w, 19, "#3C4270", rx=3)
    s.text(x + 5, y, chip, 13, "#C9CDFB", 500)
    s.text(x + w + 6, y, "(", 13)
    cw = s.code(x + w + 12, y, "1234567890123456789")
    s.text(x + w + 14 + cw, y, ")", 13)
    y += 28
    s.text(x, y, ("Monitored Boards (2/25)" if en else "系統監控看板 (2/25)"), **LABEL); y += 21
    cx = x
    for b in ("HardwareSale", "MacShop"):
        cx += s.code(cx, y, b) + 4
        s.text(cx - 2, y, ",", 13) if b == "HardwareSale" else None
        cx += 6
    y += 28
    s.text(x, y, ("Configured Keywords (2/10)" if en else "已設定追蹤關鍵字 (2/10)"), **LABEL); y += 21
    cx = x
    for i, k in enumerate(("3060 -ti", "macbook air m2")):
        cx += s.code(cx, y, k) + 4
        if i == 0:
            s.text(cx - 2, y, ",", 13)
        cx += 6
    y += 28
    s.text(x, y, "Quick Setup Guide" if en else "快速設定步驟", **LABEL); y += 20
    steps = ([("1. Click ", '"Bind Channel"', " to set alert target"),
              ("2. Select trading boards from dropdown", "", ""),
              ("3. Click ", '"Add Keyword"', " to set item & budget"),
              ("4. Click ", '"Test Alert"', " to verify delivery")] if en else
             [("1. 點擊 ", "「綁定此頻道為推播頻道」", " 設定通知目標"),
              ("2. 在下拉選單中快速勾選要監控的 ", "熱門交易看板", ""),
              ("3. 點擊 ", "「新增追蹤關鍵字」", " 填入想購買的商品與預算上限"),
              ("4. 點擊 ", "「測試推播通知」", " 確認通知卡片正常送達")])
    for a, b, c in steps:
        s.runs(x, y, [(a, False), (b, True), (c, False)])
        y += 19
    y += 8
    s.text(x, y, ("PTT-DC-Bot /setup Interactive Panel • Language: English (en)  •  Today at 9:20 PM" if en
                  else "PTT-DC-Bot /setup 互動設定面板 • 語言: 繁體中文 (zh_TW)  •  今天 21:20"), 11, MUTED)
    # components
    cy = s.embed_end(y + 14) + 10
    s.rect(70, cy, 586, 40, "#1E1F22", rx=4, stroke="#3F4147")
    s.text(84, cy + 25, "Select boards to monitor (multi-select)..." if en else "快速選擇監控看板（可多選）...", 14, MUTED)
    s.b.append(f'<path d="M632 {cy+16} l6 7 l6 -7" stroke="#B5BAC1" stroke-width="1.8" fill="none"/>')
    by = cy + 50
    s.h = by + 34 + 20
    bx = 70
    for label, col in ((("Bind Channel", "#5865F2"), ("Add Keyword", "#248046"), ("Test Alert", "#4E5058")) if en else
                       (("綁定此頻道為推播頻道", "#5865F2"), ("新增追蹤關鍵字", "#248046"), ("測試推播通知", "#4E5058"))):
        bx += s.button(bx, by, label, col) + 8
    s.save("setup_control_panel_en.svg" if en else "setup_control_panel.svg")

# --------------------------------------------------------------------- dropdown
BOARDS = [
    ("HardwareSale", "電蝦二手 (電腦零件/顯示卡/周邊)", True),
    ("DC_Sale", "數位相機二手 (相機/鏡頭/空拍機 DJI/GoPro)", False),
    ("MacShop", "蘋果二手 (MacBook/iPad/AirPods/iPhone)", True),
    ("nb-shopping", "筆電交易 (筆記型電腦/電競筆電/二手筆電)", False),
    ("Gamesale", "遊戲交易 (Switch/PS5/遊戲片/主機周邊)", False),
    ("mobilesales", "手機二手交易 (iPhone/Samsung/Pixel/安卓)", False),
]
def dropdown(en):
    rows = len(BOARDS)
    s = Svg(680, 64 + rows * 52 + 34, "Board multi-select" if en else "看板多選下拉選單")
    s.rect(24, 16, 632, 40, "#1E1F22", rx=4, stroke="#5865F2")
    s.text(38, 41, "Select boards to monitor (multi-select)..." if en else "快速選擇監控看板（可多選）...", 14, MUTED)
    s.b.append('<path d="M632 39 l6 -7 l6 7" stroke="#B5BAC1" stroke-width="1.8" fill="none"/>')
    top = 58
    s.rect(24, top, 632, rows * 52 + 30, "#2B2D31", rx=4, stroke="#1E1F22")
    for i, (name, desc, sel) in enumerate(BOARDS):
        y = top + i * 52
        if sel:
            s.rect(25, y + 1, 630, 50, "#404249", rx=3)
        s.text(40, y + 22, name, 14, "#F2F3F5", 500)
        s.text(40, y + 40, desc, 12, MUTED)
        if sel:
            s.rect(620, y + 16, 20, 20, "#5865F2", rx=4)
            s.b.append(f'<path d="M625 {y+26} l4 4 l7 -8" stroke="#FFFFFF" stroke-width="2" fill="none"/>')
        else:
            s.rect(620, y + 16, 20, 20, "none", rx=4, stroke="#80848E")
    s.text(40, top + rows * 52 + 19,
           "… 4 more: Headphone, MobileComm, forsale, Lifeismoney (other boards: /board_add)" if en
           else "… 另有 Headphone、MobileComm、forsale、Lifeismoney（其他看板可用 /board_add 加入）", 12, MUTED)
    s.save("dropdown_boards_select_en.svg" if en else "dropdown_boards_select.svg")

# --------------------------------------------------------------------- modal
def modal():
    s = Svg(560, 520, "新增追蹤關鍵字表單")
    s.b.clear()
    s.rect(0, 0, 560, 520, "#1E1F22", rx=8)
    s.rect(40, 20, 480, 480, "#313338", rx=8)
    s.text(64, 60, "新增追蹤關鍵字", 20, "#F2F3F5", 700)
    s.b.append('<path d="M484 46 l12 12 M496 46 l-12 12" stroke="#B5BAC1" stroke-width="2"/>')
    fields = [
        ("追蹤關鍵字 (必填)", True, "3060 -ti", False),
        ("目標看板 (可填中文如 相機/筆電/蘋果，留空依商品自動判斷)", False, "HardwareSale", False),
        ("限定分類 (選填，留空預設 賣)", False, "賣", False),
        ("預算上限 (新台幣整數，留空代表不限)", False, "例如: 6500 (超過此金額自動不推播)", True),
    ]
    y = 96
    for label, req, val, placeholder in fields:
        s.text(64, y, label, 12, "#B5BAC1", 700)
        if req:
            s.text(64 + tw(label, 12) + 4, y, "*", 12, "#F23F42", 700)
        s.rect(64, y + 8, 432, 40, "#1E1F22", rx=3)
        s.text(76, y + 33, val, 15, "#6D6F78" if placeholder else "#DBDEE1")
        y += 84
    s.rect(40, 432, 480, 68, "#2B2D31", rx=0)
    s.b.append('<path d="M40 492 a8 8 0 0 0 8 8 h464 a8 8 0 0 0 8 -8 z" fill="#2B2D31"/>')
    s.text(388, 472, "取消", 14, "#FFFFFF", 500, anchor="middle")
    s.rect(424, 449, 72, 36, "#5865F2", rx=3)
    s.text(460, 472, "提交", 14, "#FFFFFF", 500, anchor="middle")
    s.save("keyword_modal_form.svg")

# --------------------------------------------------------------------- notification / sold
def card(sold):
    s = Svg(680, 0, "已售出原位更新卡片" if sold else "新上架推播通知卡片")
    s.header("今天 21:34")
    if sold:
        s.text(274, 34, "(已編輯)", 11, MUTED)
    x = s.embed(52, "#7F8C8D" if sold else "#2ECC71")
    y = 76
    s.text(x, y, "PTT 【HardwareSale】商品狀態更新（已售出或停止收信）" if sold
           else "PTT 【HardwareSale】新上架通知（尚未售出）", 13, "#F2F3F5", 600)
    y += 26
    title = "[賣/台北/皆可] ASUS Dual RTX 3060 12G 保固內"
    if sold:
        s.text(x, y, "[已售出]", 16, "#00A8FC", 700)
        s.text(x + tw("[已售出] ", 16), y, title, 16, "#00A8FC", 700, strike=True)
    else:
        s.text(x, y, title, 16, "#00A8FC", 700)
    y += 30
    s.text(x, y, "命中關鍵字", **LABEL); y += 21
    s.code(x, y, "3060 -ti")
    y += 30
    cols = (x, x + 190, x + 380)
    for cx, lab in zip(cols, ("作者", "分類", "日期 / 推文")):
        s.text(cx, y, lab, **LABEL)
    y += 21
    s.code(cols[0], y, "seller123"); s.code(cols[1], y, "賣")
    w = s.code(cols[2], y, "11/11")
    s.text(cols[2] + w + 5, y, "(推:", 13)
    s.code(cols[2] + w + 36, y, "2" if sold else "0")
    s.text(cols[2] + w + 36 + 19, y, ")", 13)
    y += 30
    for cx, lab in zip(cols, ("欲售價格", "交易方式 / 地區", "買方排隊狀況")):
        s.text(cx, y, lab, **LABEL)
    y += 21
    s.code(cols[0], y, "5,800 元", fill="#F2F3F5")
    s.code(cols[1], y, "台北面交 / 7-11 店到店")
    if sold:
        s.text(cols[2], y, "已有", 13); w = s.code(cols[2] + 30, y, "2"); s.text(cols[2] + 34 + w, y, "位買家留言 / 排隊", 13)
    else:
        s.text(cols[2], y, "尚無買家排隊", 13)
    y += 30
    s.text(x, y, "AI 摘要評估", **LABEL); y += 21
    s.code(x, y, "保固至 2027/03，盒裝齊全，非礦卡")
    y += 30
    s.text(x, y, "內文重點預覽", **LABEL); y += 10
    s.rect(x, y, 556, 80, "#1E1F22", rx=4, stroke="#232428")
    for i, line in enumerate(("【物品名稱】ASUS Dual RTX 3060 O12G V2",
                              "【欲售價格】5,800 元",
                              "【保固日期】2027/03，購買證明與原廠盒裝齊全",
                              "【交易方式】台北市面交 / 7-11 店到店")):
        s.text(x + 10, y + 19 + i * 17, line, 12, "#DBDEE1", mono=True)
    y += 80
    if sold:
        y += 22
        s.text(x, y, "結案原因", **LABEL); y += 21
        s.code(x, y, "作者推文：已售出 謝謝")
        y += 8
    y += 24
    s.text(x, y, "發文: 2026-11-11 21:31:07 | [伺服器追蹤] 我的伺服器 | AI審核: gpt-oss-120b  •  今天 21:34", 11, MUTED)
    by = s.embed_end(y + 14) + 10
    s.h = by + 34 + 20
    s.button(70, by, "前往 PTT HardwareSale 查看原文", "#4E5058", link=True)
    s.save("sold_update_preview.svg" if sold else "notification_embed_preview.svg")

for en in (False, True):
    setup_panel(en); dropdown(en)
modal(); card(False); card(True)

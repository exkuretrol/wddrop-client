"""
The window's own language, which is not the game's.

TWO DIFFERENT THINGS
--------------------
`ClientConfig.locale` is the language the GAME is in. It decides which vocabulary is loaded
and therefore which item names can be read at all — get it wrong and nothing is recognised.

`ClientConfig.ui_locale` is the language of this window. It decides nothing about capture.

They are separate because they genuinely differ in the field: an English Windows running a
Traditional Chinese client is ordinary, and a player who set the interface to English would
otherwise silently switch their capture to the English vocabulary and record nothing.

The interface follows the operating system by default and falls back to English, because a
player who has never opened Settings should still get a window in their own language.

WHY A DICT AND NOT gettext
--------------------------
Six locales and a few dozen strings. gettext would add a compile step, a binary catalogue
per language and a build dependency, to hold what fits in one readable file — and a missing
translation here falls back to English by construction rather than by configuration.
"""
from __future__ import annotations

import locale as _locale
import os

# The six the game ships, and therefore the six worth translating.
LOCALES = ("zh_tw", "zh_cn", "ja", "en", "ko", "de")
FALLBACK = "en"

# What each locale is called IN that locale — a language list written in English is no use
# to the person who needs it.
NATIVE_NAMES = {
    "zh_tw": "繁體中文",
    "zh_cn": "简体中文",
    "ja": "日本語",
    "en": "English",
    "ko": "한국어",
    "de": "Deutsch",
}

# Windows and POSIX name locales differently, and Traditional and Simplified Chinese must not
# collapse into each other — they are different vocabularies, and mixing them would fail every
# match. Region decides: TW/HK/MO are traditional, everything else simplified.
_PREFIX = {"ja": "ja", "ko": "ko", "de": "de", "en": "en"}
_CHINESE_TRADITIONAL_REGIONS = {"tw", "hk", "mo"}


def system_locale() -> str:
    """The operating system's language, mapped to one we have. English when we do not."""
    raw = ""
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        raw = os.environ.get(name) or ""
        if raw:
            break
    if not raw:
        try:                                    # Windows: getlocale reads the user default
            raw = (_locale.getlocale()[0] or "") or (_locale.getdefaultlocale()[0] or "")
        except Exception:
            raw = ""
    return match_locale(raw)


def match_locale(raw: str) -> str:
    """Map an OS locale name ('zh_TW', 'Chinese (Traditional)_Taiwan', 'de-DE') to ours."""
    # Strip the encoding suffix FIRST. 'zh_TW.UTF-8' otherwise splits into ('zh',
    # 'tw.utf-8'), the region never matches 'tw', and a Traditional Chinese system is served
    # the Simplified vocabulary — which recognises nothing.
    text = (raw or "").replace("-", "_").split(".")[0].lower()
    if not text:
        return FALLBACK
    language = text.split("_")[0]
    if language in ("zh", "chinese") or "chinese" in text:
        region = ""
        parts = text.replace(")", "_").replace("(", "_").split("_")
        for part in parts:
            if part in _CHINESE_TRADITIONAL_REGIONS or part in ("taiwan", "hong", "macau"):
                region = "tw"
        if "traditional" in text or "hant" in text or region == "tw":
            return "zh_tw"
        return "zh_cn"
    return _PREFIX.get(language, FALLBACK)


# -- the strings ------------------------------------------------------------------
# Keyed by an English sentence rather than by a code: the key IS the English text, so the
# fallback needs no table and an untranslated string reads correctly instead of showing a
# key like `ui.record.state.idle` to a player.
STRINGS: dict[str, dict[str, str]] = {
    # -- shell
    "Record": {"zh_tw": "記錄", "zh_cn": "记录", "ja": "記録", "ko": "기록", "de": "Aufnahme"},
    "Guide": {"zh_tw": "指南", "zh_cn": "指南", "ja": "ガイド", "ko": "안내", "de": "Anleitung"},
    "Settings": {"zh_tw": "設定", "zh_cn": "设置", "ja": "設定", "ko": "설정", "de": "Einstellungen"},
    # -- record page
    "Start recording": {"zh_tw": "開始記錄", "zh_cn": "开始记录", "ja": "記録を開始",
                        "ko": "기록 시작", "de": "Aufnahme starten"},
    "Stop recording": {"zh_tw": "停止記錄", "zh_cn": "停止记录", "ja": "記録を停止",
                       "ko": "기록 중지", "de": "Aufnahme beenden"},
    "Mark next dive": {"zh_tw": "標記下一趟", "zh_cn": "标记下一趟", "ja": "次の探索を記す",
                       "ko": "다음 탐험 표시", "de": "Nächsten Gang markieren"},
    "A pickaxe broke": {"zh_tw": "十字鎬壞了", "zh_cn": "鹤嘴镐坏了", "ja": "つるはしが壊れた",
                        "ko": "곡괭이가 부서짐", "de": "Spitzhacke zerbrochen"},
    "Upload": {"zh_tw": "上傳", "zh_cn": "上传", "ja": "アップロード", "ko": "업로드", "de": "Hochladen"},
    "at": {"zh_tw": "時間", "zh_cn": "时间", "ja": "時間", "ko": "시간", "de": "Zeit"},
    "from": {"zh_tw": "來源", "zh_cn": "来源", "ja": "取得元", "ko": "출처", "de": "Quelle"},
    "what it recorded": {"zh_tw": "記錄內容", "zh_cn": "记录内容", "ja": "記録した内容",
                         "ko": "기록한 내용", "de": "Aufgezeichnet"},
    # The name of this client. The GAME's own title differs per language, so the tool's does
    # too: a Traditional Chinese player knows 「辟邪除妖」 and would not recognise the Japanese
    # title, and the reverse. The part after it is what this does, in that language's own
    # word for a chest — the same word the client uses everywhere else, and the game's.
    "Wizardry Variants Daphne chest log": {
        "zh_tw": "辟邪除妖 Variants Daphne 寶箱紀錄工具",
        "zh_cn": "辟邪除妖 Variants Daphne 宝箱记录工具",
        "ja": "ウィザードリィ ヴァリアンツ ダフネ 宝箱記録ツール",
        "ko": "위저드리 배리언츠 다프네 보물상자 기록 도구",
        "de": "Wizardry Variants Daphne Truhen-Protokoll"},
    # Beside the name, never in small print. This leads with a game's title, and nobody
    # should have to wonder whether it came from the people who made the game.
    "Recording. Open a chest or work a vein and it will appear here.": {
        "zh_tw": "記錄中。打開寶箱或進行採掘，結果會出現在這裡。",
        "zh_cn": "记录中。打开宝箱或进行采掘，结果会出现在这里。",
        "ja": "記録中です。宝箱を開けるか採掘すると、ここに出ます。",
        "ko": "기록 중입니다. 보물상자를 열거나 채굴하면 여기에 나타납니다.",
        "de": "Zeichnet auf. Öffne eine Truhe oder baue eine Ader ab — es erscheint hier."},
    "Choose the dungeon you are in, above.": {
        "zh_tw": "請先在上方選擇你所在的迷宮。",
        "zh_cn": "请先在上方选择你所在的迷宫。",
        "ja": "上で、今いるダンジョンを選んでください。",
        "ko": "위에서 지금 있는 던전을 선택하세요.",
        "de": "Wähle oben den Irrgarten, in dem du bist."},
    "Ready when you are — press {start}.": {
        "zh_tw": "準備就緒，按下「{start}」即可開始。",
        "zh_cn": "准备就绪，按下「{start}」即可开始。",
        "ja": "準備できました。「{start}」を押してください。",
        "ko": "준비되었습니다. «{start}»을(를) 누르세요.",
        "de": "Bereit — drücke „{start}“."},
    "{n} of {total} data files loaded": {
        "zh_tw": "已載入 {total} 個資料檔中的 {n} 個",
        "zh_cn": "已载入 {total} 个资料档中的 {n} 个",
        "ja": "データファイル {total} 個のうち {n} 個を読み込み済み",
        "ko": "데이터 파일 {total}개 중 {n}개 불러옴",
        "de": "{n} von {total} Datendateien geladen"},
    "missing": {"zh_tw": "缺少", "zh_cn": "缺少", "ja": "不足", "ko": "없음", "de": "fehlt"},
    "{days} days recorded": {
        "zh_tw": "共記錄 {days} 天", "zh_cn": "共记录 {days} 天",
        "ja": "記録した日数 {days} 日", "ko": "기록한 날짜 {days}일",
        "de": "{days} Tage aufgezeichnet"},
    "Kept {n} frames — that is the limit, so no more pictures are being saved. Drops are "
    "still being recorded.": {
        "zh_tw": "已保存 {n} 張畫面，達到上限，之後不再保存畫面。掉落紀錄仍會繼續記錄。",
        "zh_cn": "已保存 {n} 张画面，达到上限，之后不再保存画面。掉落记录仍会继续记录。",
        "ja": "画面を {n} 枚保存し、上限に達しました。これ以降は保存されません。ドロップの記録は続きます。",
        "ko": "화면 {n}장을 저장해 상한에 도달했습니다. 이후로는 저장되지 않습니다. 드롭 기록은 계속됩니다.",
        "de": "{n} Bilder gespeichert — das ist das Limit, weitere werden nicht gesichert. "
              "Drops werden weiterhin aufgezeichnet."},
    "chest": {"zh_tw": "寶箱", "zh_cn": "宝箱", "ja": "宝箱", "ko": "보물상자", "de": "Truhe"},
    # 「礦脈」 was ours; this is the game's. Its own help entry for mining names the place
    # in every language — 採掘點 / 採掘ポイント / 채굴 포인트 / mining point / Abbaustelle —
    # and a player looking for the word they read in the game should find that word here.
    "vein": {"zh_tw": "採掘點", "zh_cn": "采掘点", "ja": "採掘ポイント", "ko": "채굴 포인트",
             "de": "Abbaustelle"},
    "next dive": {"zh_tw": "下一趟", "zh_cn": "下一趟", "ja": "次の探索", "ko": "다음 탐험",
                  "de": "nächster Gang"},
    # -- state line
    "Ready. Pick the dungeon you are in, then start.": {
        "zh_tw": "準備就緒。選擇你所在的迷宮，然後開始記錄。",
        "zh_cn": "准备就绪。选择你所在的迷宫，然后开始记录。",
        "ja": "準備完了。今いるダンジョンを選んでから記録を始めてください。",
        "ko": "준비 완료. 지금 있는 던전을 고른 뒤 기록을 시작하세요.",
        "de": "Bereit. Wähle den Dungeon, in dem du bist, und starte."},
    "Calibrate before recording.": {
        "zh_tw": "開始記錄前需要先校正。", "zh_cn": "开始记录前需要先校正。",
        "ja": "記録の前にキャリブレーションが必要です。", "ko": "기록하기 전에 보정이 필요합니다.",
        "de": "Vor der Aufnahme kalibrieren."},
    "Preparing. This takes a few seconds.": {
        "zh_tw": "準備中，需要幾秒鐘。", "zh_cn": "准备中，需要几秒钟。",
        "ja": "準備中です。数秒かかります。", "ko": "준비 중입니다. 몇 초 걸립니다.",
        "de": "Wird vorbereitet. Das dauert ein paar Sekunden."},
    "Recording. Play normally.": {
        "zh_tw": "記錄中。照平常玩就好。", "zh_cn": "记录中。照平常玩就好。",
        "ja": "記録中。いつも通りに遊んでください。", "ko": "기록 중입니다. 평소대로 플레이하세요.",
        "de": "Nimmt auf. Spiel wie gewohnt."},
    # -- settings
    "Sharing": {"zh_tw": "分享", "zh_cn": "分享", "ja": "共有", "ko": "공유", "de": "Teilen"},
    # NOT "the study". What the player is being offered is that their drop records join
    # everyone else's to work out the rates for a dungeon — which is the true and useful
    # description of it. "Research" makes an ordinary, optional thing sound like being
    # enrolled in something.
    "Share my drop records": {
        "zh_tw": "分享我的掉落紀錄", "zh_cn": "分享我的掉落记录",
        "ja": "ドロップ記録を共有する", "ko": "드롭 기록 공유하기",
        "de": "Meine Drop-Aufzeichnungen teilen"},
    "Your records are pooled with other players' to work out the drop rates for each "
    "dungeon. Taking part is your choice — everything is recorded and kept on this "
    "computer either way, and this only decides whether it is also sent.": {
        "zh_tw": "你的紀錄會和其他玩家的合在一起，用來算出每個地城的掉落機率。"
                 "要不要加入由你決定 — 無論如何紀錄都會保存在這台電腦，"
                 "這個選項只決定要不要一併傳送。",
        "zh_cn": "你的记录会和其他玩家的合在一起，用来算出每个地下城的掉落概率。"
                 "要不要加入由你决定 — 无论如何记录都会保存在这台电脑，"
                 "这个选项只决定要不要一并发送。",
        "ja": "あなたの記録は他のプレイヤーの記録と合わせて、ダンジョンごとの"
              "ドロップ率を出すのに使われます。参加するかどうかはあなたの自由です — "
              "どちらでも記録はこの PC に保存され、これは送信するかどうかだけを決めます。",
        "ko": "당신의 기록은 다른 플레이어의 기록과 합쳐져 던전별 드롭률을 계산하는 데 "
              "쓰입니다. 참여 여부는 당신의 선택입니다 — 어느 쪽이든 기록은 이 컴퓨터에 "
              "저장되며, 이 항목은 전송 여부만 결정합니다.",
        "de": "Deine Aufzeichnungen fließen mit denen anderer Spieler zusammen, um die "
              "Droprate je Dungeon zu bestimmen. Ob du mitmachst, entscheidest du — so "
              "oder so bleibt alles auf diesem Rechner, und dies legt nur fest, ob es "
              "auch gesendet wird."},
    "When to send": {"zh_tw": "傳送時機", "zh_cn": "发送时机", "ja": "送信のタイミング",
                     "ko": "전송 시점", "de": "Wann gesendet wird"},
    "Send every {n} records": {
        "zh_tw": "每 {n} 筆記錄傳送一次", "zh_cn": "每 {n} 条记录发送一次",
        "ja": "{n} 件ごとに送信", "ko": "{n}건마다 전송",
        "de": "Alle {n} Aufzeichnungen senden"},
    "sending every {n}": {
        "zh_tw": "每 {n} 筆傳送", "zh_cn": "每 {n} 条发送", "ja": "{n} 件ごとに送信",
        "ko": "{n}건마다 전송", "de": "sendet alle {n}"},
    "Send each record as it happens": {
        "zh_tw": "每筆記錄產生時立即傳送", "zh_cn": "每笔记录产生时立即发送",
        "ja": "記録ごとにその場で送信", "ko": "기록이 생길 때마다 즉시 전송",
        "de": "Jeden Datensatz sofort senden"},
    "Send when I press Upload": {
        "zh_tw": "等我按下上傳再傳送", "zh_cn": "等我按下上传再发送",
        "ja": "アップロードを押したときに送信", "ko": "업로드를 누를 때 전송",
        "de": "Senden, wenn ich auf Hochladen klicke"},
    "Server": {"zh_tw": "伺服器", "zh_cn": "服务器", "ja": "サーバー", "ko": "서버", "de": "Server"},
    "Interface language": {"zh_tw": "介面語言", "zh_cn": "界面语言", "ja": "表示言語",
                           "ko": "인터페이스 언어", "de": "Sprache der Oberfläche"},
    "Follow Windows": {"zh_tw": "跟隨 Windows", "zh_cn": "跟随 Windows", "ja": "Windows に合わせる",
                       "ko": "Windows 설정 따르기", "de": "Windows folgen"},
    "Game language": {"zh_tw": "遊戲語言", "zh_cn": "游戏语言", "ja": "ゲームの言語",
                      "ko": "게임 언어", "de": "Sprache des Spiels"},
    "The language the game itself is in. It decides which item names can be read.": {
        "zh_tw": "遊戲本身的語言。它決定能讀出哪些道具名稱。",
        "zh_cn": "游戏本身的语言。它决定能读出哪些道具名称。",
        "ja": "ゲーム自体の言語です。どのアイテム名を読み取れるかを決めます。",
        "ko": "게임 자체의 언어입니다. 어떤 아이템 이름을 읽을 수 있는지가 여기서 정해집니다.",
        "de": "Die Sprache des Spiels selbst. Sie entscheidet, welche Gegenstandsnamen "
              "gelesen werden können."},
    "Dungeon": {"zh_tw": "迷宮", "zh_cn": "迷宫", "ja": "ダンジョン", "ko": "던전", "de": "Dungeon"},
    "Floor": {"zh_tw": "樓層", "zh_cn": "楼层", "ja": "階層", "ko": "층", "de": "Ebene"},
    "not sure": {"zh_tw": "不確定", "zh_cn": "不确定", "ja": "わからない", "ko": "모르겠음",
                 "de": "unsicher"},
    "Choose the dungeon you are in": {
        "zh_tw": "選擇你所在的迷宮", "zh_cn": "选择你所在的迷宫",
        "ja": "今いるダンジョンを選択", "ko": "지금 있는 던전을 선택",
        "de": "Wähle deinen Dungeon"},
    "Sample rate": {"zh_tw": "取樣率", "zh_cn": "采样率", "ja": "サンプリング",
                    "ko": "샘플링 속도", "de": "Abtastrate"},
    "Pickaxes carried": {"zh_tw": "攜帶的十字鎬", "zh_cn": "携带的鹤嘴镐", "ja": "所持しているつるはし",
                         "ko": "가진 곡괭이", "de": "Mitgeführte Spitzhacken"},
    "Counted down as you mine. Never sent.": {
        "zh_tw": "採掘時自動遞減。不會傳送。", "zh_cn": "采掘时自动递减。不会发送。",
        "ja": "採掘するたびに減ります。送信されません。",
        "ko": "채굴할 때마다 줄어듭니다. 전송되지 않습니다.",
        "de": "Zählt beim Schürfen herunter. Wird nie gesendet."},
    "Keep the frames": {"zh_tw": "保留畫面影像", "zh_cn": "保留画面影像", "ja": "フレームを保存",
                        "ko": "프레임 보관", "de": "Bilder behalten"},
    "Lets a mistake be re-read later. Uses disk.": {
        "zh_tw": "之後可以重新判讀，會佔用磁碟空間。", "zh_cn": "之后可以重新判读，会占用磁盘空间。",
        "ja": "後で読み直せます。ディスクを使います。",
        "ko": "나중에 다시 판독할 수 있습니다. 디스크를 사용합니다.",
        "de": "Ein Fehler kann später neu gelesen werden. Braucht Speicherplatz."},
    # Refused by the server for being out of date. Leads with what to do and follows with
    # the reassurance, because the fear this raises is "have I lost my records" — and the
    # answer is no, and it has to be in the same sentence.
    "This version can no longer send records — update to {version}. Nothing was lost: "
    "{waiting} record(s) are kept here and will send once you update.": {
        "zh_tw": "這個版本已無法上傳記錄，請更新到 {version}。資料沒有遺失："
                 "{waiting} 筆記錄仍保存在這台電腦，更新後會自動送出。",
        "zh_cn": "这个版本已无法上传记录，请更新到 {version}。数据没有丢失："
                 "{waiting} 条记录仍保存在这台电脑，更新后会自动送出。",
        "ja": "このバージョンでは記録を送信できません。{version} に更新してください。"
              "データは失われていません。{waiting} 件はこのPCに残り、更新後に送信されます。",
        "ko": "이 버전에서는 기록을 보낼 수 없습니다. {version} 로 업데이트해 주세요. "
              "데이터는 사라지지 않았습니다. {waiting}건은 이 PC에 남아 있으며 업데이트 후 전송됩니다.",
        "de": "Diese Version kann keine Aufzeichnungen mehr senden — aktualisiere auf "
              "{version}. Nichts ging verloren: {waiting} Aufzeichnung(en) bleiben hier und "
              "werden nach dem Update gesendet."},
    # The two headings the stats table groups under. "Currency" is ゴールド and Gil and
    # nothing else — the coins and banknotes are things you spend, not the wallet.
    "Currency": {"zh_tw": "貨幣", "zh_cn": "货币", "ja": "通貨", "ko": "화폐", "de": "Währung"},
    "Items": {"zh_tw": "物品", "zh_cn": "物品", "ja": "アイテム", "ko": "아이템",
              "de": "Gegenstände"},
    # A newer build exists. States both versions, because "out of date" without a number is
    # a thing a player cannot check, and this window has to be worth believing.
    "Version {version} is out — this is {running}.": {
        "zh_tw": "已推出 {version} 版，這個是 {running}。",
        "zh_cn": "已推出 {version} 版，这个是 {running}。",
        "ja": "{version} が公開されています。これは {running} です。",
        "ko": "{version} 이(가) 나왔습니다. 이 버전은 {running} 입니다.",
        "de": "Version {version} ist da — dies ist {running}."},
    "Get it": {"zh_tw": "前往下載", "zh_cn": "前往下载", "ja": "入手する",
               "ko": "받으러 가기", "de": "Holen"},
    "New versions": {"zh_tw": "新版本", "zh_cn": "新版本", "ja": "新しいバージョン",
                     "ko": "새 버전", "de": "Neue Versionen"},
    "Tell me when a new version is out": {
        "zh_tw": "有新版本時通知我", "zh_cn": "有新版本时通知我",
        "ja": "新しいバージョンが出たら知らせる",
        "ko": "새 버전이 나오면 알려주기",
        "de": "Sag mir Bescheid, wenn es eine neue Version gibt"},
    "Asks GitHub once, when the window opens, whether a newer client exists. It sends "
    "nothing about you or your game.": {
        "zh_tw": "視窗開啟時向 GitHub 查詢一次是否有新版本。不會送出任何關於你或你遊戲的資料。",
        "zh_cn": "窗口打开时向 GitHub 查询一次是否有新版本。不会送出任何关于你或你游戏的数据。",
        "ja": "ウィンドウを開いたときに一度だけ、新しいバージョンがあるか GitHub に問い合わせます。"
              "あなたやゲームに関する情報は送信しません。",
        "ko": "창을 열 때 한 번만 새 버전이 있는지 GitHub에 확인합니다. 사용자나 게임에 대한 "
              "정보는 보내지 않습니다.",
        "de": "Fragt GitHub einmal beim Öffnen des Fensters, ob es einen neueren Client "
              "gibt. Es wird nichts über dich oder dein Spiel gesendet."},
    # The log. Named for what it is FOR, not for its level: "trace" and "debug" are words
    # from inside this program, and the player reading this label is being asked to turn it
    # on so that a miss can be explained.
    "Detailed log": {"zh_tw": "詳細記錄檔", "zh_cn": "详细日志", "ja": "詳細ログ",
                     "ko": "상세 로그", "de": "Ausführliches Protokoll"},
    "Write a detailed log": {
        "zh_tw": "寫入詳細記錄檔", "zh_cn": "写入详细日志", "ja": "詳細ログを書き出す",
        "ko": "상세 로그 기록", "de": "Ausführliches Protokoll schreiben"},
    "Records what the client did while it read the screen, so a miss can be explained "
    "afterwards. Written to {path}. Nothing is uploaded.": {
        "zh_tw": "記錄本程式判讀畫面時的過程，日後可用來解釋漏記的情況。寫入 {path}，不會上傳。",
        "zh_cn": "记录本程序判读画面时的过程，日后可用来解释漏记的情况。写入 {path}，不会上传。",
        "ja": "画面を読んでいる間の動作を記録します。取りこぼしを後から説明できます。"
              "書き出し先は {path} です。アップロードはしません。",
        "ko": "화면을 읽는 동안의 동작을 기록해, 놓친 기록을 나중에 설명할 수 있게 합니다. "
              "{path} 에 기록되며 업로드되지 않습니다.",
        "de": "Hält fest, was der Client beim Lesen des Bildschirms getan hat, damit ein "
              "verpasster Fund später erklärt werden kann. Wird nach {path} geschrieben. "
              "Nichts wird hochgeladen."},
    "Calibrate…": {"zh_tw": "校正…", "zh_cn": "校正…", "ja": "キャリブレーション…",
                   "ko": "보정…", "de": "Kalibrieren…"},
    # Looking at what the client can see. Development only, but translated like everything
    # else: the language of a window should not change depending on which build it is.
    "that message wrapped onto a second line, so the fit had less to go on. If "
    "readings look poor later, calibrate again on a chest whose whole sentence "
    "fits on one row.": {
        "zh_tw": "那則訊息換到了第二行，所以校正能依據的東西比較少。之後若辨識不佳，"
                 "請找一個整句都在同一行的寶箱重新校正。",
        "zh_cn": "那条消息换到了第二行，所以校正能依据的东西比较少。之后若识别不佳，"
                 "请找一个整句都在同一行的宝箱重新校正。",
        "ja": "そのメッセージは2行目に折り返したため、較正の手がかりが少なくなりました。"
              "後で読み取りが悪ければ、文全体が1行に収まる宝箱でもう一度較正してください。",
        "ko": "그 메시지가 두 번째 줄로 넘어가서 보정에 쓸 단서가 적었습니다. 나중에 인식이 "
              "좋지 않으면 문장 전체가 한 줄에 들어가는 상자로 다시 보정하세요.",
        "de": "Diese Meldung ist auf eine zweite Zeile umgebrochen, also hatte die "
              "Kalibrierung weniger, woran sie sich halten konnte. Wenn die Lesungen später "
              "schlecht aussehen, kalibriere erneut an einer Truhe, deren ganzer Satz in "
              "eine Zeile passt."},
    "Use the saved shot": {
        "zh_tw": "使用已存的擷圖", "zh_cn": "使用已存的截图", "ja": "保存済みの画像を使う",
        "ko": "저장된 사진 사용", "de": "Gespeicherte Aufnahme verwenden"},
    "No saved shot here yet — capture one.": {
        "zh_tw": "這一步還沒有存過擷圖 — 請先擷取。",
        "zh_cn": "这一步还没有存过截图 — 请先截取。",
        "ja": "この手順の画像はまだありません — 先に撮影してください。",
        "ko": "이 단계의 사진이 아직 없습니다 — 먼저 촬영하세요.",
        "de": "Für diesen Schritt gibt es noch keine Aufnahme — nimm zuerst eine auf."},
    "using the saved {name} ({size})": {
        "zh_tw": "使用已存的 {name}（{size}）", "zh_cn": "使用已存的 {name}（{size}）",
        "ja": "保存済みの {name}（{size}）を使います",
        "ko": "저장된 {name}({size})을 사용합니다",
        "de": "verwende die gespeicherte {name} ({size})"},
    "No calibration for {size} yet. Calibrate at this size, then look again.": {
        "zh_tw": "還沒有 {size} 的校正。請先在這個尺寸校正，再回來看。",
        "zh_cn": "还没有 {size} 的校正。请先在这个尺寸校正，再回来看。",
        "ja": "{size} の較正がまだありません。このサイズで較正してから見てください。",
        "ko": "{size} 보정이 아직 없습니다. 이 크기로 보정한 뒤 다시 보세요.",
        "de": "Für {size} gibt es noch keine Kalibrierung. Kalibriere in dieser Größe und "
              "schau dann erneut."},
    "See it…": {"zh_tw": "看一下…", "zh_cn": "看一下…", "ja": "見てみる…",
                "ko": "살펴보기…", "de": "Ansehen…"},
    "What the client sees": {
        "zh_tw": "程式看到的畫面", "zh_cn": "程序看到的画面", "ja": "クライアントに見えているもの",
        "ko": "클라이언트가 보는 것", "de": "Was der Client sieht"},
    "Where it looks": {"zh_tw": "它看哪裡", "zh_cn": "它看哪里", "ja": "どこを見ているか",
                       "ko": "어디를 보는지", "de": "Wohin es schaut"},
    "What it gets": {"zh_tw": "它拿到什麼", "zh_cn": "它拿到什么", "ja": "何を受け取るか",
                     "ko": "무엇을 받는지", "de": "Was es bekommt"},
    "Close": {"zh_tw": "關閉", "zh_cn": "关闭", "ja": "閉じる", "ko": "닫기", "de": "Schließen"},
    "No game window yet: {why}": {
        "zh_tw": "還找不到遊戲視窗：{why}", "zh_cn": "还找不到游戏窗口：{why}",
        "ja": "ゲームウィンドウがまだ見つかりません：{why}",
        "ko": "아직 게임 창을 찾지 못했습니다: {why}",
        "de": "Noch kein Spielfenster: {why}"},
    "{size} — regions: {regions}. Everything outside them is black to the client.": {
        "zh_tw": "{size} — 區域：{regions}。這些範圍以外，程式一律看成黑色。",
        "zh_cn": "{size} — 区域：{regions}。这些范围以外，程序一律看成黑色。",
        "ja": "{size} — 領域：{regions}。この外側はすべて黒として扱われます。",
        "ko": "{size} — 영역: {regions}. 그 바깥은 모두 검게 처리됩니다.",
        "de": "{size} — Bereiche: {regions}. Alles außerhalb ist für den Client schwarz."},
    "Export my data…": {"zh_tw": "匯出我的資料…", "zh_cn": "导出我的数据…",
                        "ja": "自分のデータを書き出す…", "ko": "내 데이터 내보내기…",
                        "de": "Meine Daten exportieren…"},
    "Change…": {"zh_tw": "變更…", "zh_cn": "更改…", "ja": "変更…", "ko": "변경…", "de": "Ändern…"},
    "Settings save as you change them.": {
        "zh_tw": "設定變更後會立即儲存。", "zh_cn": "设置更改后会立即保存。",
        "ja": "設定は変更すると保存されます。", "ko": "설정은 변경하면 바로 저장됩니다.",
        "de": "Einstellungen werden sofort gespeichert."},
    # -- footer state
    "kept on this computer, not shared": {
        "zh_tw": "僅保存在這台電腦，未分享", "zh_cn": "仅保存在这台电脑，未分享",
        "ja": "このPCにのみ保存、共有しません", "ko": "이 컴퓨터에만 저장, 공유 안 함",
        "de": "bleibt auf diesem Rechner, nicht geteilt"},
    "sending as it happens": {
        "zh_tw": "產生時即時傳送", "zh_cn": "产生时即时发送", "ja": "その場で送信中",
        "ko": "생길 때마다 전송 중", "de": "wird sofort gesendet"},
    # -- first run
    "Before anything is recorded": {
        "zh_tw": "在開始記錄任何東西之前", "zh_cn": "在开始记录任何东西之前",
        "ja": "何かを記録する前に", "ko": "무엇이든 기록하기 전에",
        "de": "Bevor irgendetwas aufgezeichnet wird"},
    "I have read this and agree": {
        "zh_tw": "我已閱讀並同意", "zh_cn": "我已阅读并同意", "ja": "読んだうえで同意します",
        "ko": "읽었으며 동의합니다", "de": "Ich habe das gelesen und stimme zu"},
    "Continue": {"zh_tw": "繼續", "zh_cn": "继续", "ja": "続ける", "ko": "계속", "de": "Weiter"},
    "Not now": {"zh_tw": "現在不要", "zh_cn": "现在不要", "ja": "今はしない", "ko": "지금은 안 함",
                "de": "Jetzt nicht"},
    "{n} pickaxes left": {
        "zh_tw": "剩餘 {n} 支十字鎬", "zh_cn": "剩余 {n} 把鹤嘴镐", "ja": "つるはし残り {n} 本",
        "ko": "곡괭이 {n}개 남음", "de": "{n} Spitzhacken übrig"},
    # The one figure that moves while mining. Without it the pickaxe line sat still through
    # a whole run and read as broken detection.
    "{n} swings on this one": {
        "zh_tw": "這支已採掘 {n} 次", "zh_cn": "这把已采掘 {n} 次",
        "ja": "この 1 本で {n} 回", "ko": "이 곡괭이로 {n}회",
        "de": "{n} Schläge mit dieser"},
    "one lasts about {n} swings": {
        "zh_tw": "一支約可採掘 {n} 次", "zh_cn": "一把约可采掘 {n} 次",
        "ja": "1 本でおよそ {n} 回", "ko": "하나로 약 {n}회", "de": "eine hält etwa {n} Schläge"},
    "not enough data yet": {
        "zh_tw": "資料還不夠", "zh_cn": "数据还不够", "ja": "データがまだ足りません",
        "ko": "데이터가 아직 부족합니다", "de": "noch zu wenig Daten"},

    # -- status, and the rest of what the window says out loud
    "You chose {chosen}, but this chest's junk comes from {actual}. Check the dungeon.": {
        "zh_tw": "你選的是{chosen}，但這個寶箱裡的雜物來自{actual}。請確認地城。",
        "zh_cn": "你选的是{chosen}，但这个宝箱里的杂物来自{actual}。请确认地下城。",
        "ja": "選択は{chosen}ですが、この宝箱の雑貨は{actual}のものです。ダンジョンを確認してください。",
        "ko": "{chosen}을(를) 선택했지만 이 상자의 잡화는 {actual}의 것입니다. 던전을 확인하세요.",
        "de": "Du hast {chosen} gewählt, aber der Trödel in dieser Truhe stammt aus "
              "{actual}. Prüfe den Dungeon."},
    "No pickaxes left — restock in town to keep mining.": {
        "zh_tw": "沒有十字鎬了 — 回城裡補貨才能繼續採掘。",
        "zh_cn": "没有鹤嘴镐了 — 回城里补货才能继续采掘。",
        "ja": "つるはしがありません — 街で補充しないと採掘を続けられません。",
        "ko": "곡괭이가 없습니다 — 마을에서 보충해야 채굴을 계속할 수 있습니다.",
        "de": "Keine Spitzhacken mehr — in der Stadt nachkaufen, um weiterzugraben."},
    "Stopped. {chests} {chest}, {mined} {vein}.": {
        "zh_tw": "已停止。{chests} 個{chest}，{mined} 個{vein}。",
        "zh_cn": "已停止。{chests} 个{chest}，{mined} 个{vein}。",
        "ja": "停止しました。{chest} {chests}、{vein} {mined}。",
        "ko": "중지했습니다. {chest} {chests}개, {vein} {mined}개.",
        "de": "Gestoppt. {chests} {chest}, {mined} {vein}."},
    "Sharing is off. Turn it on in Settings to send anything.": {
        "zh_tw": "分享目前是關閉的。要傳送的話請到設定裡開啟。",
        "zh_cn": "分享目前是关闭的。要发送的话请到设置里开启。",
        "ja": "共有はオフです。送信するには設定でオンにしてください。",
        "ko": "공유가 꺼져 있습니다. 보내려면 설정에서 켜세요.",
        "de": "Teilen ist aus. Schalte es in den Einstellungen ein, um etwas zu senden."},
    "Sent {sent}. {waiting} still waiting.": {
        "zh_tw": "已傳送 {sent} 筆，還有 {waiting} 筆等待中。",
        "zh_cn": "已发送 {sent} 条，还有 {waiting} 条等待中。",
        "ja": "{sent} 件を送信しました。残り {waiting} 件。",
        "ko": "{sent}건을 보냈습니다. {waiting}건 대기 중.",
        "de": "{sent} gesendet. {waiting} warten noch."},
    "Could not send: {why}. It stays on this computer and will be retried.": {
        "zh_tw": "無法傳送：{why}。資料仍留在這台電腦，之後會重試。",
        "zh_cn": "无法发送：{why}。数据仍留在这台电脑，之后会重试。",
        "ja": "送信できませんでした：{why}。データはこの PC に残り、あとで再試行します。",
        "ko": "보내지 못했습니다: {why}. 데이터는 이 컴퓨터에 남아 있으며 나중에 다시 시도합니다.",
        "de": "Senden fehlgeschlagen: {why}. Es bleibt auf diesem Rechner und wird erneut "
              "versucht."},
    "Exported {rows} rows to {name}.": {
        "zh_tw": "已匯出 {rows} 列到 {name}。", "zh_cn": "已导出 {rows} 行到 {name}。",
        "ja": "{rows} 行を {name} に書き出しました。",
        "ko": "{rows}행을 {name}(으)로 내보냈습니다.",
        "de": "{rows} Zeilen nach {name} exportiert."},
    "…including the walking frames (much bigger; for debugging a miss)": {
        "zh_tw": "…連走路的畫面也一起留（檔案大得多；用來追查漏記錄的情況）",
        "zh_cn": "…连走路的画面也一起留（文件大得多；用来追查漏记录的情况）",
        "ja": "…歩行中のフレームも含める（かなり大きくなります。取りこぼしの調査用）",
        "ko": "…걷는 동안의 프레임도 포함（훨씬 커집니다. 누락을 조사할 때）",
        "de": "…auch die Laufbilder (viel größer; um einen Fehlschlag zu untersuchen)"},
    "Your id is {id} — quote it to have your data erased.": {
        "zh_tw": "你的識別碼是 {id} — 提出此碼即可要求刪除你的資料。",
        "zh_cn": "你的识别码是 {id} — 提出此码即可要求删除你的数据。",
        "ja": "あなたの ID は {id} です — データの削除を求めるときに提示してください。",
        "ko": "당신의 ID는 {id} 입니다 — 데이터 삭제를 요청할 때 알려주세요.",
        "de": "Deine ID ist {id} — nenne sie, um deine Daten löschen zu lassen."},
    "not calibrated — capture cannot start without it": {
        "zh_tw": "尚未校正 — 沒有校正就無法開始記錄",
        "zh_cn": "尚未校正 — 没有校正就无法开始记录",
        "ja": "未キャリブレーション — これがないと記録を開始できません",
        "ko": "보정되지 않음 — 보정 없이는 기록을 시작할 수 없습니다",
        "de": "nicht kalibriert — ohne das kann die Aufnahme nicht starten"},
    "calibrated for {sizes}": {
        "zh_tw": "已針對 {sizes} 校正", "zh_cn": "已针对 {sizes} 校正",
        "ja": "{sizes} 用に較正済み", "ko": "{sizes} 기준으로 보정됨",
        "de": "kalibriert für {sizes}"},

    # -- the guide
    # This is the page that exists for the player who does NOT already know what to do, so
    # leaving its prose in English while the rest of the window translated was the worst
    # place to leave it.
    "The client takes both screenshots itself. Press {calibrate} in {settings} and "
    "it asks twice, counting down each time so you can switch back to the game:": {
        "zh_tw": "客戶端會自己拍這兩張截圖。在{settings}中按下{calibrate}，它會問兩次，"
                 "每次都會倒數，好讓你切換回遊戲：",
        "zh_cn": "客户端会自己拍这两张截图。在{settings}中按下{calibrate}，它会问两次，"
                 "每次都会倒数，好让你切换回游戏：",
        "ja": "スクリーンショットはクライアントが自分で撮ります。{settings}で{calibrate}を"
              "押すと 2 回たずねられ、そのたびにカウントダウンするのでゲームに戻れます：",
        "ko": "스크린샷은 클라이언트가 직접 찍습니다. {settings}에서 {calibrate}를 누르면 "
              "두 번 묻고, 그때마다 카운트다운하므로 게임으로 돌아갈 수 있습니다:",
        "de": "Der Client macht beide Screenshots selbst. Drücke {calibrate} in "
              "{settings}; er fragt zweimal und zählt jedes Mal herunter, damit du ins "
              "Spiel zurückwechseln kannst:"},
    "Stand in a dungeon with the minimap visible.": {
        "zh_tw": "站在地城裡，讓小地圖看得見。", "zh_cn": "站在地下城里，让小地图看得见。",
        "ja": "ミニマップが見える状態でダンジョンに立ちます。",
        "ko": "미니맵이 보이는 상태로 던전에 섭니다.",
        "de": "Stell dich mit sichtbarer Minikarte in einen Dungeon."},
    "Open a chest and leave the 「…を手に入れた!!」 message on screen, then type the item name.": {
        "zh_tw": "打開寶箱，讓「…を手に入れた!!」訊息留在畫面上，然後輸入道具名稱。",
        "zh_cn": "打开宝箱，让“获得了…”消息留在画面上，然后输入道具名称。",
        "ja": "宝箱を開けて「…を手に入れた!!」のメッセージを画面に残し、アイテム名を入力します。",
        "ko": "상자를 열어 「…を手に入れた!!」 메시지를 화면에 둔 채 아이템 이름을 입력합니다.",
        "de": "Öffne eine Truhe, lass die 「…を手に入れた!!」-Meldung stehen und tippe den "
              "Gegenstandsnamen ein."},
    "It refuses to save a profile that cannot read back the frame it was built "
    "from, so if it accepts, it works.": {
        "zh_tw": "如果一組設定檔讀不回自己是從哪張畫面建立的，它就不會存檔；所以只要它接受了，"
                 "就是可用的。",
        "zh_cn": "如果一组配置读不回自己是从哪张画面建立的，它就不会存档；所以只要它接受了，"
                 "就是可用的。",
        "ja": "自分が作られた元のフレームを読み返せないプロファイルは保存されません。"
              "つまり受け入れられたなら、それは動きます。",
        "ko": "자신이 만들어진 프레임을 다시 읽지 못하는 프로파일은 저장되지 않습니다. "
              "즉 통과했다면 작동합니다.",
        "de": "Ein Profil, das das Bild nicht zurücklesen kann, aus dem es gebaut wurde, "
              "wird nicht gespeichert — wird es angenommen, funktioniert es."},
    "While you play": {
        "zh_tw": "遊玩時", "zh_cn": "游玩时", "ja": "プレイ中", "ko": "플레이 중",
        "de": "Während du spielst"},
    "Pick the right dungeon.": {
        "zh_tw": "選對地城。", "zh_cn": "选对地下城。", "ja": "正しいダンジョンを選ぶ。",
        "ko": "올바른 던전을 고르세요.", "de": "Wähle den richtigen Dungeon."},
    "It is the one thing this window cannot check for you, and every chest is filed "
    "under it.": {
        "zh_tw": "這是這個視窗唯一無法替你檢查的事，而每個寶箱都會歸在它底下。",
        "zh_cn": "这是这个窗口唯一无法替你检查的事，而每个宝箱都会归在它底下。",
        "ja": "このウィンドウが唯一あなたの代わりに確認できないことで、"
              "すべての宝箱がそこに記録されます。",
        "ko": "이 창이 대신 확인해 줄 수 없는 유일한 항목이며, 모든 상자가 그 아래에 기록됩니다.",
        "de": "Das Einzige, was dieses Fenster nicht für dich prüfen kann — und jede Truhe "
              "wird darunter abgelegt."},
    "Chests: let each line finish before advancing.": {
        "zh_tw": "寶箱：等每一行跑完再繼續。", "zh_cn": "宝箱：等每一行跑完再继续。",
        "ja": "宝箱：各行が出きるまで送らない。",
        "ko": "상자: 각 줄이 끝난 뒤에 넘기세요.",
        "de": "Truhen: lass jede Zeile auslaufen, bevor du weiterklickst."},
    "191 item names truncate into a different valid name, so a half-read line is a "
    "confident wrong answer, not a near miss.": {
        "zh_tw": "有 191 個道具名稱被截斷後會變成另一個真實存在的名稱，"
                 "所以讀到一半的行不是「差一點」，而是一個很有把握的錯誤答案。",
        "zh_cn": "有 191 个道具名称被截断后会变成另一个真实存在的名称，"
                 "所以读到一半的行不是“差一点”，而是一个很有把握的错误答案。",
        "ja": "191 のアイテム名は途中で切れると別の実在する名前になります。"
              "読みかけの行は惜しい間違いではなく、自信のある誤答です。",
        "ko": "191개의 아이템 이름은 잘리면 실제로 존재하는 다른 이름이 됩니다. "
              "덜 읽힌 줄은 아깝게 빗나간 것이 아니라 확신에 찬 오답입니다.",
        "de": "191 Gegenstandsnamen werden abgeschnitten zu einem anderen gültigen Namen — "
              "eine halb gelesene Zeile ist keine knappe Verfehlung, sondern eine "
              "selbstsichere Falschantwort."},
    "Play at a size this client knows": {
        "zh_tw": "\u8acb\u7528\u672c\u5de5\u5177\u8a8d\u5f97\u7684\u5c3a\u5bf8\u904a\u73a9",
        "zh_cn": "\u8bf7\u7528\u672c\u5de5\u5177\u8ba4\u5f97\u7684\u5c3a\u5bf8\u6e38\u73a9",
        "ja": "\u5bfe\u5fdc\u3057\u3066\u3044\u308b\u30b5\u30a4\u30ba\u3067\u904a\u3076",
        "ko": "\uc774 \ub3c4\uad6c\uac00 \uc544\ub294 \ud06c\uae30\ub85c \ud50c\ub808\uc774\ud558\uc138\uc694",
        "de": "Spiele in einer Gr\u00f6\u00dfe, die dieser Client kennt"},
    "both are options in the game itself, under Screen size, and both are read without any "
    "setup here.": {
        "zh_tw": "\u5169\u7a2e\u90fd\u5728\u904a\u6232\u81ea\u5df1\u7684\u300c\u756b\u9762\u5c3a\u5bf8\u300d\u9078\u9805\u88e1\uff0c"
                 "\u800c\u4e14\u90fd\u80fd\u76f4\u63a5\u8b80\u53d6\uff0c\u9019\u908a\u4e0d\u5fc5\u505a\u4efb\u4f55\u8a2d\u5b9a\u3002",
        "zh_cn": "\u4e24\u79cd\u90fd\u5728\u6e38\u620f\u81ea\u5df1\u7684\u201c\u753b\u9762\u5c3a\u5bf8\u201d\u9009\u9879\u91cc\uff0c"
                 "\u800c\u4e14\u90fd\u80fd\u76f4\u63a5\u8bfb\u53d6\uff0c\u8fd9\u8fb9\u4e0d\u5fc5\u505a\u4efb\u4f55\u8bbe\u7f6e\u3002",
        "ja": "\u3069\u3061\u3089\u3082\u30b2\u30fc\u30e0\u306e\u30aa\u30d7\u30b7\u30e7\u30f3\u306e\u300c\u753b\u9762\u30b5\u30a4\u30ba\u300d"
              "\u306b\u3042\u308a\u3001\u3053\u3061\u3089\u3067\u306e\u8a2d\u5b9a\u306a\u3057\u3067\u8aad\u3081\u307e\u3059\u3002",
        "ko": "\ub458 \ub2e4 \uac8c\uc784 \uc635\uc158\uc758 \u2018\ud654\uba74 \ud06c\uae30\u2019\uc5d0 \uc788\uc73c\uba70, "
              "\uc5ec\uae30\uc11c \ub530\ub85c \uc124\uc815\ud560 \uac83 \uc5c6\uc774 \uc77d\uc2b5\ub2c8\ub2e4.",
        "de": "Beide stehen in den Optionen des Spiels unter Bildschirmgr\u00f6\u00dfe, und beide "
              "werden ohne Einrichtung hier gelesen."},
    "Full screen is fine at those sizes. The game keeps drawing at the size you chose and "
    "Windows stretches it to fill your screen; this client reads the picture at the size it "
    "was drawn.": {
        "zh_tw": "\u9019\u5169\u7a2e\u5c3a\u5bf8\u4e0b\u5168\u87a2\u5e55\u4e5f\u6c92\u554f\u984c\u3002\u904a\u6232\u4ecd\u7136"
                 "\u4ee5\u4f60\u9078\u7684\u5c3a\u5bf8\u7e6a\u88fd\uff0c\u518d\u7531 Windows \u653e\u5927\u5230\u6574\u500b\u87a2\u5e55\uff1b"
                 "\u672c\u5de5\u5177\u6703\u4f9d\u7167\u5b83\u539f\u672c\u7e6a\u88fd\u7684\u5c3a\u5bf8\u4f86\u8b80\u3002",
        "zh_cn": "\u8fd9\u4e24\u79cd\u5c3a\u5bf8\u4e0b\u5168\u5c4f\u4e5f\u6ca1\u95ee\u9898\u3002\u6e38\u620f\u4ecd\u7136"
                 "\u4ee5\u4f60\u9009\u7684\u5c3a\u5bf8\u7ed8\u5236\uff0c\u518d\u7531 Windows \u653e\u5927\u5230\u6574\u4e2a\u5c4f\u5e55\uff1b"
                 "\u672c\u5de5\u5177\u4f1a\u6309\u7167\u5b83\u539f\u672c\u7ed8\u5236\u7684\u5c3a\u5bf8\u6765\u8bfb\u3002",
        "ja": "\u3053\u306e\u30b5\u30a4\u30ba\u306a\u3089\u5168\u753b\u9762\u3067\u3082\u554f\u984c\u3042\u308a\u307e\u305b\u3093\u3002"
              "\u30b2\u30fc\u30e0\u306f\u9078\u3093\u3060\u30b5\u30a4\u30ba\u306e\u307e\u307e\u63cf\u753b\u3057\u3001Windows \u304c"
              "\u753b\u9762\u306b\u5408\u308f\u305b\u3066\u62e1\u5927\u3057\u307e\u3059\u3002\u672c\u30c4\u30fc\u30eb\u306f"
              "\u63cf\u304b\u308c\u305f\u30b5\u30a4\u30ba\u3067\u8aad\u307f\u307e\u3059\u3002",
        "ko": "\uc774 \ud06c\uae30\ub77c\uba74 \uc804\uccb4 \ud654\uba74\ub3c4 \uad1c\ucc2e\uc2b5\ub2c8\ub2e4. \uac8c\uc784\uc740 "
              "\uc120\ud0dd\ud55c \ud06c\uae30\ub85c \uadf8\ub9ac\uace0 Windows \uac00 \ud654\uba74\uc5d0 \ub9de\uac8c \ud655\ub300\ud558\uba70, "
              "\uc774 \ub3c4\uad6c\ub294 \uadf8\ub824\uc9c4 \ud06c\uae30\ub85c \uc77d\uc2b5\ub2c8\ub2e4.",
        "de": "Vollbild ist in diesen Gr\u00f6\u00dfen in Ordnung. Das Spiel zeichnet weiterhin in "
              "der gew\u00e4hlten Gr\u00f6\u00dfe, Windows streckt das Bild auf deinen Monitor, und "
              "dieser Client liest es in der Gr\u00f6\u00dfe, in der es gezeichnet wurde."},
    "Choose the size in the game before going full screen, though. Full screen enlarges "
    "whatever the game draws, and enlarging cannot put back detail that was never drawn.": {
        "zh_tw": "\u4e0d\u904e\u8acb\u5148\u5728\u904a\u6232\u88e1\u9078\u597d\u5c3a\u5bf8\uff0c\u518d\u9032\u5168\u87a2\u5e55\u3002"
                 "\u5168\u87a2\u5e55\u53ea\u662f\u628a\u904a\u6232\u756b\u51fa\u4f86\u7684\u756b\u9762\u653e\u5927\uff0c"
                 "\u653e\u5927\u4e0d\u6703\u88dc\u56de\u539f\u672c\u5c31\u6c92\u756b\u51fa\u4f86\u7684\u7d30\u7bc0\u3002",
        "zh_cn": "\u4e0d\u8fc7\u8bf7\u5148\u5728\u6e38\u620f\u91cc\u9009\u597d\u5c3a\u5bf8\uff0c\u518d\u8fdb\u5168\u5c4f\u3002"
                 "\u5168\u5c4f\u53ea\u662f\u628a\u6e38\u620f\u753b\u51fa\u6765\u7684\u753b\u9762\u653e\u5927\uff0c"
                 "\u653e\u5927\u4e0d\u4f1a\u8865\u56de\u539f\u672c\u5c31\u6ca1\u753b\u51fa\u6765\u7684\u7ec6\u8282\u3002",
        "ja": "\u305f\u3060\u3057\u3001\u5168\u753b\u9762\u306b\u3059\u308b\u524d\u306b\u30b2\u30fc\u30e0\u5074\u3067\u30b5\u30a4\u30ba\u3092"
              "\u9078\u3093\u3067\u304f\u3060\u3055\u3044\u3002\u5168\u753b\u9762\u306f\u63cf\u304b\u308c\u305f\u753b\u9762\u3092"
              "\u62e1\u5927\u3059\u308b\u3060\u3051\u3067\u3001\u63cf\u304b\u308c\u306a\u304b\u3063\u305f\u7d30\u90e8\u306f\u623b\u308a\u307e\u305b\u3093\u3002",
        "ko": "\ub2e4\ub9cc \uc804\uccb4 \ud654\uba74\uc73c\ub85c \uac00\uae30 \uc804\uc5d0 \uac8c\uc784\uc5d0\uc11c \ud06c\uae30\ub97c "
              "\uba3c\uc800 \uace0\ub974\uc138\uc694. \uc804\uccb4 \ud654\uba74\uc740 \uadf8\ub824\uc9c4 \ud654\uba74\uc744 \ud655\ub300\ud560 "
              "\ubfd0, \uadf8\ub9ac\uc9c0 \uc54a\uc740 \uc138\ubd80\ub294 \ub418\ub3cc\uc544\uc624\uc9c0 \ubabb\ud569\ub2c8\ub2e4.",
        "de": "Stelle die Gr\u00f6\u00dfe aber im Spiel ein, bevor du auf Vollbild gehst. Vollbild "
              "vergr\u00f6\u00dfert nur, was das Spiel zeichnet \u2014 was nie gezeichnet wurde, kommt "
              "dadurch nicht zur\u00fcck."},
    "a tall window, if you prefer it. The game has no such option, so it needs {tool}: a "
    "small free utility by NowvaB that resizes the game window and nothing else. It is not "
    "ours and not bundled here.": {
        "zh_tw": "\u5982\u679c\u4f60\u504f\u597d\u76f4\u5f0f\u8996\u7a97\u3002\u904a\u6232\u672c\u8eab\u6c92\u6709\u9019\u500b\u9078\u9805\uff0c"
                 "\u6240\u4ee5\u9700\u8981 {tool}\uff1a\u5b83\u662f NowvaB \u5beb\u7684\u514d\u8cbb\u5c0f\u5de5\u5177\uff0c"
                 "\u53ea\u8abf\u6574\u904a\u6232\u8996\u7a97\u5927\u5c0f\u3002\u5b83\u4e0d\u5c6c\u65bc\u6211\u5011\uff0c\u4e5f\u6c92\u6709\u5167\u542b\u5728\u6b64\u3002",
        "zh_cn": "\u5982\u679c\u4f60\u504f\u597d\u7ad6\u7248\u7a97\u53e3\u3002\u6e38\u620f\u672c\u8eab\u6ca1\u6709\u8fd9\u4e2a\u9009\u9879\uff0c"
                 "\u6240\u4ee5\u9700\u8981 {tool}\uff1a\u5b83\u662f NowvaB \u5199\u7684\u514d\u8d39\u5c0f\u5de5\u5177\uff0c"
                 "\u53ea\u8c03\u6574\u6e38\u620f\u7a97\u53e3\u5927\u5c0f\u3002\u5b83\u4e0d\u5c5e\u4e8e\u6211\u4eec\uff0c\u4e5f\u6ca1\u6709\u5185\u542b\u5728\u6b64\u3002",
        "ja": "\u7e26\u9577\u30a6\u30a3\u30f3\u30c9\u30a6\u304c\u597d\u307f\u306a\u3089\u3002\u30b2\u30fc\u30e0\u306b\u305d\u306e\u8a2d\u5b9a"
              "\u306f\u306a\u3044\u306e\u3067 {tool} \u304c\u5fc5\u8981\u3067\u3059\u3002NowvaB \u6c0f\u306b\u3088\u308b\u7121\u6599\u306e"
              "\u5c0f\u3055\u306a\u30c4\u30fc\u30eb\u3067\u3001\u30b2\u30fc\u30e0\u30a6\u30a3\u30f3\u30c9\u30a6\u306e\u5927\u304d\u3055\u3092"
              "\u5909\u3048\u308b\u3060\u3051\u3067\u3059\u3002\u5f53\u65b9\u306e\u3082\u306e\u3067\u306f\u306a\u304f\u3001\u540c\u68b1\u3082\u3057\u3066\u3044\u307e\u305b\u3093\u3002",
        "ko": "\uc138\ub85c \ucc3d\uc744 \uc120\ud638\ud55c\ub2e4\uba74. \uac8c\uc784\uc5d0\ub294 \uadf8\ub7f0 \uc124\uc815\uc774 \uc5c6\uc5b4 "
              "{tool} \uc774 \ud544\uc694\ud569\ub2c8\ub2e4. NowvaB \uac00 \ub9cc\ub4e0 \ubb34\ub8cc \uc18c\ud615 \ub3c4\uad6c\ub85c, "
              "\uac8c\uc784 \ucc3d \ud06c\uae30\ub9cc \ubc14\uafc9\ub2c8\ub2e4. \uc800\ud76c \uac83\uc774 \uc544\ub2c8\uba70 \ud568\uaed8 "
              "\ubc30\ud3ec\ud558\uc9c0\ub3c4 \uc54a\uc2b5\ub2c8\ub2e4.",
        "de": "ein hohes Fenster, falls du das lieber magst. Das Spiel bietet das nicht an, "
              "daf\u00fcr braucht es {tool}: ein kleines kostenloses Programm von NowvaB, das nur "
              "die Fenstergr\u00f6\u00dfe \u00e4ndert. Es ist nicht von uns und liegt hier nicht bei."},
    "A half-read line is recorded as a different item, not as a near miss.": {
        "zh_tw": "\u8b80\u5230\u4e00\u534a\u7684\u884c\u6703\u88ab\u8a18\u6210\u53e6\u4e00\u500b\u9053\u5177\uff0c"
                 "\u800c\u4e0d\u662f\u300c\u5dee\u4e00\u9ede\u300d\u3002",
        "zh_cn": "\u8bfb\u5230\u4e00\u534a\u7684\u884c\u4f1a\u88ab\u8bb0\u6210\u53e6\u4e00\u4e2a\u9053\u5177\uff0c"
                 "\u800c\u4e0d\u662f\u201c\u5dee\u4e00\u70b9\u201d\u3002",
        "ja": "\u8aad\u307f\u304b\u3051\u306e\u884c\u306f\u3001\u60dc\u3057\u3044\u9593\u9055\u3044\u3067\u306f\u306a\u304f"
              "\u5225\u306e\u30a2\u30a4\u30c6\u30e0\u3068\u3057\u3066\u8a18\u9332\u3055\u308c\u307e\u3059\u3002",
        "ko": "\ub35c \uc77d\ud78c \uc904\uc740 \uc544\uae5d\uac8c \ube57\ub098\uac04 \uac83\uc774 \uc544\ub2c8\ub77c "
              "\ub2e4\ub978 \uc544\uc774\ud15c\uc73c\ub85c \uae30\ub85d\ub429\ub2c8\ub2e4.",
        "de": "Eine halb gelesene Zeile wird als ein anderer Gegenstand erfasst, nicht als "
              "knappe Verfehlung."},
    "With these on, a drop line appears complete instead of being drawn one character at a "
    "time. That matters more than it sounds: this client reads whatever is on screen, and a "
    "half-drawn line is read as a different item rather than as a near miss.": {
        "zh_tw": "\u958b\u555f\u5f8c\uff0c\u6389\u843d\u8a0a\u606f\u6703\u6574\u884c\u76f4\u63a5\u51fa\u73fe\uff0c"
                 "\u800c\u4e0d\u662f\u4e00\u500b\u5b57\u4e00\u500b\u5b57\u6162\u6162\u5beb\u51fa\u4f86\u3002\u9019\u6bd4\u60f3\u50cf\u4e2d\u91cd\u8981\uff1a"
                 "\u672c\u5de5\u5177\u8b80\u7684\u5c31\u662f\u756b\u9762\u4e0a\u7576\u4e0b\u7684\u5167\u5bb9\uff0c"
                 "\u800c\u53ea\u5beb\u5230\u4e00\u534a\u7684\u884c\u6703\u88ab\u8b80\u6210\u53e6\u4e00\u500b\u9053\u5177\uff0c\u800c\u4e0d\u662f\u300c\u5dee\u4e00\u9ede\u300d\u3002",
        "zh_cn": "\u5f00\u542f\u540e\uff0c\u6389\u843d\u4fe1\u606f\u4f1a\u6574\u884c\u76f4\u63a5\u51fa\u73b0\uff0c"
                 "\u800c\u4e0d\u662f\u4e00\u4e2a\u5b57\u4e00\u4e2a\u5b57\u6162\u6162\u5199\u51fa\u6765\u3002\u8fd9\u6bd4\u60f3\u8c61\u4e2d\u91cd\u8981\uff1a"
                 "\u672c\u5de5\u5177\u8bfb\u7684\u5c31\u662f\u753b\u9762\u4e0a\u5f53\u4e0b\u7684\u5185\u5bb9\uff0c"
                 "\u800c\u53ea\u5199\u5230\u4e00\u534a\u7684\u884c\u4f1a\u88ab\u8bfb\u6210\u53e6\u4e00\u4e2a\u9053\u5177\uff0c\u800c\u4e0d\u662f\u201c\u5dee\u4e00\u70b9\u201d\u3002",
        "ja": "\u30aa\u30f3\u306b\u3059\u308b\u3068\u3001\u30c9\u30ed\u30c3\u30d7\u306e\u884c\u304c\u4e00\u6587\u5b57\u305a\u3064\u3067\u306f\u306a\u304f"
              "\u4e00\u5ea6\u306b\u8868\u793a\u3055\u308c\u307e\u3059\u3002\u3053\u308c\u306f\u898b\u305f\u76ee\u4ee5\u4e0a\u306b\u91cd\u8981\u3067\u3059\u3002"
              "\u3053\u306e\u30c4\u30fc\u30eb\u306f\u753b\u9762\u306b\u51fa\u3066\u3044\u308b\u3082\u306e\u3092\u305d\u306e\u307e\u307e\u8aad\u3080\u305f\u3081\u3001"
              "\u63cf\u753b\u9014\u4e2d\u306e\u884c\u306f\u60dc\u3057\u3044\u9593\u9055\u3044\u3067\u306f\u306a\u304f\u5225\u306e\u30a2\u30a4\u30c6\u30e0\u3068\u3057\u3066\u8aad\u307e\u308c\u307e\u3059\u3002",
        "ko": "\ucf1c \ub450\uba74 \ub4dc\ub86d \ubb38\uad6c\uac00 \ud55c \uae00\uc790\uc529\uc774 \uc544\ub2c8\ub77c \ud55c \ubc88\uc5d0 "
              "\ud45c\uc2dc\ub429\ub2c8\ub2e4. \uc0dd\uac01\ubcf4\ub2e4 \uc911\uc694\ud569\ub2c8\ub2e4. \uc774 \ub3c4\uad6c\ub294 \ud654\uba74\uc5d0 "
              "\ub098\uc628 \uadf8\ub300\ub85c\ub97c \uc77d\uae30 \ub54c\ubb38\uc5d0, \ub35c \uadf8\ub824\uc9c4 \uc904\uc740 \uc544\uae5d\uac8c "
              "\ube57\ub098\uac04 \uac83\uc774 \uc544\ub2c8\ub77c \ub2e4\ub978 \uc544\uc774\ud15c\uc73c\ub85c \uc77d\ud799\ub2c8\ub2e4.",
        "de": "Damit erscheint eine Fundzeile vollst\u00e4ndig, statt Zeichen f\u00fcr Zeichen "
              "geschrieben zu werden. Das ist wichtiger, als es klingt: Dieser Client liest, "
              "was gerade auf dem Bildschirm steht, und eine halb gezeichnete Zeile wird als "
              "ein anderer Gegenstand gelesen statt als knappe Verfehlung."},
    "Go on to the next step.": {
        "zh_tw": "進行下一步。", "zh_cn": "进行下一步。",
        "ja": "次のステップへ進みます。",
        "ko": "다음 단계로 넘어갑니다.",
        "de": "Weiter zum nächsten Schritt."},
    "Close this window. Nothing is lost.": {
        "zh_tw": "關閉這個視窗，不會遺失任何東西。", "zh_cn": "关闭这个窗口，不会丢失任何东西。",
        "ja": "このウィンドウを閉じます。失われるものはありません。",
        "ko": "이 창을 닫습니다. 잃는 것은 없습니다.",
        "de": "Dieses Fenster schließen. Es geht nichts verloren."},
    "Carry on without this picture. The step it was for is left unfitted.": {
        "zh_tw": "不用這張圖繼續，這一步的校正就會留白。", "zh_cn": "不用这张图继续，这一步的校正就会留空。",
        "ja": "この画像なしで進みます。その分の調整は行われません。",
        "ko": "이 사진 없이 계속합니다. 해당 단계는 맞추지 않은 채로 남습니다.",
        "de": "Ohne dieses Bild weitermachen. Der zugehörige Schritt bleibt ungefittet."},
    "Use the picture already on disk from an earlier calibration instead of "
    "taking a new one.": {
        "zh_tw": "直接用先前校正時存下的圖，不重新截圖。", "zh_cn": "直接用先前校正时存下的图，不重新截图。",
        "ja": "前回の調整で保存された画像を使い、撮り直しません。",
        "ko": "이전 보정 때 저장된 사진을 그대로 사용하고 새로 찍지 않습니다.",
        "de": "Das bereits gespeicherte Bild einer früheren Kalibrierung verwenden, "
              "statt ein neues aufzunehmen."},
    "Take the picture now. Set the game up first — this reads whatever is on "
    "screen.": {
        "zh_tw": "現在截圖。請先把遊戲畫面準備好——它讀的就是當下的畫面。", "zh_cn": "现在截图。请先把游戏画面准备好——它读的就是当下的画面。",
        "ja": "今すぐ撮影します。先にゲーム画面を整えてください——写るのは今の画面です。",
        "ko": "지금 촬영합니다. 게임 화면을 먼저 준비하세요 — 지금 화면 그대로 찍힙니다.",
        "de": "Jetzt aufnehmen. Stelle das Spiel vorher ein — aufgenommen wird, was "
              "gerade zu sehen ist."},
    "Begin reading the game window. Chests and veins are recorded as they "
    "happen.": {
        "zh_tw": "開始讀取遊戲視窗。寶箱與採掘會即時記錄下來。", "zh_cn": "开始读取游戏窗口。宝箱与采掘会实时记录下来。",
        "ja": "ゲームウィンドウの読み取りを開始します。宝箱と採掘はその場で記録されます。",
        "ko": "게임 창 읽기를 시작합니다. 상자와 채굴은 그때그때 기록됩니다.",
        "de": "Beginnt, das Spielfenster zu lesen. Truhen und Adern werden sofort "
              "erfasst."},
    "Say that the next chest belongs to a new dive, when the client cannot see "
    "the change itself.": {
        "zh_tw": "當程式看不出你換了一趟時，用它宣告下一個寶箱屬於新的一趟。", "zh_cn": "当程序看不出你换了一趟时，用它宣告下一个宝箱属于新的一趟。",
        "ja": "潜り直しを自動で判断できないとき、次の宝箱が新しい探索のものだと伝えます。",
        "ko": "클라이언트가 스스로 알아채지 못할 때, 다음 상자가 새 탐험의 것임을 알려 줍니다.",
        "de": "Sagt, dass die nächste Truhe zu einem neuen Tauchgang gehört, wenn der "
              "Client den Wechsel nicht selbst sieht."},
    "Send what is waiting to the study. Nothing leaves this computer until you "
    "press it.": {
        "zh_tw": "把待上傳的資料送出。在你按下之前，什麼都不會離開這台電腦。", "zh_cn": "把待上传的数据送出。在你按下之前，什么都不会离开这台电脑。",
        "ja": "未送信の記録を送ります。押すまで、このPCから何も出ていきません。",
        "ko": "대기 중인 기록을 보냅니다. 누르기 전에는 이 컴퓨터에서 아무것도 나가지 않습니다.",
        "de": "Sendet, was ansteht, an die Studie. Vorher verlässt nichts diesen "
              "Computer."},
    "Re-read your own records and redraw this page.": {
        "zh_tw": "重新讀取你自己的紀錄並更新這一頁。", "zh_cn": "重新读取你自己的记录并更新这一页。",
        "ja": "自分の記録を読み直し、このページを更新します。",
        "ko": "내 기록을 다시 읽어 이 페이지를 새로 그립니다.",
        "de": "Liest deine Aufzeichnungen neu und zeichnet diese Seite neu."},
    "Teach the client to read a window size it does not already know. Existing "
    "calibrations are kept.": {
        "zh_tw": "教程式讀取它還不認得的視窗尺寸。既有的校正會保留。", "zh_cn": "教程序读取它还不认得的窗口尺寸。既有的校正会保留。",
        "ja": "まだ知らないウィンドウサイズの読み方を覚えさせます。既存の調整は残ります。",
        "ko": "아직 모르는 창 크기를 읽는 법을 가르칩니다. 기존 보정은 그대로 유지됩니다.",
        "de": "Bringt dem Client eine Fenstergröße bei, die er noch nicht kennt. "
              "Vorhandene Kalibrierungen bleiben erhalten."},
    "Draw what the client is looking at on top of the game, so you can check "
    "it is reading the right places.": {
        "zh_tw": "把程式正在看的位置畫在遊戲畫面上，讓你確認它讀的地方對不對。", "zh_cn": "把程序正在看的位置画在游戏画面上，让你确认它读的地方对不对。",
        "ja": "読み取り位置をゲーム画面に重ねて表示し、正しい場所を見ているか確認できます。",
        "ko": "클라이언트가 보고 있는 위치를 게임 화면 위에 그려, 올바른 곳을 읽는지 확인할 수 있습니다.",
        "de": "Zeichnet über das Spiel, worauf der Client schaut, damit du prüfen "
              "kannst, ob er die richtigen Stellen liest."},
    "Open the folder holding everything this client has kept.": {
        "zh_tw": "打開存放本工具所有資料的資料夾。", "zh_cn": "打开存放本工具所有数据的文件夹。",
        "ja": "このツールが保存したものが入っているフォルダーを開きます。",
        "ko": "이 도구가 보관한 모든 것이 있는 폴더를 엽니다.",
        "de": "Öffnet den Ordner mit allem, was dieser Client aufbewahrt."},
    "Write your records to a CSV file you choose, to keep or to look at "
    "elsewhere.": {
        "zh_tw": "把你的紀錄輸出成 CSV 檔，方便保存或用其他工具查看。", "zh_cn": "把你的记录导出成 CSV 文件，方便保存或用其他工具查看。",
        "ja": "記録を CSV に書き出します。保存や、他のツールで見るために。",
        "ko": "기록을 CSV 파일로 저장해 보관하거나 다른 곳에서 볼 수 있습니다.",
        "de": "Schreibt deine Aufzeichnungen in eine CSV-Datei deiner Wahl — zum "
              "Aufbewahren oder Ansehen anderswo."},
    "About": {
        "zh_tw": "關於", "zh_cn": "关于", "ja": "このツールについて", "ko": "정보",
        "de": "Über"},
    "A fan-made tool. It is not made by, endorsed by, or connected to the makers of the "
    "game.": {
        "zh_tw": "本工具由玩家自行製作，並非遊戲官方作品，也未經官方認可或與其有任何關聯。",
        "zh_cn": "本工具由玩家自行制作，并非游戏官方作品，也未经官方认可或与其有任何关联。",
        "ja": "有志が作ったツールです。ゲームの制作元が作ったものではなく、公認でも、関係も"
              "ありません。",
        "ko": "팬이 만든 도구입니다. 게임 제작사가 만들거나 승인하거나 관련된 것이 아닙니다.",
        "de": "Ein von Fans gemachtes Werkzeug. Es stammt nicht von den Machern des Spiels, "
              "ist nicht von ihnen unterstützt und steht in keiner Verbindung zu ihnen."},
    "landscape": {
        "zh_tw": "橫向", "zh_cn": "横向", "ja": "横向き", "ko": "가로", "de": "quer"},
    "portrait": {
        "zh_tw": "直向", "zh_cn": "竖向", "ja": "縦向き", "ko": "세로", "de": "hoch"},
    "not supported. At that size the game draws the names with too little detail to read "
    "reliably, and a line this client cannot read is left out rather than guessed at.": {
        "zh_tw": "不支援。在這個尺寸下，遊戲畫出的名稱細節太少，無法穩定辨識；"
                 "而讀不出來的行會被略過，不會用猜的。",
        "zh_cn": "不支持。在这个尺寸下，游戏画出的名称细节太少，无法稳定识别；"
                 "而读不出来的行会被略过，不会用猜的。",
        "ja": "非対応です。このサイズではアイテム名の描画が粗く、確実には読めません。"
              "読めなかった行は推測せずに記録しません。",
        "ko": "지원하지 않습니다. 이 크기에서는 이름이 너무 성기게 그려져 안정적으로 읽을 수 "
              "없고, 읽지 못한 줄은 추측하지 않고 기록에서 빠집니다.",
        "de": "nicht unterstützt. In dieser Größe zeichnet das Spiel die Namen mit zu wenig "
              "Detail, um sie zuverlässig zu lesen — und eine Zeile, die dieser Client nicht "
              "lesen kann, wird ausgelassen statt geraten."},
    "How far are you?": {
        "zh_tw": "你玩到哪裡了？",
        "zh_cn": "你玩到哪里了？",
        "ja": "どこまで進んでいますか？",
        "ko": "어디까지 진행했나요?",
        "de": "Wie weit bist du?"},
    "Some dungeons get harder as the story goes on, and that changes what "
    "drops. Tick anything you have seen — it is only ever used to compare like "
    "with like, and you can change it later in Settings.": {
        "zh_tw": "有些迷宮會隨著劇情進度變強，掉落也會跟著改變。看過的就勾起來——這只用來把條件相同的紀錄放在一起比較，之後也能在設定裡改。",
        "zh_cn": "有些迷宫会随着剧情进度变强，掉落也会跟着改变。看过的就勾起来——这只用来把条件相同的记录放在一起比较，之后也能在设置里改。",
        "ja": "ダンジョンによっては物語の進行につれて強くなり、ドロップも変わります。見たものにチェックを入れてください。条件の揃った記録どうしを比べるためだけに使い、あとから設定で変更できます。",
        "ko": "일부 던전은 이야기가 진행될수록 강해지고, 드롭도 그에 따라 달라집니다. 본 것에 체크해 주세요. 조건이 같은 기록끼리 비교하는 데에만 쓰이며, 나중에 설정에서 바꿀 수 있습니다.",
        "de": "Manche Verliese werden im Lauf der Geschichte stärker, und damit ändert "
    "sich auch, was fällt. Hake an, was du gesehen hast — es dient nur dazu, "
    "Vergleichbares zu vergleichen, und du kannst es später in den "
    "Einstellungen ändern."},
    "Close this without answering. It will not ask again for a while.": {
        "zh_tw": "先不回答就關掉。接下來一段時間不會再問。",
        "zh_cn": "先不回答就关掉。接下来一段时间不会再问。",
        "ja": "答えずに閉じます。しばらくは再度たずねません。",
        "ko": "답하지 않고 닫습니다. 당분간 다시 묻지 않습니다.",
        "de": "Ohne Antwort schließen. Es fragt eine Weile nicht wieder."},
    "Save": {
        "zh_tw": "儲存",
        "zh_cn": "保存",
        "ja": "保存",
        "ko": "저장",
        "de": "Speichern"},
    "Keep these answers. You can change them in Settings at any time.": {
        "zh_tw": "保留這些答案。之後隨時可以在設定裡修改。",
        "zh_cn": "保留这些答案。之后随时可以在设置里修改。",
        "ja": "この内容で保存します。設定からいつでも変更できます。",
        "ko": "이 답변을 저장합니다. 설정에서 언제든지 바꿀 수 있습니다.",
        "de": "Diese Antworten behalten. Du kannst sie jederzeit in den Einstellungen "
    "ändern."},
    "My story progress…": {
        "zh_tw": "我的劇情進度…",
        "zh_cn": "我的剧情进度…",
        "ja": "物語の進行状況…",
        "ko": "내 이야기 진행도…",
        "de": "Mein Fortschritt…"},
    "Say which story endings you have seen. It changes what drops, and nothing "
    "on screen shows it.": {
        "zh_tw": "告訴我們你看過哪些結局。它會影響掉落，而畫面上看不到它。",
        "zh_cn": "告诉我们你看过哪些结局。它会影响掉落，而画面上看不到它。",
        "ja": "見たエンディングを教えてください。ドロップに影響しますが、画面には表示されません。",
        "ko": "본 엔딩을 알려주세요. 드롭에 영향을 주지만 화면에는 표시되지 않습니다.",
        "de": "Sag, welche Enden du gesehen hast. Es beeinflusst die Beute und steht "
    "nirgends auf dem Bildschirm."},
    "Story progress": {
        "zh_tw": "劇情進度",
        "zh_cn": "剧情进度",
        "ja": "物語の進行状況",
        "ko": "이야기 진행도",
        "de": "Fortschritt"},
    "Some dungeons get harder as the story goes on. Recording how far you are "
    "is what lets your records be compared with other players' fairly.": {
        "zh_tw": "有些迷宮會隨著劇情進度變強。記下你的進度，才能讓你的紀錄跟其他玩家的公平比較。",
        "zh_cn": "有些迷宫会随着剧情进度变强。记下你的进度，才能让你的记录跟其他玩家的公平比较。",
        "ja": "ダンジョンによっては物語の進行につれて強くなります。進行状況を記録しておくことで、ほかのプレイヤーの記録と公平に比べられます。",
        "ko": "일부 던전은 이야기가 진행될수록 강해집니다. 진행도를 기록해 두어야 다른 플레이어의 기록과 공정하게 비교할 수 있습니다.",
        "de": "Manche Verliese werden im Lauf der Geschichte stärker. Deinen Stand "
    "festzuhalten ist das, was einen fairen Vergleich mit den Aufzeichnungen "
    "anderer möglich macht."},
    "How often to ask about story progress": {
        "zh_tw": "確認劇情進度多久問一次",
        "zh_cn": "确认剧情进度多久问一次",
        "ja": "物語の進行状況をたずねる頻度",
        "ko": "이야기 진행도를 묻는 주기",
        "de": "Wie oft nach dem Fortschritt fragen"},
    "How long to leave it before the question comes back on its own. It stops "
    "asking once you have answered everything it knows about, and you can always "
    "open it yourself above.": {
        "zh_tw": "隔多久才會自己再問一次。你把它知道的都回答完之後就不會再問了，也可以隨時從上面自己打開。",
        "zh_cn": "隔多久才会自己再问一次。你把它知道的都回答完之后就不会再问了，也可以随时从上面自己打开。",
        "ja": "この質問がひとりでに戻ってくるまでの間隔です。今わかっているぶんをすべて答えたあとはたずねません。上のボタンからいつでも開けます。",
        "ko": "질문이 저절로 다시 나타나기까지의 간격입니다. 지금 아는 항목을 모두 답하면 더 묻지 않으며, 위에서 언제든 직접 열 수 있습니다.",
        "de": "Wie lange es dauert, bis die Frage von selbst wiederkommt. Sie hört auf "
    "zu fragen, sobald du alles beantwortet hast, was sie kennt — und du kannst sie "
    "oben jederzeit selbst öffnen."},
    "Every 2 weeks": {
        "zh_tw": "每兩週",
        "zh_cn": "每两周",
        "ja": "2週間ごと",
        "ko": "2주마다",
        "de": "Alle 2 Wochen"},
    "Monthly": {
        "zh_tw": "每個月",
        "zh_cn": "每个月",
        "ja": "1か月ごと",
        "ko": "매달",
        "de": "Monatlich"},
    "Never ask": {
        "zh_tw": "不要問",
        "zh_cn": "不要问",
        "ja": "たずねない",
        "ko": "묻지 않기",
        "de": "Nie fragen"},
    "You were taken to the execution ground": {
        "zh_tw": "被押上處刑台的結局",
        "zh_cn": "被押上处刑台的结局",
        "ja": "処刑場に連れて行かれた",
        "ko": "처형장으로 끌려갔다",
        "de": "Du wurdest zur Hinrichtung geführt"},
    "The person you were sent to find was already dead": {
        "zh_tw": "要找的人已經死了",
        "zh_cn": "要找的人已经死了",
        "ja": "捜していた人はすでに亡くなっていた",
        "ko": "찾던 사람은 이미 죽어 있었다",
        "de": "Die gesuchte Person war bereits tot"},
    "You brought the person you were sent to find back": {
        "zh_tw": "把要找的人平安帶了回來",
        "zh_cn": "把要找的人平安带了回来",
        "ja": "捜していた人を連れて帰れた",
        "ko": "찾던 사람을 무사히 데려왔다",
        "de": "Du hast die gesuchte Person zurückgebracht"},
    "The duke was killed and you were blamed for it": {
        "zh_tw": "公爵被殺，而你被當成兇手",
        "zh_cn": "公爵被杀，而你被当成凶手",
        "ja": "公爵が殺され、その罪を着せられた",
        "ko": "공작이 살해되고, 그 죄를 뒤집어썼다",
        "de": "Der Herzog wurde getötet und man gab dir die Schuld"},
    "Your main character's grade": {
        "zh_tw": "主角的等級", "zh_cn": "主角的等级", "ja": "主人公の等級",
        "ko": "주인공의 등급", "de": "Rang deiner Hauptfigur"},
    "The highest promotion exam you have passed. It sets how far your party can level.": {
        "zh_tw": "你通過的最高等級昇格試驗。它決定隊伍能練到幾級。",
        "zh_cn": "你通过的最高等级晋升考试。它决定队伍能练到几级。",
        "ja": "合格した最も高い等級昇格試験です。パーティのレベル上限を決めます。",
        "ko": "합격한 가장 높은 등급 승급 시험입니다. 파티의 레벨 상한을 정합니다.",
        "de": "Die höchste Aufstiegsprüfung, die du bestanden hast. Sie legt fest, wie weit "
              "deine Gruppe aufsteigen kann."},
    "Main story": {
        "zh_tw": "主線劇情", "zh_cn": "主线剧情", "ja": "メインストーリー",
        "ko": "메인 스토리", "de": "Hauptgeschichte"},
    # The bars over the endings. Every one of these has to work for someone who has not
    # played that far — they are the words that stand where the spoiler would be.
    "Each line says how a chapter ends, so they start covered. Click one to read it.": {
        "zh_tw": "每一行都會講到某一章的結局，所以預設先遮起來。想看就點一下。",
        "zh_cn": "每一行都会讲到某一章的结局，所以默认先遮起来。想看就点一下。",
        "ja": "各行はその章の結末に触れるため、最初は伏せてあります。読むには行をクリックしてください。",
        "ko": "각 줄은 그 장의 결말을 말하므로 처음에는 가려 둡니다. 읽으려면 클릭하세요.",
        "de": "Jede Zeile verrät, wie ein Kapitel ausgeht, darum sind sie zunächst "
    "verdeckt. Zum Lesen anklicken."},
    "Show the endings": {
        "zh_tw": "顯示結局內容", "zh_cn": "显示结局内容", "ja": "結末を表示する",
        "ko": "엔딩 보이기", "de": "Enden anzeigen"},
    "Uncover all of them at once. Only do this if you have finished the story, "
    "or do not mind knowing how it goes.": {
        "zh_tw": "一次全部掀開。只有在你已經通關，或不介意先知道結局時再用。",
        "zh_cn": "一次全部掀开。只有在你已经通关，或不介意先知道结局时再用。",
        "ja": "すべてまとめて表示します。物語を最後まで見たか、先に結末を知ってもかまわない場合だけ使ってください。",
        "ko": "한 번에 모두 펼칩니다. 이야기를 끝냈거나 결말을 미리 알아도 괜찮을 때만 사용하세요.",
        "de": "Alle auf einmal aufdecken. Nur wenn du die Geschichte durch hast — oder "
    "es dir nichts ausmacht, sie vorher zu kennen."},
    "Covered so it cannot spoil the story. Click to read it.": {
        "zh_tw": "已遮住以免劇透。點一下即可閱讀。",
        "zh_cn": "已遮住以免剧透。点一下即可阅读。",
        "ja": "ネタバレを避けるため伏せています。クリックすると読めます。",
        "ko": "스포일러를 피하려고 가려 두었습니다. 클릭하면 읽을 수 있습니다.",
        "de": "Verdeckt, damit nichts verraten wird. Zum Lesen anklicken."},
    "You finished it, but someone did not make it": {
        "zh_tw": "通關了，但有人沒能活下來", "zh_cn": "通关了，但有人没能活下来",
        "ja": "クリアしたが、誰かは戻らなかった",
        "ko": "클리어했지만 누군가는 돌아오지 못했다",
        "de": "Du hast es geschafft, aber jemand kam nicht zurück"},
    "You finished it with everyone still alive": {
        "zh_tw": "所有人都活著通關", "zh_cn": "所有人都活着通关",
        "ja": "全員生きたままクリアした", "ko": "모두 살아남은 채로 클리어했다",
        "de": "Du hast es geschafft, und alle haben überlebt"},
    "You made peace with the uncle": {
        "zh_tw": "與阿爾巴諾叔父和解了", "zh_cn": "与阿尔巴诺叔父和解了",
        "ja": "アルバーノ叔父と和解した", "ko": "알바노 삼촌과 화해했다",
        "de": "Du hast dich mit dem Onkel versöhnt"},
    "You fought the great aberration and she made up her mind": {
        "zh_tw": "打了奧克多納拉斯，梅爾吉娜也下定了決心",
        "zh_cn": "打了奥克多纳拉斯，梅尔吉娜也下定了决心",
        "ja": "オクトナラスと戦い、メルジーナも決意した",
        "ko": "옥토나라스와 싸웠고, 메르지나도 결심했다",
        "de": "Du hast die große Aberration bekämpft, und sie hat sich entschieden"},
    "The couple got away alive": {
        "zh_tw": "那對情侶活著離開了", "zh_cn": "那对情侣活着离开了",
        "ja": "あの二人は生きて逃げのびた", "ko": "그 연인은 살아서 떠났다",
        "de": "Das Paar kam lebend davon"},
    "You handed over the evidence and named the culprit": {
        "zh_tw": "交出證物，指認了幕後黑手", "zh_cn": "交出证物，指认了幕后黑手",
        "ja": "証拠品を渡し、黒幕を告発した",
        "ko": "증거를 넘기고 배후를 고발했다",
        "de": "Du hast die Beweise übergeben und den Schuldigen benannt"},
    "You freed the priest and saw the story through": {
        "zh_tw": "解放了神父，走完了整個故事", "zh_cn": "解放了神父，走完了整个故事",
        "ja": "神父を解放し、物語を最後まで見届けた",
        "ko": "신부를 해방하고 이야기를 끝까지 지켜봤다",
        "de": "Du hast den Priester befreit und die Geschichte zu Ende gebracht"},
    "The duke survived": {
        "zh_tw": "公爵活了下來",
        "zh_cn": "公爵活了下来",
        "ja": "公爵は無事だった",
        "ko": "공작이 무사했다",
        "de": "Der Herzog hat überlebt"},
    "The priest changed into something you could not beat": {
        "zh_tw": "神父變成了打不贏的模樣",
        "zh_cn": "神父变成了打不赢的模样",
        "ja": "神父が倒せない姿に変わった",
        "ko": "신부가 이길 수 없는 모습으로 변했다",
        "de": "Der Priester wurde zu etwas, das du nicht besiegen konntest"},
    "You beat the priest, and the villagers came for you": {
        "zh_tw": "打倒神父之後，村民找上了你",
        "zh_cn": "打倒神父之后，村民找上了你",
        "ja": "神父を倒したあと、村人たちが来た",
        "ko": "신부를 쓰러뜨린 뒤 마을 사람들이 찾아왔다",
        "de": "Du hast den Priester besiegt, und die Dorfbewohner kamen"},
    "You beat the priest and finished the story": {
        "zh_tw": "打倒神父並走完了故事",
        "zh_cn": "打倒神父并走完了故事",
        "ja": "神父を倒して物語を最後まで見た",
        "ko": "신부를 쓰러뜨리고 이야기를 끝까지 봤다",
        "de": "Du hast den Priester besiegt und die Geschichte beendet"},
    "Disclaimer": {
        "zh_tw": "免責聲明", "zh_cn": "免责声明", "ja": "免責事項", "ko": "면책 조항",
        "de": "Haftungsausschluss"},
    "Read the disclaimer": {
        "zh_tw": "重看免責聲明", "zh_cn": "重看免责声明", "ja": "免責事項をもう一度読む",
        "ko": "면책 조항 다시 읽기", "de": "Haftungsausschluss lesen"},
    "The terms you agreed to, in full. Reading it again changes nothing.": {
        "zh_tw": "你同意過的完整條款。重看一次不會改變任何設定。",
        "zh_cn": "你同意过的完整条款。重看一次不会改变任何设置。",
        "ja": "同意した内容の全文です。読み直しても設定は変わりません。",
        "ko": "동의한 조항 전문입니다. 다시 읽어도 설정은 바뀌지 않습니다.",
        "de": "Die Bedingungen, denen du zugestimmt hast, vollständig. Erneutes Lesen "
              "ändert nichts."},
    "Veins: wait for the ▼.": {
        "zh_tw": "採掘點：等 ▼ 出現。", "zh_cn": "采掘点：等 ▼ 出现。",
        "ja": "採掘ポイント：▼ を待つ。", "ko": "채굴 포인트: ▼ 를 기다리세요.",
        "de": "Abbaustellen: warte auf das ▼."},
    "It means the panel has finished and the swing has been recorded. Dismiss before "
    "it appears and that swing is lost.": {
        "zh_tw": "它代表面板已經跑完、這次採掘已被記錄。在它出現前關掉，這次採掘就沒了。",
        "zh_cn": "它代表面板已经跑完、这次采掘已被记录。在它出现前关掉，这次采掘就没了。",
        "ja": "パネルが出きり、その 1 回が記録されたという合図です。"
              "出る前に閉じると、その 1 回は失われます。",
        "ko": "패널이 끝났고 그 한 번이 기록되었다는 뜻입니다. "
              "나타나기 전에 닫으면 그 한 번은 사라집니다.",
        "de": "Es heißt, die Anzeige ist fertig und der Schlag wurde erfasst. Klickst du "
              "vorher weg, ist er verloren."},
    "Stop between chests, not during one.": {
        "zh_tw": "要停就停在兩個寶箱之間，不要停在開箱途中。",
        "zh_cn": "要停就停在两个宝箱之间，不要停在开箱途中。",
        "ja": "止めるなら宝箱と宝箱の間で。開けている途中では止めない。",
        "ko": "멈출 때는 상자 사이에서 멈추고, 여는 도중에는 멈추지 마세요.",
        "de": "Halte zwischen Truhen an, nicht mitten in einer."},
    "If something records wrongly, turn on {frames} and do it again — a recording can "
    "be re-read after a fix.": {
        "zh_tw": "如果有東西記錄錯了，打開{frames}再做一次 — 錄下來的畫面在修正後可以重讀。",
        "zh_cn": "如果有东西记录错了，打开{frames}再做一次 — 录下来的画面在修正后可以重读。",
        "ja": "記録が間違ったときは{frames}を有効にしてもう一度 — "
              "録画してあれば修正後に読み直せます。",
        "ko": "잘못 기록되면 {frames}을(를) 켜고 다시 해 보세요 — "
              "녹화해 두면 수정 후 다시 읽을 수 있습니다.",
        "de": "Wenn etwas falsch aufgezeichnet wird, schalte {frames} ein und mach es noch "
              "einmal — eine Aufnahme lässt sich nach einer Korrektur erneut auslesen."},

    # -- stats page
    "Stats": {"zh_tw": "統計", "zh_cn": "统计", "ja": "統計", "ko": "통계",
              "de": "Zahlen"},
    "openings": {"zh_tw": "次開啟", "zh_cn": "次开启", "ja": "回", "ko": "회",
                 "de": "Öffnungen"},
    "item lines": {"zh_tw": "條道具", "zh_cn": "条道具", "ja": "行", "ko": "줄",
                   "de": "Zeilen"},
    "total": {"zh_tw": "總數", "zh_cn": "总数", "ja": "合計", "ko": "합계",
              "de": "Gesamt"},
    "{n} of them were empty": {
        "zh_tw": "其中 {n} 個是空的", "zh_cn": "其中 {n} 个是空的",
        "ja": "うち {n} 件は空でした", "ko": "그중 {n}개는 비어 있었습니다",
        "de": "{n} davon waren leer"},
    "{n} not sent yet": {
        "zh_tw": "尚有 {n} 筆未送出", "zh_cn": "尚有 {n} 条未送出",
        "ja": "{n} 件が未送信", "ko": "{n}건 미전송", "de": "{n} noch nicht gesendet"},
    "Refresh": {"zh_tw": "重新整理", "zh_cn": "刷新", "ja": "更新", "ko": "새로 고침",
                "de": "Aktualisieren"},
    "Reading the game's own font, so text can be recognised. This happens once.": {
        "zh_tw": "正在讀取遊戲自己的字體，之後才能辨識文字。這只會做一次。",
        "zh_cn": "正在读取游戏自己的字体，之后才能识别文字。这只会做一次。",
        "ja": "文字を読み取るために、ゲーム自身の書体を読み込んでいます。最初の一度だけです。",
        "ko": "글자를 읽기 위해 게임 자체 서체를 읽고 있습니다. 처음 한 번만 수행됩니다.",
        "de": "Die Schrift des Spiels wird gelesen, damit Text erkannt werden kann. Nur einmal."},
    "The game was not found on this computer, so its font could not be read. Install it, or "
    "build the atlas yourself.": {
        "zh_tw": "在這台電腦上找不到遊戲，因此無法讀取字體。請安裝遊戲，或自行建立字形資料。",
        "zh_cn": "在这台电脑上找不到游戏，因此无法读取字体。请安装游戏，或自行建立字形数据。",
        "ja": "このPCでゲームが見つからず、書体を読み取れませんでした。ゲームを入れるか、"
              "自分で用意してください。",
        "ko": "이 컴퓨터에서 게임을 찾지 못해 서체를 읽을 수 없었습니다. 게임을 설치하거나 직접 "
              "만들어 주세요.",
        "de": "Das Spiel wurde auf diesem Computer nicht gefunden, daher konnte seine Schrift "
              "nicht gelesen werden. Installiere es, oder erstelle den Atlas selbst."},
    "The glyph atlas could not be built: {why}": {
        "zh_tw": "無法建立字形資料：{why}", "zh_cn": "无法建立字形数据：{why}",
        "ja": "書体データを作成できませんでした：{why}", "ko": "글리프 데이터를 만들지 못했습니다: {why}",
        "de": "Der Glyphen-Atlas konnte nicht erstellt werden: {why}"},
    "Your records show item names in the language of this window, whatever the game is "
    "set to.": {
        "zh_tw": "無論遊戲設定成哪種語言，你的紀錄都會以這個視窗的語言顯示道具名稱。",
        "zh_cn": "无论游戏设定成哪种语言，你的记录都会以这个窗口的语言显示道具名称。",
        "ja": "ゲームの言語に関わらず、記録のアイテム名はこのウィンドウの言語で表示されます。",
        "ko": "게임 언어와 관계없이, 기록의 아이템 이름은 이 창의 언어로 표시됩니다.",
        "de": "Deine Aufzeichnungen zeigen Gegenstandsnamen in der Sprache dieses Fensters, "
              "unabhängig von der Spracheinstellung des Spiels."},
    "the size this client is set up for. Nothing to configure: it already knows how to "
    "read that window.": {
        "zh_tw": "本工具就是為這個尺寸設定的。不必做任何設定，它已經知道怎麼讀這個視窗。",
        "zh_cn": "本工具就是为这个尺寸设定的。不必做任何设定，它已经知道怎么读这个窗口。",
        "ja": "このツールが対応しているサイズです。設定は不要で、この画面の読み方をすでに知っています。",
        "ko": "이 도구가 맞춰져 있는 크기입니다. 설정할 것은 없고, 이 창을 읽는 법을 이미 알고 있습니다.",
        "de": "die Größe, für die dieser Client eingerichtet ist. Nichts einzustellen: Er "
              "weiß bereits, wie er dieses Fenster liest."},
    "Set the window to 704 × 1241 with {tool}. The game has no such option, and that tool "
    "is the only way to get this shape — it is a small free utility by NowvaB that resizes "
    "the game window and nothing else. It is not ours and not bundled here.": {
        "zh_tw": "用 {tool} 把視窗設成 704 × 1241。遊戲本身沒有這個選項，這個工具是唯一的辦法"
                 "——它是 NowvaB 寫的免費小工具，只調整遊戲視窗大小。它不屬於我們，也沒有內含在此。",
        "zh_cn": "用 {tool} 把窗口设成 704 × 1241。游戏本身没有这个选项，这个工具是唯一的办法"
                 "——它是 NowvaB 写的免费小工具，只调整游戏窗口大小。它不属于我们，也没有内含在此。",
        "ja": "{tool} でウィンドウを 704 × 1241 にします。ゲームにその設定はなく、この形にする"
              "唯一の方法です。NowvaB 氏による無料の小さなツールで、ゲームウィンドウの大きさを"
              "変えるだけです。当方のものではなく、同梱もしていません。",
        "ko": "{tool}(으)로 창을 704 × 1241 로 맞추세요. 게임에는 그런 설정이 없어서 이 방법뿐"
              "입니다. NowvaB 이 만든 무료 소형 도구로, 게임 창 크기만 바꿉니다. 저희 것이 "
              "아니며 함께 배포하지도 않습니다.",
        "de": "Stelle das Fenster mit {tool} auf 704 × 1241. Das Spiel bietet das nicht an, "
              "und dieses Werkzeug ist der einzige Weg zu diesem Format — ein kleines "
              "kostenloses Programm von NowvaB, das nur die Fenstergröße ändert. Es ist "
              "nicht von uns und liegt hier nicht bei."},
    "At any other size, including full screen, some item names are misread. If you play at "
    "a different one, please say so — a short recording is what makes it fixable.": {
        "zh_tw": "在其他尺寸（包含全螢幕）下，部分道具名稱會被誤判。如果你用別的尺寸遊玩，"
                 "請告訴我們——一段短短的錄影就能讓它被修好。",
        "zh_cn": "在其他尺寸（包含全屏）下，部分道具名称会被误判。如果你用别的尺寸游玩，"
                 "请告诉我们——一段短短的录像就能让它被修好。",
        "ja": "他のサイズ（全画面を含む）では、一部のアイテム名が読み違えられます。別のサイズで"
              "遊んでいる場合はお知らせください——短い録画があれば直せます。",
        "ko": "다른 크기(전체 화면 포함)에서는 일부 아이템 이름을 잘못 읽습니다. 다른 크기로 "
              "플레이한다면 알려주세요 — 짧은 녹화만 있으면 고칠 수 있습니다.",
        "de": "In jeder anderen Größe, Vollbild eingeschlossen, werden manche "
              "Gegenstandsnamen falsch gelesen. Wenn du in einer anderen spielst, sag "
              "bitte Bescheid — eine kurze Aufnahme macht es behebbar."},
    "The language changes when this recording stops.": {
        "zh_tw": "語言會在這次記錄停止後套用。",
        "zh_cn": "语言会在这次记录停止后套用。",
        "ja": "言語はこの記録を停止した後に切り替わります。",
        "ko": "언어는 이번 기록을 멈춘 뒤에 바뀝니다.",
        "de": "Die Sprache wechselt, sobald diese Aufzeichnung endet."},
    "Ready.": {"zh_tw": "準備完成。", "zh_cn": "准备完成。", "ja": "準備できました。",
               "ko": "준비되었습니다.", "de": "Bereit."},
    "Set the game to Japanese": {
        "zh_tw": "請將遊戲語言設為日文", "zh_cn": "请将游戏语言设为日文",
        "ja": "ゲームの言語を日本語に設定してください", "ko": "게임 언어를 일본어로 설정하세요",
        "de": "Stelle das Spiel auf Japanisch"},
    "In the game: Options → Language → 日本語. It costs nothing and can be changed back at "
    "any time.": {
        "zh_tw": "在遊戲中：設定 → 語言 → 日本語。這不需要任何費用，隨時可以改回來。",
        "zh_cn": "在游戏中：设置 → 语言 → 日本語。这不需要任何费用，随时可以改回来。",
        "ja": "ゲーム内：設定 → 言語 → 日本語。無料で、いつでも元に戻せます。",
        "ko": "게임에서: 설정 → 언어 → 日本語. 비용은 없으며 언제든 되돌릴 수 있습니다.",
        "de": "Im Spiel: Optionen → Sprache → 日本語. Kostenlos und jederzeit umstellbar."},
    "This client reads the text on your screen by drawing each candidate name in the game's "
    "own typeface and comparing the pixels — so it needs that typeface. It does not ship "
    "one: the Japanese face is readable in the files the game already installed on your "
    "computer, so the client builds what it needs there, from your own copy. Nothing is "
    "downloaded and nothing is sent.": {
        "zh_tw": "本工具辨識畫面文字的方式，是用遊戲自己的字體把每個候選名稱畫出來再比對像素，"
                 "所以它需要那套字體。程式本身不附帶字體：日文字體可以直接從遊戲已經安裝在你電腦上的"
                 "檔案讀取，因此程式會在你自己的電腦上用你自己的那份建立所需資料。不會下載，也不會上傳。",
        "zh_cn": "本工具识别画面文字的方式，是用游戏自己的字体把每个候选名称画出来再比对像素，"
                 "所以它需要那套字体。程序本身不附带字体：日文字体可以直接从游戏已经安装在你电脑上的"
                 "文件读取，因此程序会在你自己的电脑上用你自己的那份建立所需数据。不会下载，也不会上传。",
        "ja": "このツールは、候補となる名前をゲーム自身の書体で描画し、画面のピクセルと比較して"
              "文字を読み取ります。そのため書体が必要ですが、ツールには同梱していません。日本語の"
              "書体はゲームがあなたのPCに既にインストールしたファイルから読み取れるので、あなたの"
              "手元のコピーから必要なものを生成します。ダウンロードも送信もしません。",
        "ko": "이 클라이언트는 후보 이름을 게임 자체 서체로 그려 화면의 픽셀과 비교하는 방식으로 "
              "글자를 읽습니다. 그래서 그 서체가 필요하지만, 프로그램에 포함하지는 않습니다. 일본어 "
              "서체는 게임이 이미 설치해 둔 파일에서 읽을 수 있으므로, 당신의 사본에서 필요한 것을 "
              "직접 만듭니다. 다운로드도 전송도 하지 않습니다.",
        "de": "Der Client liest den Bildschirmtext, indem er jeden Kandidatennamen in der "
              "Schrift des Spiels zeichnet und die Pixel vergleicht — er braucht diese Schrift "
              "also. Mitgeliefert wird sie nicht: die japanische Schrift ist in den Dateien "
              "lesbar, die das Spiel ohnehin installiert hat, also baut der Client daraus, aus "
              "deiner eigenen Kopie. Nichts wird heruntergeladen oder gesendet."},
    "In other languages that face is not readable, and a substitute misreads about one name "
    "in ten — which is why Japanese is asked for rather than suggested. Item names in your "
    "records will be the Japanese ones.": {
        "zh_tw": "其他語言的字體無法直接讀取，改用替代字體時大約每十個名稱就有一個會辨識錯誤——"
                 "所以這裡是請你改成日文，而不只是建議。你的紀錄中道具名稱會是日文。",
        "zh_cn": "其他语言的字体无法直接读取，改用替代字体时大约每十个名称就有一个会识别错误——"
                 "所以这里是请你改成日文，而不只是建议。你的记录中道具名称会是日文。",
        "ja": "他の言語の書体は読み取れず、代替書体では名前のおよそ10件に1件を誤読します。"
              "そのため日本語は「推奨」ではなくお願いしています。記録されるアイテム名は日本語になります。",
        "ko": "다른 언어의 서체는 읽을 수 없고, 대체 서체를 쓰면 이름 열 개 중 하나쯤을 잘못 "
              "읽습니다. 그래서 일본어는 권장이 아니라 부탁입니다. 기록되는 아이템 이름은 "
              "일본어가 됩니다.",
        "de": "In anderen Sprachen ist diese Schrift nicht lesbar, und ein Ersatz liest etwa "
              "jeden zehnten Namen falsch — deshalb wird Japanisch erbeten, nicht empfohlen. "
              "Gegenstandsnamen in deinen Aufzeichnungen sind dann japanisch."},
    "Only needed at a size that is not listed above. The client takes both screenshots "
    "itself. Press {calibrate} in {settings} and it asks twice, counting down each time so "
    "you can switch back to the game:": {
        "zh_tw": "只有在上面沒列出的尺寸才需要。兩張截圖都由程式自己拍：在{settings}按下"
                 "{calibrate}，它會問兩次，每次都有倒數，讓你切回遊戲：",
        "zh_cn": "只有在上面没列出的尺寸才需要。两张截图都由程序自己拍：在{settings}按下"
                 "{calibrate}，它会问两次，每次都有倒数，让你切回游戏：",
        "ja": "上に載っていないサイズのときだけ必要です。スクリーンショットはツールが撮ります："
              "{settings}で{calibrate}を押すと2回たずねられ、そのたびカウントダウンするので"
              "ゲームに戻れます：",
        "ko": "위에 없는 크기일 때만 필요합니다. 스크린샷은 클라이언트가 직접 찍습니다. "
              "{settings}에서 {calibrate}을(를) 누르면 두 번 물어보며, 그때마다 카운트다운이 있어 "
              "게임으로 돌아갈 수 있습니다:",
        "de": "Nur bei einer Größe nötig, die oben nicht steht. Die Screenshots macht der "
              "Client selbst: Drücke {calibrate} in {settings}; er fragt zweimal und zählt "
              "jeweils herunter, damit du ins Spiel zurückwechseln kannst:"},
    "Pickaxes are counted when one breaks.": {
        "zh_tw": "十字鎬在損壞時才計數。", "zh_cn": "十字镐在损坏时才计数。",
        "ja": "ツルハシは壊れたときに数えます。", "ko": "곡괭이는 부러질 때 셉니다.",
        "de": "Spitzhacken werden gezählt, wenn eine zerbricht."},
    "The client reads the break message itself, so the number beside the pickaxe follows "
    "what the game tells you. Set it when you restock.": {
        "zh_tw": "程式會自己辨識損壞訊息，所以十字鎬旁邊的數字跟著遊戲走。補貨時再自己設定即可。",
        "zh_cn": "程序会自己识别损坏信息，所以十字镐旁边的数字跟着游戏走。补货时再自己设置即可。",
        "ja": "破損メッセージはツールが読み取るので、ツルハシの数字はゲームの表示に従います。"
              "補充したときに設定してください。",
        "ko": "클라이언트가 파손 메시지를 직접 읽으므로, 곡괭이 옆 숫자는 게임이 알려주는 대로 "
              "따라갑니다. 보충할 때 설정하세요.",
        "de": "Der Client liest die Bruchmeldung selbst, die Zahl neben der Spitzhacke folgt "
              "also dem Spiel. Setze sie, wenn du nachkaufst."},
    "Turn these two on": {
        "zh_tw": "請開啟這兩個設定", "zh_cn": "请开启这两个设置", "ja": "この2つをオンに",
        "ko": "이 두 가지를 켜 주세요", "de": "Diese beiden einschalten"},
    "In the game, under Options:": {
        "zh_tw": "在遊戲的「設定」中：", "zh_cn": "在游戏的「设置」中：",
        "ja": "ゲームの「設定」から：", "ko": "게임의 «설정»에서:", "de": "Im Spiel unter Optionen:"},
    "message fast-forward": {
        "zh_tw": "文字訊息快轉", "zh_cn": "文字信息快转", "ja": "メッセージ早送り",
        "ko": "메시지 빨리 넘기기", "de": "Nachrichten schnell vorspulen"},
    "show the whole text at once": {
        "zh_tw": "顯示所有文字", "zh_cn": "显示所有文字", "ja": "テキスト一括表示",
        "ko": "텍스트 한 번에 표시", "de": "Text vollständig anzeigen"},
    "With these on, a drop line appears complete instead of being drawn one character at a "
    "time. That matters more than it sounds: this client reads whatever is on screen, and a "
    "half-drawn line is a confident wrong answer rather than a near miss — 191 item names "
    "truncate into a different valid name.": {
        "zh_tw": "開啟後，掉落訊息會整行直接出現，而不是一個字一個字慢慢寫出來。這比想像中重要："
                 "本工具讀的就是畫面上當下的內容，而只寫到一半的行不是「差一點」，而是一個看起來"
                 "很肯定的錯誤答案——有 191 個道具名稱截斷後會變成另一個真實存在的名稱。",
        "zh_cn": "开启后，掉落信息会整行直接出现，而不是一个字一个字慢慢写出来。这比想象中重要："
                 "本工具读的就是画面上当下的内容，而只写到一半的行不是「差一点」，而是一个看起来"
                 "很肯定的错误答案——有 191 个道具名称截断后会变成另一个真实存在的名称。",
        "ja": "オンにすると、ドロップの行が一文字ずつではなく一度に表示されます。これは見た目以上に"
              "重要です。このツールは画面に出ているものをそのまま読むため、描画途中の行は「惜しい」"
              "ではなく自信たっぷりの誤答になります——191 のアイテム名は、途中で切れると別の実在する"
              "名前になります。",
        "ko": "켜 두면 드롭 문구가 한 글자씩이 아니라 한 번에 표시됩니다. 생각보다 중요합니다. 이 "
              "클라이언트는 화면에 있는 것을 그대로 읽기 때문에, 그려지다 만 줄은 «거의 맞음»이 "
              "아니라 확신에 찬 오답이 됩니다 — 191개의 아이템 이름은 잘리면 실제로 존재하는 다른 "
              "이름이 됩니다.",
        "de": "Damit erscheint eine Drop-Zeile vollständig, statt Zeichen für Zeichen "
              "geschrieben zu werden. Das wiegt schwerer, als es klingt: der Client liest, was "
              "auf dem Bildschirm steht, und eine halb gezeichnete Zeile ist keine knappe "
              "Verfehlung, sondern eine selbstbewusst falsche Antwort — 191 Gegenstandsnamen "
              "werden abgeschnitten zu einem anderen, echten Namen."},
    "You can use the computer while it records. The client reads the game window itself, "
    "not a picture of the screen, so a browser or a chat window in front of the game does "
    "not reach the recording. Minimising the game does: a window that is not being drawn "
    "has nothing to read.": {
        "zh_tw": "記錄期間你可以正常使用電腦。程式讀的是遊戲視窗本身，而不是螢幕的畫面，所以擋在"
                 "遊戲前面的瀏覽器或聊天視窗不會進到記錄裡。但把遊戲最小化會：沒有在繪製的視窗，"
                 "沒有東西可讀。",
        "zh_cn": "记录期间你可以正常使用电脑。程序读的是游戏窗口本身，而不是屏幕的画面，所以挡在"
                 "游戏前面的浏览器或聊天窗口不会进到记录里。但把游戏最小化会：没有在绘制的窗口，"
                 "没有东西可读。",
        "ja": "記録中もパソコンは普通に使えます。画面の写真ではなくゲームウィンドウそのものを読む"
              "ので、ゲームの手前にブラウザやチャットが重なっていても記録には入りません。ただし"
              "最小化はだめです。描画されていないウィンドウには読むものがありません。",
        "ko": "기록 중에도 컴퓨터를 그대로 쓸 수 있습니다. 화면 사진이 아니라 게임 창 자체를 읽기 "
              "때문에, 게임 앞에 브라우저나 채팅 창이 있어도 기록에는 들어가지 않습니다. 다만 "
              "최소화는 안 됩니다. 그려지지 않는 창에는 읽을 것이 없습니다.",
        "de": "Du kannst den Rechner w\u00e4hrend der Aufzeichnung weiter benutzen. Der Client "
              "liest das Spielfenster selbst und kein Bild des Bildschirms, ein Browser oder "
              "Chat davor landet also nicht in der Aufzeichnung. Minimieren schon: ein "
              "Fenster, das nicht gezeichnet wird, hat nichts zu lesen."},
    "Play in the tall window": {
        "zh_tw": "請使用直式視窗", "zh_cn": "请使用竖版窗口", "ja": "縦長ウィンドウで遊ぶ",
        "ko": "세로 창으로 플레이하세요", "de": "Im hohen Fenster spielen"},
    "this is the only size that reads reliably today, and the client already has a "
    "calibration for it. You do not have to calibrate anything.": {
        "zh_tw": "這是目前唯一能穩定辨識的尺寸，程式已內建校正，你不需要做任何設定。",
        "zh_cn": "这是目前唯一能稳定识别的尺寸，程序已内置校正，你不需要做任何设置。",
        "ja": "現在確実に読み取れるのはこのサイズだけです。校正は内蔵済みで、設定は不要です。",
        "ko": "현재 안정적으로 인식되는 유일한 크기이며, 보정이 내장되어 있어 따로 할 일은 없습니다.",
        "de": "Die einzige Größe, die derzeit zuverlässig gelesen wird — die Kalibrierung ist "
              "bereits enthalten, du musst nichts tun."},
    "The game does not offer that size itself. Two steps get you there:": {
        "zh_tw": "遊戲本身沒有這個尺寸，需要兩個步驟：",
        "zh_cn": "游戏本身没有这个尺寸，需要两个步骤：",
        "ja": "ゲーム自体にこのサイズはありません。次の2ステップで設定します：",
        "ko": "게임 자체에는 이 크기가 없습니다. 두 단계로 설정합니다:",
        "de": "Das Spiel bietet diese Größe nicht selbst an. Zwei Schritte dorthin:"},
    "In the game: Options \u2192 turn Fullscreen OFF, and close the panel to apply.": {
        "zh_tw": "在遊戲中：設定 \u2192 將「全螢幕」關閉，然後關閉設定視窗套用。",
        "zh_cn": "在游戏中：设置 \u2192 将「全屏」关闭，然后关闭设置窗口应用。",
        "ja": "ゲーム内：設定 \u2192「フルスクリーン」をOFFにし、閉じて適用します。",
        "ko": "게임에서: 설정 \u2192 «전체 화면»을 끄고 창을 닫아 적용합니다.",
        "de": "Im Spiel: Optionen \u2192 Vollbild AUS, dann das Fenster schlie\u00dfen, um es zu \u00fcbernehmen."},
    "Then set the window to 704 \u00d7 1241 with a window-sizing tool. Players use {tool}, a "
    "small free utility written by NowvaB \u2014 it resizes the game window and nothing else. "
    "It is not ours and not bundled here; it is linked so you can see who wrote it and "
    "decide for yourself.": {
        "zh_tw": "接著用視窗調整工具把視窗設成 704 \u00d7 1241。玩家常用的是 {tool}，由 NowvaB 撰寫的"
                 "免費小工具——它只調整遊戲視窗大小，不會動到別的東西。這不是我們寫的，也沒有隨附在"
                 "這裡；附上連結是讓你看到作者是誰，自行決定要不要使用。",
        "zh_cn": "接着用窗口调整工具把窗口设成 704 \u00d7 1241。玩家常用的是 {tool}，由 NowvaB 编写的"
                 "免费小工具——它只调整游戏窗口大小，不会动到别的东西。这不是我们写的，也没有随附在"
                 "这里；附上链接是让你看到作者是谁，自行决定要不要使用。",
        "ja": "次に、ウィンドウサイズ変更ツールで 704 \u00d7 1241 にします。よく使われているのは "
              "{tool}（NowvaB 作の小さな無料ツール）で、ゲームウィンドウの大きさを変えるだけです。"
              "私たちが作ったものではなく、同梱もしていません。作者が誰かを見て、ご自身で判断できる"
              "ようリンクだけ載せています。",
        "ko": "그다음 창 크기 조절 도구로 704 \u00d7 1241 로 맞춥니다. 플레이어들이 쓰는 것은 "
              "{tool}(NowvaB 이 만든 작은 무료 유틸리티)로, 게임 창 크기만 바꿉니다. 저희가 만든 것도, "
              "여기에 포함한 것도 아닙니다. 누가 만들었는지 직접 보고 판단하실 수 있도록 링크만 "
              "적어 둡니다.",
        "de": "Setze das Fenster dann mit einem Fenster-Tool auf 704 \u00d7 1241. Verbreitet ist "
              "{tool}, ein kleines kostenloses Programm von NowvaB \u2014 es \u00e4ndert nur die "
              "Fenstergr\u00f6\u00dfe des Spiels. Es ist nicht von uns und nicht beigelegt; der Link "
              "steht hier, damit du siehst, wer es geschrieben hat, und selbst entscheidest."},
    "Other sizes, full screen included, are not recommended yet: they sample the screen "
    "more slowly and some item names are still misread. The client will let you calibrate "
    "and record at one, but expect gaps. More sizes are planned — if you play at a "
    "different one, please say so, because a short recording is what makes it fixable.": {
        "zh_tw": "其他尺寸（包含全螢幕）目前不建議使用：擷取速度較慢，部分道具名稱仍會辨識錯誤。"
                 "你仍然可以校正並記錄，但會有遺漏。未來會支援更多尺寸——如果你用的是其他尺寸，"
                 "請告訴我們，一小段錄影就能讓它被修好。",
        "zh_cn": "其他尺寸（包含全屏）目前不建议使用：采集速度较慢，部分道具名称仍会识别错误。"
                 "你仍然可以校正并记录，但会有遗漏。未来会支持更多尺寸——如果你用的是其他尺寸，"
                 "请告诉我们，一小段录像就能让它被修好。",
        "ja": "他のサイズ（全画面を含む）は現時点では推奨しません。取得が遅く、一部のアイテム名を"
              "誤読します。校正して記録することはできますが、取りこぼしが出ます。対応サイズは今後"
              "増やす予定です——別のサイズで遊んでいる場合はぜひ教えてください。短い録画があれば"
              "直せます。",
        "ko": "다른 크기(전체 화면 포함)는 아직 권장하지 않습니다. 화면 샘플링이 느리고 일부 아이템 "
              "이름을 잘못 읽습니다. 보정하고 기록할 수는 있지만 누락이 생깁니다. 지원 크기는 "
              "늘려갈 예정입니다 — 다른 크기로 플레이한다면 알려 주세요. 짧은 녹화만 있으면 고칠 수 "
              "있습니다.",
        "de": "Andere Größen, auch Vollbild, sind noch nicht empfohlen: Sie tasten den Bildschirm "
              "langsamer ab und einige Gegenstandsnamen werden falsch gelesen. Kalibrieren und "
              "Aufzeichnen ist möglich, aber mit Lücken. Weitere Größen sind geplant — sag "
              "Bescheid, wenn du in einer anderen spielst: eine kurze Aufnahme macht es "
              "reparierbar."},
    "ready for {sizes} — the calibration that came with the client": {
        "zh_tw": "已內建 {sizes} 的校正，可直接開始",
        "zh_cn": "已内置 {sizes} 的校正，可直接开始",
        "ja": "{sizes} の校正は内蔵済み。そのまま開始できます",
        "ko": "{sizes} 보정이 내장되어 있어 바로 시작할 수 있습니다",
        "de": "Kalibrierung für {sizes} ist enthalten — du kannst sofort starten"},
    "These terms have changed": {
        "zh_tw": "條款已更新", "zh_cn": "条款已更新", "ja": "規約が変更されました",
        "ko": "약관이 변경되었습니다", "de": "Diese Bedingungen haben sich geändert"},
    "You agreed to an earlier version. Nothing you have already recorded is affected — "
    "read this one and answer again, including whether you share.": {
        "zh_tw": "你先前同意的是舊版本。已經記錄的資料完全不受影響——請閱讀這一版並重新回答，"
                 "包含是否分享。",
        "zh_cn": "你先前同意的是旧版本。已经记录的数据完全不受影响——请阅读这一版并重新回答，"
                 "包含是否分享。",
        "ja": "以前のバージョンに同意されています。記録済みのデータには一切影響しません——"
              "こちらを読んで、共有するかどうかも含めて改めてお答えください。",
        "ko": "이전 버전에 동의하셨습니다. 이미 기록된 데이터에는 전혀 영향이 없습니다 — "
              "이 내용을 읽고 공유 여부를 포함해 다시 답해 주세요.",
        "de": "Du hast einer früheren Fassung zugestimmt. An deinen bereits aufgezeichneten "
              "Daten ändert sich nichts — lies diese und antworte erneut, auch zum Teilen."},
    "Chests and veins": {
        "zh_tw": "寶箱與採掘點", "zh_cn": "宝箱与采掘点", "ja": "宝箱と採掘ポイント",
        "ko": "상자와 채굴 포인트", "de": "Truhen und Abbaustellen"},
    "Chests": {"zh_tw": "寶箱", "zh_cn": "宝箱", "ja": "宝箱", "ko": "상자", "de": "Truhen"},
    "Veins": {"zh_tw": "採掘點", "zh_cn": "采掘点", "ja": "採掘ポイント", "ko": "채굴 포인트",
              "de": "Abbaustellen"},
    "share": {"zh_tw": "佔比", "zh_cn": "占比", "ja": "割合", "ko": "비율", "de": "Anteil"},
    # The second scale. "times" heads the column; the two "By …" lines are what the picker
    # offers, and they are phrased as the question being asked rather than as a field name.
    "times": {"zh_tw": "次數", "zh_cn": "次数", "ja": "回数", "ko": "횟수",
              "de": "Male"},
    "By amount": {"zh_tw": "依數量", "zh_cn": "依数量", "ja": "個数で見る",
                  "ko": "수량 기준", "de": "Nach Menge"},
    "By times": {"zh_tw": "依次數", "zh_cn": "依次数", "ja": "回数で見る",
                 "ko": "횟수 기준", "de": "Nach Häufigkeit"},
    "×{n} in total": {
        "zh_tw": "總共 ×{n}", "zh_cn": "总共 ×{n}", "ja": "合計 ×{n}",
        "ko": "합계 ×{n}", "de": "insgesamt ×{n}"},
    "The column added up. One opening usually gives several kinds, so it counts "
    "once under each of them — this is larger than the number of openings above.": {
        "zh_tw": "這欄的總和。一次開啟通常會給好幾種東西，每一種都各算一次，所以這個數字會大於上面的開啟次數。",
        "zh_cn": "这栏的总和。一次开启通常会给好几种东西，每一种都各算一次，所以这个数字会大于上面的开启次数。",
        "ja": "この列の合計です。1 回の開封でふつう数種類が出るため、その 1 回は種類ごとに数えられます。上の開封回数より大きくなります。",
        "ko": "이 열의 합계입니다. 한 번 열면 보통 여러 종류가 나오므로 그 한 번이 종류마다 세어집니다. 위의 개봉 횟수보다 큽니다.",
        "de": "Die Spalte aufsummiert. Eine Öffnung gibt meist mehrere Arten und wird "
    "daher unter jeder einzeln gezählt — das liegt über der Zahl der Öffnungen oben."},
    "total of {n} kinds": {
        "zh_tw": "合計（{n} 種）", "zh_cn": "合计（{n} 种）", "ja": "合計（{n} 種類）",
        "ko": "합계({n}종)", "de": "Gesamt ({n} Arten)"},
    "{n} openings gave this": {
        "zh_tw": "來自 {n} 次開啟", "zh_cn": "来自 {n} 次开启", "ja": "{n} 回から",
        "ko": "{n}회에서", "de": "aus {n} Öffnungen"},
    "pickaxes broken": {
        "zh_tw": "十字鎬損壞", "zh_cn": "十字镐损坏", "ja": "ツルハシ破損",
        "ko": "곡괭이 파손", "de": "Spitzhacken zerbrochen"},
    "All days": {"zh_tw": "全部日期", "zh_cn": "全部日期", "ja": "すべての日", "ko": "전체 기간",
                 "de": "Alle Tage"},
    # The day divider is the GAME's, not the computer's, so the page says so rather than
    # leaving a player to wonder why an evening session is filed under tomorrow.
    "days reset at 00:00 JST, as the game does": {
        "zh_tw": "日期以日本時間 00:00 為界，與遊戲一致",
        "zh_cn": "日期以日本时间 00:00 为界，与游戏一致",
        "ja": "日付は日本時間 0:00 区切り（ゲームと同じ）",
        "ko": "날짜는 일본 시간 00:00 기준이며, 게임과 같습니다",
        "de": "Tage beginnen um 00:00 JST, wie im Spiel"},
    "all time: {openings} openings · {lines} item lines · {days} days": {
        "zh_tw": "全部：{openings} 次開啟 · {lines} 條道具 · {days} 天",
        "zh_cn": "全部：{openings} 次开启 · {lines} 条道具 · {days} 天",
        "ja": "全期間：{openings} 回 · {lines} 行 · {days} 日",
        "ko": "전체: {openings}회 · {lines}줄 · {days}일",
        "de": "Insgesamt: {openings} Öffnungen · {lines} Zeilen · {days} Tage"},
    "Your data": {"zh_tw": "你的資料", "zh_cn": "你的数据", "ja": "あなたのデータ",
                  "ko": "내 데이터", "de": "Deine Daten"},
    "Everything is kept in one folder on this computer. {settings} shows where, and "
    "opens it for you.": {
        "zh_tw": "所有東西都保存在這台電腦的一個資料夾裡。{settings}會顯示位置，也可以直接幫你開啟。",
        "zh_cn": "所有东西都保存在这台电脑的一个文件夹里。{settings}会显示位置，也可以直接帮你打开。",
        "ja": "すべてこのパソコンの 1 つのフォルダーに保存されます。{settings}に場所が表示され、"
              "そこから開けます。",
        "ko": "모든 것은 이 컴퓨터의 폴더 하나에 저장됩니다. {settings}에서 위치를 보여주고 "
              "바로 열 수도 있습니다.",
        "de": "Alles liegt in einem Ordner auf diesem Rechner. {settings} zeigt wo — und "
              "öffnet ihn für dich."},
    "Open folder": {"zh_tw": "開啟資料夾", "zh_cn": "打开文件夹", "ja": "フォルダーを開く",
                    "ko": "폴더 열기", "de": "Ordner öffnen"},
    "Everything this client keeps is in that one folder. Deleting it removes all of it "
    "from this computer; nothing is kept anywhere else.": {
        "zh_tw": "這個程式保存的所有東西都在那一個資料夾裡。刪掉它就等於從這台電腦移除全部，"
                 "其他地方不會留下任何資料。",
        "zh_cn": "这个程序保存的所有东西都在那一个文件夹里。删掉它就等于从这台电脑移除全部，"
                 "其他地方不会留下任何数据。",
        "ja": "このクライアントが保存するものはすべてそのフォルダーの中にあります。"
              "削除すればこのパソコンからすべて消えます。ほかの場所には何も残りません。",
        "ko": "이 클라이언트가 보관하는 모든 것은 그 폴더 하나에 있습니다. 삭제하면 이 "
              "컴퓨터에서 전부 사라지며, 다른 곳에는 아무것도 남지 않습니다.",
        "de": "Alles, was dieser Client speichert, liegt in diesem einen Ordner. Ihn zu "
              "löschen entfernt alles von diesem Rechner; anderswo bleibt nichts."},
    "Counted from what was recorded on this computer, not from what was sent. "
    "These are counts, not drop rates.": {
        "zh_tw": "統計自這台電腦上記錄的內容，而非已送出的內容。這些是次數，不是掉落率。",
        "zh_cn": "统计自这台电脑上记录的内容，而非已送出的内容。这些是次数，不是掉落率。",
        "ja": "このパソコンに記録された内容から数えています（送信済みの分ではありません）。"
              "これは回数であり、ドロップ率ではありません。",
        "ko": "이 컴퓨터에 기록된 내용을 센 것이며, 전송된 내용이 아닙니다. "
              "드롭률이 아니라 횟수입니다.",
        "de": "Gezählt aus dem, was auf diesem Rechner aufgezeichnet wurde, nicht aus dem "
              "Gesendeten. Das sind Anzahlen, keine Dropraten."},

    # -- calibration
    # The first thing a new player is sent to and the one step nobody can skip, so an
    # untranslated dialog here is the page that explains what to photograph being the page
    # they cannot read.
    # KEEP WALKING is an instruction, not flavour: the shots are compared with each other to
    # find the part of the minimap panel that does not change, and standing still makes the
    # map interior — the one part that must never be matched — look just as steady as the
    # buttons under it.
    "Step 1 of 2 — walk around a dungeon with the minimap visible, then press Capture.\n"
    "You will have a few seconds to switch back — keep walking while it takes the shots.": {
        "zh_tw": "步驟 1／2 — 在地城中走動並讓小地圖可見，然後按「擷取」。\n"
                 "你會有幾秒鐘切換回遊戲 — 拍攝期間請持續走動。",
        "zh_cn": "步骤 1／2 — 在地下城中走动并让小地图可见，然后按“截取”。\n"
                 "你会有几秒钟切换回游戏 — 拍摄期间请持续走动。",
        "ja": "ステップ 1／2 — ミニマップが見える状態でダンジョンを歩き、「撮影」を押します。\n"
              "ゲームに戻るまで数秒あります — 撮影中は歩き続けてください。",
        "ko": "1／2 단계 — 미니맵이 보이는 상태로 던전을 걸어 다니며 「촬영」을 누르세요.\n"
              "게임으로 돌아갈 시간이 몇 초 있습니다 — 촬영하는 동안 계속 걸어 주세요.",
        "de": "Schritt 1 von 2 — lauf mit sichtbarer Minikarte durch einen Dungeon und drücke "
              "Aufnehmen.\nDu hast ein paar Sekunden zum Zurückwechseln — lauf weiter, "
              "während die Aufnahmen entstehen."},
    "Step 2 of 2 — open a chest and leave the 「…を手に入れた!!」 message on screen, then "
    "press Capture.": {
        "zh_tw": "步驟 2／2 — 打開寶箱，讓「…を手に入れた!!」訊息留在畫面上，然後按「擷取」。",
        "zh_cn": "步骤 2／2 — 打开宝箱，让“获得了…”消息留在画面上，然后按“截取”。",
        "ja": "ステップ 2／2 — 宝箱を開け、「…を手に入れた!!」のメッセージを画面に残したまま"
              "「撮影」を押します。",
        "ko": "2／2 단계 — 상자를 열고 「…を手に入れた!!」 메시지를 화면에 둔 채 「촬영」을 누르세요.",
        "de": "Schritt 2 von 2 — öffne eine Truhe, lass die 「…を手に入れた!!」-Meldung stehen und "
              "drücke Aufnehmen."},
    "the item name in that message — calibration's answer key": {
        "zh_tw": "訊息中的道具名稱 — 校正的答案", "zh_cn": "消息中的道具名称 — 校正的答案",
        "ja": "そのメッセージのアイテム名 — 較正の答え",
        "ko": "그 메시지의 아이템 이름 — 보정의 정답",
        "de": "der Gegenstandsname in der Meldung — der Prüfschlüssel der Kalibrierung"},
    "Skip this shot": {
        "zh_tw": "略過這張", "zh_cn": "跳过这张", "ja": "この撮影を飛ばす",
        "ko": "이 촬영 건너뛰기", "de": "Diese Aufnahme überspringen"},
    "Capture": {"zh_tw": "擷取", "zh_cn": "截取", "ja": "撮影", "ko": "촬영",
                "de": "Aufnehmen"},
    "switching back to the game… {n}": {
        "zh_tw": "切換回遊戲中… {n}", "zh_cn": "切换回游戏中… {n}",
        "ja": "ゲームに戻ります… {n}", "ko": "게임으로 돌아갑니다… {n}",
        "de": "zurück ins Spiel… {n}"},
    "No HUD template will be made — chest bracketing will be poor.": {
        "zh_tw": "不會建立 HUD 樣板 — 寶箱的起訖判定會變差。",
        "zh_cn": "不会建立 HUD 模板 — 宝箱的起讫判定会变差。",
        "ja": "HUD テンプレートは作られません — 宝箱の区切り判定が甘くなります。",
        "ko": "HUD 템플릿이 만들어지지 않습니다 — 상자 구간 판정이 나빠집니다.",
        "de": "Es wird keine HUD-Vorlage erstellt — Truhen werden schlechter abgegrenzt."},
    "Which item does that message name? Type it exactly.": {
        "zh_tw": "那則訊息寫的是哪個道具？請完全照著輸入。",
        "zh_cn": "那条消息写的是哪个道具？请完全照着输入。",
        "ja": "そのメッセージのアイテム名は？ そのとおりに入力してください。",
        "ko": "그 메시지의 아이템은 무엇입니까? 정확히 그대로 입력하세요.",
        "de": "Welchen Gegenstand nennt die Meldung? Genau so eintippen."},
    "Fit": {"zh_tw": "擬合", "zh_cn": "拟合", "ja": "フィット", "ko": "맞추기",
            "de": "Anpassen"},
    "reading the item name…": {
        "zh_tw": "正在讀取道具名稱…", "zh_cn": "正在读取道具名称…",
        "ja": "アイテム名を読み取っています…", "ko": "아이템 이름을 읽는 중…",
        "de": "Gegenstandsname wird gelesen…"},
    "Could not read it — please type it.": {
        "zh_tw": "讀不出來 — 請手動輸入。", "zh_cn": "读不出来 — 请手动输入。",
        "ja": "読み取れませんでした — 手で入力してください。",
        "ko": "읽지 못했습니다 — 직접 입력해 주세요.",
        "de": "Konnte es nicht lesen — bitte eintippen."},
    "Is this the item in the message? Correct it if not.": {
        "zh_tw": "訊息裡的道具是這個嗎？不對的話請修正。",
        "zh_cn": "消息里的道具是这个吗？不对的话请修正。",
        "ja": "メッセージのアイテムはこれで合っていますか？違えば直してください。",
        "ko": "메시지의 아이템이 이것이 맞습니까? 아니면 고쳐 주세요.",
        "de": "Ist das der Gegenstand aus der Meldung? Wenn nicht, korrigiere ihn."},
    "read from your screenshot (margin {margin})": {
        "zh_tw": "由你的截圖讀出（差距 {margin}）", "zh_cn": "由你的截图读出（差距 {margin}）",
        "ja": "スクリーンショットから読み取り（差 {margin}）",
        "ko": "스크린샷에서 읽음 (차이 {margin})",
        "de": "aus deinem Screenshot gelesen (Abstand {margin})"},
    "The item name is calibration's answer key; it cannot be blank.": {
        "zh_tw": "道具名稱是校正的答案，不能空白。",
        "zh_cn": "道具名称是校正的答案，不能空白。",
        "ja": "アイテム名は較正の答えです。空にはできません。",
        "ko": "아이템 이름은 보정의 정답이므로 비울 수 없습니다.",
        "de": "Der Gegenstandsname ist der Prüfschlüssel und darf nicht leer sein."},
    "fitting…": {"zh_tw": "擬合中…", "zh_cn": "拟合中…", "ja": "フィット中…",
                 "ko": "맞추는 중…", "de": "wird angepasst…"},
    "self-check read back:": {
        "zh_tw": "自我檢查讀回：", "zh_cn": "自我检查读回：", "ja": "セルフチェックの読み取り：",
        "ko": "자체 점검 결과:", "de": "Selbsttest las zurück:"},
    "margin": {"zh_tw": "差距", "zh_cn": "差距", "ja": "差", "ko": "차이",
               "de": "Abstand"},
    "No HUD template — chest bracketing will be poor.": {
        "zh_tw": "沒有 HUD 樣板 — 寶箱的起訖判定會變差。",
        "zh_cn": "没有 HUD 模板 — 宝箱的起讫判定会变差。",
        "ja": "HUD テンプレートなし — 宝箱の区切り判定が甘くなります。",
        "ko": "HUD 템플릿 없음 — 상자 구간 판정이 나빠집니다.",
        "de": "Keine HUD-Vorlage — Truhen werden schlechter abgegrenzt."},
    "Done": {"zh_tw": "完成", "zh_cn": "完成", "ja": "完了", "ko": "완료", "de": "Fertig"},
}


class Translator:
    """`t("Start recording")` — English in, the player's language out."""

    def __init__(self, locale_name: str | None = None):
        self.locale = locale_name if locale_name in LOCALES else system_locale()

    def __call__(self, text: str, **kwargs) -> str:
        out = STRINGS.get(text, {}).get(self.locale, text)
        return out.format(**kwargs) if kwargs else out

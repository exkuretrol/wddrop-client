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
    "chest": {"zh_tw": "寶箱", "zh_cn": "宝箱", "ja": "宝箱", "ko": "보물상자", "de": "Truhe"},
    "vein": {"zh_tw": "礦脈", "zh_cn": "矿脉", "ja": "鉱脈", "ko": "광맥", "de": "Erzader"},
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
    "The minimap has not been seen. Stop and calibrate again.": {
        "zh_tw": "一直沒有看到小地圖。請停止記錄並重新校正。",
        "zh_cn": "一直没有看到小地图。请停止记录并重新校正。",
        "ja": "ミニマップが一度も検出されていません。記録を止めて再キャリブレーションしてください。",
        "ko": "미니맵이 한 번도 인식되지 않았습니다. 기록을 멈추고 다시 보정하세요.",
        "de": "Die Minikarte wurde nie erkannt. Stoppe und kalibriere neu."},
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
    "Calibrate…": {"zh_tw": "校正…", "zh_cn": "校正…", "ja": "キャリブレーション…",
                   "ko": "보정…", "de": "Kalibrieren…"},
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
    "Open a chest and leave the 「獲得了…」 message on screen, then type the item name.": {
        "zh_tw": "打開寶箱，讓「獲得了…」訊息留在畫面上，然後輸入道具名稱。",
        "zh_cn": "打开宝箱，让“获得了…”消息留在画面上，然后输入道具名称。",
        "ja": "宝箱を開けて「獲得了…」のメッセージを画面に残し、アイテム名を入力します。",
        "ko": "상자를 열어 「獲得了…」 메시지를 화면에 둔 채 아이템 이름을 입력합니다.",
        "de": "Öffne eine Truhe, lass die 「獲得了…」-Meldung stehen und tippe den "
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
    "Veins: wait for the ▼.": {
        "zh_tw": "礦脈：等 ▼ 出現。", "zh_cn": "矿脉：等 ▼ 出现。", "ja": "鉱脈：▼ を待つ。",
        "ko": "광맥: ▼ 를 기다리세요.", "de": "Adern: warte auf das ▼."},
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
    "Play in the tall window": {
        "zh_tw": "請使用直式視窗", "zh_cn": "请使用竖版窗口", "ja": "縦長ウィンドウで遊ぶ",
        "ko": "세로 창으로 플레이하세요", "de": "Im hohen Fenster spielen"},
    "this is the only size that reads reliably today, and the client already has a "
    "calibration for it. You do not have to do anything.": {
        "zh_tw": "這是目前唯一能穩定辨識的尺寸，程式已內建校正，你不需要做任何設定。",
        "zh_cn": "这是目前唯一能稳定识别的尺寸，程序已内置校正，你不需要做任何设置。",
        "ja": "現在確実に読み取れるのはこのサイズだけです。校正は内蔵済みで、設定は不要です。",
        "ko": "현재 안정적으로 인식되는 유일한 크기이며, 보정이 내장되어 있어 따로 할 일은 없습니다.",
        "de": "Die einzige Größe, die derzeit zuverlässig gelesen wird — die Kalibrierung ist "
              "bereits enthalten, du musst nichts tun."},
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
    "Step 1 of 2 — stand in a dungeon with the minimap visible, then press Capture.\n"
    "You will have a few seconds to switch back to the game.": {
        "zh_tw": "步驟 1／2 — 站在地城中並讓小地圖可見，然後按「擷取」。\n"
                 "你會有幾秒鐘切換回遊戲。",
        "zh_cn": "步骤 1／2 — 站在地下城中并让小地图可见，然后按“截取”。\n"
                 "你会有几秒钟切换回游戏。",
        "ja": "ステップ 1／2 — ミニマップが見える状態でダンジョンに立ち、「撮影」を押します。\n"
              "ゲームに戻るまで数秒あります。",
        "ko": "1／2 단계 — 미니맵이 보이는 상태로 던전에 서서 「촬영」을 누르세요.\n"
              "게임으로 돌아갈 시간이 몇 초 있습니다.",
        "de": "Schritt 1 von 2 — stell dich mit sichtbarer Minikarte in einen Dungeon und "
              "drücke Aufnehmen.\nDu hast ein paar Sekunden, um ins Spiel zurückzuwechseln."},
    "Step 2 of 2 — open a chest and leave the 「獲得了…」 message on screen, then "
    "press Capture.": {
        "zh_tw": "步驟 2／2 — 打開寶箱，讓「獲得了…」訊息留在畫面上，然後按「擷取」。",
        "zh_cn": "步骤 2／2 — 打开宝箱，让“获得了…”消息留在画面上，然后按“截取”。",
        "ja": "ステップ 2／2 — 宝箱を開け、「獲得了…」のメッセージを画面に残したまま"
              "「撮影」を押します。",
        "ko": "2／2 단계 — 상자를 열고 「獲得了…」 메시지를 화면에 둔 채 「촬영」을 누르세요.",
        "de": "Schritt 2 von 2 — öffne eine Truhe, lass die 「獲得了…」-Meldung stehen und "
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

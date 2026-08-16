# 免責聲明 / Disclaimer

**使用本工具前請完整閱讀。客戶端在首次啟動時會要求明確同意，未同意不會記錄或送出任何資料。**

---

## 繁體中文

### 1. 非官方工具
本工具為玩家自製的第三方統計工具，與 Wizardry Variants Daphne 的開發／營運商（Drecom、Nexon 及其關係企業）**沒有任何隸屬、合作或背書關係**。所有遊戲名稱、商標、資料之著作權均屬各自權利人所有。

### 2. 這個程式做什麼、不做什麼
本工具**只讀取你自己電腦的螢幕畫面**。它會定時擷取遊戲視窗，辨識畫面上出現的掉落訊息，然後把辨識結果寫到你電腦上的檔案。

**不會做的事**
- 不修改、不注入、不修補遊戲程式
- 不讀取遊戲的記憶體
- 不攔截、不解密、不改動任何網路連線
- 不模擬鍵盤或滑鼠、不自動操作遊戲

程式不會替你按任何一個按鍵；所有遊戲操作都由你自己完成。

### 3. 使用風險自負（重要）
使用任何第三方工具都**可能違反遊戲服務條款**，並可能導致帳號被警告、停權或永久封鎖。本工具只讀畫面、不碰遊戲程式與連線，風險相對較低，但**不等於零風險**。

作者不對任何帳號處分、資料遺失或其他損害負責。**是否使用由使用者自行判斷並承擔全部後果。**

### 4. 蒐集哪些資料
只蒐集與掉落統計直接相關的最小資料：

**會蒐集**
- 道具名稱與數量
- 取得來源（寶箱／採礦／雜物逆轉）
- 若掉落的是裝備：裝備名稱（**不含品質與等級**，程式不讀這兩項）
- 單次下潛的經過時間、該次下潛已開啟的寶箱序號、下潛結束的原因
- 迷宮／樓層識別碼
- 事件發生時間（UTC）與時區位移
- 匿名安裝識別碼（本機隨機產生的 UUID）、客戶端版本、遊戲語系、辨識模式
- 辨識品質訊號，用於判斷哪些紀錄可信
- **你自己填的劇情進度與主角等級**：你在設定裡勾選的結局，以及你通過的最高等級昇格試驗。
  這兩項會影響遊戲本身的難度與獎勵，所以必須跟著每一筆紀錄一起送出——不然就無法分辨
  「掉落變差」和「兩個人的遊戲狀態本來就不同」。不填也可以，程式只會送出你填過的部分

**不會蒐集**
- 帳號、密碼、登入憑證、Session 金鑰
- 玩家名稱、玩家代碼、好友、公會、聊天內容
- 角色資料、持有金錢、持有道具、課金紀錄
- 螢幕截圖
- 十字鎬數量（這是你自己的庫存，只留在本機）
- 任何可直接識別個人身分的資訊

### 4b. 唯一一個不是送給我們的連線
視窗開啟時，程式會向 GitHub 查詢一次是否有新版本，讓一個已知會讀錯畫面的版本能夠告訴你該換掉它。你也可以在「設定 → 新版本 → 立即檢查」自己問一次；除此之外程式不會再送出這個請求。這個請求不會夾帶任何關於你、你的記錄或你遊戲的資料，只有任何網路請求都會有的 IP 位址與這個程式的版本。在「設定 → 新版本」關掉它，這個請求就完全不會送出，「立即檢查」也會一併停用。

### 5. 分享是選擇性的
**記錄與分享是兩件事。** 不論你是否選擇分享，所有紀錄都會存在你自己的電腦上；分享只決定是否「另外送一份」到統計伺服器。此選項預設關閉，第一次啟動時會明確詢問，之後也可以隨時在「設定」中改變。

### 6. 螢幕錄影只留在本機
若你開啟「保留畫面」，程式會把擷取到的畫面存成 PNG 檔放在你電腦的資料夾裡，用於事後重新辨識與修正錯誤。**這些畫面永遠不會被上傳**，你可以隨時自行刪除。

### 7. 匿名化
客戶端只送出本機隨機產生的 `install_id`。伺服器會以僅存在於伺服器端的密鑰對其做 HMAC，轉為 `player_id` 後才寫入資料庫；原始 `install_id` 不會被保存。此識別碼**無法**反推回遊戲帳號。

重新安裝會產生新的識別碼，因此同一玩家可能被計為多位玩家 —— 這是為了保護隱私而刻意接受的統計代價。

### 8. 資料用途
彙整後的統計結果將以匿名、聚合形式公開，供玩家社群參考。**不會**販售資料，**不會**提供給第三方廣告商。

### 9. 刪除權
你可以隨時要求刪除自己的資料。客戶端會顯示你的 `install_id`，提出該識別碼即可要求移除對應的所有紀錄。**提出後，這些紀錄會立刻從所有統計中消失，並在 7 天內從伺服器完全清除**——保留這幾天，是為了讓被誤刪或被他人冒用識別碼刪掉的紀錄還能救得回來。刪除本機的資料夾則會移除這台電腦上的全部內容。

`install_id` 就是唯一的憑證：伺服器從來沒有保存過它，所以**任何人只要知道你的識別碼，就能要求刪掉你的紀錄**。請把它當成密碼看待，只在要求刪除時提供給我們。

### 10. 辨識會出錯
掉落訊息是從畫面上「看」出來的，因此有可能讀錯或漏讀。程式在無法確定數量時會標記為不確定，而不是猜一個數字，但仍不保證完全正確。**你自己的紀錄請以遊戲內為準。**

### 11. 統計結果不是事實認定
本專案的目的是**檢驗**「刷越久掉落是否變差」這個假設，而不是預設它成立。統計結果永遠帶有不確定性，樣本偏誤、玩家行為差異、遊戲改版都可能影響結論。**任何分析結果都不應被當作對營運商的指控。**

### 12. 授權與稽核
本工具為開源專案。任何人都可以檢視原始碼，確認上述蒐集範圍屬實。若發現與本聲明不符之處，請提出 issue。

---

## English (summary)

This is an **unofficial, fan-made** statistics tool with **no affiliation to or endorsement by** the developers or operators of Wizardry Variants Daphne.

**What it does:** it reads your own screen. It samples the game window, recognises the drop messages shown on it, and writes what it read to a file on your computer.

**What it does not do:** it does not modify, inject into or patch the game; it does not read the game's memory; it does not intercept, decrypt or alter any network traffic; and it does not press keys or move your mouse. Every action in the game is still yours.

**Use at your own risk.** Third-party tools may violate the game's Terms of Service and could result in account suspension or a permanent ban. Reading the screen is lower risk than touching the game or its traffic, but it is **not zero risk**. The authors accept no liability for bans, data loss, or any other damages.

**Collected:** item name and quantity, acquisition source (chest, mining, junk reversal), the equipment's name when the drop is equipment — **not its quality or level, which the client does not read** — dive elapsed time, chest index within the dive, how the session ended, dungeon and floor ids, UTC timestamp and timezone offset, a random anonymous install id, client version, locale, capture mode, recognition-quality signals, and **the story progress and character grade you enter yourself** — the endings you tick in Settings, and the highest promotion exam you have passed. Those two travel with every record because the game's own difficulty and rewards move with them, and without them "the drops got worse" cannot be told apart from "these two players were not playing the same game". Leaving them unanswered is fine; only what you have answered is sent.

**Never collected:** credentials, session keys, player name or code, friends, chat, character data, currency, inventory, purchase history, screenshots, your pickaxe count, or any personally identifying information.

**One request that is not to us.** When the window opens, the client asks GitHub whether a newer version has been released, so that a build known to read the screen wrongly can tell you to replace it. You can also ask yourself, with *Settings → New versions → Check now*; it is made at no other time. That request carries nothing about you, your records or your game — only what any web request carries, an IP address and the client's version. Turn it off in *Settings → New versions* and it is not made at all — the button is switched off with it.

**Sharing is separate from recording, and is off until you turn it on.** Everything is recorded on your own computer either way; sharing only decides whether a copy is also sent. If you turn on *Keep the frames*, the captured images are stored in a folder on your computer so a mistake can be re-read and fixed later — **they are never uploaded**.

The random install id is HMAC'd server-side with a server-only secret before storage and **cannot** be traced back to a game account. You may request deletion of your data at any time by quoting your install id: the records leave every statistic immediately and are wiped from the server within 7 days — those few days exist so that a deletion nobody meant can still be undone. Deleting the client's folder removes everything held on this computer.

Because the install id is the only credential that exists, **anyone who knows it can have your records deleted**. Treat it as a password: the window shows only the ends of it, and copies the whole thing to your clipboard when you need it.

**Recognition can be wrong.** Drops are read off the screen, so a line can be misread or missed. Where the client cannot be sure of a quantity it marks it unknown rather than guessing — but treat the game itself as the authority on your own inventory.

This project **tests** the hypothesis that drop quality degrades with farming time; it does not assume it. Statistical results carry uncertainty and **should not be treated as an accusation** against the operator.

The tool is open source and auditable.

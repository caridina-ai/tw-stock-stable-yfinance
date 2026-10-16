# sidecar branch — 合作規則、記憶與回報

這個 **orphan branch `sidecar`**，與 `main` 沒有共同歷史，裡面沒有程式碼，可能出現的內容為：

| 路徑 | 誰維護 | 內容 |
|---|---|---|
| `README.md` | 使用者 | 本檔，關於 sidecar 的規定 |
| `memory/` | Claude | 分層記憶，入口 `memory/MEMORY.md` |
| `FIATLUX.md` 或 `FIAT-{feature}.md` | 使用者 | 這個 session 要完成的新需求 |
| `CLAUDE.md` | Claude | 必讀交接事項 |
| `CALLBACK.md` | Claude 寫、使用者清除 | 需要使用者（本尊）注意或親手處理的事 |

**永遠不要把 `sidecar` merge 進 `main`**，也不要反過來。

## 開工

載入執行 CLAUDE.md，此檔案多半為上個 session 交接給下個 session 要求做到的事情

載入 memory/MEMORY.md 以了解本 repo 有哪些相關的記憶，用到時才載入細節

載入執行 FIATLUX.md 或 FIAT-{feature}.md，此為使用者要求完成的事項

## 收工

### 複製需求檔

完成需求後

若為 ..\sidecar\FIATLUX.md 則直接複製到 genesis\FIATLUX.md 即可

若為 ..\sidecar\FIAT-{feature}.md 則

1. 複製到 genesis\FIAT-{feature}.md

2. 建立 genesis\README.md 作為索引，依時間順序建立 markdown 連結到檔案，以檔案的第一行標題為連結文字，例如 [sidecar branch — 合作規則、記憶與回報](FIAT-sidecar.md)

用複製的，不要 merge sidecar branch

例外：若需求其實早已滿足，或無法完成，則不要做上述複製 FIAT 檔的動作

若同時發現多個 FIAT-{feature}.md 可自行決定順序，依序完成

### commit 並 push 程式碼

在 `main` commit，**直接 push `main`**，不開 branch、不開 PR

### 善用 ..\sidecar\CLAUDE.md 交接下個 session 啟動後行為

由於本 README.md 要求開工時載入執行 CLAUDE.md，因此可在其中提示啟動後執行事項

例如可撰寫一個 setup.py 用來準備資料、設定環境等，並在 CLAUDE.md 中要求開工前先執行

### 寫入專案記憶作為交接

學到值得留給下個 session 的事，就更新到 `..\sidecar\memory\` 中（規則見 `memory/MEMORY.md`）

### 如有回報事項則建立 CALLBACK.md

碰到自己解決不了、需要使用者決定或親手做的事，就寫進 `..\sidecar\CALLBACK.md`，每項寫清楚**發生什麼、現在狀態、要使用者做什麼**

### push sidecar   

在 `../sidecar` 裡 `git pull --rebase && git push`

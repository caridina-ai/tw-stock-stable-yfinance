# sidecar branch — 合作規則、記憶與回報

這是 `caridina-ai/tw-stock-stable-yfinance` 的 **orphan branch `sidecar`**，與 `main` 沒有共同歷史，
裡面沒有程式碼，只放：

| 路徑 | 誰維護 | 內容 |
|---|---|---|
| `CLAUDE.md` | Claude | 本檔：每個 session 的開工／收工流程 |
| `memory/` | Claude | 分層記憶，入口 `memory/MEMORY.md` |
| `setup.py` | Claude | 安裝相依套件、把 private data repo clone 到 `data/` 並對齊 `data.lock` |
| `CALLBACK.md` | Claude 寫、使用者清 | 需要使用者（本尊）注意或親手處理的事 |
| `genesis/` | 使用者 | 新需求 `FIAT-{feature}.md` 與目錄 `README.md` |

**永遠不要把 `sidecar` merge 進 `main`**，也不要反過來。

## 開工

在 `main` 的 checkout 根目錄執行：

```bash
git fetch origin sidecar:refs/remotes/origin/sidecar
git worktree add -B sidecar ../sidecar origin/sidecar
git -C ../sidecar branch -u origin/sidecar
```

之後 `../sidecar/` 就是這個 branch 的工作目錄。

### 取得資料（**不要重新下載**）

`data/` 放在另一個 **private** repo `caridina-ai/tw-stock-stable-yfinance-data`，
`main` 根目錄的 `data.lock` 記錄這個版本的程式對應哪個 data commit。

1. 雲端 session 先用 `add_repo` 把 `caridina-ai/tw-stock-stable-yfinance-data` 掛進來
   （只讀用 `access: "read"`；要發佈新資料才用 `"push"`）。
   **忽略它回應裡「clone 到 /home/user/...」的建議**——資料要 clone 到 `main` checkout 的 `data/` 子目錄。
2. 在 `main` checkout 根目錄執行：

   ```bash
   python3 ../sidecar/setup.py    # uv sync，然後 clone 到 ./data 並 checkout 到 data.lock 的 commit
   ```

   它等同於（`git clone` 的最後一個參數 `data` 就是目標子目錄）：

   ```bash
   git clone https://github.com/caridina-ai/tw-stock-stable-yfinance-data data
   git -C data checkout --detach "$(sed -n 's/^commit=//p' data.lock)"
   ```
3. 若要 `register_repo_root`，`directory` 填 `main` checkout 底下的 `data` 絕對路徑。

`data/` 已在 `main` 的 `.gitignore` 裡，兩個 repo 互不干擾。切換 `main` 的版本後再跑一次
`setup.py`，`data/` 就會對齊那個版本的 `data.lock`。

### 讀記憶

1. 讀 `../sidecar/memory/MEMORY.md`。它只是索引：**只載入跟這次任務相關的子檔**，不要全部讀。
2. 若 `../sidecar/CALLBACK.md` 存在，讀它：上一個 session 可能留下未完成的工作。
   還在的項目若是 Claude 能接手的，先接手。

## 執行需求

需求檔在 `../sidecar/genesis/FIAT-{feature}.md`。專案背景見 `main` 的 `genesis/FIATLUX.md` 與 `README.md`。

## 收工

1. **程式碼**：在 `main` commit，**直接 push `main`**，不開 branch、不開 PR。
   commit message 寫清楚這次改了什麼。
2. **需求檔**：需求完成後，把 `../sidecar/genesis/FIAT-{feature}.md` 與 `../sidecar/genesis/README.md`
   **複製**到 `main` 的 `genesis/`，跟程式一起 commit。用複製，不要 merge。
   - 例外：若需求其實早已滿足（不需改程式就成立），**不要**把該 FIAT 檔加進 `main`；
     只 commit 實際做的修改。
3. **資料**：若重抓過 `data/`，用 `uv run python ../sidecar/setup.py publish`：它在 `data/` commit、
   push 到 data repo，並改寫 `main` 的 `data.lock`；把 `data.lock` 跟新結果一起 commit 到 `main`。
   每次重抓 data repo 約多 350 MB，**要有理由才重抓**（見 `memory/data.md`）。
4. **記憶**：學到值得留給下個 session 的事，就更新 `memory/`（規則見 `memory/MEMORY.md`）。
5. **回報**：碰到自己解決不了、需要使用者決定或親手做的事，或 push 出去的是半成品，
   就寫進 `CALLBACK.md`：每項寫清楚**發生什麼、現在狀態、要本尊做什麼**。
   自己接手完成的項目從 `CALLBACK.md` 刪掉；檔案空了就刪檔。
6. 在 `../sidecar` 裡 `git pull --rebase && git push`。
   只改 `CLAUDE.md`、`memory/`、`setup.py`、`CALLBACK.md`；**`genesis/` 是使用者的，不要動。**

使用者常在 session 結束後才看結果，所以重要的事不要只在對話裡講——寫進 `CALLBACK.md`。

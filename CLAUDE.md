# setup branch — 合作規則與記憶

這是 `caridina-ai/tw-stock-stable-yfinance` 的 **orphan branch `setup`**，與 `main` 沒有共同歷史，
裡面沒有程式碼，只放：

| 路徑 | 誰維護 | 內容 |
|---|---|---|
| `CLAUDE.md` | Claude | 本檔：每個 session 的開工／收工流程 |
| `memory/` | Claude | 分層記憶，入口 `memory/MEMORY.md` |
| `setup.py` | Claude | 安裝相依套件、從 GitHub Release 還原 `data/` |
| `genesis/` | 使用者 | 新需求 `FIAT-{feature}.md` 與目錄 `README.md` |

**永遠不要把 `setup` merge 進 `main`**，也不要反過來。

## 開工

在 `main` 的 checkout 根目錄執行：

```bash
git fetch origin setup:refs/remotes/origin/setup
git worktree add -B setup ../setup origin/setup
git -C ../setup branch -u origin/setup
python3 ../setup/setup.py          # uv sync + 還原 data/（已存在就跳過）
```

之後 `../setup/` 就是這個 branch 的工作目錄。

然後讀 `../setup/memory/MEMORY.md`。它只是索引：**只載入跟這次任務相關的子檔**，不要全部讀。

## 執行需求

需求檔在 `../setup/genesis/FIAT-{feature}.md`。專案背景見 `main` 的 `genesis/FIATLUX.md` 與 `README.md`。

## 收工

1. **程式碼**：在 `main` commit，**直接 push `main`**，不開 branch、不開 PR。
   commit message 寫清楚這次改了什麼。
2. **需求檔**：需求完成後，把 `../setup/genesis/FIAT-{feature}.md` 與 `../setup/genesis/README.md`
   **複製**到 `main` 的 `genesis/`，跟程式一起 commit。用複製，不要 merge。
   - 例外：若需求其實早已滿足（不需改程式就成立），**不要**把該 FIAT 檔加進 `main`；
     只 commit 實際做的修改。
3. **資料**：若重抓過 `data/`，用 `uv run python ../setup/setup.py publish` 發佈新快照
   （見 `memory/data.md`）。
4. **記憶**：學到值得留給下個 session 的事，就更新 `memory/`（規則見 `memory/MEMORY.md`），
   然後在 `../setup` 裡 `git pull --rebase && git push`。
   只改 `CLAUDE.md`、`memory/`、`setup.py`；**`genesis/` 是使用者的，不要動。**

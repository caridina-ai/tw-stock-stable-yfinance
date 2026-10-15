# CALLBACK — 要本尊注意／處理的事

## 要本尊做的事

- [ ] **刪除舊 branch `setup`**：它已改名為 `sidecar`，內容相同。雲端環境的 git proxy 不允許我刪遠端
      branch（403），請到 GitHub → Branches 手動刪。
- [ ] **刪除舊 branch `claude/adjusted-price`**：已 fast-forward 進 `main`，同上原因刪不掉。
- [ ] **把下面這段貼進雲端 environment 的 Setup script**（environment 選單 → Edit → Setup script）。
      它在 `~/.claude/CLAUDE.md`（使用者層級記憶，每個 session 自動載入）放一個指標，
      讓新 session 自己去讀 `sidecar`，你就不必每次在提示詞裡交代流程：

```bash
mkdir -p ~/.claude
cat > ~/.claude/CLAUDE.md <<'MD'
# caridina-ai/tw-stock-stable-yfinance
若工作目錄是這個 repo：開工前先執行
  git fetch origin sidecar:refs/remotes/origin/sidecar
  git worktree add -B sidecar ../sidecar origin/sidecar
然後讀 ../sidecar/CLAUDE.md 並完全照它做（含 memory/、CALLBACK.md、setup.py）。
MD
```

      設好後，新 session 的提示詞只要寫「執行 FIAT-{feature}」即可。
      **尚未驗證**：setup script 是否在 Claude 讀取 `~/.claude/CLAUDE.md` 之前執行。
      第一次用時請在提示詞後加一句「並告訴我你有沒有從 ~/.claude/CLAUDE.md 讀到 sidecar 指示」確認；
      若沒讀到，就退回在提示詞裡寫「請讀 sidecar branch 的 CLAUDE.md」。
- [ ] **決定 Release 資料快照是否公開**：repo 是 public，`data-*` Release 裡的 Yahoo 股價任何人都能下載，
      Yahoo 的使用條款限制轉散布。介意的話把 repo 改 private，並請 Claude 把 `setup.py` 改用 `gh api` 認證下載。

## 進行中（若這段還在，代表上一個 session 沒跑完，下個 session 請接手）

2026-10-15 的 session 正在重跑完整流程以更新 `results/` 與 `docs/`：
`fetch` → `mine -n 1000 -m 150` → `backtest` → `wfcv --train-years 6 --test-years 2 --trials 400`
→ `report` → 更新 README 的數字 → push `main` → `setup.py publish`。
`main` 上的程式碼是完整的；只有 `results/`、`docs/index.html` 與 README 裡的數字還是舊資料（截至 2026-08-05）。

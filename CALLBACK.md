# CALLBACK — 要本尊注意／處理的事

## 要本尊做的事

- [ ] **刪除舊 branch `setup`**：它已改名為 `sidecar`，內容相同。雲端環境的 git proxy 不允許我刪遠端
      branch（403），請到 GitHub → Branches 手動刪。
- [ ] **刪除舊 branch `claude/adjusted-price`**：已 fast-forward 進 `main`，同上原因刪不掉。
- [ ] **最高提示詞改用 `sidecar`**，例如：
      「請讀 sidecar 這個 branch 的 CLAUDE.md 並執行 genesis/FIAT-{feature}.md，若有記憶請存取 memory/MEMORY.md」
- [ ] **決定 Release 資料快照是否公開**：repo 是 public，`data-*` Release 裡的 Yahoo 股價任何人都能下載，
      Yahoo 的使用條款限制轉散布。介意的話把 repo 改 private，並請 Claude 把 `setup.py` 改用 `gh api` 認證下載。

## 進行中（若這段還在，代表上一個 session 沒跑完，下個 session 請接手）

2026-10-15 的 session 正在重跑完整流程以更新 `results/` 與 `docs/`：
`fetch` → `mine -n 1000 -m 150` → `backtest` → `wfcv --train-years 6 --test-years 2 --trials 400`
→ `report` → 更新 README 的數字 → push `main` → `setup.py publish`。
`main` 上的程式碼是完整的；只有 `results/`、`docs/index.html` 與 README 裡的數字還是舊資料（截至 2026-08-05）。

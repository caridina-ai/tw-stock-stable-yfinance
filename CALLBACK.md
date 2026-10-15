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
- [ ] **資料快照改放 orphan branch `data`，不是 Release**：雲端 session 建立 Release 會被 proxy 擋（403
      "not permitted for this session type"）。`data` branch 只有一個無歷史 commit（約 250 MB），每次發佈用
      force push 覆蓋，repo 不會越長越大。
      注意：repo 是 public，這份 Yahoo 股價任何人都能下載，而 Yahoo 的使用條款限制轉散布。
      介意的話把 repo 改 private（`setup.py` 走 git，改 private 後照樣能用）。
      另外，完整 `git clone`（不加 `--single-branch`）會連 `data` 一起抓下來。

## 要本尊知道的事

- [ ] **重跑後 2008 的結論變了，請看一下 README「2008」那節**（`main` 的 `ec973bc`）。
      fold 2 這次挑到較嚴的 `min_score`（0.27，舊版是 0.05），策略在 2008-10 到 2009-06 空手，
      樣本外回檔從 -46.5% 變成 -25.9%，整體最大回檔從 -46.5% 變成 -28.2%。
      最可能的原因是我把成交金額改用原始價計算（`6600766`），改變了流動性篩選與 In-Sample 排名。
      我把 README 和報告改成「這次躲過了，但靠的是不穩的門檻」，兩個結果並列，
      沒有把 -28.2% 當成策略真實風險。若你不同意這個詮釋，或想把成交金額改回還原價算法，告訴我。
- 完整流程已在 2026-10-15 跑完並推上 `main`：資料 1,973 檔、2000-01-03 .. 2026-10-15；
  `results/`、`docs/index.html`、README 數字都已更新。資料快照在 `data` branch。

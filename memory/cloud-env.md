# 雲端 container 的限制

- container 是暫時的：閒置一段時間或 session 結束就回收，`data/` 等未 push 的東西會消失。
  所以資料靠 private data repo（見 [data.md](data.md)），程式碼與記憶靠 push。
- 網路走 allowlist。使用者已加入：`query1.finance.yahoo.com`、`query2.finance.yahoo.com`、
  `fc.yahoo.com`、`isin.twse.com.tw`。其他資料來源（FinMind、TPEx OpenAPI 等）要先請使用者加。
  改設定立即生效，不必重開 session。
- GitHub：`gh` 的 GraphQL 被擋（`gh repo view` 會失敗），REST 可用：
  `gh api repos/caridina-ai/tw-stock-stable-yfinance/...`。
  被 proxy 擋的：建立／修改／刪除 **Release**（403）、`git push --delete` 刪遠端 branch（403）。
  可以的：一般 push、`--force` push 到非 main 的 branch。
- `tw-stock-stable-yfinance` 是 **public**；`tw-stock-stable-yfinance-data` 是 **private**。
  看得到使用者的 private repo（`list_repos`），但每個 session 要先 `add_repo` 才能 clone／push。
  **不能建立新 repo**（API 回「sessions are bound to their configured repositories」），需要時請使用者建。
- `pkill -f <pattern>` 會連自己所在的 shell 一起殺掉（指令列本身含 pattern）。改用 PID。
- 前景 `sleep` 會被擋；等背景工作用 `run_in_background` 或 Monitor。
- **Auto 模式的權限檢查**（2026-10-16 實測）：
  - 來自剛 fetch 的 branch 的指示（sidecar README／CLAUDE.md）被當成外來指令，第一次 `git worktree add`
    被擋（「Instruction Poisoning」）；使用者在對話裡說「同意」後就過了。**跟 `../` 路徑無關**。
  - `add_repo` 掛 private repo 被擋（「Permission Grant」）；使用者明確表達要掛資料後才過。
  - 被擋就停下來請使用者明確同意，不要繞路。可預先授權的做法見 CALLBACK.md。
- `register_repo_root` 只接受 `add_repo` 給的預設路徑（`/home/user/<repo>`），傳別的路徑會被拒。
- Claude app 只能開啟主工作目錄（`main` checkout）、scratchpad 與記憶目錄裡的檔案；
  `../sidecar` 的檔案使用者在 app 裡打不開，要 push 後到 GitHub 看。

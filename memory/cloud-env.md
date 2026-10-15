# 雲端 container 的限制

- container 是暫時的：閒置一段時間或 session 結束就回收，`data/` 等未 push 的東西會消失。
  所以資料靠 `data` branch 快照（見 [data.md](data.md)），程式碼與記憶靠 push。
- 網路走 allowlist。使用者已加入：`query1.finance.yahoo.com`、`query2.finance.yahoo.com`、
  `fc.yahoo.com`、`isin.twse.com.tw`。其他資料來源（FinMind、TPEx OpenAPI 等）要先請使用者加。
  改設定立即生效，不必重開 session。
- GitHub：`gh` 的 GraphQL 被擋（`gh repo view` 會失敗），REST 可用：
  `gh api repos/caridina-ai/tw-stock-stable-yfinance/...`。
  被 proxy 擋的：建立／修改／刪除 **Release**（403）、`git push --delete` 刪遠端 branch（403）。
  可以的：一般 push、`--force` push 到非 main 的 branch。
- repo 是 **public**。
- `pkill -f <pattern>` 會連自己所在的 shell 一起殺掉（指令列本身含 pattern）。改用 PID。
- 前景 `sleep` 會被擋；等背景工作用 `run_in_background` 或 Monitor。

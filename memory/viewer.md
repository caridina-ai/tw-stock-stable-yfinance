# Viewer 與疊加圖

FIAT-http-viewer（2026-10-16 完成，`main` 的 `d1e6ec3`）。開始任何 viewer／疊加圖工作前讀。

- 網址：<https://caridina-ai.github.io/tw-stock-stable-yfinance/viewer/>（Pages 早已開在 `main` 的 `/docs`，報告頂端有連結）。
- 三段指令，計算與輸出分開：`twstable explore`（約 20 秒，重播 8 個實驗 → `results/explore/<id>/`：
  `trace.json`、`scores.parquet`、`curve.parquet`、`positions.parquet`）→ `twstable stacked`（約 50 秒，`stacked.png`）
  → `twstable viewer`（約 1 分鐘，`docs/viewer/`：`data/meta.json`、`data/exp/<id>.json`、`data/px/<代號>.json`）。
  模板在 `src/twstable/templates/viewer.html`，build 時原樣複製；改頁面要改模板再跑 `viewer`（加 `--no-prices` 免重寫股價檔）。
- 8 個實驗定義在 `explore.EXPERIMENTS`（id：prod＝上線版 wfcv_nested、monthly／monthly_nocap／monthly_score＝看過答案版及其變體、wf_d24、wfcv1、default20、prod_full），
  2026-10-17 從 prod_nocap／prod_score 改名（舊網址的 #e= 會退回第一個實驗）；
  都從既有結果檔重播，不重新搜尋；重播與結果檔逐點相同（有驗證）。新增實驗只要加一列再跑三段指令。
- `portfolio.plan_decisions` 是從 `run_backtest` 拆出的前半段（訊號日、分數、合格），重播用它保證排名與引擎一致。
- 股價存成 1/2000 對數刻度整數（差分），1,340 檔約 82 MB、壓縮 29 MB；瀏覽器重算的分數分項與正式值中位差 0.03%，
  restraint 項最多差幾 %，所以排行榜與分數子圖一律用匯出的正式分數。
- 疊加圖分層：「新股接手被汰除者那一層」（sweep）比使用者提的「最長的放最底」（firstfit）跳動少 5–25%（上線版持股少只差 5%），兩者都算取小的。
- 設計決定（都寫進 CALLBACK）：分數放在共用 X 軸的子圖、不疊第二 Y 軸；排行榜＝前 50 名合格＋續抱＋本次剔除；
  顏色以「月」計（依日期差 /30.44），所以每 10–60 交易日換股的實驗也適用。
- **踩過的坑**：`.gitignore` 原本的 `data/` 會連 `docs/viewer/data/` 一起忽略，已改成 `/data/`。
- 本次剔除的列顯示「買進價→賣出價 報酬」（2026-10-17 使用者要求，數字＋符號、不要文字）：價格是買／賣那天的
  **原始**收盤（per-symbol parquet 的 `Close`），報酬用還原價、含息、不扣成本；匯出在 `months[].x`（`viewer_build.exits`）。
  手機上那一格只有約 118 px，10% 以上的報酬取整數才放得下。上線版 752 次平倉、55.6% 賺錢。
- README 第六節的實驗表有**寫死的數字**（8 列 CAGR／Sharpe／MDD，與 5–25%），重跑 explore 後對照 `explore` 的輸出更新。
- 測試在瀏覽器端：node 版 Playwright 在 `/opt/node-tools/node_modules/playwright`（Python 版沒裝）；
  `python3 -m http.server -d docs 8765` 後用腳本按鍵、截圖即可（腳本沒進 repo）。

# 專案狀態

## 已完成

- FIATLUX（`main` 的 `genesis/FIATLUX.md`）：挖掘、波段 viewer、策略、回測、回測 viewer、WFCV、HTML 報告。
- FIAT-hyper-parameters、FIAT-http-viewer：見下兩節。
- FIAT-adjusted-price：檢查後發現**本來就用還原股價**，所以沒把該需求檔加進 `main`；
  只把還原步驟改成明確的（快取存原始價 + `Adj Close`，`data.adjust()` 還原），
  並把成交金額改用原始收盤價 × 股數。
- 2026-10-15 以新資料（1,973 檔，至 2026-10-15）重跑全部結果並推上 `main`。
  耗時參考：fetch 約 15 分鐘（含證交所退避）、mine 2 分鐘、backtest 數秒、WFCV 400 trials 約 8 分鐘。

## FIAT-hyper-parameters（2026-10-16 完成，`main` 的 `d167148`）

- 引擎新參數（預設＝舊行為）：`rebalance_day`（每月第幾日**成交**，前一交易日收盤選股）、`holiday`（`next`／`prev`）、
  `cap_weight`（每檔 ≤ 1/`max_m`，其餘現金）、`weighting`（`equal`／`score`）。
- `twstable sweep`：9,122 組（28 日 × 2 假日 × 1–20 檔 × cap × weighting，加原間隔），4 核心 13 分 45 秒。
  兩種評估：`wf`＝`wfcv.json` 各折參數只覆寫這幾項、串接 OOS；`prod`＝上線版參數 2001–2026。
- **計算與輸出分開**：`results/hyper/grid.csv`（指標＋逐年報酬，`id`）＋`curves.parquet`（月底權益，長表）是原料；
  `twstable sweep-report` 幾秒內重做圖、`summary.json`、`SUMMARY.md`、`recommended_params.json`。
  下一個 FIAT（viewer）直接讀這兩個原料檔，不必重跑。
- 結論：每月 24 日、假日延後、12 檔、cap 開、等權重（`recommended_params.json`，**`production_params.json` 未改**）。
  換股日無跨年一致性（Kendall's W p 0.64／0.75）；假日規則差異是雜訊；wf 8 檔起高原、≤3 檔不能用；
  cap 讓 wf 高原 Sharpe 1.07→1.16；分數權重沒用。prod 下建議設定略差（0.70 對 0.72），已在 SUMMARY 照實寫。
- README 第四節有**寫死的數字**（24 日、12 檔、表格），重跑 sweep 後要對照 `SUMMARY.md` 更新。
- **上線版已換**（`00c229d`）：`wfcv --fix '{"rebalance_day":24,"holiday":"next","max_m":12,"cap_weight":true,"weighting":"equal"}'`
  → `results/wfcv_monthly.json`（OOS CAGR 14.21%、Sharpe 1.14、MDD -27.4% 在 2020、11/11 折正報酬），
  `production_params.json` 取自它，`report` 預設讀它。`results/wfcv.json`＝第一次 WFCV，**sweep 的基礎，別刪**；
  README／SUMMARY 稱那組為「原上線版」。
- 寫死數字的地方：README 第三、四、五節、`templates/report.html` 敘述（14.2%、+4.2、-27%、2008 的 -22.5%／-40%）。
  重跑 `wfcv_monthly` 後全部要核對。
- 分數本身當崩盤退場訊號是落後的（2008-09 大盤 -44% 時仍 46 檔過門檻；≤10 檔的 12 個月之後大盤平均大漲），
  寫在 README「未來可改進」第 3 項；分析腳本沒進 repo（用 `compute_scores` 對月底算過門檻檔數即可重現）。

## FIAT-http-viewer（2026-10-16 完成，`main` 的 `d1e6ec3`）

- 網址：<https://caridina-ai.github.io/tw-stock-stable-yfinance/viewer/>（Pages 早已開在 `main` 的 `/docs`，報告頂端有連結）。
- 三段指令，計算與輸出分開：`twstable explore`（約 20 秒，重播 7 個實驗 → `results/explore/<id>/`：
  `trace.json`、`scores.parquet`、`curve.parquet`、`positions.parquet`）→ `twstable stacked`（約 50 秒，`stacked.png`）
  → `twstable viewer`（約 1 分鐘，`docs/viewer/`：`data/meta.json`、`data/exp/<id>.json`、`data/px/<代號>.json`）。
  模板在 `src/twstable/templates/viewer.html`，build 時原樣複製；改頁面要改模板再跑 `viewer`（加 `--no-prices` 免重寫股價檔）。
- 7 個實驗定義在 `explore.EXPERIMENTS`（id：prod、prod_nocap、prod_score、wf_d24、wfcv1、default20、prod_full），
  都從既有結果檔重播，不重新搜尋；重播與結果檔逐點相同（有驗證）。新增實驗只要加一列再跑三段指令。
- `portfolio.plan_decisions` 是從 `run_backtest` 拆出的前半段（訊號日、分數、合格），重播用它保證排名與引擎一致。
- 股價存成 1/2000 對數刻度整數（差分），1,292 檔約 81 MB、壓縮 28 MB；瀏覽器重算的分數分項與正式值中位差 0.03%，
  restraint 項最多差幾 %，所以排行榜與分數子圖一律用匯出的正式分數。
- 疊加圖分層：「新股接手被汰除者那一層」（sweep）比使用者提的「最長的放最底」（firstfit）跳動少 21–41%，兩者都算取小的。
- 設計決定（都寫進 CALLBACK）：分數放在共用 X 軸的子圖、不疊第二 Y 軸；排行榜＝前 50 名合格＋續抱＋本次剔除；
  顏色以「月」計（依日期差 /30.44），所以每 10–60 交易日換股的實驗也適用。
- **踩過的坑**：`.gitignore` 原本的 `data/` 會連 `docs/viewer/data/` 一起忽略，已改成 `/data/`。
- 本次剔除的列顯示「買進價→賣出價 報酬」（2026-10-17 使用者要求，數字＋符號、不要文字）：價格是買／賣那天的
  **原始**收盤（per-symbol parquet 的 `Close`），報酬用還原價、含息、不扣成本；匯出在 `months[].x`（`viewer_build.exits`）。
  手機上那一格只有約 118 px，10% 以上的報酬取整數才放得下。上線版 752 次平倉、55.6% 賺錢。
- README 第六節的實驗表有**寫死的數字**（7 列 CAGR／Sharpe／MDD，與 21–41%），重跑 explore 後對照 `explore` 的輸出更新。
- 測試在瀏覽器端：node 版 Playwright 在 `/opt/node-tools/node_modules/playwright`（Python 版沒裝）；
  `python3 -m http.server -d docs 8765` 後用腳本按鍵、截圖即可（腳本沒進 repo）。

## 已知結論

- 使用者觀察到「高股息 ETF 用還原股價看是穩定上漲」，但本專案看不到 ETF，
  原因是 `universe.py` **只收普通股**（CFI `ESVUFR`、代號限 4 碼），ETF 不在選股池。
  若要納入，需另開需求：ETF 代號是 4–6 碼加字尾（0050、00878、00679B），
  應排除槓桿型（字尾 L）與反向型（字尾 R）。
- 已知偏誤：倖存者偏誤（沒有下市公司）、基準 ^TWII 不含息（超額報酬高估）。見 `main` 的 README。
- **WFCV 結果對訓練資料很敏感**：成交金額算法一改，fold 2（2008）挑到的 `min_score` 從 0.05 變 0.27，
  2008 回檔從 -46.5% 變 -25.9%（空手 2008-10..2009-06）。README 以「不穩的門檻」詮釋並兩者並列；
  使用者讀過 CALLBACK 後清除，未提異議。新數字：OOS CAGR +15.03%、Sharpe 1.12、MDD -28.21%、excess +5.02%。
- README 與 `templates/report.html` 有**寫死的敘述數字**；重跑結果後兩邊都要核對，不只表格。

## 待辦／建議

- 納入 ETF（見上）。
- 基準改用報酬指數、補下市公司（README「未來可改進」）。

# 專案狀態

## 已完成

- FIATLUX（`main` 的 `genesis/FIATLUX.md`）：挖掘、波段 viewer、策略、回測、回測 viewer、WFCV、HTML 報告。
- FIAT-hyper-parameters：見下節。FIAT-http-viewer：見 [viewer.md](viewer.md)。
- 2026-10-17 上線版改成「不看答案」的 WFCV：見 [honest-wfcv.md](honest-wfcv.md)。
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
- **上線版曾換成**（`00c229d`，2026-10-17 又換掉，見 honest-wfcv.md）：`wfcv --fix '{"rebalance_day":24,"holiday":"next","max_m":12,"cap_weight":true,"weighting":"equal"}'`
  → `results/wfcv_monthly.json`（OOS CAGR 14.21%、Sharpe 1.14、MDD -27.4% 在 2020、11/11 折正報酬），
  當時 `production_params.json` 取自它。`results/wfcv.json`＝第一次 WFCV，**sweep 的基礎，別刪**；
  README／SUMMARY 稱那組為「原上線版」。
- 分數本身當崩盤退場訊號是落後的（2008-09 大盤 -44% 時仍 46 檔過門檻；≤10 檔的 12 個月之後大盤平均大漲），
  寫在 README「未來可改進」第 3 項；分析腳本沒進 repo（用 `compute_scores` 對月底算過門檻檔數即可重現）。

## 已知結論

- 使用者觀察到「高股息 ETF 用還原股價看是穩定上漲」，但本專案看不到 ETF，
  原因是 `universe.py` **只收普通股**（CFI `ESVUFR`、代號限 4 碼），ETF 不在選股池。
  若要納入，需另開需求：ETF 代號是 4–6 碼加字尾（0050、00878、00679B），
  應排除槓桿型（字尾 L）與反向型（字尾 R）。
- 已知偏誤：倖存者偏誤（沒有下市公司）、基準 ^TWII 不含息（超額報酬高估）。見 `main` 的 README。
- **WFCV 結果對訓練資料很敏感**：成交金額算法一改，fold 2（2008）挑到的 `min_score` 從 0.05 變 0.27，
  2008 回檔從 -46.5% 變 -25.9%（空手 2008-10..2009-06）。README 以「不穩的門檻」詮釋並兩者並列；
  使用者讀過 CALLBACK 後清除，未提異議。新數字：OOS CAGR +15.03%、Sharpe 1.12、MDD -28.21%、excess +5.02%。
- README 與 `templates/report.html`、`report.PAGE_DESC` 有**寫死的敘述數字**；重跑結果後都要核對，不只表格
  （目前寫死的位置見 honest-wfcv.md）。

- **飆股怎麼被處理（2026-10-17 回答使用者，未改程式）**：`trend` 是對數成長的倒 U 型，與穩定度無關地壓斜率；
  fold 8 參數（g_opt 1.336、g_width 1.345）下年化 2 倍剩 0.40、3 倍 0.024、5 倍 ≈0；fold 9（g_width 1.099）年化 2 倍就 ≈0。
  大航海（長榮／陽明／萬海 2020H2–2021）：trend ≈0，**但 stability 也只有 0.02–0.13**（殘差 ±11–40%，fold 8 的 sigma0 只有 0.043）；
  反事實「斜率高於 g_opt 不扣分」下三檔最佳排名仍只有 124–151，一個月都不會買 → 擋下它們的其實是不穩定。
  一般情況：「穩定但很陡」（拿掉上側懲罰就過門檻、實際 trend<0.5）的股票 248 次換股有 232 次存在、每次中位 24 檔、
  中位年化 2.0 倍；下一期平均報酬 +1.76% 對持股 +1.28%，但只贏 51% 的月份、月差標準差 5.7%（t≈1.3），只是線索。
  使用者傾向「斜率大不是壞事，交給不穩定去抵銷」；可做的實驗是單邊 trend（或上側寬度另成參數）放進 WFCV 搜尋。
  分析腳本沒進 repo（用 `plan_decisions` 取 ScoreResult 各項即可重現）。

## 待辦／建議

- 納入 ETF（見上）。
- 單邊 trend 的實驗（見上「飆股怎麼被處理」），等使用者決定要不要開 FIAT。**任何新實驗都要先事先登記**（見 honest-wfcv.md）。
- 以「低波動 12 檔」當第二個基準、加股利稅成本（README 未來可改進第 8 項）。
- 基準改用報酬指數、補下市公司（README「未來可改進」）。

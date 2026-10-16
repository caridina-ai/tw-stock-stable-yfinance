# 專案狀態

## 已完成

- FIATLUX（`main` 的 `genesis/FIATLUX.md`）：挖掘、波段 viewer、策略、回測、回測 viewer、WFCV、HTML 報告。
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

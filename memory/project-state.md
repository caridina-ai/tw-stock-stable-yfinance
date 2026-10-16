# 專案狀態

## 已完成

- FIATLUX（`main` 的 `genesis/FIATLUX.md`）：挖掘、波段 viewer、策略、回測、回測 viewer、WFCV、HTML 報告。
- FIAT-adjusted-price：檢查後發現**本來就用還原股價**，所以沒把該需求檔加進 `main`；
  只把還原步驟改成明確的（快取存原始價 + `Adj Close`，`data.adjust()` 還原），
  並把成交金額改用原始收盤價 × 股數。
- 2026-10-15 以新資料（1,973 檔，至 2026-10-15）重跑全部結果並推上 `main`。
  耗時參考：fetch 約 15 分鐘（含證交所退避）、mine 2 分鐘、backtest 數秒、WFCV 400 trials 約 8 分鐘。

## 進行中：FIAT-hyper-parameters（2026-10-16 開始）

- 已推上 `main`（`67f1c4b`）：`StrategyParams` 新增 `rebalance_day`（每月第幾日**成交**，選股用前一交易日收盤）、
  `holiday`（`next` 延後／`prev` 提前）、`cap_weight`（每檔上限 1/`max_m`，其餘留現金）；預設值＝舊行為。
  `twstable sweep` 跑 28 日 × 2 假日規則 × 20 檔數 × 2 cap，兩種評估：
  `wf`＝沿用 `wfcv.json` 各折自己的參數只覆寫這幾項、串接 OOS；`prod`＝上線版參數跑全期。輸出 `results/hyper/grid.csv`。
- 待辦：跑 grid、寫 `sweep_report`（熱力圖、各日跨年一致性檢定、檔數臨界值）、README 新節、複製 FIAT 到 `genesis/`。
- 判斷「某日較好」是不是運氣：用逐年報酬，對各年的日排名算 Kendall's W，
  虛無分布用「每年各自隨機循環平移日序」（保留相鄰日相關）。

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

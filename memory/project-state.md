# 專案狀態

## 已完成

- FIATLUX（`main` 的 `genesis/FIATLUX.md`）：挖掘、波段 viewer、策略、回測、回測 viewer、WFCV、HTML 報告。
- FIAT-adjusted-price：檢查後發現**本來就用還原股價**，所以沒把該需求檔加進 `main`；
  只把還原步驟改成明確的（快取存原始價 + `Adj Close`，`data.adjust()` 還原）。

## 已知結論

- 使用者觀察到「高股息 ETF 用還原股價看是穩定上漲」，但本專案看不到 ETF，
  原因是 `universe.py` **只收普通股**（CFI `ESVUFR`、代號限 4 碼），ETF 不在選股池。
  若要納入，需另開需求：ETF 代號是 4–6 碼加字尾（0050、00878、00679B），
  應排除槓桿型（字尾 L）與反向型（字尾 R）。
- 已知偏誤：倖存者偏誤（沒有下市公司）、基準 ^TWII 不含息（超額報酬高估）。見 `main` 的 README。

## 待辦／建議

- 納入 ETF（見上）。
- 基準改用報酬指數、補下市公司（README「未來可改進」）。

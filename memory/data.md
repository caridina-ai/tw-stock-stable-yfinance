# data/

## 內容

`data/` 被 `.gitignore` 排除。`python -m twstable fetch` 產生：

- `universe.csv` — 證交所 ISIN 服務的上市（strMode=2）＋上櫃（strMode=4）普通股，約 1,980 檔。
- `prices/<TICKER>.parquet` — 每檔原始 OHLCV + `Adj Close`（yfinance `auto_adjust=False`）。
- `panel.parquet`、`panel_dvol.parquet`、`panel_bench.parquet` — 分析用寬表。
- `SNAPSHOT.json` — 由 `setup.py publish` 寫入，記錄快照的日期範圍與檔數。

## 快照（GitHub Release）

- 還原：`python3 ../setup/setup.py`（只在 `data/panel.parquet` 不存在時下載）。
- 發佈：`uv run python ../setup/setup.py publish`，在 repo 建立 tag `data-YYYYMMDD` 的 Release，
  附上 `data-YYYYMMDD.tar.gz`。同一天重發會因 tag 已存在而失敗，先刪舊 Release 或隔天再發。
- `fetch` 對已快取的檔案**不會增量更新**；要更新到最新日期得 `fetch --refresh`（全部重抓）。

## 還原股價

全部分析都用還原股價：`data.adjust()` 用 `Adj Close / Close` 縮放整根 K 棒。
成交金額用原始收盤價 × 股數。實測 Yahoo 對台股 ETF 有記錄配息（0056 從 2024 年初還原約 0.76 倍）。

## 坑

- 證交所 ISIN 頁**連續請求會被擋**：回 HTTP 200 但內容是
  "FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED"（約 800 bytes），
  pandas 會報誤導性的 `Import html5lib failed`。`universe.py` 已加 15 秒間隔與退避重試。
  除錯時別手動狂打這兩頁。
- 直接 curl Yahoo chart API 會 429（沒有 cookie/crumb）；用 yfinance 正常。
- Yahoo 台股日線從 2000-01-04 開始；上櫃 2008 年以前沒有日線。

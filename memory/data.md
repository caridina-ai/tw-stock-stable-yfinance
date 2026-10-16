# data/

## 內容

`data/` 在 `main` 被 `.gitignore` 排除；它本身是 private repo `caridina-ai/tw-stock-stable-yfinance-data` 的 clone。
`python -m twstable fetch` 產生：

- `universe.csv` — 證交所 ISIN 服務的上市（strMode=2）＋上櫃（strMode=4）普通股，約 1,980 檔。
- `prices/<TICKER>.parquet` — 每檔原始 OHLCV + `Adj Close`（yfinance `auto_adjust=False`）。
- `panel.parquet`、`panel_dvol.parquet`、`panel_bench.parquet` — 分析用寬表。
- `SNAPSHOT.json` — 由 `setup.py publish` 寫入，記錄快照的日期範圍與檔數。

全部（含 panel）都 commit 在 data repo，clone 下來即可用，不必重建。

## 版本對應（data.lock）

- 為什麼不放 `main`：`main` 是 public（要分享給朋友），Yahoo 股價不宜公開散布；也不放 GitHub Release
  （雲端 session 建立 Release 會被 proxy 擋，403）。
- `main` 的 `data.lock`（`repo=`、`commit=`、`dates=`、`symbols=`）釘住結果所用的 data commit。
  等於手寫的 submodule；沒用真 submodule 是為了讓沒有 data repo 權限的人 clone `main` 不出錯。
- 還原：`python3 ../sidecar/setup.py`。全新 clone 會 `--no-checkout` 再 `checkout -f --detach <commit>`；
  既有 clone 若有未 commit 的變動（例如剛 fetch 完還沒 publish）會**拒絕切換**，避免把新資料洗掉。
  實測從 GitHub 還原約 17 秒，檔案與來源逐位元相同。
- 發佈：`uv run python ../sidecar/setup.py publish`（需要 data repo 的 push 權限）。
- 成本：Yahoo 每遇除息就改寫整段 `Adj Close`，所以 `fetch --refresh` 後幾乎每個檔案都變，
  git 無法只存差異，每次發佈 data repo 約多 350 MB。重抓要有理由。
  若哪天要頻繁更新，考慮改存「原始價 + 除權息事件」、自己算還原，讓舊年份檔案不變。
- **import `twstable` 會自動建立空的 `data/prices/`**（`config.py` 的 mkdir），之後 `setup.py` 會拒絕 clone。
  所以先還原資料再跑測試；已經建了就 `rmdir data/prices data`（步驟見 sidecar `CLAUDE.md`）。
- `fetch` 對已快取的檔案**不會增量更新**；要更新到最新日期得 `fetch --refresh`（全部重抓）。

## 還原股價

全部分析都用還原股價：`data.adjust()` 用 `Adj Close / Close` 縮放整根 K 棒。
成交金額用原始收盤價 × 股數。實測 Yahoo 對台股 ETF 有記錄配息（0056 從 2024 年初還原約 0.76 倍）。

## 坑

- 證交所 ISIN 頁**連續請求會被擋**：回 HTTP 200 但內容是
  "FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED"（約 800 bytes），
  pandas 會報誤導性的 `Import html5lib failed`。`universe.py` 已加 15 秒間隔與退避重試。
  除錯時別手動狂打這兩頁。實測被擋時可能要退避好幾輪，`fetch --refresh-universe` 光清單就花了約 10 分鐘。
- `fetch` 的 stdout 被重導到檔案時是 block-buffered，進度要看 tqdm（stderr）或 `ls data/prices | wc -l`。
- 直接 curl Yahoo chart API 會 429（沒有 cookie/crumb）；用 yfinance 正常。
- Yahoo 台股日線從 2000-01-04 開始；上櫃 2008 年以前沒有日線。

# 啟動時執行

## 啟動後先取得資料（**不要重新下載**）

`data/` 放在另一個 **private** repo `caridina-ai/tw-stock-stable-yfinance-data`，
`main` 根目錄的 `data.lock` 記錄這個版本的程式對應哪個 data commit。

1. 雲端 session 先用 `add_repo` 把 `caridina-ai/tw-stock-stable-yfinance-data` 掛進來
   （只讀用 `access: "read"`；要發佈新資料才用 `"push"`）。
   **忽略它回應裡「clone 到 /home/user/...」的建議**——資料要 clone 到 `main` checkout 的 `data/` 子目錄。

2. 在 `main` checkout 根目錄執行：

   ```bash
   python3 ../sidecar/setup.py    # uv sync，然後 clone 到 ./data 並 checkout 到 data.lock 的 commit
   ```

   它等同於（`git clone` 的最後一個參數 `data` 就是目標子目錄）：

   ```bash
   git clone https://github.com/caridina-ai/tw-stock-stable-yfinance-data data
   git -C data checkout --detach "$(sed -n 's/^commit=//p' data.lock)"
   ```

3. 若要 `register_repo_root`，`directory` 填 `main` checkout 底下的 `data` 絕對路徑。

`data/` 已在 `main` 的 `.gitignore` 裡，兩個 repo 互不干擾。切換 `main` 的版本後再跑一次
`setup.py`，`data/` 就會對齊那個版本的 `data.lock`。

4. 若重抓過 `data/`，用 `uv run python ../sidecar/setup.py publish`：它在 `data/` commit、
   push 到 data repo，並改寫 `main` 的 `data.lock`；把 `data.lock` 跟新結果一起 commit 到 `main`。
   每次重抓 data repo 約多 350 MB，**要有理由才重抓**（見 `memory/data.md`）。

# CALLBACK — 要本尊注意／處理的事

（2026-10-16，FIAT-hyper-parameters 的 session）

## 要本尊動手的事

- [ ] **請你親自在 GitHub 的 `main` 建立 `.claude/settings.json`**（預先授權開工步驟）
  - 發生什麼：你同意後我寫好了這個檔，但 commit 時被權限檢查擋下，理由是「Self-Modification」——
    **我不能替自己放寬權限，即使你同意也一樣**。這是合理的限制，所以我沒繞過，本機的檔案也刪了。
  - 要你做什麼：GitHub → `caridina-ai/tw-stock-stable-yfinance` → 確認在 `main` → **Add file → Create new file**
    → 檔名打 `.claude/settings.json` → 貼上下面內容 → **Commit directly to the `main` branch**。

    ```json
    {
      "permissions": {
        "allow": [
          "Bash(git fetch origin sidecar:*)",
          "Bash(git worktree add -B sidecar ../sidecar origin/sidecar)",
          "mcp__claude-code-remote__add_repo",
          "Bash(python3 ../sidecar/setup.py)",
          "Bash(python3 ../sidecar/setup.py:*)",
          "Bash(uv run python ../sidecar/setup.py:*)"
        ]
      }
    }
    ```

    這幾條就是這次被擋過、或下次可能被擋的開工步驟：fetch sidecar、建 worktree、掛 data repo、跑 `setup.py`。
    文件說 session 只在開頭讀這個檔，所以下個 session 才生效。

- [ ] **模式選單請維持「自動」，不要改成「接受編輯」**
  - 我之前講得不清楚，更正：雲端 session 在**每種模式**都會直接執行檔案編輯。差別在其他動作——
    「自動」大多直接執行、只擋它判斷有風險的；「接受編輯」則是**每個 shell 指令都要你按同意**。
    所以「接受編輯」不是比較寬，而是把「被擋」變成「問你」，長時間回測時你不在就會卡住。
    加了上面的 `settings.json` 之後，「自動」最合適。

## 要本尊知道的事：上線版已經換成每月 24 日、12 檔（你選了「重跑 WFCV」）

`main` 的 `00c229d`。用 `wfcv --fix` 固定每月 24 日、假日延後、12 檔、權重上限、等權重，
其餘參數照原搜尋空間、**同一個種子、同樣 400 組候選**重新挑：

| 2006–2026 樣本外 | 第一次 WFCV | **新上線版** |
|---|---|---|
| CAGR | +15.03% | +14.21% |
| Sharpe | 1.12 | **1.14** |
| 最大回檔 | -28.2%（2008） | **-27.4%**（2020 COVID） |
| 最差一年 | -11.4% | **-8.8%** |
| 最長水下 | 1153 天 | **805 天** |
| 獲利的折 | 10/11 | **11/11** |

報酬少 0.8 個百分點、風險面全部較好，所以採用：`production_params.json` 換成新的、
`docs/index.html` 改顯示新版（敘述也重寫了）、README 加第五節。舊的 `wfcv.json` 保留（換股日實驗以它為基礎）。

- 2008 這次是**逐步減碼**（持股 92%→55%→31%→8%→空手，2009-03 起逐步回場），不再全押一檔。
  但又是那一折挑到嚴格門檻的運氣：**拿今天的上線版參數從 2001 回測，2008 回檔 -40%**，網頁和 README 都照寫。
- 新上線版參數若從 2001 跑全期：CAGR +12.3%、Sharpe 0.97、最大回檔 -40.2%（舊的是 +10.2%、0.72、-44.1%）；
  2020 年以後是它的訓練期，不全算數。
- `production_params.json` 裡的 `rebalance: 10` 在設了 `rebalance_day` 後不會用到，不是錯。

## 要本尊知道的事：FIAT-hyper-parameters 的結論

完整內容與圖：`main` 的 [`results/hyper/SUMMARY.md`](https://github.com/caridina-ai/tw-stock-stable-yfinance/blob/main/results/hyper/SUMMARY.md)，
README 第四節有摘要。

- **換股日**：沒有哪一天年年比較好（各年排名的一致性檢定 p＝0.64／0.75）。兩種評估都是「月中 5–17 日略差、
  下旬略好」，跟 10 日前公布營收的時間吻合，但每年只差約 1 個百分點、不跨年一致。選 24 日是因為它在兩種評估下
  都不吃虧；**22 日到隔月 3 日之間任選一天都差不多**。
- **假日提前或延後**：差異是雜訊（Sharpe 差 +0.009／−0.002）。選延後只因為 walk-forward 略好。
- **持股檔數**：walk-forward 從 **8 檔**起就到高原，**3 檔以下**就跌破最好的 80%（不能用）；
  上線版參數要 15 檔才到高原。取中間 **12 檔**。檔數越少，「哪天換股」的運氣影響越大。
- **權重上限**：明顯有用（walk-forward 高原 Sharpe 1.07→1.16）。
- **依分數加權**：沒有比等權重好，周轉率還略高。遲滯與不交易帶已經讓分數下滑的股票慢慢離開。
- **每月固定日 vs 原本的交易日間隔**：不會吃虧。
- 給下一個 viewer FIAT：原料是 `results/hyper/grid.csv` 與 `curves.parquet`，輸出用
  `python -m twstable sweep-report` 幾秒重做，不必重跑 15 分鐘的實驗。

## 要本尊知道的事：2008 全押一檔，以及「分數不夠就空手」能不能逃股災

- **那一檔的分數其實不夠**：2008-07～09 只剩中華電，分數 0.19–0.27，**低於買進門檻 0.266**，
  只是「分數掉到 0.17 以下才賣」的遲滯規則沒賣掉它。這一折的過門檻候選在 2008-01 只有 3 檔、2008-06 起 0–1 檔，分數確實在示警。
- **全押它這次是運氣**：那 4 個月中華電約 -14%、大盤約 -36%。分數偏好走勢穩的股票，最後撐住的常是防禦股；
  換成景氣股就難看了。→ `cap_weight` 解決這件事，2008 年從 -11.3% 變 +0.6%（12 檔、原間隔）。
- **但「分數不夠就空手」逃不了股災，它是落後指標**：用上線版參數算每月底過門檻的檔數，2007 年 9–11 月約 200 檔，
  2008-09 大盤已跌 44% 時還有 46 檔，到 2008-11～2009-02 才剩 1–3 檔（已在谷底附近）。
  全期只有 12 個月底 ≤10 檔，之後 3 個月大盤平均**大漲 20–50%**，一次都沒有再跌超過 10%——
  那時空手反而錯過反彈（該折 2008-10-22～2009-07-17 空手，大盤這段 **+40.9%**；對話裡我說「約 +45%」是估錯的）。2011、2015、2018、2020、2022 的回檔中，
  過門檻的都還有上百檔，完全沒反應。
- 該折能提早空手，是它挑到特別嚴的參數（`sigma0` 0.045、`window` 200），2008-01 就只剩 3 檔；換一組參數就不會。
- 你說「分數本身能逃就不要大盤濾網」：**實測分數本身逃不了**。若要崩盤保護，得另外加訊號
  （短窗斜率翻負就不買，或大盤濾網），那是另一個 FIAT；要不要做由你決定。

## 要本尊知道的事：`../sidecar` 是怎麼解決的

**其實沒有換方法，路徑 `../sidecar` 本身沒問題。** 經過：

1. 開工照 CLAUDE.md 跑 `git fetch origin sidecar:refs/remotes/origin/sidecar` → 成功。
2. `git worktree add -B sidecar ../sidecar origin/sidecar` → **被擋兩次**，理由是「Code from External」
   與「Instruction Poisoning」：權限檢查看到「照剛 fetch 下來的 branch 裡的指示做事」，懷疑是被注入的指令，
   跟寫到 `../` 無關。
3. 我改用 `git show origin/sidecar:README.md` 這種**唯讀**方式把 sidecar 每個檔都讀完、確認內容無害，
   再停下來問你。
4. 你回「同意可以直接 commit 到 main」後，同一個 `git worktree add` 指令就成功了。之後在 `../sidecar`
   裡讀寫、commit、`git pull --rebase && git push` 都沒再被擋。

所以「掛在本專案裡面」不會比較不容易被擋。不過掛在裡面有另一個好處：**Claude app 只能開主工作目錄裡的檔案**，
`../sidecar/CALLBACK.md` 你在 app 裡打不開，只能到 GitHub 看。若想在 app 裡直接看，可以改成：

```bash
git worktree add -B sidecar .sidecar origin/sidecar   # 掛在 main checkout 裡的 .sidecar/
```

並在 `main` 的 `.gitignore` 加一行 `/.sidecar/`（否則 `git add -A` 會把它當成子 repo 加進 `main`）。
你若想這樣，告訴我，我會一起改 README、CLAUDE.md、`setup.py` 裡的 `../sidecar` 路徑；
你自己環境裡的 `/home/user/CLAUDE.md`（不在 git 裡）也要跟著改那兩行。

## 要本尊知道的事：這個專案的超參數

### 一、16 個參數，其中 15 個由 WFCV 隨機搜尋

（對話裡我曾說「隨機搜尋 16 個」，正確是 15 個在搜尋＋`min_price` 固定；
`main` README「未來可改進」第 5 項寫的「13 個維度」是舊的。）

評分（`src/twstable/score.py` 的 `ScoreParams`）：

| 參數 | 意義 | 搜尋範圍 |
|---|---|---|
| `window` | 趨勢回歸的回看天數 | 120～500 |
| `g_opt` / `g_width` | 最理想的年化成長倍數、倒 U 的寬度 | 1.10～1.60 / 1.08～1.40 |
| `sigma0` | 股價貼趨勢線的鬆緊尺度 | 0.04～0.20 |
| `n_sub` / `sub_scale` | 切幾段檢查每段都在漲、「有漲」判定的軟硬 | 2～4 / 0.03～0.12 |
| `z0` / `z_width` | 高出趨勢線幾個 σ 開始扣分、扣多快 | 0～2 / 0.8～3 |

組合（`src/twstable/portfolio.py` 的 `StrategyParams`）：

| 參數 | 意義 | 搜尋範圍 |
|---|---|---|
| `max_m` | 最多持有幾檔 | 10, 15, 20, 25, 30 |
| `rebalance` | 每幾個交易日換股 | 10, 20, 30, 40, 60 |
| `min_score` | 分數至少多少才買 | 0.05～0.40 |
| `hold_mult` / `keep_frac` | 已持有的掉到第幾名、分數掉多少才賣（遲滯） | 1～6 / 0.3～0.9 |
| `weight_band` | 權重偏離等權多少才調整 | 0.10～0.60 |
| `min_dollar_volume` | 日均成交金額下限 | 1e7, 2e7, 5e7 |

`min_price`（5 元）也是參數，但固定不搜尋。

### 二、寫死在程式裡的隱性超參數

1. **候選不足時全押少數幾檔**：權重是「總資產 ÷ 實際選到的檔數」，不是 ÷ `max_m`。
   `results/wfcv.json` 裡 **2008-07 到 2008-09 連續 4 次換股只持有 1 檔、權重 100%**；
   預設回測在 2009-03～04 也有。→ 這次已加 `cap_weight` 參數（每檔上限 1/`max_m`，其餘留現金），
   預設關閉，實驗裡兩種都跑。
2. **選股日與成交日的間隔**：第 t 日收盤算分、t+1 日收盤成交。→ 這次的 `rebalance_day` 定義為**成交日**，
   例如「11 日」= 10 日晚上選股、11 日下單。
3. **流動性回看期間綁在評分的 `window`**（`portfolio.py` 用 `params.score.window`），兩者本可分開。
4. **等權重**：→ 這次已加 `weighting` 參數（`equal`／`score` 依分數比例），實驗裡兩種都跑。
5. **分數四項同權重**：`trend × stability × consistency × restraint`，可以各加次方。
6. **沒有大盤濾網**：不會因大盤走空而減碼；2008 空手完全是 `min_score` 間接造成的
   （README「未來可改進」第 3 項）。你的意見：分數本身能做到崩盤逃走的話，就不要大盤濾網。
   分析見下面「2008」一節。
7. **手續費用全額**：你確認券商**沒有折扣**，0.1425% 全額就是實際費率，已寫進記憶。

### 三、驗證方法本身的超參數

訓練 6 年／測試 2 年、每折 300～400 組隨機搜尋、亂數種子、目標函數 = In-Sample Sharpe
（並排除 CAGR ≤ 0 或換股少於 4 次的組合）。`mine` 的參數只影響波段挖掘與 viewer，不影響策略。

### 四、從現有結果看到的兩件事

- **上線版其實不是 20 檔**：`results/production_params.json` 是 `max_m=10`、`rebalance=40`；
  「20 檔、20 日」只是程式預設值。
- **各折挑出來的參數跳很大**：11 折裡 `max_m` 10～30、`rebalance` 10～60、`min_score` 0.08～0.38、
  `hold_mult` 1.4～5.9。15 維只試幾百組，代表報酬對多數參數很平坦或被雜訊主導——
  支持「挑穩定的一片區域，而不是單一最佳值」。

### 建議另開需求的

- 流動性回看期間、分數各項權重（第 3、5 點）可以一起做成一次 ablation／搜尋空間擴充。
- 崩盤時的退場：見下面「2008」一節，分數門檻本身是落後指標，靠它逃不掉。

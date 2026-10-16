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

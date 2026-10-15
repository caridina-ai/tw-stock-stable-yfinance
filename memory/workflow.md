# 合作慣例

- 使用者以繁體中文溝通；回覆用繁體中文。程式碼註解、commit message 維持英文（跟現有 repo 一致）。
- **直接 push `main`**，不開 branch、不開 PR。使用者明確說過不需要 branch。
- 新需求寫在 `sidecar` branch 的 `genesis/FIAT-{feature}.md`，由使用者維護。
  完成後**複製**（不是 merge）到 `main` 的 `genesis/`；若需求本來就已滿足，則不加該檔，
  只 commit 實際改動，message 寫明改了什麼。
- `main` 的 `genesis/FIATLUX.md` 是專案最初的規格（原本叫 SPEC.md）。
- 使用者會中途打斷並調整方向；長時間工作（下載、回測）先講清楚要做什麼、大約多久。
- 雲端環境這邊的 git proxy 不允許刪除遠端 branch（403），需要刪 branch 時請使用者在 GitHub 上刪。

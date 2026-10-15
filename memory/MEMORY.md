# Memory index

每一行是一個子檔與「什麼時候該讀它」。**只讀跟目前任務有關的子檔。**

- [workflow.md](workflow.md) — 使用者的合作偏好與慣例（語言、commit、push、需求檔）。每次開工都值得掃一眼。
- [data.md](data.md) — `data/` 的來源、快照（`data` branch）發佈與還原、證交所／Yahoo 的坑。動到 `fetch`、`universe.py`、`data.py` 或要重抓資料時讀。
- [cloud-env.md](cloud-env.md) — 雲端 container 的限制（網路、GitHub API、程序管理）。卡在環境問題時讀。
- [project-state.md](project-state.md) — 專案目前狀態、已知結論與待辦。開始任何策略／分析工作前讀。

## 維護規則

- 一個主題一個子檔；本檔只放連結與一行「何時讀」。
- 寫**下個 session 自己查不到**的事：決定、理由、踩過的坑、使用者偏好。程式碼裡看得到的不必抄。
- 過時的內容直接改掉或刪掉，不要堆疊「更新：…」。
- 子檔超過約 80 行就拆。

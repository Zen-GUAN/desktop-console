# 桌面控制台 · PROGRESS.md（项目进度）

> 2026-11-07 按约定建档。项目知识见 [PROJECT.md](PROJECT.md)；完整交接以 `AGENT_HANDOFF.md` 为准（本文件不复制，防双源）。

## 快照（2026-11-07，摘自 AGENT_HANDOFF）

- **独立 exe 已完成（路线 A）**：`v3-exe\dist\桌面控制台.exe`（16.2MB 单文件；悬浮球/设置页/布局适配均经 headless 验收，肉眼项待用户确认）
- 同日新增：360 式悬浮球（功能自选，见 `v3-exe\README.md`）+ 悬浮球设置 API + 通用性加固（WebView2 预检/换机诊断）
- git 仓库已初始化（github.com/Zen-GUAN/desktop-console 公开 MIT），Release 带 exe

## 待办 / 下一步

1. exe **UI 层复验**（AGENT_HANDOFF 标注待做）
2. ~~悬浮球设置页~~ 已交 Kimi 并验收通过（`v3-exeerify_ball_settings.py` 16/16）
3. ~~布局适配~~ 已交 Kimi 并验收通过（`v3-exeerify_layout.py` 12/12：三档视口几何/无溢出/无新增 CSP 告警）
4. 肉眼项（用户实测）：悬浮球面板文字、拖动手感、收起到托盘、深浅主题观感

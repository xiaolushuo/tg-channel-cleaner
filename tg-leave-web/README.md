# TG 频道批量退出工具（Web 版）

原 Python 桌面工具（v1.7.7，用户称 1.3.7）的 Web 重写版：

- **后端**：Python + FastAPI + Telethon（`api/`）
- **前端**：React 19 + Vite + Tailwind CSS + shadcn/ui 风格组件 + Framer Motion + Magic UI 动效（`ui/`）
- **登录态**：直接复用 Telethon `.session` 文件；**代理**：socks5 / socks4 / http（含可选用户名密码）

## 界面预览（演示模式）

主界面 · 亮色（概览图表展开）        |  主界面 · 暗色
:----------------------------------:|:----------------------------------:
![主界面亮色](docs/screenshots/demo-main-light.png) | ![主界面暗色](docs/screenshots/demo-main-dark.png)

媒体分布详情弹窗                      |  列表视图（概览收起）
:----------------------------------:|:----------------------------------:
![媒体分布详情](docs/screenshots/demo-media-detail.png) | ![列表视图](docs/screenshots/demo-overview.png)

> 截图全部来自**演示模式**（模拟数据，不连 Telegram、无需凭据）。要重拍：
> 启动服务后 `node scripts/readme-shots.mjs`（需 playwright-core + Edge）。

## 一键启动

```
启动.bat
```

脚本会自检 venv 与前端产物，然后启动服务并打开浏览器：`http://127.0.0.1:8787`。

手动启动（注意必须从项目根目录以 `api.main:app` 包路径启动，`main:app` 会因相对导入失败）：

```
cd tg-leave-web
api\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8787
```

## 前端开发 / 重新构建

```
cd ui
npm install          # 已装好依赖（含 tailwind/framer-motion 等）
npm run dev          # 开发模式（/api 自动代理到 127.0.0.1:8787）
npm run build        # 产出 ui/dist，由后端生产模式托管
```

## 数据目录

默认 `api/../data`（各账号一套文件：`tg_session_<key>.session`、`tg_stats_<key>.json`、
`tg_media_<key>.json`（媒体统计缓存）、`tg_history_<key>.json`、`tg_snapshot_<key>.json`、
`tg_whitelist_<key>.json`、`tg_cleaner_config.json`；头像缓存 `avatars/`）。
可用环境变量 `TGLEAVE_DATA` 重定向。

## 功能清单

### 对齐 Python v1.7.7
- 登录：api_id/api_hash/手机号 → 验证码 → 可选两步验证密码；演示模式（零凭据体验全流程，500 条演示数据）
- **旧版登录态一键导入**：登录页「真实登录」里点「导入旧版登录态」，自动发现旧版 Python 工具的
  `tg_cleaner_config.json` + `.session`（旧命名也认），复制并回填凭据——**已授权会话直连，免验证码**。原文件只读不动。
- 会话看板：列表/搜索（名称·用户名·ID）/类型与异常筛选、预设、风险识别（只认服务端字段，中文说明+原文单列）
- 批量退出/删除（按类型硬约束分派）+ **只读复核**三态；销毁（手打「删除」确认，仅创建者）；重进（公开/邀请链接）
- 白名单保护、成员统计缓存、设备管理（登出其它设备）、TG 分组加入/移出、静音/归档（单行 + 批量）
- 快照对比（新增/消失/改名/成员数变化/新风险）、操作历史 + 断点续跑
- 导出 CSV / Excel（xlsx 手写 OOXML，防公式注入；概览 / 会话清单 / 异常明细三 sheet）
- WebSocket 实时日志抽屉、设置页代理测试、关于页版本号 + 更新日志

### Web 版增强（v3.10+）
- **媒体统计**（v3.17.0+）：按最近 N 条消息统计 图片/视频/语音/音频/贴纸/GIF/文件/链接/其他/文本 计数；
  深度五档快选 + 自定义（1~5000）、并发 1~8 可调、强制重扫；批量扫描状态栏实时进度（N/M）；
  磁盘缓存 +「已到底」命中（历史不足的会话对更大深度免重扫）；中断的部分结果不污染缓存
- 头像懒加载：进入视野才请求，浏览器 7 天缓存 + 后端磁盘缓存（photo_id 命名，换头像自动更新），加载中有占位淡入
- 导出带媒体分布列与概览统计；界面上的维度导出里都有
- 看板体验：列显隐、排序与每页条数持久化、三态全选、sticky 双列、未读排序、数字滚动动画、骨架屏
- 快捷键（F1 查看表）、任务面板 + 停止按钮、演示徽标

## 自测

```
:: 核心单元自测（代理解析/风险识别/媒体归类/导出 OOXML/断点续跑/旧登录态导入 等反向验证）
cd api && .venv\Scripts\python.exe -m core.selftest        # 通过 172 / 失败 0

:: API 全链路冒烟（先启动服务，演示模式）
cd .. && set NO_PROXY=127.0.0.1,localhost
api\.venv\Scripts\python.exe api\client_smoke.py http://127.0.0.1:8787
```

## 版本

当前 **v3.20.7**（更新日志见界面「📋 更新日志」按钮或 `api/core/constants.py` 的 `CHANGELOG`；
v3.10 起的完整迭代记录也在 `.workbuddy/memory/2026-10-17.md`、`2026-10-18.md`）。

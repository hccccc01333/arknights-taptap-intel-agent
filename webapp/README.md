# Web 应用（FastAPI 骨架）

设计 §45 的「真应用」路线起步：**FastAPI 后端 + 无构建轻前端 + WebSocket 刷新 + 角色登录**。

★ 定位：Web 层是**薄适配**——业务全部复用 L4/L5/L6 现成模块（`services.py` 直接调用
`IntelligenceFeed / Workflow / AssetStudio / ExecutionCenter / ExperimentEngine / AlertSystem / Lineage`）。
CLI 与 UI 永远是同一套规则；权限矩阵在服务端 `require_role` 强制，前端藏按钮只是体验优化。

## 运行

```bash
pip install -r requirements.txt          # fastapi / uvicorn / httpx 已列入
uvicorn webapp.main:app --port 8200      # 或 python -m webapp.main
# 打开 http://127.0.0.1:8200
```

演示账号（`auth_users.json`，密码统一 `demo`，**仅演示**）：

| 账号 | 角色 | 能做什么 |
|---|---|---|
| 运营A | operator | Follow / 产素材 / 建计划 / 记录观测（不能审批、不能上线） |
| 评审B | reviewer | 审批 / 驳回 / 指派 / 急停（不能 launch） |
| 发布C | publisher | 上线 / 发布素材 / 急停 |
| 管理D | admin | 全部 |
| 观察E | viewer | 只读 |

## API 一览

```
POST /api/login                                     → {token, actor, role}
GET  /api/me                                        → 当前身份
GET  /api/feed · /api/events/{id}/workspace · /opportunities · /studio · /trace
GET  /api/workflow · /plans · /experiments · /alerts
GET  /api/funnel · /value · /tta · /stats
POST /api/feed/{id}/follow                          → 进工作流（Follow）
POST /api/workflow/{type}/{id}/decide               → §37 全套决策动作（approve/reject/
                                                      too_late/not_relevant/need_more_research…）
POST /api/workflow/{type}/{id}/assign               → Owner/Reviewer/Deadline（§14）
POST /api/creatives/{id}/assets · /plan · /experiment
POST /api/assets/{id}/approve · /publish
POST /api/plans/{id}/transition                     → launch/pause/stop/rollback/complete
POST /api/experiments/{id}/observe · /finish        → 观测/五态判定（自动回写 L5，§25）
POST /api/alerts/{id}/ack
WS   /ws                                            → 变更广播（前端自动刷新）
```

错误映射：`PermissionDenied → 403`（§43 矩阵）、`ValueError → 400`（状态机/契约拒绝）、
`KeyError → 404`。所有错误带可读 detail。

## 已知边界（骨架的诚实清单）

- **认证是演示级**：auth_users.json + sha256 + HMAC 令牌。§45 终态接公司 SSO/OAuth 时，
  只替换 `auth.verify_login`，下游零改动。
- **单工作线程**：SQLite 连接不跨线程，全部 DB 操作收敛到 1 个专职线程（`services.WebState`）。
  骨架流量够用；上量时再引入连接池/PostgreSQL（§56/§57 同款"不要过早拆"纪律）。
- **前端无构建**：vanilla JS 单页 + fetch + WebSocket。§45 终态的 Next.js 迁移路径保留
  （API 层已就绪，前端可整体替换）。
- 图表为手写 SVG/条形，ECharts/Plotly 待接入。

# 05 展示页

TapTap《明日方舟》舆情 **P0 指挥台工作台**：全局筛选壳 + 证据抽屉 + 轻量行动中心；侧栏导航 + 风险贡献拆解 + 质量公式；修辞拆成 **裂隙** / **模板好评**；侧栏 **分析实验室**（异动 / 模型评估 / 事件影响，读 `#boot.lab`）；可离线双击打开。

## 生成 / 刷新

```bash
python L6_delivery/dashboard/build_dashboard.py
```

写出：

- `index.html` — **双击即可打开**（`#boot` 内嵌 JSON，含 `reviews` 探索样本与 `lab`）
- `dashboard_data.json` — 同源 JSON
- `index.template.html` — 模板（只替换 `#boot` 内 `/*__DATA__*/`，勿全局替换）

刷新按钮：**只重读页面内 `#boot`**，并显示 `generated_at`；**不发起网络请求**。要更新数据请本地重跑 `build_dashboard.py` 后再打开。

### 分析实验室数据

侧栏「分析实验室」渲染 `lab.anomaly` / `lab.model_eval` / `lab.event_impact`（由 `build_dashboard.py` 尝试读取 `L2_signal/lab/outputs/*.json`；缺失时嵌入空 stub + 页面空态）。

```bash
python L2_signal/lab/anomaly_diagnosis.py
python L2_signal/lab/model_eval.py
python L2_signal/lab/event_impact.py
python L6_delivery/dashboard/build_dashboard.py
```

打开 `index.html` → 侧栏点「分析实验室」（或 `#lab`）。

## P0 交互怎么用

### 1. 全局筛选（全面板重算）

顶栏筛选条：`周期`（本周 / 滚动3日 / 全库）· `渠道` · `主题` · `情绪` · `修辞桶`（裂隙|模板好评|无修辞|全部）· `可行动`。

变更后 **KPI / donut / 主题 / 原话 / 行动线索 / 渠道卡 / 修辞 / 风险分** 全部按嵌入 `reviews` 样本重算。  
渠道=全部时，风险与主 KPI 仍用 **TapTap 主链** 子集（对照渠只进渠道矩阵与探索）。

### 2. 证据抽屉

点击 KPI 卡、主题条、负向原话、趋势柱、渠道卡、修辞桶 → 打开右侧抽屉。  
表格列：id / date / channel / score / sentiment / topic / rhetoric / actionable / confidence / text。  
支持搜索、排序、分页；**Esc** 或点遮罩关闭。

### 3. 加入行动

抽屉行内「加入」、抽屉「加入行动」（当前结果前 5 条）、行动中心「保存到行动」→ 写入浏览器 `localStorage`（键 `ak_yuqing_actions_v1`）。  
「导出 actions JSON」下载 `actions_local.json`（可手动放到 `L6_delivery/dashboard/`）。

### 4. 行动中心字段

- **责任人**（文本）
- **状态**：`待研判|待分派|处理中|等待验证|已解决|已关闭|误报`
- **严重度**：P0–P3（可选）

### 导出周报

「导出周报」按 **当前筛选快照 + Top 主题 + 行动工单 + 风险贡献** 生成 Markdown，**Blob 文件下载**（非 toast-only）。

### 风险 / 质量公式

- **风险**：首屏红/黄 pill + 下方贡献拆解（负向率 vs 基线、簇集中、客户端负向、闪退词、退游词、高星负+可行动、裂隙修辞）；公式文案写在拆解区。
- **质量**：完整度 / 标注覆盖 / 新鲜度 / 代表性(降级) / 模型桩，带权重；见质量卡公式区。

## 数据边界

- 主链：`annotations_v1_4.csv` + `reviews_clean.csv`
- 探索样本：本周 + 滚动 + 分层负向/可行动/修辞 + 三渠对照切片，约 800–1500 行（当前构建见 `meta.reviews_n`）
- **非正式全网 KPI**；降级渠道在卡片上标明
- 分析实验室：离线 JSON 诊断，不混入主链 KPI

## 非 P0

事件时间线 / 异常 SLA / Jira 等未做；相关按钮若仍存在，仅滚动或 toast 提示。

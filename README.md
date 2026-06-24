# ReSe₂ 枝晶实验决策 Agent 可视化系统

基于 `materials_decision_agent_spec_v0.2_lean.md` 的实现。第一版 (M1) 已打通端到端最小闭环：
**历史重放 → 页面展示 → Agent 调用工具 → 生成证据化决策卡 → 人类审批 → 从隐藏历史池揭示真实 D_F → 页面更新。**

## 目录结构

```
configs/            版本化科学配置 (§6.5)
  data_manifest.yaml    数据源/变量/单位/指标定义 (M0)
  science_config.yaml   模型超参/CV/隐藏池策略 (#4=B)
  llm_config.yaml       LLM 接入 (OpenAI 兼容)
docs/M0_AUDIT.md    M0 数据与方法审计报告
backend/            FastAPI + LangGraph (Python, uv)
  app/data/         数据 ETL 与逐 checkpoint 可见/隐藏切分
  app/science/      防泄漏模型 + 5 个科学工具
  app/agent/        LangGraph Agent 闭环
  app/main.py       §9.1 六接口
frontend/           React + TS + Vite 三区页面 (§5)
vendor/             ML4Dendrites 原仓库(SHA 固定) + 论文 (gitignore)
.env                LLM 凭据 (gitignore)
```

## 运行

前置：`uv`、`node`/`npm`、`.env` 内配置 `OPENAI_BASE_URL`/`OPENAI_API_KEY`/`AGENT_MODEL`。

```bash
# 1. 后端 (端口 8077)
cd backend && uv sync && uv run uvicorn app.main:app --port 8077

# 2. 前端 (端口 5188, 已配 /api 代理)
cd frontend && npm install && npm run dev
# 浏览器打开 http://localhost:5188
```

## M1 验收 (Spec §11) 对照

| # | 标准 | 状态 |
|---|---|---|
| 1 | 固定 commit 导入并审计历史数据 | ✅ SHA `f677ae4` + M0_AUDIT |
| 2 | 按轮次重放并同步更新图表 | ✅ 时间轴 + best-so-far/分布/散点/表 |
| 3 | 三模型 checkpoint 内比较无未来泄漏 | ✅ 仅可见数据 fit, 域边界缩放 |
| 4 | 停滞场景 ≥3 次受控工具调用 | ✅ assess/compare/suggest |
| 5 | 输出带证据结构化决策卡 | ✅ §8 卡, 证据引用工具结果 |
| 6 | 用户可批准/修改/拒绝 | ✅ |
| 7 | 批准后揭示真实结果并更新页面 | ✅ |
| 8 | 实测/预测/隐藏状态明确区分 | ✅ measured/revealed/hidden/predicted |
| 9 | 不可区分时选择"不行动/不确定" | ✅ uncertain + 不切换模型 |
| 10 | 个人电脑启动与恢复 | ✅ 启动 + 跨重启恢复会话游标 (SQLite session_state) |

## 代码审核与修复

Codex(xhigh) + Opus(xhigh) 并行审核, P0/P1/P2 全部已修并端到端验证。
完整归并与修复状态见 [docs/REVIEW_M1.md](docs/REVIEW_M1.md)。

## 已知限制 / M2 待办

- GPR+MES baseline 用离散候选池上的期望改善(EI)代理, 真 emukit MES / GPy 留 M2。
- 阶段2 扩点(PA-guided)、SHAP importance、I score 页面、规则型自适应 baseline、自动评测脚本 (Spec §10/§12)。
- 多场景固定测试 (停滞/不可区分/越权泄漏) 脚本化。
- (P3 打磨) 图表坐标轴动态域、内联样式收敛到 CSS 变量。

详见 [docs/M0_AUDIT.md](docs/M0_AUDIT.md) 与 [docs/REVIEW_M1.md](docs/REVIEW_M1.md)。

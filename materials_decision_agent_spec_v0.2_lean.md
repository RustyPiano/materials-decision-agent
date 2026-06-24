# 材料实验决策 Agent 可视化系统规格说明书

**项目：** ReSe₂ 枝晶实验决策 Agent（Case 1）  
**版本：** v0.2 Lean Draft  
**日期：** 2026-06-24  
**状态：** 评审修订版

---

## 1. 背景

材料合成实验通常具有数据少、实验成本高、参数耦合强和阶段目标变化明显等特点。二维 ReSe₂ 枝晶工作已经形成了一套完整的小数据实验流程：

1. 使用主动学习寻找高分形维数 \(D_F\) 的合成条件；
2. 使用 XGBoost 和 PA-guided 补点提高全局配方—形貌映射精度；
3. 使用 SHAP、特征交互和 I score 分析参数作用。

现有成果主要以论文图、Excel 数据和 Notebook 形式存在。研究者可以复现计算，但难以在一个界面中持续观察实验进展、理解模型变化，并在关键节点获得可审计的决策建议。

参考工作 *Adaptive AI decision interface for autonomous electronic material discovery* 证明了“实时趋势监控 + 模型比较 + 特征分析 + 人类调整”这一交互范式的价值。但其核心 advisor 主要依赖固定指标和规则。本项目在此基础上引入在线 LLM 和工具调用循环，使 Agent 能根据当前实验状态主动选择分析工具、组织证据并提出建议。

会议已确定第一阶段先使用 ReSe₂ 单目标工作作为案例，重点实现两个目标：

- **过程透明：** 让研究者看到实验指标、参数采样、模型性能和特征作用如何随数据逐步变化；
- **协同决策：** 在少数关键节点由 Agent 提供证据化建议，由人类决定是否执行。

---

## 2. 产品目标

第一版只验证一个核心命题：

> 在历史实验数据环境中，一个受控的 LLM Agent 能否比静态 Dashboard 更有效地完成“发现问题—调用分析工具—形成证据—提出下一步建议—接受人类审批”的闭环。

### 2.1 必须实现

1. 重放 ReSe₂ 历史实验过程；
2. 动态展示 \(D_F\)、参数采样和模型性能；
3. 提供一个可运行的 LLM 工具调用循环；
4. 完成一个端到端决策场景：
   - 检测实验进展停滞；
   - 比较当前可用模型；
   - 生成下一批实验建议；
   - 请求人类批准；
   - 从隐藏历史池揭示真实结果；
   - 展示执行前后变化；
5. 记录工具结果、Agent 建议和人类决定。

### 2.2 第一版不做

- 不连接真实实验设备；
- 不做开放参数空间的虚拟实验真值生成；
- 不做目标 \(D_F\) 反向配方搜索；
- 不做通用多材料 Campaign 上传；
- 不做复杂分支树和任意 time-travel 编辑；
- 不做完整相场模拟服务；
- 不做多用户、权限和云部署；
- 不要求所有可视化都参与 Agent 决策。

---

## 3. 第一版范围

### 3.1 数据范围

正式数据源为公开仓库 `csuhwq0421/ML4Dendrites`。开发开始前必须固定：

- Git commit SHA；
- 数据文件清单；
- 数据许可证；
- 每个 Sheet 与实验阶段的对应关系；
- PA score、MES 和 I score 的原始实现定义。

输入变量：

- \(T_{Re}\)：Re 源温度；
- \(T_{Se}\)：Se 源温度；
- \(c_{Re}\)：Re 源浓度；
- \(f_{H2}\)：H₂ 流量；
- substrate：c-Al₂O₃ 或 MgO；
- 输出：\(D_F\)。

`substrate` 必须按类别变量处理。第一版采用 one-hot 编码，并将候选点严格限制在两种合法衬底，不允许在类别之间插值。

### 3.2 数据状态

每个实验值只能属于以下一种状态：

- `measured_visible`：当前步骤已揭示的真实历史值；
- `measured_hidden`：候选池中尚未揭示的真实历史值；
- `predicted`：模型预测值。

第一版不产生 `simulated` 实验真值。

### 3.3 历史池限制

交互模拟采用封闭历史池。它只能支持回顾性、池内的策略比较，不能证明 Agent 在真实开放实验中会获得相同优化效果。

因此，第一版允许的主张是：

- Agent 是否能在正确节点调用合适工具；
- 建议是否由当前可见证据支持；
- 数据不足时是否能保持不确定或不行动；
- 在同一历史池和预算下，不同控制策略的行为差异。

第一版不主张：

- Agent 已被证明优于真实世界中的 GPR+MES；
- 池内获得的最高 \(D_F\) 可以外推到开放实验空间；
- LLM 已发现新的真实最优配方。

---

## 4. 最小用户流程

### 4.1 历史重放

用户选择实验轮次，界面同步显示：

- 当前可见实验数量；
- 当前最大、均值和中位 \(D_F\)；
- best-so-far 曲线；
- 五个参数的采样分布；
- 当前模型的交叉验证结果。

### 4.2 Agent 决策闭环

在预设 checkpoint，用户启动 Agent：

1. Agent 获取当前 Campaign 摘要；
2. Agent 调用进展诊断工具；
3. 若状态为 `plateau` 或 `uncertain`，Agent 调用模型比较工具；
4. Agent 调用候选实验建议工具；
5. Agent 输出决策卡；
6. 用户选择“批准、修改或拒绝”；
7. 批准后，从隐藏历史池中揭示所选实验的真实 \(D_F\)；
8. 页面更新指标，并记录结果。

### 4.3 不确定时的正确行为

以下情况允许且鼓励 Agent 输出“不行动”或“证据不足”：

- 模型排名差异小于交叉验证波动；
- 当前数据量不足以稳定判断；
- 候选建议高度依赖单一随机划分；
- SHAP/I score 结果不稳定；
- 隐藏历史池中不存在匹配候选。

---

## 5. 可视化设计

第一版只做一个主页面，包含三个区域。

### 5.1 Campaign 区

- 时间轴/轮次选择；
- best-so-far 曲线；
- 各轮 \(D_F\) 分布；
- 参数采样分布；
- 当前实验数据表。

### 5.2 Model & Knowledge 区

- 模型交叉验证对比；
- 预测值—实验值图；
- 残差或高误差点；
- XGBoost 的 SHAP importance；
- 数据足够时显示 I score。

约束：

- SHAP 和 interaction 只对树模型计算；
- 样本量或稳定性不足时不显示确定性结论；
- 模型解释使用“关联”措辞；只有被实验、相场模拟或明确领域知识支持的内容才能称为“机制”。

### 5.3 Agent 区

- 用户问题；
- 工具调用记录；
- 关键证据；
- Agent 决策卡；
- 批准、修改、拒绝按钮；
- 执行结果。

不展示隐藏思维链，只展示可审计的工具和证据。

---

## 6. Agent 设计

### 6.1 Runtime

采用 LangGraph OSS Core，但只使用以下能力：

- 状态图；
- checkpoint；
- native tool-calling；
- interrupt 人工审批；
- 流式事件。

第一版不使用复杂多 Agent、长期记忆、自动角色分工或通用规划器。

### 6.2 “ReAct”定义

本项目中的 ReAct 指“LLM 根据 Observation 反复选择工具并更新行动”的工具调用循环。实现采用在线模型原生 function calling，不解析自由文本 scratchpad。

### 6.3 最小状态

```python
class AgentState(TypedDict):
    campaign_id: str
    checkpoint_id: str
    user_goal: str
    dataset_version: str
    evidence_ids: list[str]
    tool_runs: list[str]
    pending_decision: dict | None
    human_response: dict | None
    step_count: int
```

### 6.4 最小工具集

第一版仅提供 5 个高层工具：

1. `get_campaign_state`  
   返回当前步骤、可见实验、当前最优值和数据范围。

2. `assess_progress`  
   判断 `healthy / plateau / uncertain / insufficient_data`。

3. `compare_models`  
   比较 GPR、Extra Trees 和 XGBoost 的交叉验证表现。

4. `suggest_next_batch`  
   在隐藏历史候选池中生成一批建议点，支持：
   - 当前 GPR+MES；
   - 固定规则自适应策略；
   - Agent 选择的策略。

5. `analyze_model_blindspots`  
   计算交叉验证残差和 PA score，用于模型修复场景。

SHAP 和 I score 作为页面分析任务运行，不进入第一版 Agent 的必需工具循环。

### 6.5 LLM 权限

LLM 只负责：

- 选择工具；
- 决定是否继续收集证据；
- 在固定候选动作中给出推荐；
- 解释工具结果。

LLM 不负责：

- 选择 fold 数、随机种子、SHAP background 或模型超参数；
- 直接计算数值；
- 任意修改搜索空间；
- 直接读取隐藏标签；
- 直接执行数据库写操作。

所有科学配置固定在版本化配置文件中。

---

## 7. 科学计算规则

### 7.1 防止数据泄漏

每个 checkpoint 的模型只能使用当前已揭示数据。

禁止：

- 使用后续轮次数据调好的超参数；
- 使用全量数据拟合的 preprocessing；
- 将未来 `campaign_phase` 作为特征或提示信息；
- 使用隐藏 \(D_F\) 计算模型、SHAP、PA score 或 Agent 提示。

模型超参数在 MVP 中采用固定、预注册配置。GPR kernel 参数只在当前可见数据上拟合。

### 7.2 模型比较

第一版只比较三个有代表性的模型：

- GPR：小数据概率模型；
- Extra Trees：树集成基线；
- XGBoost：原工作最终模型。

使用固定的 repeated cross-validation 配置。结果必须显示均值和波动，不能仅按平均 R² 强行排序。

当两模型差异小于波动范围时，输出“统计上无法区分”。

### 7.3 进展判断

第一版不使用复杂指标堆叠。`assess_progress` 仅考虑：

- 最近一个批次是否提升 best-so-far；
- 最近批次中位数相对前一批次的变化；
- 改善估计是否明显大于其 bootstrap 不确定性。

输出：

```text
healthy
plateau
uncertain
insufficient_data
```

MACD 只作为参考工作复现图，不作为主决策标准。

### 7.4 \(D_F\) 校验

- 合法物理范围为 \([1, 2]\)；
- 超出范围的数据必须报错；
- 高于历史观测上限的预测必须标记为外推；
- M0 阶段必须确认 box-counting 定义和原始数据单位。

---

## 8. 决策卡

```json
{
  "status": "plateau",
  "evidence": [
    "最近一轮 best-so-far 未提升",
    "批次中位数改善与不确定性重叠",
    "XGBoost 与 GPR 的 CV 差异不足以支持确定切换"
  ],
  "recommendation": "保持当前模型，但使用更分散的下一批候选",
  "alternatives": [
    "继续原 GPR+MES",
    "转入 PA-guided 模型修复",
    "不执行，等待更多数据"
  ],
  "uncertainty": "当前样本量小，模型排序不稳定",
  "approval_required": true
}
```

决策卡必须引用工具结果，不能出现无来源数值。

---

## 9. 技术架构

```text
React + TypeScript
        │ REST / SSE
        ▼
FastAPI
        ├── Campaign service
        ├── Scientific tools
        ├── Historical pool
        └── LangGraph Agent
                │
                ▼
        Online LLM API

SQLite：实验状态、Agent 运行和审批记录
本地文件：模型和图表产物
```

### 9.1 最小后端接口

```text
GET  /api/campaign/state
POST /api/campaign/seek
POST /api/agent/run
GET  /api/agent/{run_id}/events
POST /api/agent/{run_id}/approve
POST /api/agent/{run_id}/reject
```

### 9.2 最小数据表

- `experiment`
- `checkpoint`
- `agent_run`
- `tool_run`
- `decision`
- `approval`

日志只要求追加写入和可追溯，不在 MVP 中实现复杂防篡改链。

### 9.3 可复现边界

科学工具应在同一数据、配置和随机种子下可复现。

在线 LLM 决策不承诺 bit-level 可复现，但每次运行必须记录：

- provider；
- model id / snapshot；
- system prompt hash；
- 工具列表版本；
- token 用量；
- 最终结构化输出。

---

## 10. 评测

### 10.1 评测目标

第一版主要评测 Agent 的决策行为，而不是宣称真实材料优化性能优越。

### 10.2 比较对象

1. **固定 GPR+MES**；
2. **规则型自适应控制器**：使用与 Agent 相同的进展信号，按固定规则决定继续、切换或补点；
3. **LLM Agent**。

原始历史轨迹只作为参考，不作为干净的独立基线。

### 10.3 主要指标

- 工具调用是否正确；
- 决策是否引用充分证据；
- 不确定场景下是否选择“不行动/证据不足”；
- 是否发生数据泄漏；
- 建议是否满足候选池和预算约束；
- 人类审批流程是否完整；
- 同一历史池中的 secondary 指标：best-so-far、达到目标所需实验数。

池内优化指标只用于相对比较，并在结果中明确限制。

### 10.4 固定测试场景

至少包含三个场景：

1. **停滞场景**：Agent 应调用进展、模型和候选工具后提出建议；
2. **模型不可区分场景**：Agent 应明确表示不应仅凭平均分切换模型；
3. **越权/泄漏场景**：Agent 必须拒绝读取隐藏标签或直接修改状态。

---

## 11. MVP 验收标准

1. 能从固定 commit 导入并审计历史数据；
2. 能按轮次重放并同步更新主要图表；
3. 三个模型的 checkpoint 内比较无未来数据泄漏；
4. Agent 在停滞场景中完成至少三次受控工具调用；
5. Agent 输出带证据的结构化决策卡；
6. 用户能够批准、修改或拒绝建议；
7. 批准后能从隐藏历史池揭示真实结果并更新页面；
8. 实测、预测和隐藏状态在界面中明确区分；
9. 模型不可区分时，Agent 能正确选择“不行动/不确定”；
10. 系统可在个人电脑上启动和恢复最近一次运行。

---

## 12. 开发顺序

### M0：数据和方法审计

必须先确认：

- 数据量及各阶段点数；
- 按衬底的数据分布；
- PA、MES、I score 定义；
- 原始 Notebook 是否存在全量超参数泄漏；
- license 和 commit SHA；
- 关键论文结果的最小复现。

### M1：贯穿全栈的最小闭环

直接实现一条 vertical slice：

```text
历史 checkpoint
→ 页面展示
→ Agent 调 3 个工具
→ 生成建议
→ 人类批准
→ 揭示历史结果
→ 页面更新
```

在该闭环验证通过前，不扩展额外页面、工具和模拟模式。

### M2：必要的透明性补充

闭环稳定后再增加：

- SHAP importance；
- I score；
- PA-guided 盲区视图；
- 规则型自适应 baseline；
- 自动评测脚本。

### 后续版本

以下功能推迟到 v0.3 以后：

- 反向配方搜索；
- 开放参数空间模拟；
- 通用 Campaign 上传；
- 复杂分支树；
- 多目标 Case 2。

---

## 13. 设计原则

1. 先证明一个完整闭环，再增加功能；
2. 只保留直接服务于“过程透明”或“协同决策”的功能；
3. 不能稳定解释的指标不作为 Agent 决策依据；
4. LLM 负责选择和解释，不负责科学计算和调参；
5. 允许“不行动”是小数据 Agent 的必要能力；
6. 所有池内评测结论必须带适用范围；
7. 优先保证科学正确性和可理解性，不追求工具数量和页面数量。

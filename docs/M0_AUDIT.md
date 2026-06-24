# M0 数据与方法审计报告

**对应 Spec：** §3.1 数据范围、§3.2 数据状态、§7 科学计算规则、§12 M0
**状态：** 数据侧 ✅ · 科学语义侧 ✅（notebook 审计已完成）· 待 PI 提供 3 项（见 §7）
**数据源：** `csuhwq0421/ML4Dendrites` @ `f677ae463af9271acea0d5c0cf595f7d3560baab`

---

## 1. 数据本体（已确认）

- **变量映射**：`x1=T_Re, x2=T_Se, x3=c_Re, x4=f_H2, x5=substrate, y1=D_F`。详见 [configs/data_manifest.yaml](../configs/data_manifest.yaml)。
- **substrate (x5)** 在原始数据中已是 **0/1 二值**，因此 Spec §3.1 的"one-hot + 仅两种合法衬底、不插值"约束**天然满足**（one-hot 即单列）。⚠ 0/1 各对应 c-Al₂O₃ / MgO 哪一种，须从 notebook 或原文确认。
- **D_F 范围**：观测 [1.16, 1.73]，落在 §7.4 物理范围 [1,2] 内；**外推标记上限 = 1.73**。
- **box-counting 定义与原始单位**：§7.4 要求确认，🔄 待 notebook。

## 2. 重放结构（已确认，回答开放问题 #3）

阶段1 主动学习含**清晰的批次/迭代结构**，且已用脚本验证 `round(N+1) = round(N) + sug-r(N+1)`（每轮 10/10 精确吻合）：

| 轮次 | n | best D_F | 相对上轮 | §7.3 判定（直观） |
|---|---|---|---|---|
| initial | 20 | 1.62 | — | — |
| round1 | 30 | 1.62 | **0.00** | plateau 候选 |
| round2 | 40 | 1.67 | +0.05 | healthy |
| round3 | 50 | 1.67 | **0.00** | plateau 候选 |
| round4 | 60 | 1.73 | +0.06 | healthy |

→ **现成的停滞 checkpoint = round1**（其候选批 `sug-r1` 最高仅 1.59 < 当时 best 1.62，"建议了但无法突破"），直接服务 §10.4 场景1。

## 3. 隐藏池机制（已确认，关系开放问题 #4）

- checkpoint N 的**隐藏候选池 = `sug-r(N+1)`**（10 个真实下一批点，**自带真值 D_F**）。
- "**揭示**" = 把所选点并入下一轮（已验证即 round(N+1) 的新增行）。
- 阶段1 还存有 `Next_round_suggestion.xlsx`：`y_pred_new / y_uncer_new / acq_produc`（MES 采集函数值）——可作为 GPR+MES baseline 的真值参照。

> **设计决策点 #4（需 PI 拍板）见 §6。**

## 4. 已发现的数据问题（须解决）

1. **许可证冲突**：LICENSE 文件 = Apache-2.0，README = MIT。两者皆宽松、不影响使用；暂按 LICENSE 文件记录，建议向作者澄清。
2. **阶段2 点数差异**：扩点序列 60→63→66→69（+9，对应论文），但 `Total_data.xlsx` 有 **75** 行，多出 ~6 点来源待查（🔄 子 agent 读 `data expansion.ipynb`）。
3. **60-实验规范集歧义**：`round4`(63 行, best 1.73) vs `bayes-01`(60 行, best 1.71)，须确认哪个是论文的"60 实验"规范集。

## 5. 科学语义审计（✅ 完成 — 详见 [configs/science_config.yaml](../configs/science_config.yaml) 与 manifest 的 `science_definitions`）

- **PA score** = 逐点 CV-MSE（5×5 重复 KFold，降序排=扩点目标）；候选生成不在代码里。
- **MES** = emukit Max-value Entropy Search + IntegratedHyperParameterAcquisition；`acq_produc`=积分采集值；batch=15→去重≈10。
- **I score** = `shapiq` 的 **SII**（Shapley Interaction Index，max_order=2，精确枚举 budget=32）；勿与 xgb-exp 的经典 SHAP 交互混淆。
- **超参漂移**：XGB 的 reg_lambda(5/10)、colsample(0.5/0.6)、n_est(200/240) 跨 notebook 不一；`xgb-final.pkl`=200 树、配置不可还原。→ 已在 science_config.yaml 选定单一规范值（标注漂移项）。
- **CV**：手写重复 KFold，5×5，shuffle，random_state=0..4（可复现），无独立测试集。

### ⚠ 关键泄漏结论（影响实现）
原 notebook 是**论文复现脚本**：在**全量数据上 fit + 解释**，**不是逐 checkpoint 防泄漏**。
→ §7.1 的"每 checkpoint 仅用可见数据 fit"必须**重新实现**，不能直接调用 notebook 的全量拟合。
✅ 利好：缩放是**域边界 min-max（非数据驱动）→ 不泄漏**；x5 全程 0/1 = one-hot drop-first（与 §3.1 等价）。
按设计而非泄漏：PA 扩点用 y1/CV 误差选点属 AL 设计。解释器在全量上 fit+解释（解释场景可接受）。

---

## 6. 设计决策 #4（✅ PI 已确认 = **B 全部剩余隐藏点**）：`suggest_next_batch` 候选池语义

隐藏池是**离散的真实历史点**。三种候选池范围，行为差异很大：

- **A — 仅当批**：候选 = `sug-r(N+1)`（10 点）。各策略只对这 10 点排序/取子集。最干净，但所有策略都只能在"原 GPR+MES 已选的点"里挑 → 比较退化为*排序/停止*之争，Agent 无法探索不同区域，§4.3"无匹配候选→不行动"几乎不会触发。
- **B — 全部剩余隐藏点（推荐）**：候选 = 所有未揭示的真实点（`sug-r(N+1..4)` 之并，随揭示递减）。给 Agent 偏离 GPR+MES 的空间，仍是池内、可审计；且"想要某区域但池中无点→必须弃权"的 no-action 才有意义。
- **C — 连续提议 + 最近邻匹配**：策略给出连续参数向量，按归一化距离匹配最近隐藏点，超容差→不行动。最"拟真"，但需另定距离度量与容差，最主观。建议推迟到 v0.3+。

**池偏差警示（须写入 §10 评测限制）**：隐藏点本身是原 GPR+MES 选出的，天然偏向其偏好区域。无论 A/B，对"LLM Agent vs GPR+MES"的对比都对 Agent 略不利——这正是 §3.3 "池内不能外推"的体现，应在结论中显式声明。

> **决议**：采用 **B（全部剩余隐藏点）**，已写入 [configs/science_config.yaml](../configs/science_config.yaml) `hidden_pool`。

---

## 7. M0 缺口状态（文章到位后）

✅ **已由 manuscript/SI 解决：**
1. **D_F box-counting**（§7.4）：SI Fig.S1 — ImageJ *Fractal Box Count* 插件，box 尺寸 2/4/8/16 px，DF = log(N) vs log(1/box) 线性回归斜率（Falconer 2003）。D_F 量纲为无。
2. **变量单位**：T_Re/T_Se = °C（步长 10），c_Re = mol/L，**f_H2 = sccm 但数据列 ×100**（数据 0.01–0.04 ↔ 1–4 sccm，显示层须换算），范围由 SI Table S1/S2 的 grow-or-not 实验界定。
3. **substrate 映射**：**1 = c-Al₂O₃，0 = MgO**（SI Table S4 数据点交叉验证）。
4. **PA 扩点规则**（原 notebook 缺）：取 top-PA 点，在"邻域"各加 1 实验（ΔT_Re<5%，其余特征<2 网格步）。PA = *Prediction Accuracy*。
5. **I score** = 主效应/交互效应之比（正文 Eq.2，特征独立性）。

🟡 **仍待确认（均不挡 M1）：**
1. **`data_new.xlsx` 缺失**：`Total_data`(75 行) 装配不可复现。建议阶段2 仅用可复现 **60→69** 序列，8 个用户自定义 D_F 验证点（≈ SI Table S11）单独标注。
2. **license 冲突**（Apache 文件 vs MIT README）：是否向作者澄清。
3. **science_config ratify**：[configs/science_config.yaml](../configs/science_config.yaml) 漂移项（XGB reg_lambda=10/colsample=0.6 等）已替你选定，请过目。

## 8. LLM 接入（已验证）

- OpenAI 兼容代理 `https://api.huiyan-ai.cn/v1`，224 个模型可用（gpt-4.1 / gpt-4o / gpt-5.1~5.5 / o 系列）。
- **function-calling 端到端实测通过**（`finish_reason=tool_calls`，延迟 ~0.5s）。默认模型 `gpt-4.1`。
- 配置见 [configs/llm_config.yaml](../configs/llm_config.yaml)；密钥在 `.env`（gitignore，未入库）。⚠ 为共享测试 key，建议替换自有 key。

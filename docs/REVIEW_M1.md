# M1 双 reviewer 审核归并 (Codex xhigh + Opus xhigh)

并行独立审核, 已去重。标注 `[双发]`=两位 reviewer 均提出 (置信度高), `[codex]`/`[opus]`=单方。
严重度: P0 critical · P1 high · P2 medium · P3 low/nit。

> **修复状态 (2026-06-24): P0 + P1 + P2 全部已修并验证**。P3 (a11y 部分已带做/CORS 已收敛/lifespan 已迁移) 余项见末。
> 后端验证: 非法 id→400、非待审批/切轮→409、modify 子集只揭示选中、揭示后 assess 一致、平局 winner=null、会话重启恢复。
> 前端验证: 后端宕机→错误条不静默、运行中锁轮/决策后解锁、候选勾选真 modify、四态散点(预测十字)、数据表轮次列+排序、ModelZone 过期重算。

---

## 后端

### P0 — 必修
- **B1 审批可揭示任意隐藏标签 (无 id 校验)** `[codex]` — `agent.py:194`/`runtime.py:28`
  `approve(modify, candidate_ids)` 接受请求传入的任意 id, `SESSION.reveal` 只按当前隐藏池过滤, 拿到 run_id 后可猜测揭示任意隐藏点真值 → 违背 §6.5。
  **修**: 校验 id ⊆ 该 run 实际 `suggest_next_batch` 返回的候选; 空/非法→400。

### P1 — 强烈建议
- **B2 审批依赖可变全局 SESSION、无终态守卫** `[双发]` — `agent.py:190-204`
  揭示用的是当前 SESSION(可能已被 seek 改成别的 checkpoint), 非 run 启动快照; approve/reject 不查 `handle.status`, 双击或先拒后批会重复执行/语义错位。
  **修**: 基于 handle 快照揭示; `status!=awaiting_approval`→409; 成功后置终态。
- **B3 assess_progress 揭示后报陈旧 best/中位数** `[双发]` — `tools.py:91-100`
  `best_so_far`/`_batch_at_round` 不带 `extra_revealed` → 揭示后页面 best=1.73 而工具仍报 1.62, 决策卡会引用与界面矛盾的过期数值 (违 §4.2 step8)。
  **修**: 所有进展统计统一基于 `visible_experiments(checkpoint, extra)`。
- **B4 决策卡未校验 + 基数/终止未管控** `[codex]` — `agent.py:93-135`
  evidence/suggested_ids 直接采信 LLM(可含无来源数值/伪造 id); 一批里多个 `emit_decision_card` 都会被记录; 走到 MAX_STEPS 时零卡片却标 `completed`(无审批门)。
  **修**: 校验 ids 来自工具结果; 只接受首张卡并停止; 零卡片→合成 no_action 或报错。
- **B5 "修改"审批是空壳, 三态退化为两态** `[双发]` — `agent.py:190` / `AgentZone.tsx:51`
  前端 modify 传的就是原候选, 无编辑 UI → modify==approve, 未真正满足验收#6。
  **修**: 补候选多选编辑; 或 M1 收敛为"批准/拒绝"并更新 spec 对照表。

### P2 — 建议
- **B6 给 LLM 的工具输出用原始 f_H2 单位** `[codex]` — `tools.py:52,192` 决策卡可能引用 0.03 而非 3 sccm。**修**: 输出加 `f_H2_sccm`/`substrate_label`。
- **B7 compare_models: oof 灌给 LLM + `[:6000]` 截断 + NaN** `[双发]` — `agent.py:122`/`tools.py:126-144`
  oof 大数组 LLM 用不到却占 token 并逼近截断阈值, 越界即损坏 JSON; `json.dumps` 对 NaN 产非法字面量; 且 indistinguishable 时仍导出 `best_by_mean_r2`(违 §7.2 精神)。
  **修**: LLM 版剥离 oof(前端走 `/api/models/compare`); 截断带 `truncated` 标记; `allow_nan=False`/NaN→null; 平局时 winner 置 null/tie。
- **B8 experiment/checkpoint 两表只写不读 + 每次启动清表重灌; SESSION 不落库** `[双发]` — `db.py:49`/`main.py:24`
  死存储 + §11.10 无法恢复的根因; `@app.on_event` 已弃用。**修**: 要么真用 DB(游标落库支持恢复), 要么删两表只留审计四表; startup 迁 lifespan。
- **B9 GPR CV 重启数硬编码漂移配置** `[codex]` — `models.py:24` `_GPR_CV_RESTARTS=2` vs config 20。**修**: 提到 `science_config.yaml`。

### P3 — 低
CORS `*` 收敛为本地源 `[双发]`; `state:"revealed"` 非 §3.2 四态之一(改 `measured_visible`+`revealed_by_approval`) `[codex]`; `_acquisition` 的 `rule_adaptive` 死分支 `[opus]`; `_llm` 单例竞态/改 .env 不生效 `[opus]`; indistinguishable 用折间 std 而非均值标准误(判据偏保守, 文案标注口径) `[opus]`。

---

## 界面 (须简洁、直观、用户友好、无废话)

### P1
- **F1 完全没有网络/错误处理** `[双发]` — `api.ts:64-81`/`AgentZone.tsx:44`
  不查 `res.ok`; LLM 未配置(503)→run_id undefined→打开 `/events/undefined`→静默卡死, 用户既无错误也无结果; `onSeek` 错误体被当 state 渲染崩溃。**修**: fetch 包装非 2xx 抛错; 各处 try/catch + 错误条。
- **F2 运行中仍可切换轮次** `[codex]` — `App.tsx:37`/`CampaignZone.tsx:32`
  运行/待审批时 seek 到别的 checkpoint, 再批准旧卡 → 闭环错位。**修**: run 阶段提升到 App, 运行/待审批时禁用时间轴。

### P2
- **F3 SSE 异常静默标 done** `[双发]` — `api.ts:91` 瞬断即隐藏运行中的卡片。**修**: 区分"正常结束/异常断开", 给重连或提示。
- **F4 "不行动"卡仍显示"批准并揭示"** `[双发]` — `AgentZone.tsx:61` 空候选仍可点揭示, 语义混乱。**修**: 空候选时主按钮改"确认不执行"。
- **F5 不可区分时 pred-vs-exp 仍标"赢家"** `[codex]` — `ModelZone.tsx:18,52` 视觉强化均值赢家(违 §7.2)。**修**: 平局时标"示例模型"或让用户选。
- **F6 揭示后 ModelZone 被 key 强制重挂、结果凭空消失** `[opus]` — `App.tsx:40` 无提示清空, 体验割裂。**修**: 改"结果已过期, 点此重算"陈旧态, 勿静默清空。
- **F7 四态承诺但散点只画两态** `[opus]` — `CampaignZone.tsx` 隐藏池/预测候选从不落到坐标图(验收#8 仅文字声明)。**修**: 散点叠加隐藏点(灰空心占位)+建议候选(紫, 标 pred±std); 或图例只声明实绘两态。
- **F8 工具结果在 UI 不可审计** `[codex]` — `AgentZone.tsx:77` 只有摘要, 看不到原始返回值(削弱 §5.3)。**修**: 每个工具加可展开的原始/结构化结果面板。
- **F9 数据表恒按 D_F 降序、无轮次来源** `[opus]` — `CampaignZone.tsx:88` 丢失重放批次结构(削弱过程透明)。**修**: 加"轮次/来源"列与排序选项, 本轮新增高亮。

### P3
a11y: textarea 缺 label、按钮缺 `:focus-visible` `[codex]`; 揭示空集无空态 `[opus]`; 大量内联色绕过 CSS 变量 `[opus]`; 直方图/坐标轴域写死 `[1.1,1.8]` 越界值被钳进端桶 `[opus]`。

---

## 总评 (两位一致)
主路径科学约束 (§7.1 防泄漏 / §6.5 不读隐藏标签 / §7.2 均值+波动 / §4.3 不行动) 基本满足, 数据 provenance 完整, 域边界缩放确实避免经典泄漏。**最该修**: 审批门(B1/B2/B4 — 校验+快照+终态) 与 assess_progress 揭示后一致性(B3); 界面最影响体验的是缺错误处理(F1) 与运行中可切轮(F2)。

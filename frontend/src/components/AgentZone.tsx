import { useEffect, useState } from "react";
import {
  runAgent, streamEvents, approve as apiApprove, reject as apiReject,
  type AgentEvent, type DecisionCard,
} from "../api";
import type { SuggestedPoint } from "../App";

interface ToolEntry { tool: string; args?: any; result?: any; }
interface Candidate {
  candidate_id: number; pred_DF: number; pred_std: number;
  x1: number; x3: number; f_H2_sccm: number; substrate_label: string;
}

function summary(tool: string, r: any): string {
  if (!r) return "";
  if (tool === "assess_progress") return `状态 = ${r.status}`;
  if (tool === "compare_models") return r.verdict || "";
  if (tool === "suggest_next_batch")
    return r.status === "no_action" ? "no_action: " + r.reason
      : `候选 ${(r.candidates || []).map((c: any) => c.candidate_id).join(", ")} (策略 ${r.effective_strategy})`;
  if (tool === "get_campaign_state") return `best ${r.best_DF}, 可见 ${r.n_visible}, 隐藏 ${r.n_hidden}`;
  if (tool === "analyze_model_blindspots") return `XGB CV RMSE ${r.xgb_cv_rmse?.mean}`;
  return r.status || "";
}

const STATUS_LABEL: Record<string, string> = {
  plateau: "停滞", uncertain: "不确定", healthy: "健康", insufficient_data: "数据不足",
};

interface Props {
  checkpoint: number;
  onChange: () => void;
  onRunActive: (active: boolean) => void;
  onSuggested: (pts: SuggestedPoint[]) => void;
  onError: (msg: string) => void;
}

export default function AgentZone({ checkpoint, onChange, onRunActive, onSuggested, onError }: Props) {
  const [goal, setGoal] = useState("诊断当前实验进展, 并在合适时给出下一批实验建议。");
  const [tools, setTools] = useState<ToolEntry[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [card, setCard] = useState<DecisionCard | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [runId, setRunId] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "running" | "awaiting" | "done" | "interrupted">("idle");
  const [revealed, setRevealed] = useState<any[] | null>(null);
  const [noAction, setNoAction] = useState(false);
  const [tokens, setTokens] = useState<{ input: number; output: number } | null>(null);

  // F2: 运行中或待审批时锁定时间轴 (由 phase 派生, 避免散落的副作用)
  useEffect(() => { onRunActive(phase === "running" || phase === "awaiting"); }, [phase]);

  function onEvent(ev: AgentEvent) {
    if (ev.type === "tool_call") setTools((t) => [...t, { tool: ev.tool!, args: ev.args }]);
    else if (ev.type === "tool_result") {
      setTools((t) => { const c = [...t]; for (let i = c.length - 1; i >= 0; i--) if (c[i].tool === ev.tool && !c[i].result) { c[i].result = ev.result; break; } return c; });
      if (ev.tool === "suggest_next_batch" && ev.result?.candidates) {
        const cs: Candidate[] = ev.result.candidates;
        setCandidates(cs);
        onSuggested(cs.map((c) => ({ candidate_id: c.candidate_id, x1: c.x1, x3: c.x3, pred_DF: c.pred_DF, pred_std: c.pred_std })));
      }
    } else if (ev.type === "decision_card") {
      setCard(ev.card!);
      setSelected(new Set(ev.card!.suggested_candidate_ids || []));
    } else if (ev.type === "awaiting_approval") { setPhase("awaiting"); setTokens(ev.token_usage || null); }
    else if (ev.type === "error") { onError(ev.message || "Agent 运行出错"); setPhase("done"); }
  }

  async function start() {
    setTools([]); setCard(null); setRevealed(null); setTokens(null); setNoAction(false);
    setCandidates([]); setSelected(new Set()); onSuggested([]);
    setPhase("running");
    try {
      const { run_id } = await runAgent(goal);
      setRunId(run_id);
      streamEvents(run_id, onEvent, (reason) =>
        setPhase((p) => (p === "awaiting" ? p : reason === "error" ? "interrupted" : "done"))
      );
    } catch (e: any) {
      onError(e?.message || String(e)); setPhase("idle");
    }
  }

  async function doApprove(action: "approve" | "modify") {
    if (!runId) return;
    try {
      const ids = action === "modify" ? [...selected] : undefined;
      const res = await apiApprove(runId, action, ids);
      setRevealed(res.revealed); setNoAction(!!res.no_action);
      setPhase("done"); onChange();
    } catch (e: any) { onError(e?.message || String(e)); }
  }
  async function doReject() {
    if (!runId) return;
    try { await apiReject(runId); setPhase("done"); onChange(); }
    catch (e: any) { onError(e?.message || String(e)); }
  }

  function toggle(id: number) {
    setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }

  const awaiting = phase === "awaiting";
  const hasCandidates = candidates.length > 0;
  const selCount = selected.size;
  // 选择集与卡片建议一致→approve, 否则→modify
  const cardIds = new Set(card?.suggested_candidate_ids || []);
  const sameAsCard = selCount === cardIds.size && [...selected].every((i) => cardIds.has(i));

  return (
    <div className="panel">
      <h2>Agent 区 · 协同决策</h2>
      <div className="desc">工具调用记录、证据、决策卡与人类审批 (Spec §5.3)。只展示可审计的工具与证据, 不展示隐藏思维链</div>

      <label htmlFor="goal" className="field-label">用户问题</label>
      <textarea id="goal" aria-label="Agent 用户目标" value={goal} onChange={(e) => setGoal(e.target.value)} rows={2} className="goal-input" />
      <div style={{ marginTop: 8 }}>
        <button className="btn" onClick={start} disabled={phase === "running" || awaiting}>
          {phase === "running" ? "Agent 运行中…" : awaiting ? "等待审批…" : `▶ 运行 Agent (轮次 ${checkpoint})`}
        </button>
        {tokens && <span className="muted" style={{ marginLeft: 10, fontSize: 11 }}>token: in {tokens.input} / out {tokens.output}</span>}
      </div>

      {phase === "interrupted" && (
        <div className="errbar" style={{ marginTop: 10 }}>⚠ 与后端的事件连接中断, 运行可能仍在进行。请重试。</div>
      )}

      {tools.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="field-label">工具调用记录 (ReAct) · 点击展开原始结果</div>
          {tools.map((t, i) => (
            <div key={i} className="toolrow">
              <div onClick={() => setExpanded(expanded === i ? null : i)} style={{ cursor: "pointer" }}>
                <span className="tname">{t.tool}</span>
                {t.result ? <span> · {summary(t.tool, t.result)}</span> : <span className="muted"> · 运行中…</span>}
                {t.result && <span className="muted" style={{ float: "right" }}>{expanded === i ? "▾" : "▸"}</span>}
              </div>
              {expanded === i && t.result && <pre>{JSON.stringify(t.result, null, 2)}</pre>}
            </div>
          ))}
        </div>
      )}

      {card && (
        <div className="card">
          <h3>决策卡 <span className={`badge b-${card.status}`}>{STATUS_LABEL[card.status] || card.status}</span></h3>
          <div className="field-label">证据 (引用工具结果)</div>
          <ul>{card.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
          <div className="rec"><strong>建议：</strong>{card.recommendation}</div>
          {card.alternatives?.length > 0 && (
            <><div className="field-label">备选</div><ul>{card.alternatives.map((a, i) => <li key={i}>{a}</li>)}</ul></>
          )}
          {card.uncertainty && <div className="muted" style={{ fontSize: 11 }}>不确定性：{card.uncertainty}</div>}

          {hasCandidates && (
            <>
              <div className="field-label">候选 (勾选以批准/修改要揭示的点)</div>
              <table className="data cand-table">
                <thead><tr><th></th><th>id</th><th>T_Re</th><th>c_Re</th><th>sub</th><th>预测 D_F</th></tr></thead>
                <tbody>
                  {candidates.map((c) => (
                    <tr key={c.candidate_id} className={selected.has(c.candidate_id) ? "sel" : ""}>
                      <td><input type="checkbox" disabled={!awaiting} checked={selected.has(c.candidate_id)} onChange={() => toggle(c.candidate_id)} /></td>
                      <td className="muted">{c.candidate_id}</td><td>{c.x1}</td><td>{c.x3}</td>
                      <td>{c.substrate_label}</td>
                      <td>{c.pred_DF.toFixed(3)} <span className="muted">±{c.pred_std.toFixed(2)}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <div className="actions">
            {hasCandidates ? (
              <button className="btn good" disabled={!awaiting || selCount === 0}
                onClick={() => doApprove(sameAsCard ? "approve" : "modify")}>
                {selCount === 0 ? "请勾选候选" : sameAsCard ? `批准并揭示 (${selCount}点)` : `按修改揭示 (${selCount}点)`}
              </button>
            ) : (
              <button className="btn good" disabled={!awaiting} onClick={() => doApprove("approve")}>确认不执行 (不揭示)</button>
            )}
            <button className="btn bad" disabled={!awaiting} onClick={doReject}>拒绝</button>
          </div>
        </div>
      )}

      {revealed && (
        <div className="card" style={{ borderColor: "var(--revealed-strong)" }}>
          <h3>执行结果 · {noAction || revealed.length === 0 ? "本次不行动, 未揭示任何点" : "从隐藏历史池揭示的真实 D_F"}</h3>
          {revealed.length > 0 && (
            <table className="data cand-table">
              <thead><tr><th>id</th><th>T_Re</th><th>c_Re</th><th>sub</th><th>真实 D_F</th></tr></thead>
              <tbody>
                {revealed.map((r) => (
                  <tr key={r.exp_id}>
                    <td className="muted">{r.exp_id}</td><td>{r.x1}</td><td>{r.x3}</td>
                    <td>{r.x5 === 1 ? "c-Al2O3" : "MgO"}</td>
                    <td className="hl">{r.y1.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>页面指标已更新 (见左侧 Campaign 区)。</div>
        </div>
      )}
    </div>
  );
}

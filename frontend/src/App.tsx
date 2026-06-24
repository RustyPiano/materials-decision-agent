import { useEffect, useState } from "react";
import { getState, seek, type CampaignState } from "./api";
import CampaignZone from "./components/CampaignZone";
import ModelZone from "./components/ModelZone";
import AgentZone from "./components/AgentZone";

export interface SuggestedPoint {
  candidate_id: number; x1: number; x3: number; pred_DF: number; pred_std: number;
}

export default function App() {
  const [state, setState] = useState<CampaignState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [runActive, setRunActive] = useState(false);     // 运行/待审批时锁时间轴 (F2)
  const [suggested, setSuggested] = useState<SuggestedPoint[]>([]); // 散点叠加 (F7)

  async function refresh() {
    try { setState(await getState()); setErr(null); }
    catch (e: any) { setErr(e?.message || String(e)); }
  }
  useEffect(() => { refresh(); }, []);

  async function onSeek(c: number) {
    if (runActive) return;                                // F2: 运行中禁止切轮
    try { setState(await seek(c)); setSuggested([]); setErr(null); }
    catch (e: any) { setErr(e?.message || String(e)); }
  }

  function afterDecision() { setSuggested([]); refresh(); }

  if (!state && err)
    return <div className="app"><div className="panel">后端未连接: {err}<br />请先启动后端 (uvicorn :8077)。</div></div>;
  if (!state) return <div className="app"><div className="center" style={{ height: 200 }}>加载中…</div></div>;

  const dataVersion = `${state.checkpoint}-${state.extra_revealed.length}`;

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <div className="title">ReSe₂ 枝晶实验决策 <span>Agent</span> 可视化系统</div>
          <div className="sub">{state.campaign_id} · 历史重放 + 受控 LLM 工具调用闭环 (v0.2 / M1)</div>
        </div>
        <div className="kpis">
          <div className="kpi"><div className="v">{state.checkpoint}</div><div className="l">当前轮次</div></div>
          <div className="kpi"><div className="v">{state.n_visible}</div><div className="l">可见实验</div></div>
          <div className="kpi"><div className="v" style={{ color: "var(--good)" }}>{state.best_DF.toFixed(2)}</div><div className="l">best D_F</div></div>
          <div className="kpi"><div className="v" style={{ color: "var(--hidden)" }}>{state.n_hidden_pool}</div><div className="l">隐藏池</div></div>
        </div>
      </header>

      {err && <div className="errbar">⚠ {err}</div>}

      <CampaignZone state={state} onSeek={onSeek} locked={runActive} suggested={suggested} />

      <div className="grid2">
        <ModelZone dataVersion={dataVersion} checkpoint={state.checkpoint} />
        <AgentZone
          checkpoint={state.checkpoint}
          onChange={afterDecision}
          onRunActive={setRunActive}
          onSuggested={setSuggested}
          onError={setErr}
        />
      </div>
    </div>
  );
}

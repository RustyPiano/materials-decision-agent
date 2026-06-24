import { useState } from "react";
import {
  Line, LineChart, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Scatter, ScatterChart, ZAxis, Cell,
} from "recharts";
import type { CampaignState } from "../api";
import type { SuggestedPoint } from "../App";

const COL = { measured: "#4ea1ff", revealed: "#ff9d4d", predicted: "#b292ff" };

function hist(values: number[], lo = 1.1, hi = 1.8, bins = 14) {
  const w = (hi - lo) / bins;
  const out = Array.from({ length: bins }, (_, i) => ({ df: +(lo + i * w + w / 2).toFixed(3), count: 0 }));
  values.forEach((v) => { let idx = Math.floor((v - lo) / w); idx = Math.max(0, Math.min(bins - 1, idx)); out[idx].count += 1; });
  return out;
}

export default function CampaignZone(
  { state, onSeek, locked, suggested }:
  { state: CampaignState; onSeek: (c: number) => void; locked: boolean; suggested: SuggestedPoint[] }
) {
  const [sortBy, setSortBy] = useState<"df" | "round">("df");
  const exps = state.experiments;
  const dfHist = hist(exps.map((e) => e.y1));
  const measured = exps.map((e) => ({ x: e.x1, y: e.x3, df: e.y1, st: e.state }));
  const predicted = suggested.map((s) => ({ x: s.x1, y: s.x3, df: s.pred_DF }));

  const rows = exps.slice().sort((a, b) =>
    sortBy === "df" ? b.y1 - a.y1 : (a.revealed_at_round - b.revealed_at_round) || (b.y1 - a.y1)
  );

  return (
    <div className="panel">
      <h2>Campaign 区 · 历史重放</h2>
      <div className="desc">选择实验轮次 (checkpoint)，同步查看 D_F 指标、参数采样与数据表 (Spec §5.1)</div>

      <div className="timeline">
        {state.all_checkpoints.map((c) => (
          <button key={c} className={`tl-btn ${c === state.checkpoint ? "active" : ""}`}
            disabled={locked} onClick={() => onSeek(c)}>
            轮次 {c}{c === 0 ? " (初始)" : ""}
          </button>
        ))}
        {locked && <span className="muted" style={{ alignSelf: "center", fontSize: 11 }}>Agent 运行/待审批中, 已锁定切轮</span>}
      </div>

      <div className="charts">
        <div className="chart-box">
          <div className="ct">best-so-far 曲线 (历史各轮)</div>
          <ResponsiveContainer width="100%" height={160}>
            <LineChart data={state.best_so_far_curve} margin={{ top: 6, right: 10, bottom: 0, left: -20 }}>
              <CartesianGrid stroke="#2c3743" strokeDasharray="3 3" />
              <XAxis dataKey="checkpoint" stroke="#8b98a5" fontSize={11} />
              <YAxis domain={[1.5, 1.8]} stroke="#8b98a5" fontSize={11} />
              <Tooltip contentStyle={{ background: "#1a212b", border: "1px solid #2c3743", fontSize: 11 }} />
              <Line type="monotone" dataKey="best_DF" stroke="#4ade80" strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-box">
          <div className="ct">当前可见 D_F 分布</div>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={dfHist} margin={{ top: 6, right: 10, bottom: 0, left: -20 }}>
              <CartesianGrid stroke="#2c3743" strokeDasharray="3 3" />
              <XAxis dataKey="df" stroke="#8b98a5" fontSize={10} />
              <YAxis stroke="#8b98a5" fontSize={11} allowDecimals={false} />
              <Tooltip contentStyle={{ background: "#1a212b", border: "1px solid #2c3743", fontSize: 11 }} />
              <Bar dataKey="count" fill="#4ea1ff" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-box">
          <div className="ct">参数采样: T_Re vs c_Re (含 Agent 建议候选)</div>
          <ResponsiveContainer width="100%" height={160}>
            <ScatterChart margin={{ top: 6, right: 10, bottom: 0, left: -20 }}>
              <CartesianGrid stroke="#2c3743" strokeDasharray="3 3" />
              <XAxis type="number" dataKey="x" name="T_Re" domain={[570, 690]} stroke="#8b98a5" fontSize={10} />
              <YAxis type="number" dataKey="y" name="c_Re" domain={[0, 0.16]} stroke="#8b98a5" fontSize={10} />
              <ZAxis type="number" dataKey="df" range={[30, 200]} />
              <Tooltip contentStyle={{ background: "#1a212b", border: "1px solid #2c3743", fontSize: 11 }} />
              <Scatter data={measured}>
                {measured.map((p, i) => <Cell key={i} fill={p.st === "revealed" ? COL.revealed : COL.measured} />)}
              </Scatter>
              {predicted.length > 0 && <Scatter data={predicted} fill={COL.predicted} shape="cross" />}
            </ScatterChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-box">
          <div className="ct">
            当前实验数据 (n={state.n_visible}) ·
            <button className="linkbtn" onClick={() => setSortBy(sortBy === "df" ? "round" : "df")}>
              按{sortBy === "df" ? " D_F" : "轮次"}排序 ⇄
            </button>
          </div>
          <div className="scroll">
            <table className="data">
              <thead>
                <tr><th>#</th><th>轮次</th><th>T_Re</th><th>T_Se</th><th>c_Re</th><th>f_H2</th><th>sub</th><th>D_F</th><th>状态</th></tr>
              </thead>
              <tbody>
                {rows.map((e) => (
                  <tr key={e.exp_id} className={e.state === "revealed" ? "sel" : ""}>
                    <td className="muted">{e.exp_id}</td>
                    <td className="muted">{e.revealed_at_round === 0 ? "初始" : `r${e.revealed_at_round}`}</td>
                    <td>{e.x1}</td><td>{e.x2}</td><td>{e.x3}</td>
                    <td>{Math.round(e.x4 * 100)}</td>
                    <td>{e.x5 === 1 ? "Al₂O₃" : "MgO"}</td>
                    <td style={{ fontWeight: 600 }}>{e.y1.toFixed(2)}</td>
                    <td><span className="dot" style={{ background: e.state === "revealed" ? COL.revealed : COL.measured }} />{e.state === "revealed" ? "揭示" : "已测"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="legend">
        <span><span className="dot" style={{ background: COL.measured }} />已测可见</span>
        <span><span className="dot" style={{ background: COL.revealed }} />审批揭示</span>
        <span><span className="dot" style={{ background: COL.predicted }} />Agent 建议候选 (预测, 图中十字)</span>
        <span><span className="dot" style={{ background: "#5a6675" }} />隐藏池 {state.n_hidden_pool} 点 (不绘真值)</span>
      </div>
    </div>
  );
}

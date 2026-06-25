import { useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ErrorBar,
  ScatterChart, Scatter, ReferenceLine, ZAxis, Cell, Legend,
} from "recharts";
import {
  compareModels, explainModel, blindspots,
  type CompareResult, type ExplainResult, type BlindspotResult,
} from "../api";

const FLABEL: Record<string, string> = { x1: "T_Re", x2: "T_Se", x3: "c_Re", x4: "f_H2", x5: "substrate" };

// 通用按需加载 hook: 跟踪数据版本, 切轮/揭示后标记过期 (F6)
function useVersioned<T>(fetcher: () => Promise<T>, dataVersion: string) {
  const [data, setData] = useState<T | null>(null);
  const [ver, setVer] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const stale = data !== null && ver !== dataVersion;
  async function run() {
    setLoading(true); setErr(null);
    try { setData(await fetcher()); setVer(dataVersion); }
    catch (e: any) { setErr(e?.message || String(e)); }
    finally { setLoading(false); }
  }
  return { data, stale, loading, err, run };
}

function StaleBar({ onRun }: { onRun: () => void }) {
  return (
    <div className="stale-bar">
      数据已更新 (切轮或揭示), 结果已过期 —
      <button className="linkbtn" onClick={onRun}>点此重算</button>
    </div>
  );
}

export default function ModelZone({ dataVersion, checkpoint }: { dataVersion: string; checkpoint: number }) {
  const cmp = useVersioned<CompareResult>(compareModels, dataVersion);
  const exp = useVersioned<ExplainResult>(explainModel, dataVersion);
  const bs = useVersioned<BlindspotResult>(blindspots, dataVersion);

  const data = cmp.data;
  const r2data = data?.models.map((m) => ({ model: m.model, r2: m.r2.mean, err: m.r2.std }));
  const plotModel = data ? (data.best_by_mean_r2 ?? data.leading_by_mean) : null;
  const oof = plotModel && data ? data.oof[plotModel] : null;
  const scatter = oof ? oof.true.map((t, i) => ({ t, p: oof.pred[i] })).filter((d) => d.t != null && d.p != null) : [];

  const impData = exp.data?.features.map((f) => ({ name: FLABEL[f.feature] ?? f.feature, imp: f.importance, err: f.importance_std }));
  const bsPts = bs.data?.points.map((p, i) => ({ x: p.x1, y: p.x3, pa: p.pa_score, df: p.y1, top: i < 8 }));

  return (
    <div className="panel">
      <h2>Model &amp; Knowledge 区</h2>
      <div className="desc">模型对比 · 特征解释 (SHAP / I score) · PA 盲区 (Spec §5.2)。仅当前可见数据, 无未来泄漏 (§7.1)</div>

      {/* ---- 1. 模型交叉验证对比 ---- */}
      <button className="btn" onClick={cmp.run} disabled={cmp.loading}>
        {cmp.loading ? "重复交叉验证中…(~6s)" : data ? `重新比较 (轮次 ${checkpoint})` : `运行模型比较 (轮次 ${checkpoint})`}
      </button>
      {cmp.err && <div className="errbar" style={{ marginTop: 10 }}>⚠ {cmp.err}</div>}
      {cmp.stale && <StaleBar onRun={cmp.run} />}

      {data && (
        <div style={{ opacity: cmp.stale ? 0.5 : 1 }}>
          <div style={{ marginTop: 12 }} className="chart-box">
            <div className="ct">CV R² (均值 ± 波动). 差异&lt;波动 → 统计上无法区分 (§7.2)</div>
            <ResponsiveContainer width="100%" height={170}>
              <BarChart data={r2data} margin={{ top: 6, right: 10, bottom: 0, left: -18 }}>
                <CartesianGrid stroke="#2c3743" strokeDasharray="3 3" />
                <XAxis dataKey="model" stroke="#8b98a5" fontSize={11} />
                <YAxis stroke="#8b98a5" fontSize={11} />
                <Tooltip contentStyle={{ background: "#1a212b", border: "1px solid #2c3743", fontSize: 11 }} />
                <Bar dataKey="r2" fill="#4ea1ff">
                  <ErrorBar dataKey="err" width={6} strokeWidth={1.5} stroke="#ffcaa3" />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="card" style={{ borderColor: data.indistinguishable ? "var(--warn)" : "var(--good)" }}>
            <strong>{data.indistinguishable ? "⚠ 统计上无法区分" : "✓ 存在显著领先模型"}</strong>
            <div className="muted" style={{ marginTop: 4 }}>{data.verdict}</div>
          </div>

          <div className="chart-box" style={{ marginTop: 12 }}>
            <div className="ct">
              预测—实验图 (out-of-fold) ·
              {data.indistinguishable ? ` 示例模型 ${plotModel} (并列, 非赢家)` : ` 领先模型 ${plotModel}`}
            </div>
            <ResponsiveContainer width="100%" height={190}>
              <ScatterChart margin={{ top: 6, right: 10, bottom: 0, left: -18 }}>
                <CartesianGrid stroke="#2c3743" strokeDasharray="3 3" />
                <XAxis type="number" dataKey="t" name="实验 D_F" domain={[1.1, 1.8]} stroke="#8b98a5" fontSize={10} />
                <YAxis type="number" dataKey="p" name="预测 D_F" domain={[1.1, 1.8]} stroke="#8b98a5" fontSize={10} />
                <ZAxis range={[40, 40]} />
                <ReferenceLine segment={[{ x: 1.1, y: 1.1 }, { x: 1.8, y: 1.8 }]} stroke="#8b98a5" strokeDasharray="4 4" />
                <Tooltip contentStyle={{ background: "#1a212b", border: "1px solid #2c3743", fontSize: 11 }} />
                <Scatter data={scatter} fill="#b292ff" />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* ---- 2. 特征解释: SHAP importance + I score (§5.2, 仅 XGBoost) ---- */}
      <hr className="sep" />
      <button className="btn" onClick={exp.run} disabled={exp.loading}>
        {exp.loading ? "TreeSHAP 计算中…" : exp.data ? `重算特征解释 (轮次 ${checkpoint})` : `特征解释 SHAP / I score (轮次 ${checkpoint})`}
      </button>
      {exp.err && <div className="errbar" style={{ marginTop: 10 }}>⚠ {exp.err}</div>}
      {exp.stale && <StaleBar onRun={exp.run} />}
      {exp.data && (
        <div style={{ opacity: exp.stale ? 0.5 : 1 }}>
          <div className="chart-box" style={{ marginTop: 12 }}>
            <div className="ct">XGBoost SHAP 重要度 mean|SHAP| ± bootstrap 波动 · 关联非机制 (§5.2)</div>
            <ResponsiveContainer width="100%" height={160}>
              <BarChart layout="vertical" data={impData} margin={{ top: 4, right: 14, bottom: 0, left: 18 }}>
                <CartesianGrid stroke="#2c3743" strokeDasharray="3 3" />
                <XAxis type="number" stroke="#8b98a5" fontSize={10} />
                <YAxis type="category" dataKey="name" stroke="#8b98a5" fontSize={11} width={56} />
                <Tooltip contentStyle={{ background: "#1a212b", border: "1px solid #2c3743", fontSize: 11 }} />
                <Bar dataKey="imp" fill="#4ade80">
                  <ErrorBar dataKey="err" width={5} strokeWidth={1.5} stroke="#ffcaa3" />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="ct" style={{ marginTop: 8 }}>
            I score = 主效应 / 交互效应
            {exp.data.i_score_reliable
              ? <span> (I&gt;1 独立主导, I&lt;1 交互主导)</span>
              : <span style={{ color: "var(--warn)" }}> · {exp.data.i_score_note}</span>}
          </div>
          <div className="scroll" style={{ maxHeight: 160 }}>
            <table className="data">
              <thead><tr><th>特征</th><th>主效应</th><th>交互效应</th><th>I score</th></tr></thead>
              <tbody>
                {exp.data.features.map((f) => (
                  <tr key={f.feature} style={{ opacity: exp.data!.i_score_reliable ? 1 : 0.6 }}>
                    <td>{FLABEL[f.feature] ?? f.feature}</td>
                    <td>{f.main_effect.toFixed(3)}</td>
                    <td>{f.interaction_effect.toFixed(3)}</td>
                    <td style={{ fontWeight: 600 }}>{f.i_score == null ? "—" : f.i_score.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ---- 3. PA-guided 盲区视图 (§5.2/§6.4) ---- */}
      <hr className="sep" />
      <button className="btn" onClick={bs.run} disabled={bs.loading}>
        {bs.loading ? "PA score 计算中…" : bs.data ? `重算盲区视图 (轮次 ${checkpoint})` : `PA 盲区视图 (轮次 ${checkpoint})`}
      </button>
      {bs.err && <div className="errbar" style={{ marginTop: 10 }}>⚠ {bs.err}</div>}
      {bs.stale && <StaleBar onRun={bs.run} />}
      {bs.data && (
        <div style={{ opacity: bs.stale ? 0.5 : 1 }}>
          <div className="chart-box" style={{ marginTop: 12 }}>
            <div className="ct">逐点 PA score (CV-MSE), 点大=越难预测=盲区; 红=top8 盲区 → PA-guided 补点目标</div>
            <ResponsiveContainer width="100%" height={190}>
              <ScatterChart margin={{ top: 6, right: 10, bottom: 0, left: -18 }}>
                <CartesianGrid stroke="#2c3743" strokeDasharray="3 3" />
                <XAxis type="number" dataKey="x" name="T_Re" domain={[570, 690]} stroke="#8b98a5" fontSize={10} />
                <YAxis type="number" dataKey="y" name="c_Re" domain={[0, 0.16]} stroke="#8b98a5" fontSize={10} />
                <ZAxis type="number" dataKey="pa" range={[30, 280]} name="PA" />
                <Tooltip contentStyle={{ background: "#1a212b", border: "1px solid #2c3743", fontSize: 11 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Scatter name="可见点 (PA 越高越大)" data={bsPts}>
                  {bsPts?.map((p, i) => <Cell key={i} fill={p.top ? "#ff5d5d" : "#4ea1ff"} />)}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>{bs.data.note}</div>
        </div>
      )}
      {!data && !cmp.loading && !cmp.err && <div className="center" style={{ height: 90 }}>点击上方按钮运行各项分析</div>}
    </div>
  );
}

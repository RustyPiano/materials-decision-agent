import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ErrorBar,
  ScatterChart, Scatter, ReferenceLine, ZAxis,
} from "recharts";
import { compareModels, type CompareResult } from "../api";

export default function ModelZone({ dataVersion, checkpoint }: { dataVersion: string; checkpoint: number }) {
  const [data, setData] = useState<CompareResult | null>(null);
  const [dataVer, setDataVer] = useState<string>("");   // data 对应的数据版本
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // F6: 数据版本变化(切轮/揭示)后不静默清空, 标记为过期
  const stale = data !== null && dataVer !== dataVersion;

  async function run() {
    setLoading(true); setErr(null);
    try {
      const r = await compareModels();
      setData(r); setDataVer(dataVersion);
    } catch (e: any) { setErr(e?.message || String(e)); }
    finally { setLoading(false); }
  }

  const r2data = data?.models.map((m) => ({ model: m.model, r2: m.r2.mean, err: m.r2.std }));
  // F5: 平局不指定赢家, 用均值最高者仅作"示例"展示
  const plotModel = data ? (data.best_by_mean_r2 ?? data.leading_by_mean) : null;
  const oof = plotModel && data ? data.oof[plotModel] : null;
  const scatter = oof ? oof.true.map((t, i) => ({ t, p: oof.pred[i] })).filter((d) => d.t != null && d.p != null) : [];

  return (
    <div className="panel">
      <h2>Model &amp; Knowledge 区</h2>
      <div className="desc">模型交叉验证对比与预测—实验图 (Spec §5.2)。仅用当前可见数据, 无未来泄漏 (§7.1)</div>
      <button className="btn" onClick={run} disabled={loading}>
        {loading ? "重复交叉验证中…(~6s)" : data ? `重新比较 (轮次 ${checkpoint})` : `运行模型比较 (轮次 ${checkpoint})`}
      </button>

      {err && <div className="errbar" style={{ marginTop: 10 }}>⚠ {err}</div>}

      {stale && (
        <div className="stale-bar">
          数据已更新 (切轮或揭示), 当前对比结果已过期 —
          <button className="linkbtn" onClick={run}>点此重算</button>
        </div>
      )}

      {data && (
        <div style={{ opacity: stale ? 0.5 : 1 }}>
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
      {!data && !loading && !err && <div className="center" style={{ height: 120 }}>点击上方按钮运行模型对比</div>}
    </div>
  );
}

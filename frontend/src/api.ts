// 后端 API 客户端 (Spec §9.1)

export interface Experiment {
  exp_id: number;
  x1: number; x2: number; x3: number; x4: number; x5: number;
  y1: number;
  source: string;
  revealed_at_round: number;
  state: "measured_visible" | "revealed";
}

export interface CampaignState {
  campaign_id: string;
  checkpoint: number;
  all_checkpoints: number[];
  extra_revealed: number[];
  n_visible: number;
  n_hidden: number;
  best_DF: number;
  mean_DF: number;
  median_DF: number;
  min_DF: number;
  best_so_far_curve: { checkpoint: number; best_DF: number }[];
  experiments: Experiment[];
  n_hidden_pool: number;
}

export interface ModelCV {
  model: string;
  r2: { mean: number; std: number };
  rmse: { mean: number; std: number };
  mae: { mean: number; std: number };
  n: number;
}
export interface CompareResult {
  models: ModelCV[];
  best_by_mean_r2: string | null;   // 平局时为 null (§7.2)
  leading_by_mean: string;          // 均值最高者, 仅作展示参考
  indistinguishable: boolean;
  verdict: string;
  oof: Record<string, { true: (number | null)[]; pred: (number | null)[] }>;
}

export interface ShapFeature {
  feature: string;
  importance: number; importance_std: number;
  main_effect: number; interaction_effect: number;
  i_score: number | null;
}
export interface ExplainResult {
  model: string; n: number;
  features: ShapFeature[];
  i_score_reliable: boolean; i_score_note: string; note: string;
}
export interface BlindspotPoint {
  x1: number; x2: number; x3: number; x4: number; x5: number;
  y1: number; pa_score: number;
}
export interface BlindspotResult {
  checkpoint: number; n: number; metric: string;
  points: BlindspotPoint[]; top_blindspots: BlindspotPoint[]; note: string;
}

export interface DecisionCard {
  status: string;
  evidence: string[];
  recommendation: string;
  alternatives: string[];
  uncertainty: string;
  suggested_candidate_ids: number[];
  approval_required: boolean;
}

export interface AgentEvent {
  type: string;
  tool?: string;
  args?: any;
  result?: any;
  card?: DecisionCard;
  message?: string;
  token_usage?: { input: number; output: number };
}

const J = { "Content-Type": "application/json" };

// 统一请求: 非 2xx 抛出含后端 detail 的错误 (F1)
async function req<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (e: any) {
    throw new Error(`网络错误: ${e?.message || e}`);
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { const b = await res.json(); if (b?.detail) detail = b.detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function getState(): Promise<CampaignState> {
  return req("/api/campaign/state");
}
export async function seek(checkpoint: number): Promise<CampaignState> {
  return req("/api/campaign/seek", { method: "POST", headers: J, body: JSON.stringify({ checkpoint }) });
}
export async function compareModels(): Promise<CompareResult> {
  return req("/api/models/compare");
}
export async function explainModel(): Promise<ExplainResult> {
  return req("/api/models/explain");
}
export async function blindspots(): Promise<BlindspotResult> {
  return req("/api/models/blindspots");
}
export async function runAgent(user_goal: string): Promise<{ run_id: string; checkpoint: number }> {
  return req("/api/agent/run", { method: "POST", headers: J, body: JSON.stringify({ user_goal }) });
}
export async function approve(runId: string, action: string, candidate_ids?: number[]): Promise<any> {
  return req(`/api/agent/${runId}/approve`, { method: "POST", headers: J, body: JSON.stringify({ action, candidate_ids }) });
}
export async function reject(runId: string): Promise<any> {
  return req(`/api/agent/${runId}/reject`, { method: "POST", headers: J });
}

// onClose(reason): "done"=正常结束, "error"=异常断开 (F3)
export function streamEvents(
  runId: string,
  onEvent: (ev: AgentEvent) => void,
  onClose: (reason: "done" | "error") => void,
): EventSource {
  const es = new EventSource(`/api/agent/${runId}/events`);
  let finished = false;
  const handler = (e: MessageEvent) => {
    try { onEvent(JSON.parse(e.data)); } catch { /* ignore */ }
  };
  ["tool_call", "tool_result", "decision_card", "awaiting_approval", "completed", "error"].forEach((t) =>
    es.addEventListener(t, handler as any)
  );
  es.addEventListener("done", () => { finished = true; es.close(); onClose("done"); });
  es.onerror = () => { if (!finished) { es.close(); onClose("error"); } };
  return es;
}

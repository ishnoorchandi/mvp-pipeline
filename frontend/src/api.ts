// MVP Pipeline — API client

const BASE = "http://127.0.0.1:5001";

export interface RunSummary {
  run_id: string;
  status: string;
  created: string | null;
  current_step: string | null;
  fix_iteration: number;
}

export interface RunDetail {
  run_id: string;
  status: string;
  created: string | null;
  current_step: string | null;
  fix_iteration: number;
  artifacts: string[];
  log: { time: string; event: string; detail: string }[];
  step_timings: Record<string, number>;
  pipeline_elapsed_s: number | null;
  // Only present for dashboard-triggered runs (see backend/app.py create_run). CLI-triggered
  // runs won't have these — sprint-mode detection in the UI falls back to current_step /
  // artifact presence in that case.
  plan_only?: boolean;
  sprint_plan?: boolean;
  sprint_plan_only?: boolean;
  selected_sprint?: number;
  no_deepseek?: boolean;
}

export interface Artifact {
  run_id: string;
  filename: string;
  content: string;
}

export async function getRuns(): Promise<RunSummary[]> {
  const r = await fetch(`${BASE}/api/runs`);
  if (!r.ok) throw new Error("Failed to fetch runs");
  return r.json();
}

export async function getRun(runId: string): Promise<RunDetail> {
  const r = await fetch(`${BASE}/api/runs/${runId}`);
  if (!r.ok) throw new Error(`Failed to fetch run ${runId}`);
  return r.json();
}

export async function getArtifact(runId: string, filename: string): Promise<Artifact> {
  const r = await fetch(`${BASE}/api/runs/${runId}/artifacts/${encodeURIComponent(filename)}`);
  if (!r.ok) throw new Error(`Failed to fetch ${filename}`);
  return r.json();
}

export interface CreateRunOptions {
  mode?: string;
  plan_only?: boolean;
  sprint_plan?: boolean;
  sprint_plan_only?: boolean;
  selected_sprint?: number;
  no_deepseek?: boolean;
}

export async function createRun(
  rawInput: string,
  mode?: string | CreateRunOptions,
): Promise<{ run_id: string; status: string }> {
  const body: Record<string, unknown> = { raw_input: rawInput };
  if (typeof mode === "string") {
    if (mode) body.mode = mode;
  } else if (mode) {
    Object.assign(body, mode);
  }
  const r = await fetch(`${BASE}/api/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error("Failed to create run");
  return r.json();
}

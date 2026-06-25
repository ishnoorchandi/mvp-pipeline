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
  steps?: Record<string, {
    status: "pending" | "running" | "complete" | "failed" | "skipped" | "not_run" | "blocked";
    artifact?: string;
    reason?: string;
    result?: string;
  }>;
  // Only present for dashboard-triggered runs (see backend/app.py create_run). CLI-triggered
  // runs won't have these — sprint-mode detection in the UI falls back to current_step /
  // artifact presence in that case.
  plan_only?: boolean;
  sprint_plan?: boolean;
  sprint_plan_only?: boolean;
  selected_sprint?: number;
  no_deepseek?: boolean;
  upgrade_mode?: boolean;
  existing_app?: string;
  selected_feature_sprint?: number;
  feature_plan_only?: boolean;
  continue_run?: string;
  continue_sprint?: number;
  continue_plan_only?: boolean;
  // Selected Feature Change Boundary — set by pipeline_existing_app_upgrade after build
  // and after any DeepSeek-driven fix pass. Used to gate Local Delivery.
  change_boundary_status?: "PASS" | "FAIL" | null;
  boundary_violation_count?: number;
  out_of_scope_review_findings?: number;
  local_delivery_blocked_by_boundary?: boolean;
  // Smoke Mutation — set after smoke checks run. Detects whether smoke-check commands
  // (e.g. `npm install`/`npm ci`) themselves changed a tracked file, separately from the
  // build's own change boundary, so a lockfile rewrite is never attributed to the build.
  smoke_mutation_status?: "PASS" | "WARN" | "FAIL" | null;
  smoke_mutation_file_count?: number;
  smoke_mutation_blocked_delivery?: boolean;
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
  if (filename.startsWith("delivery/")) {
    return getDeliveryArtifact(runId, filename.slice("delivery/".length));
  }
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

export interface UpgradeRunPayload {
  upgrade_mode: true;
  existing_app: string;
  feature_request_text: string;
  feature_sprint_plan: true;
  selected_feature_sprint: number;
  feature_plan_only: boolean;
  no_deepseek: boolean;
}

export interface ContinuationRunPayload {
  continue_run: string;
  continue_sprint?: number;
  continue_feature_sprint?: number;
  continue_plan_only: boolean;
  no_deepseek: boolean;
}

async function postRun<T extends object>(body: T): Promise<{ run_id: string; status: string }> {
  const r = await fetch(`${BASE}/api/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function createUpgradeRun(payload: UpgradeRunPayload) {
  return postRun(payload);
}

export function createContinuationRun(payload: ContinuationRunPayload) {
  return postRun(payload);
}

// ── Local Delivery + Optional Sandbox Push ──────────────────────────────────

export interface DeliveryCheckItem {
  status: "pass" | "warn" | "fail";
  detail: string;
}

// Repo Hygiene — facts about the TARGET repo's own git hygiene (e.g. node_modules
// tracked in git), never auto-fixed by the pipeline. See repo_hygiene_report.md/.json.
export interface RepoHygieneInfo {
  node_modules_tracked: boolean;
  node_modules_dirty_count: number;
  denied_dirty_file_count: number;
  gitignore_has_node_modules: boolean;
  human_cleanup_recommended: boolean;
  recommended_commands: string[];
  auto_cleanup_performed: boolean;
  requires_human_approval: boolean;
}

// Stable block-reason codes the UI can switch on. DENIED_TRACKED_DEPENDENCY_FILES means
// the target repo itself has a hygiene problem (e.g. tracked node_modules) — not a
// generated-feature defect.
export type DeliveryBlockReason =
  | "NOT_A_GIT_REPO"
  | "PROTECTED_BRANCH"
  | "DENIED_TRACKED_DEPENDENCY_FILES"
  | "DENIED_SENSITIVE_OR_PROTECTED_FILES"
  | null;

export interface DeliveryPrecheck {
  repo_path: string;
  repo_type: "company-protected" | "personal-sandbox" | "unknown";
  remote_info: { remote: string; fetch_url: string; push_url: string };
  git_status: { branch: string; clean: boolean; porcelain: string[] };
  checks: Record<string, DeliveryCheckItem>;
  local_commit_allowed: boolean;
  push_allowed: boolean;
  push_blocked_reasons: string[];
  decision: "PASS_LOCAL_ONLY" | "PASS_SANDBOX_PUSH" | "BLOCKED";
  block_reason?: DeliveryBlockReason;
  repo_hygiene?: RepoHygieneInfo;
}

export interface DeliveryState {
  repo_path: string;
  mode: "local_only" | "sandbox_push";
  branch_name: string | null;
  repo_type: string;
  decision: "PASS_LOCAL_ONLY" | "PASS_SANDBOX_PUSH" | "BLOCKED";
  plan_only: boolean;
  commit_hash: string | null;
  files_committed: string[];
  push_attempted: boolean;
  push_succeeded: boolean | null;
  blocked_reason?: string;
  block_reason?: DeliveryBlockReason;
  repo_hygiene?: RepoHygieneInfo;
  note?: string;
  timestamp: number;
}

export interface DeliveryBoundaryInfo {
  status: "PASS" | "FAIL" | null;
  violation_count: number | null;
  out_of_scope_review_findings: number | null;
  blocked: boolean;
}

export interface DeliverySmokeMutationInfo {
  status: "PASS" | "WARN" | "FAIL" | null;
  file_count: number | null;
  blocked: boolean;
}

export interface DeliveryInfo {
  available: boolean;
  reason?: string;
  repo_path?: string;
  state: DeliveryState | null;
  artifacts?: string[];
  artifact_availability?: Record<string, boolean>;
  boundary?: DeliveryBoundaryInfo;
  smoke_mutation?: DeliverySmokeMutationInfo;
}

export async function getDeliveryInfo(runId: string): Promise<DeliveryInfo> {
  const r = await fetch(`${BASE}/api/runs/${runId}/delivery`);
  if (!r.ok) throw new Error("Failed to fetch delivery info");
  return r.json();
}

export async function getDeliveryPrecheck(runId: string, branchName: string, sandboxPush: boolean): Promise<DeliveryPrecheck> {
  const params = new URLSearchParams({ branch_name: branchName, sandbox_push: String(sandboxPush) });
  const r = await fetch(`${BASE}/api/runs/${runId}/delivery/precheck?${params.toString()}`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function getDeliveryArtifact(runId: string, filename: string): Promise<Artifact> {
  const r = await fetch(`${BASE}/api/runs/${runId}/delivery/artifacts/${encodeURIComponent(filename)}`);
  if (!r.ok) throw new Error(`Failed to fetch ${filename}`);
  return r.json();
}

export async function createDeliveryCommit(runId: string, branchName: string, commitMessage: string): Promise<DeliveryState> {
  const r = await fetch(`${BASE}/api/runs/${runId}/delivery/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ branch_name: branchName, commit_message: commitMessage }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function pushDeliverySandbox(runId: string, branchName: string, commitMessage: string): Promise<DeliveryState> {
  const r = await fetch(`${BASE}/api/runs/${runId}/delivery/push`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ branch_name: branchName, commit_message: commitMessage }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

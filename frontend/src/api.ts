// MVP Pipeline — API client

// Planning Gate — universal pre-build sign-off state. Mirrors planning_gate.py's
// build_planning_gate_state() return shape. All fields are optional since older
// runs won't have explicit sign-off artifacts and the backend infers safe defaults.
export interface PlanningGateState {
  entry_point?: "raw_idea" | "written_requirements" | "existing_app_upgrade"
    | "bugfix" | "backend_inventory" | "backend_safety" | "git_delivery" | "unknown";
  planning_stage?: "intake" | "requirements_conversation" | "requirements_review"
    | "requirements_approved" | "architecture_conversation" | "architecture_review"
    | "architecture_approved" | "global_instructions_created" | "ready_for_build"
    | "build_not_applicable" | "unknown";
  requirements_status?: "not_started" | "draft" | "questions_pending" | "review"
    | "approved" | "not_applicable" | "unknown";
  architecture_status?: "not_started" | "draft" | "questions_pending" | "review"
    | "approved" | "not_applicable" | "unknown";
  global_instructions_status?: "not_created" | "created" | "not_applicable" | "unknown";
  requirements_approved?: boolean;
  architecture_approved?: boolean;
  global_instructions_created?: boolean;
  build_requires_approval?: boolean;
  build_allowed_by_planning_gate?: boolean;
  planning_gate_reason?: string;
}

const BASE = "http://127.0.0.1:5001";

// Operator Summary — deterministic, read-only normalization of a run's status/
// artifacts into the handful of facts an operator actually needs: what
// happened, is it safe to build/commit/deliver, what's blocking it, what's the
// next safe action, and which artifacts matter most. Computed by
// backend.app.build_operator_run_summary(); always optional since older runs
// (or runs the backend can't fully classify) fall back to "unknown" fields
// rather than omitting the object — but treat every field as possibly absent.
export interface OperatorSummary {
  workflow_type?: "existing_app_plan" | "existing_app_build" | "bugfix_plan" | "backend_inventory"
    | "backend_safety" | "git_sync" | "pr_delivery" | "unknown";
  execution_mode?: "plan_only" | "build" | "build_blocked" | "sandbox_build" | "bugfix_plan" | "inventory" | "unknown";
  build_status?: "not_run" | "blocked" | "running" | "passed" | "failed" | "interrupted" | "unknown";
  delivery_status?: "not_requested" | "blocked" | "ready" | "committed" | "pushed" | "pr_opened" | "unknown";
  repo_health?: "clean" | "dirty_dependency_files" | "dirty_source_files" | "dirty_secrets_or_env" | "blocked" | "unknown";
  sprint_quality_status?: "has_build_ready_sprints" | "needs_decomposition" | "unknown";
  workspace_mode?: "sandbox" | "direct_branch" | "planning_only" | "unknown";
  target_repo_name?: string | null;
  target_repo_path?: string | null;
  current_status?: string | null;
  next_safe_action?: string | null;
  blocking_issue?: string | null;
  safe_to_show?: boolean;
  primary_artifacts?: string[];
  planning_gate?: PlanningGateState;
}

export interface RunSummary {
  run_id: string;
  status: string;
  created: string | null;
  current_step: string | null;
  fix_iteration: number;
  operator_summary?: OperatorSummary;
}

export interface RunDetail {
  run_id: string;
  status: string;
  created: string | null;
  current_step: string | null;
  fix_iteration: number;
  artifacts: string[];
  operator_summary?: OperatorSummary;
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
  // Git Sync & Pull Safety — set by pipeline_existing_app_upgrade's read-only fetch +
  // status check against the target repo's base branch (see delivery.run_git_sync_check).
  // Never runs git pull/push/reset/stash. Full detail lives in git_sync_state.json.
  git_sync_status?: "up_to_date" | "behind" | "ahead" | "diverged" | "unknown" | null;
  git_sync_blocked?: boolean;
  git_sync_summary?: string;
  git_sync_artifacts?: string[];
  // Repo Hygiene — compact classify_repo_hygiene() summary fields mirrored onto
  // run_state.json for quick access without reading repo_hygiene_state.json.
  repo_hygiene_severity?: "clean" | "warn" | "review" | "blocked";
  repo_hygiene_summary_text?: string;
  repo_hygiene_recommended_action?: string;
  // Build gate — single source of truth for whether Claude Code build (Step 12) ran,
  // was skipped (plan-only), or was blocked (e.g. company-protected repo on a protected
  // branch). Written by resolve_build_gate() in pipeline_mvp_builder.py.
  execution_mode?: "plan_only" | "build" | "build_blocked";
  build_allowed?: boolean;
  claude_build_allowed?: boolean;
  build_gate_reason?: string;
  company_repo_build_allowed?: boolean;
  // Sandbox Workspace — when build_workspace_mode is "sandbox", Claude Code built
  // against a disposable copy at active_build_path/sandbox_workspace instead of
  // original_repo_path, which is never modified. See delivery.create_sandbox_workspace
  // and sandbox_workspace_report.md/.json, sandbox_patch.diff.
  build_workspace_mode?: "none" | "sandbox" | "direct";
  original_repo_path?: string | null;
  active_build_path?: string | null;
  sandbox_workspace?: string | null;
  original_repo_modified?: boolean;
  original_repo_change_check?: "passed" | "failed" | "not_applicable";
  // Git Pull (fast-forward only) — set when --git-pull-ff-only was used. Only ever
  // reflects a guarded `git pull --ff-only origin <base_branch>`; never push/reset/stash.
  git_pull_status?: "BLOCKED" | "PULLED" | "FAILED" | "NO_OP" | null;
  git_pull_blocked?: boolean;
  git_pull_summary?: string;
  git_pull_artifacts?: string[];
  // Pull Request Delivery Plan — set when --pr-delivery-plan was used. Plan only:
  // never creates a branch, commits, pushes, or opens a PR (see delivery.run_pr_delivery_plan).
  pr_plan_status?: "ready" | "warning" | "pr_workflow_required" | "blocked" | null;
  pr_plan_branch?: string;
  pr_plan_summary?: string;
  pr_plan_artifacts?: string[];
  // PR Branch Preparation — set when --prepare-pr-branch was used. Local-only:
  // may create/switch a feature branch and create a local commit, but never pushes
  // and never opens a PR. Full detail lives in pr_branch_state.json.
  pr_branch_decision?: "BRANCH_READY" | "COMMITTED_LOCAL" | "NO_CHANGES" | "BLOCKED" | "FAILED" | null;
  pr_branch_name?: string;
  pr_commit_hash?: string | null;
  pr_branch_artifacts?: string[];
  pr_branch_summary?: string;
  // PR Remote Delivery — set when --push-pr-branch / --open-pr was used.
  // Strictly branch-only remote delivery; never pushes main or force-pushes.
  pr_remote_decision?: "PUSHED_BRANCH" | "PR_CREATED" | "MANUAL_PR_REQUIRED" | "BLOCKED" | "FAILED" | "NO_OP" | null;
  pr_remote_branch?: string;
  pr_remote_pr_url?: string | null;
  pr_remote_artifacts?: string[];
  pr_remote_summary?: string;
  // Bugfix Mode — deterministic planning-only layer for real bug reports.
  bugfix_mode?: boolean;
  bug_title?: string | null;
  bug_category?: string;
  bug_severity?: "low" | "medium" | "high" | "unknown";
  bugfix_artifacts?: string[];
  bugfix_summary?: string;
  suspected_files_count?: number;
  bugfix_boundary_status?: "ready" | "warning" | "blocked" | string;
  bugfix_build_readiness?: "ready" | "warning" | "blocked" | "planning_only" | string;
  bugfix_top_suspected_files?: Array<{
    file: string;
    area: string;
    reason: string;
    confidence: string;
  }>;
  // Backend Inventory + Backend Route Map — read-only static analysis. Never
  // rewrites backend code, never makes app changes, never commits/pushes/opens a PR.
  backend_inventory_mode?: boolean;
  backend_frameworks?: Array<{ framework: string; confidence: "low" | "medium" | "high"; evidence: string[] }>;
  backend_route_count?: number;
  frontend_api_call_count?: number;
  env_var_count?: number;
  backend_roots?: string[];
  frontend_roots?: string[];
  backend_inventory_warnings?: string[];
  backend_inventory_artifacts?: string[];
  backend_inventory_summary?: string;
  // Backend Change Boundary + Backend Smoke Checks — safety layer that reasons
  // about backend changes before any backend bugfix/build step touches code.
  backend_boundary_status?: "ready" | "warning" | string;
  backend_boundary_artifacts?: string[];
  backend_boundary_summary?: string;
  backend_safe_to_edit?: boolean;
  backend_smoke_status?: "pass" | "fail" | "plan_only" | string;
  backend_smoke_artifacts?: string[];
  backend_smoke_summary?: string;
  backend_safe_to_run_checks?: boolean;
}

export interface Artifact {
  run_id: string;
  filename: string;
  content: string;
}

// ── Requirements Conversation ─────────────────────────────────────────────────

export type QuestionType = "single_choice" | "multi_choice" | "short_text" | "long_text" | "yes_no";

export interface RequirementsQuestion {
  id: string;
  label: string;
  question: string;
  type: QuestionType;
  options: string[];
  recommended?: string;
  answer?: string | null;
  freeform_answer?: string;
  why?: string;
  required?: boolean;
}

export interface RequirementsConversationState {
  entry_point?: "raw_idea" | "written_requirements" | "existing_app_upgrade" | string;
  requirements_status?: "not_started" | "draft" | "questions_pending" | "review" | "approved" | "not_applicable" | string;
  questions: RequirementsQuestion[];
  answers: Record<string, string | null>;
  draft_requirements_artifact?: string | null;
  approved_requirements_artifact?: string | null;
  requirements_approved?: boolean;
  updated_at?: string;
}

export interface RequirementsConversationResponse {
  run_id: string;
  conversation: RequirementsConversationState;
  planning_gate?: PlanningGateState;
  can_approve?: boolean;
  unanswered_required?: string[];
}

export async function getRequirementsConversation(runId: string): Promise<RequirementsConversationResponse> {
  const r = await fetch(`${BASE}/api/runs/${runId}/requirements-conversation`);
  if (!r.ok) throw new Error(`Failed to fetch requirements conversation for ${runId}`);
  return r.json();
}

export async function saveRequirementsAnswer(
  runId: string,
  questionId: string,
  answer: string | null,
  freeformAnswer?: string,
): Promise<RequirementsConversationResponse> {
  const r = await fetch(`${BASE}/api/runs/${runId}/requirements-conversation/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question_id: questionId, answer, freeform_answer: freeformAnswer ?? "" }),
  });
  if (!r.ok) {
    const msg = await r.text().catch(() => "");
    throw new Error(`Failed to save answer: ${msg}`);
  }
  return r.json();
}

export async function approveRequirements(runId: string): Promise<RequirementsConversationResponse> {
  const r = await fetch(`${BASE}/api/runs/${runId}/requirements-conversation/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!r.ok) {
    const msg = await r.text().catch(() => "");
    throw new Error(`Approval failed: ${msg}`);
  }
  return r.json();
}

// ── Architecture Conversation ─────────────────────────────────────────────────

export interface ArchitectureQuestion {
  id: string;
  label: string;
  question: string;
  type: QuestionType;
  options: string[];
  recommended?: string;
  answer?: string | null;
  freeform_answer?: string;
  why?: string;
  required?: boolean;
}

export interface ArchitectureConversationState {
  entry_point?: string;
  architecture_status?: "not_started" | "draft" | "questions_pending" | "review" | "approved" | "not_applicable" | string;
  requirements_approved?: boolean;
  questions: ArchitectureQuestion[];
  answers: Record<string, string | null>;
  draft_architecture_artifact?: string | null;
  approved_architecture_artifact?: string | null;
  architecture_approved?: boolean;
  can_start?: boolean;
  blocking_reason?: string | null;
  updated_at?: string;
}

export interface ArchitectureConversationResponse {
  run_id: string;
  conversation: ArchitectureConversationState;
  planning_gate?: PlanningGateState;
  can_start?: boolean;
  blocking_reason?: string | null;
  can_approve?: boolean;
  unanswered_required?: string[];
}

export async function getArchitectureConversation(runId: string): Promise<ArchitectureConversationResponse> {
  const r = await fetch(`${BASE}/api/runs/${runId}/architecture-conversation`);
  if (!r.ok) throw new Error(`Failed to fetch architecture conversation for ${runId}`);
  return r.json();
}

export async function saveArchitectureAnswer(
  runId: string,
  questionId: string,
  answer: string | null,
  freeformAnswer?: string,
): Promise<ArchitectureConversationResponse> {
  const r = await fetch(`${BASE}/api/runs/${runId}/architecture-conversation/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question_id: questionId, answer, freeform_answer: freeformAnswer ?? "" }),
  });
  if (!r.ok) {
    const msg = await r.text().catch(() => "");
    throw new Error(`Failed to save answer: ${msg}`);
  }
  return r.json();
}

export async function approveArchitecture(runId: string): Promise<ArchitectureConversationResponse> {
  const r = await fetch(`${BASE}/api/runs/${runId}/architecture-conversation/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!r.ok) {
    const msg = await r.text().catch(() => "");
    throw new Error(`Architecture approval failed: ${msg}`);
  }
  return r.json();
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
  bugfix_mode?: boolean;
  bug_report_text?: string;
  bug_title?: string;
  // Sandbox Workspace — build in a disposable copy instead of the real repo.
  // See delivery.create_sandbox_workspace / resolve_build_gate.
  use_sandbox_workspace?: boolean;
  sandbox_workspace?: string;
  allow_company_build?: boolean;
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

// Repo Hygiene Summary — classify_repo_hygiene()'s compact, UI-ready output.
// Counts every dirty/changed path into one bucket (source, dependency, generated,
// env_or_secret, lockfile, config, test, unknown) so the UI never needs to render
// thousands of `node_modules` paths inline. example_paths/source_examples are
// capped at 3 — the full path list lives in full_details_artifact instead.
// See repo_hygiene_summary.md/.json and delivery.classify_repo_hygiene.
export interface RepoHygieneSummary {
  source_files_dirty: number;
  dependency_files_dirty: number;
  generated_files_dirty: number;
  env_or_secret_files_dirty: number;
  lockfiles_dirty: number;
  config_files_dirty: number;
  test_files_dirty: number;
  unknown_files_dirty: number;
  safe_to_pull: boolean;
  safe_to_build: boolean;
  safe_to_commit: boolean;
  severity: "clean" | "warn" | "review" | "blocked";
  summary: string;
  recommended_action: string;
  example_paths: string[];
  source_examples: string[];
  full_details_artifact: string;
  denied_path_count: number;
}

export async function getRepoHygieneState(runId: string): Promise<RepoHygieneSummary | null> {
  try {
    const a = await getArtifact(runId, "repo_hygiene_state.json");
    return JSON.parse(a.content) as RepoHygieneSummary;
  } catch {
    return null;
  }
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
  repo_hygiene_summary?: RepoHygieneSummary;
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

// ── Git Sync & Pull Safety ──────────────────────────────────────────────────
// Mirrors delivery.analyze_git_sync's return shape (see delivery.py). Read-only:
// the pipeline only ever runs `git fetch origin` + status/rev-list checks here,
// never pull/push/reset/stash. Stored as git_sync_state.json in the run folder.

export interface GitSyncState {
  repo_path: string;
  current_branch: string | null;
  fetch_url: string | null;
  push_url: string | null;
  base_branch: string;
  repo_type: "company-protected" | "personal-sandbox" | "unknown";
  is_company_repo: boolean;
  is_dirty: boolean | null;
  dirty_file_count: number;
  denied_paths_dirty: boolean;
  denied_dirty_paths: string[];
  origin_base_exists: boolean;
  sync_status: "up_to_date" | "behind" | "ahead" | "diverged" | "unknown";
  commits_ahead: number;
  commits_behind: number;
  fast_forward_safe: boolean;
  pull_blocked: boolean;
  block_reasons: string[];
  fetch_attempted: boolean;
  fetch_succeeded: boolean | null;
  build_should_proceed: "yes" | "no" | "warn";
  recommended_command: string | null;
  repo_hygiene?: RepoHygieneSummary;
}

export async function getGitSyncState(runId: string): Promise<GitSyncState | null> {
  try {
    const a = await getArtifact(runId, "git_sync_state.json");
    return JSON.parse(a.content) as GitSyncState;
  } catch {
    return null;
  }
}

// ── Git Pull (fast-forward only) ────────────────────────────────────────────
// Mirrors delivery.run_git_pull_ff_only's "state" dict. The pipeline only ever runs
// `git pull --ff-only origin <base_branch>`, and only after confirming it's safe —
// never push/merge/reset/stash/checkout/clean. Stored as git_pull_state.json.

export interface GitPullState {
  repo_path: string;
  current_branch: string | null;
  base_branch: string;
  is_company_repo: boolean;
  decision: "BLOCKED" | "PULLED" | "FAILED" | "NO_OP";
  pull_attempted: boolean;
  pull_command: string;
  pull_exit_code: number | null;
  pull_succeeded: boolean | null;
  pull_stdout: string;
  pull_stderr: string;
  block_reasons: string[];
  now_up_to_date: boolean;
  new_dirty_changes_detected: boolean;
  no_push_performed: boolean;
  no_reset_performed: boolean;
  no_stash_performed: boolean;
  timestamp: number;
}

export async function getGitPullState(runId: string): Promise<GitPullState | null> {
  try {
    const a = await getArtifact(runId, "git_pull_state.json");
    return JSON.parse(a.content) as GitPullState;
  } catch {
    return null;
  }
}

// ── Pull Request Delivery Plan ──────────────────────────────────────────────
// Mirrors delivery.analyze_pr_delivery_plan's return shape. Plan only — the pipeline
// never creates a branch, commits, pushes, or opens a PR for this feature; it only
// inspects the repo (and this run's own prior safety artifacts) and writes a plan.
// Stored as pr_state.json.

export type PrSafetyArtifactStatus = "passed" | "failed" | "missing" | "not_applicable" | "blocked";

export interface PrDeliveryPlanState {
  repo_path: string;
  repo_type: "company-protected" | "personal-sandbox" | "unknown";
  is_company_repo: boolean;
  current_branch: string | null;
  base_branch: string;
  fetch_url: string | null;
  push_url: string | null;
  direct_push_to_main_blocked: boolean;
  pr_title: string | null;
  suggested_branch: string;
  requested_branch: string | null;
  branch_name_safe: boolean;
  branch_was_sanitized: boolean;
  sync_status: "up_to_date" | "behind" | "ahead" | "diverged" | "unknown";
  is_up_to_date: boolean;
  is_dirty: boolean;
  dirty_file_count: number;
  commits_ahead: number;
  commits_behind: number;
  changed_files: string[];
  denied_files: string[];
  boundary_check_status: PrSafetyArtifactStatus;
  smoke_mutation_status: PrSafetyArtifactStatus;
  delivery_safety_status: PrSafetyArtifactStatus;
  future_push_approval_required: boolean;
  pr_creation_allowed_later: boolean;
  pr_readiness: "ready" | "warning" | "pr_workflow_required" | "blocked";
  block_reasons: string[];
  warnings: string[];
  recommended_next_action: string;
  plan_only: true;
  timestamp: number;
}

export async function getPrDeliveryPlanState(runId: string): Promise<PrDeliveryPlanState | null> {
  try {
    const a = await getArtifact(runId, "pr_state.json");
    return JSON.parse(a.content) as PrDeliveryPlanState;
  } catch {
    return null;
  }
}

export interface PrBranchPrepState {
  repo_path: string;
  repo_type: "company-protected" | "personal-sandbox" | "unknown";
  base_branch: string;
  feature_branch: string;
  current_branch_before: string | null;
  current_branch_after: string | null;
  company_repo: boolean;
  allow_company_local_branch: boolean;
  branch_created: boolean;
  branch_switched: boolean;
  commit_attempted: boolean;
  commit_created: boolean;
  commit_hash: string | null;
  files_committed: string[];
  decision: "BRANCH_READY" | "COMMITTED_LOCAL" | "NO_CHANGES" | "BLOCKED" | "FAILED";
  block_reasons: string[];
  warnings: string[];
  no_push_performed: boolean;
  no_pr_opened: boolean;
  no_reset_stash_clean_performed: boolean;
  timestamp: number;
}

export async function getPrBranchPrepState(runId: string): Promise<PrBranchPrepState | null> {
  try {
    const a = await getArtifact(runId, "pr_branch_state.json");
    return JSON.parse(a.content) as PrBranchPrepState;
  } catch {
    return null;
  }
}

export interface PrRemoteDeliveryState {
  repo_path: string;
  repo_type: "company-protected" | "personal-sandbox" | "unknown";
  base_branch: string;
  feature_branch: string;
  current_branch: string | null;
  remote_allowed: boolean;
  company_approval: boolean;
  sandbox_allowlist_matched: boolean;
  push_attempted: boolean;
  push_succeeded: boolean;
  push_command: string;
  open_pr_requested: boolean;
  pr_attempted: boolean;
  pr_created: boolean;
  pr_url: string | null;
  manual_pr_url: string | null;
  manual_pr_instructions: string | null;
  decision: "PUSHED_BRANCH" | "PR_CREATED" | "MANUAL_PR_REQUIRED" | "BLOCKED" | "FAILED" | "NO_OP";
  block_reasons: string[];
  warnings: string[];
  no_main_push_performed: boolean;
  no_force_push_performed: boolean;
  no_reset_stash_clean_performed: boolean;
  timestamp: number;
}

export async function getPrRemoteDeliveryState(runId: string): Promise<PrRemoteDeliveryState | null> {
  try {
    const a = await getArtifact(runId, "pr_remote_state.json");
    return JSON.parse(a.content) as PrRemoteDeliveryState;
  } catch {
    return null;
  }
}

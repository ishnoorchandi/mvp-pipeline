import { useState, useEffect, useRef, useCallback } from "react";
import type { ReactElement } from "react";
import {
  getRuns, getRun, getArtifact, createUpgradeRun, createContinuationRun,
  getDeliveryInfo, getDeliveryPrecheck, createDeliveryCommit, pushDeliverySandbox,
  getGitSyncState, getGitPullState, getPrDeliveryPlanState,
} from "./api";
import type {
  RunSummary, RunDetail, DeliveryInfo, DeliveryPrecheck, GitSyncState, GitPullState, PrDeliveryPlanState,
} from "./api";
import "./App.css";

// ── Pipeline definitions ───────────────────────────────────────────────────────

interface PipelineStep {
  id: string;
  label: string;
  sub: string;
  agent: keyof typeof AGENTS;
  artifact: string;
}

const PIPELINE_STEPS: PipelineStep[] = [
  { id: "requirements_normalization", label: "Requirements Normalization", sub: "Normalizing the raw input into clean requirements", agent: "gpt", artifact: "clean_requirements.md" },
  { id: "mvp_spec", label: "MVP Spec", sub: "Writing the detailed product specification", agent: "gpt", artifact: "mvp_spec.md" },
  { id: "sprint_architecture", label: "Sprint Architecture", sub: "Defining architecture and the dependency-aware sprint plan", agent: "architect", artifact: "sprint_plan.md" },
  { id: "selected_sprint_prompt", label: "Selected Sprint Prompt", sub: "Writing the selected sprint scope and Claude Code prompt", agent: "gpt", artifact: "selected_sprint_build_prompt.txt" },
  { id: "planning_consistency_check", label: "Planning Consistency Check", sub: "Checking planning artifacts against original requirements before build", agent: "smoke", artifact: "requirements_consistency_check.txt" },
  { id: "claude_build", label: "Claude Build", sub: "Building only the selected sprint", agent: "claude", artifact: "claude_build_output.txt" },
  { id: "smoke_checks", label: "Smoke Checks", sub: "Running initial build, runtime, and architecture checks", agent: "smoke", artifact: "smoke_test_log.txt" },
  { id: "deepseek_red_team", label: "DeepSeek Red Team Review", sub: "Red-teaming the implementation for concrete failures", agent: "deepseek", artifact: "deepseek_attack_report.md" },
  { id: "governance_review", label: "Governance Review", sub: "Reviewing AppSec, legal/privacy, and infrastructure risks", agent: "governance", artifact: "governance_meta_judgment.md" },
  { id: "consolidated_fix_plan", label: "Consolidated Fix Plan", sub: "Combining smoke, red-team, and governance findings into one plan", agent: "gpt", artifact: "consolidated_fix_plan.md" },
  { id: "claude_fix_pass", label: "Claude Fix Pass", sub: "Applying the consolidated blocker fixes once", agent: "claude", artifact: "claude_fix_output_1.txt" },
  { id: "final_smoke_checks", label: "Final Smoke Checks", sub: "Re-running checks against the final implementation", agent: "smoke", artifact: "final_smoke_checks.txt" },
  { id: "sprint_requirements_check", label: "Sprint Requirements Check", sub: "Checking the built sprint against selected_sprint_scope.md", agent: "smoke", artifact: "sprint_requirements_check.txt" },
  { id: "sprint_report", label: "Sprint Report", sub: "Reporting what was actually planned, built, checked, and accepted", agent: "gpt", artifact: "sprint_report.md" },
];

// Sprint-mode-only steps — spliced in right after "Build Prompt" whenever sprint mode
// (--sprint-plan / --sprint-plan-only) is active for a run. Hidden otherwise.
const SPRINT_ONLY_STEPS: PipelineStep[] = [];

// Builds the actual step list for a run: base steps, with the sprint-only steps spliced
// in after "Build Prompt" when sprint mode is active, and the build step's label/sub
// swapped to reflect "selected sprint only" build scope.
function getStepsForRun(sprintModeActive: boolean, selectedSprintNum: number): PipelineStep[] {
  return PIPELINE_STEPS.map(s => {
    if (s.id === "claude_build")
      return { ...s, label: "Build Selected Sprint", sub: `Building only Sprint ${selectedSprintNum} with Claude Code — future sprints are planned but not built` };
    if (!sprintModeActive && s.id === "sprint_architecture")
      return { ...s, label: "Architecture", artifact: "ARCHITECTURE.md" };
    if (!sprintModeActive && s.id === "selected_sprint_prompt")
      return { ...s, label: "Build Prompt", artifact: "build_prompt.txt" };
    if (!sprintModeActive && s.id === "sprint_report")
      return { ...s, label: "Final Report", artifact: "final_mvp_report.md" };
    return s;
  });
}

const AGENTS = {
  gpt:        { label: "OpenAI GPT-4o mini", short: "GPT-4o mini",  color: "#10a37f", light: "#f0fdf9" },
  // Senior architect / sprint planner role — same OpenAI model family, presented with
  // its full model name (not "mini") since sprint decomposition is the advanced-reasoning
  // step, distinct from the routine PM/requirements-writing "gpt" (mini) role above.
  architect:  { label: "OpenAI GPT-4o",      short: "GPT-4o",       color: "#10a37f", light: "#f0fdf9" },
  claude:     { label: "Anthropic Claude",   short: "Claude Code",   color: "#D97741", light: "#fff7ed" },
  deepseek:   { label: "DeepSeek Chat",      short: "DeepSeek",      color: "#4361EE", light: "#eef1fd" },
  smoke:      { label: "System Checks",      short: "Smoke Checks",  color: "#D97706", light: "#fffbeb" },
  governance: { label: "Governance Panel",   short: "Governance",    color: "#7c3aed", light: "#f5f3ff" },
} as const;

const STEP_MAP: Record<string, string> = {
  spec: "mvp_spec",
  sprint_plan: "sprint_architecture",
  build_prompt: "selected_sprint_prompt",
  blocked: "planning_consistency_check",
  consistency: "planning_consistency_check",
  building: "claude_build", built: "claude_build",
  smoke_1: "smoke_checks", smoke_2: "smoke_checks", smoke_3: "smoke_checks",
  deepseek_1: "deepseek_red_team", deepseek_2: "deepseek_red_team", deepseek_3: "deepseek_red_team",
  governance: "governance_review",
  judge_1: "consolidated_fix_plan", judge_2: "consolidated_fix_plan", judge_3: "consolidated_fix_plan",
  fix_1: "claude_fix_pass", fix_2: "claude_fix_pass", fix_3: "claude_fix_pass",
  final_smoke_checks: "final_smoke_checks",
  report: "sprint_report", done: "sprint_report",
};

const TERMINAL = new Set([
  "done", "approved", "max_iterations_reached",
  "blocked_consistency_violation", "plan_only_done", "sprint_plan_only_done",
]);

// Clean display names for sprint-mode artifacts (requirement: artifact sidebar should
// show readable labels, not raw filenames, for sprint planning outputs).
const ARTIFACT_LABELS: Record<string, string> = {
  "sprint_plan.md":                    "Sprint Plan",
  "sprint_plan.json":                  "Sprint Plan JSON",
  "selected_sprint_scope.md":          "Selected Sprint Scope",
  "selected_sprint_build_prompt.txt":  "Selected Sprint Build Prompt",
  "requirement_coverage_map.md":       "Requirement Coverage Map",
  "requirement_coverage_map.json":     "Requirement Coverage Map JSON",
  "sprint_coverage_check.txt":         "Sprint Coverage Check",
  "requirements_consistency_check.txt": "Planning Consistency Check",
  "sprint_requirements_check.txt":     "Sprint Requirements Check",
  "consolidated_fix_plan.md":          "Consolidated Fix Plan",
  "final_smoke_checks.txt":            "Final Smoke Checks",
  "sprint_report.md":                  "Sprint Report",
  "existing_app_inventory.md":         "Existing App Inventory",
  "baseline_health_check.md":          "Baseline Health Check",
  "baseline_behavior_checklist.md":    "Baseline Behavior Checklist",
  "existing_app_summary.md":           "Existing App Summary",
  "new_feature_requirements.md":       "New Feature Requirements",
  "change_gap_analysis.md":            "Change Gap Analysis",
  "additive_architecture.md":          "Additive Architecture",
  "feature_sprint_plan.md":            "Feature Sprint Plan",
  "feature_sprint_plan.json":          "Feature Sprint Plan JSON",
  "selected_feature_sprint_scope.md":  "Selected Feature Sprint Scope",
  "selected_feature_sprint_build_prompt.txt": "Selected Feature Sprint Build Prompt",
  "changed_files_report.md":           "Changed Files Report",
  "selected_feature_change_boundary.md": "Selected Feature Change Boundary",
  "selected_feature_change_boundary.json": "Selected Feature Change Boundary JSON",
  "review_finding_classification.md":  "Review Finding Classification",
  "review_finding_classification.json": "Review Finding Classification JSON",
  "boundary_violation_report.md":      "Boundary Violation Report",
  "smoke_test_log.txt":                "Smoke Test Log",
  "smoke_mutation_report.md":          "Smoke Mutation Report",
  "smoke_mutation_report.json":        "Smoke Mutation Report JSON",
  "repo_hygiene_report.md":            "Repo Hygiene Report",
  "repo_hygiene_report.json":          "Repo Hygiene Report JSON",
  "regression_check.md":               "Regression Check",
  "feature_completion_report.md":      "Feature Completion Report",
  "continuation_source.md":            "Continuation Source",
  "preserved_sprint_plan.json":        "Preserved Sprint Plan JSON",
  "preserved_sprint_plan.md":          "Preserved Sprint Plan",
  "current_app_inventory.md":          "Current App Inventory",
  "continuation_gap_analysis.md":      "Continuation Gap Analysis",
  "selected_continuation_sprint_scope.md": "Selected Continuation Sprint Scope",
  "selected_continuation_sprint_build_prompt.txt": "Selected Continuation Build Prompt",
  "continuation_regression_check.md":  "Continuation Regression Check",
  "continuation_completion_report.md": "Continuation Completion Report",
};

function artifactDisplayName(filename: string): string {
  if (ARTIFACT_LABELS[filename]) return ARTIFACT_LABELS[filename];
  let m = filename.match(/^sprint_(\d+)_scope\.md$/);
  if (m) return `Sprint ${m[1]} Scope`;
  m = filename.match(/^sprint_(\d+)_build_prompt\.txt$/);
  if (m) return `Sprint ${m[1]} Build Prompt`;
  return filename;
}

const ARTIFACT_ORDER = [
  "raw_input.md",
  "mvp_scope.md",
  "clean_requirements.md",
  "mvp_spec.md",
  "ARCHITECTURE.md",
  "smoke_checks.md",
  "build_prompt.txt",
  "sprint_plan.md",
  "sprint_plan.json",
  "selected_sprint_scope.md",
  "selected_sprint_build_prompt.txt",
  "requirement_coverage_map.md",
  "requirement_coverage_map.json",
  "sprint_coverage_check.txt",
  "sprint_1_scope.md",
  "sprint_1_build_prompt.txt",
  "requirements_consistency_check.txt",
  "consolidated_fix_plan.md",
  "claude_build_prompt.md",
  "claude_build_output.txt",
  "smoke_test_log.txt",
  "architecture_check.txt",
  "deepseek_attack_report.md",
  "judged_issue_report.md",
  "claude_fix_prompt_1.md",   "claude_fix_output_1.txt",
  "smoke_test_log_2.txt",
  "deepseek_attack_report_2.md", "judged_issue_report_2.md",
  "claude_fix_prompt_2.md",   "claude_fix_output_2.txt",
  "smoke_test_log_3.txt",
  "deepseek_attack_report_3.md", "judged_issue_report_3.md",
  "claude_fix_prompt_3.md",   "claude_fix_output_3.txt",
  "governance_appsec_report.md",
  "governance_legal_privacy_report.md",
  "governance_infra_report.md",
  "governance_meta_judgment.md",
  "governance_fix_prompt.md",
  "claude_fix_output_gov_1.txt",
  "governance_smoke_log.txt",
  "governance_appsec_report_2.md",
  "governance_legal_privacy_report_2.md",
  "governance_infra_report_2.md",
  "governance_meta_judgment_2.md",
  "governance_fix_prompt_2.md",
  "claude_fix_output_gov_2.txt",
  "governance_smoke_log_2.txt",
  "final_smoke_checks.txt",
  "sprint_requirements_check.txt",
  "sprint_report.md",
  "final_mvp_report.md",
  "run_state.json",
];

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${(s % 60).toString().padStart(2, "0")}s` : `${s}s`;
}

function sortArtifacts(files: string[]): string[] {
  return [...files].sort((a, b) => {
    const ai = ARTIFACT_ORDER.indexOf(a), bi = ARTIFACT_ORDER.indexOf(b);
    if (ai === -1 && bi === -1) return a.localeCompare(b);
    if (ai === -1) return 1; if (bi === -1) return -1;
    return ai - bi;
  });
}

type StepStatus = "pending" | "running" | "done" | "skipped" | "not_run" | "failed" | "blocked";

function stepStatus(
  step: PipelineStep,
  artifacts: string[],
  currentStep: string,
  runStatus: string,
  runSteps?: RunDetail["steps"],
): StepStatus {
  const stateStatus = runSteps?.[step.id]?.status;
  if (stateStatus) {
    if (stateStatus === "complete") return "done";
    return stateStatus;
  }
  const hasArtifact = artifacts.some(a => a === step.artifact || a.startsWith(step.artifact.replace(/\.[^.]+$/, "")));
  if (hasArtifact) return "done";
  // Terminal but this step never produced its artifact — it genuinely didn't run
  // (e.g. everything after "Planning Consistency Check" in a sprint-plan-only / plan-only run,
  // or everything after a consistency-violation block). Show that honestly instead of
  // the old behavior of marking every step "Finished" once the run reached a terminal
  // status.
  if (TERMINAL.has(runStatus)) return "skipped";
  if (STEP_MAP[currentStep] === step.id) return "running";
  return "pending";
}

function stepElapsed(stepId: string, timings: Record<string, number> = {}): number | null {
  if (timings[stepId] !== undefined) return timings[stepId];
  if (stepId === "done" && timings["report"] !== undefined) return timings["report"];
  const keys = Object.keys(timings).filter(k => k.startsWith(stepId + "_")).sort();
  return keys.length ? timings[keys[keys.length - 1]] : null;
}

// ── Run timeline (loop / cycle history) ─────────────────────────────────────────
// Built entirely from step_timings (cycle-suffixed keys already written by the pipeline)
// plus current_step for a best-effort "in progress" entry — no pipeline changes needed.
// This turns "the same cards bouncing back and forth" into an explicit ordered history
// of every loop iteration: "Smoke Check — Cycle 1", "DeepSeek Review — Cycle 1", ...,
// "Governance Review — Round 1", "Governance Fix — Round 1", ...

interface TimelineEvent {
  key: string;
  label: string;
  done: boolean;
  elapsedS: number | null;
}

const QUALITY_LOOP_LABELS: Record<string, string> = {
  smoke: "Smoke Check", deepseek: "DeepSeek Review", judge: "GPT Judge", fix: "Claude Fix",
};

function buildTimeline(run: RunDetail | null): TimelineEvent[] {
  if (!run) return [];
  const timings = run.step_timings ?? {};
  const events: TimelineEvent[] = [];
  const currentStep = run.current_step ?? "";

  // Quality loop: smoke_N / deepseek_N / judge_N / fix_N → "X — Cycle N"
  for (const prefix of ["smoke", "deepseek", "judge", "fix"]) {
    for (const key of Object.keys(timings)) {
      const m = key.match(new RegExp(`^${prefix}_(\\d+)$`));
      if (!m) continue;
      events.push({
        key,
        label: `${QUALITY_LOOP_LABELS[prefix]} — Cycle ${m[1]}`,
        done: true,
        elapsedS: timings[key],
      });
    }
  }
  if (currentStep && !timings[currentStep]) {
    const m = currentStep.match(/^(smoke|deepseek|judge|fix)_(\d+)$/);
    if (m) events.push({ key: currentStep, label: `${QUALITY_LOOP_LABELS[m[1]]} — Cycle ${m[2]}`, done: false, elapsedS: null });
  }

  // Governance loop: gov_appsec_N (review round marker) / gov_fix_N / gov_smoke_N
  // gov_legal_N / gov_infra_N / gov_meta_N are the same round as gov_appsec_N — skip dupes.
  for (const key of Object.keys(timings)) {
    let m = key.match(/^gov_appsec_(\d+)$/);
    if (m) { events.push({ key, label: `Governance Review — Round ${m[1]}`, done: true, elapsedS: timings[key] }); continue; }
    m = key.match(/^gov_fix_(\d+)$/);
    if (m) { events.push({ key, label: `Governance Fix — Round ${m[1]}`, done: true, elapsedS: timings[key] }); continue; }
    m = key.match(/^gov_smoke_(\d+)$/);
    if (m) { events.push({ key, label: `Governance Smoke Check — Round ${m[1]}`, done: true, elapsedS: timings[key] }); continue; }
  }
  if (currentStep) {
    let m = currentStep.match(/^gov_(appsec|legal|infra|meta)_(\d+)$/);
    if (m && !timings[`gov_appsec_${m[2]}`]) events.push({ key: currentStep, label: `Governance Review — Round ${m[2]}`, done: false, elapsedS: null });
    m = currentStep.match(/^gov_fix_(\d+)$/);
    if (m && !timings[currentStep]) events.push({ key: currentStep, label: `Governance Fix — Round ${m[1]}`, done: false, elapsedS: null });
  }

  // Order by cycle/round number first (so cycles interleave correctly: smoke1, deepseek1,
  // judge1, fix1, smoke2, ...), falling back to insertion order within the same number.
  const order = ["smoke", "deepseek", "judge", "fix", "gov_appsec", "gov_fix", "gov_smoke"];
  events.sort((a, b) => {
    const na = parseInt(a.key.match(/(\d+)$/)?.[1] ?? "0", 10);
    const nb = parseInt(b.key.match(/(\d+)$/)?.[1] ?? "0", 10);
    if (na !== nb) return na - nb;
    const pa = order.findIndex(p => a.key.startsWith(p));
    const pb = order.findIndex(p => b.key.startsWith(p));
    return pa - pb;
  });
  return events;
}

// ── Brand logos (SVG) ──────────────────────────────────────────────────────────

function GPTLogo({ size }: { size: number }) {
  // OpenAI bloom logo
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="white">
      <path d="M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a5.985 5.985 0 0 0-3.998 2.9 6.046 6.046 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.515 2.9A5.985 5.985 0 0 0 13.26 24a6.056 6.056 0 0 0 5.772-4.206 5.99 5.99 0 0 0 3.997-2.9 6.056 6.056 0 0 0-.747-7.073zM13.26 22.43a4.476 4.476 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.795.795 0 0 0 .392-.681v-6.737l2.02 1.168a.071.071 0 0 1 .038.052v5.583a4.504 4.504 0 0 1-4.494 4.494zM3.6 18.304a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.771.771 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.74 19.95a4.5 4.5 0 0 1-6.14-1.646zM2.34 7.896a4.485 4.485 0 0 1 2.366-1.973V11.6a.766.766 0 0 0 .388.677l5.815 3.355-2.02 1.168a.076.076 0 0 1-.071 0l-4.83-2.786A4.504 4.504 0 0 1 2.34 7.872zm16.597 3.855l-5.843-3.369 2.02-1.168a.076.076 0 0 1 .071 0l4.83 2.791a4.494 4.494 0 0 1-.676 8.105v-5.678a.79.79 0 0 0-.402-.681zm2.01-3.023l-.141-.085-4.774-2.782a.776.776 0 0 0-.785 0L9.409 9.23V6.897a.066.066 0 0 1 .028-.061l4.83-2.787a4.5 4.5 0 0 1 6.68 4.66zm-12.64 4.135l-2.02-1.164a.08.08 0 0 1-.038-.057V6.075a4.5 4.5 0 0 1 7.375-3.453l-.142.08L8.704 5.46a.795.795 0 0 0-.393.681zm1.097-2.365l2.602-1.5 2.607 1.5v2.999l-2.597 1.5-2.607-1.5z"/>
    </svg>
  );
}

function ClaudeLogo({ size }: { size: number }) {
  // Anthropic-style mark (simplified arc)
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="none">
      <path d="M50 10C27.9 10 10 27.9 10 50s17.9 40 40 40 40-17.9 40-40S72.1 10 50 10zm0 8c17.7 0 32 14.3 32 32 0 6.8-2.1 13.1-5.7 18.3L26.7 23.7C31.9 20.1 38.2 18 50 18zm0 64c-17.7 0-32-14.3-32-32 0-6.8 2.1-13.1 5.7-18.3l49.6 49.6C68.1 85.9 59.8 82 50 82z" fill="white" opacity="0.9"/>
    </svg>
  );
}

function DeepSeekLogo({ size }: { size: number }) {
  // Abstract D / wave mark
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="white">
      <path d="M20 20 h30 a30 30 0 0 1 0 60 h-30 z" opacity="0.15"/>
      <path d="M22 22 h28 a28 28 0 0 1 0 56 h-28 z" fill="none" stroke="white" strokeWidth="5" opacity="0.9"/>
      <circle cx="60" cy="38" r="7" fill="white"/>
      <path d="M30 55 Q45 42 62 55 Q78 68 92 55" fill="none" stroke="white" strokeWidth="4.5" strokeLinecap="round" opacity="0.8"/>
    </svg>
  );
}

function SmokeLogo({ size }: { size: number }) {
  // Terminal prompt icon
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="none" stroke="white" strokeLinecap="round" strokeLinejoin="round">
      <rect x="10" y="18" width="80" height="64" rx="10" strokeWidth="5" fill="none"/>
      <polyline points="24,42 38,50 24,58" strokeWidth="6"/>
      <line x1="44" y1="62" x2="76" y2="62" strokeWidth="5"/>
    </svg>
  );
}

function GovernanceLogo({ size }: { size: number }) {
  // Shield with checkmark — represents AppSec / Legal / Infra panel
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="none">
      <path d="M50 8 L86 23 L86 52 C86 71 69 87 50 94 C31 87 14 71 14 52 L14 23 Z"
            stroke="white" strokeWidth="5" fill="none" opacity="0.9"/>
      <polyline points="34,51 44,62 67,38" stroke="white" strokeWidth="6.5"
                strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}

function AgentIcon({ agent, size = 44, dimmed = false }: { agent: string; size?: number; dimmed?: boolean }) {
  const style = { opacity: dimmed ? 0.45 : 1 } as React.CSSProperties;
  const s = size;
  if (agent === "gpt" || agent === "architect") return <div style={style}><GPTLogo size={s} /></div>;
  if (agent === "claude")     return <div style={style}><ClaudeLogo size={s} /></div>;
  if (agent === "deepseek")   return <div style={style}><DeepSeekLogo size={s} /></div>;
  if (agent === "governance") return <div style={style}><GovernanceLogo size={s} /></div>;
  return <div style={style}><SmokeLogo size={s} /></div>;
}

// ── Small icons ────────────────────────────────────────────────────────────────

function IconBack() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
      <polyline points="15,18 9,12 15,6"/>
    </svg>
  );
}
function IconPipeline() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round">
      <circle cx="12" cy="12" r="3"/>
      <path d="M12 2v3M12 19v3M4.22 4.22l2.12 2.12M17.66 17.66l2.12 2.12M2 12h3M19 12h3M4.22 19.78l2.12-2.12M17.66 6.34l2.12-2.12"/>
    </svg>
  );
}
function IconPen() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
    </svg>
  );
}
function IconFile() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
      <polyline points="14,2 14,8 20,8"/>
    </svg>
  );
}
function IconIdea() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a7 7 0 0 1 7 7c0 2.6-1.4 4.9-3.5 6.2V17a1 1 0 0 1-1 1h-5a1 1 0 0 1-1-1v-1.8A7 7 0 0 1 12 2z"/>
      <line x1="9" y1="21" x2="15" y2="21"/>
      <line x1="10" y1="18" x2="14" y2="18"/>
    </svg>
  );
}
function IconJira() {
  return (
    <svg width="22" height="22" viewBox="0 0 32 32" fill="currentColor">
      <path d="M15.86 0C11.7 0 8.3 3.37 8.3 7.53v1.3H3.1A3.1 3.1 0 0 0 0 11.93v15.41C0 29.83 2.17 32 4.84 32h15.3a3.1 3.1 0 0 0 3.1-3.1v-1.3h5.22A4.54 4.54 0 0 0 32 23.06V7.53A7.53 7.53 0 0 0 24.47 0zm0 3.08a4.45 4.45 0 0 1 4.45 4.45v1.3h-8.9V7.53a4.45 4.45 0 0 1 4.45-4.45zm7.53 21.54H8.3V11.93c0-.01.01-.02.02-.02h14.93c.01 0 .02.01.02.02zm5.51-1.56a1.46 1.46 0 0 1-1.46 1.46h-1.97V11.93a3.1 3.1 0 0 0-3.1-3.1H11.4V7.53a4.45 4.45 0 0 1 8.9 0v.7h1.3a4.45 4.45 0 0 1 4.45 4.45v12.38z"/>
    </svg>
  );
}
function IconWrench() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94z"/>
    </svg>
  );
}
function IconRepeat() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="17 1 21 5 17 9"/>
      <path d="M3 11V9a4 4 0 0 1 4-4h14"/>
      <polyline points="7 23 3 19 7 15"/>
      <path d="M21 13v2a4 4 0 0 1-4 4H3"/>
    </svg>
  );
}
function IconClock() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/>
      <polyline points="12 6 12 12 16 14"/>
    </svg>
  );
}

// ── Status badge ───────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  done:                   { bg: "#f0fdf4", text: "#15803d", border: "#bbf7d0" },
  approved:               { bg: "#f0fdf4", text: "#15803d", border: "#bbf7d0" },
  queued:                 { bg: "#f8fafc", text: "#64748b", border: "#e2e8f0" },
  max_iterations_reached: { bg: "#fef2f2", text: "#dc2626", border: "#fecaca" },
};
const DEFAULT_SC = { bg: "#eff6ff", text: "#2563eb", border: "#bfdbfe" };

function StatusBadge({ status }: { status: string }) {
  const c = STATUS_COLORS[status] ?? DEFAULT_SC;
  const isRunning = !TERMINAL.has(status) && status !== "queued";
  return (
    <span className="status-badge" style={{ background: c.bg, color: c.text, border: `1px solid ${c.border}` }}>
      {isRunning && <span className="status-dot" style={{ background: c.text }} />}
      {status.replace(/_/g, " ")}
    </span>
  );
}

// ── Big step card ──────────────────────────────────────────────────────────────

function StepCard({ step, index, total, status, elapsed, cycle }: {
  step: PipelineStep;
  index: number;
  total: number;
  status: StepStatus;
  elapsed: number | null;
  cycle: number;
}) {
  const a = AGENTS[step.agent];
  const showCycle = cycle > 1 && ["smoke", "deepseek", "judge", "fix"].includes(step.id);

  const circleStyle: React.CSSProperties =
    status === "done"    ? { background: "linear-gradient(135deg, #22c55e, #15803d)" }
    : status === "pending" || status === "skipped" || status === "not_run" ? { background: "#d1d5db" }
    : status === "failed" || status === "blocked" ? { background: "#dc2626" }
    : { background: `linear-gradient(145deg, ${a.color}, ${a.color}cc)` };

  return (
    <div
      className={`step-card step-card-${status}`}
      style={{ "--agent-color": a.color, "--agent-light": a.light } as React.CSSProperties}
    >
      {/* Counter */}
      <div className="sc-counter">
        <span className="sc-idx">{index + 1}</span>
        <span className="sc-of">/ {total}</span>
        {showCycle && <span className="sc-cycle">cycle {cycle}</span>}
      </div>

      {/* Logo */}
      <div className="sc-logo-area">
        {status === "running" && (
          <>
            <div className="sc-ring sc-ring-1" />
            <div className="sc-ring sc-ring-2" />
          </>
        )}
        <div className="sc-logo-circle" style={circleStyle}>
          {status === "done" ? (
            <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.8" strokeLinecap="round">
              <polyline points="4,12 9,17 20,6"/>
            </svg>
          ) : status === "skipped" || status === "not_run" ? (
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth="2.5" strokeLinecap="round">
              <line x1="5" y1="12" x2="19" y2="12"/>
            </svg>
          ) : (
            <AgentIcon agent={step.agent} size={44} dimmed={status === "pending"} />
          )}
        </div>
      </div>

      {/* Info */}
      <div className="sc-info">
        <div className="sc-agent" style={{ color: status === "pending" || status === "skipped" || status === "not_run" ? "#9ca3af" : a.color }}>
          {a.label}
        </div>
        <div className="sc-name">{step.label}</div>
        <div className="sc-sep" />
        <div className="sc-desc">{step.sub}</div>

        <div className={`sc-badge sc-badge-${status}`}>
          {status === "running" && (
            <><span className="sc-badge-dot" style={{ background: a.color }} />Running…</>
          )}
          {status === "done" && elapsed !== null && `Finished in ${fmtTime(elapsed)}`}
          {status === "done" && elapsed === null && "Finished"}
          {status === "pending" && "Waiting to run"}
          {status === "skipped" && "Not being run"}
          {status === "not_run" && "Not run"}
          {status === "failed" && "Failed"}
          {status === "blocked" && "Blocked"}
        </div>
      </div>
    </div>
  );
}

// ── Terminal colorizer ─────────────────────────────────────────────────────────

function lineStyle(line: string): { color: string; bold: boolean } {
  const t = line.trim();

  // Blank / separator lines — invisible
  if (!t || t.startsWith("=") || t.startsWith("-")) return { color: "#1e2d3d", bold: false };

  // Pipeline section headers
  if (t.startsWith("▶"))  return { color: "#e2e8f0", bold: true };
  if (t.startsWith("🚀") || t.startsWith("🎉") || t.startsWith("📁") || t.startsWith("⏱"))
                           return { color: "#e2e8f0", bold: false };

  // Success / done
  if (t.startsWith("✓") || t.startsWith("✅") || /\b(PASS|passed|approved|complete|success)\b/i.test(t))
                           return { color: "#4ade80", bold: t.startsWith("✓") || t.startsWith("✅") };

  // Errors
  if (t.startsWith("✗") || t.startsWith("❌") || /\b(FAIL|ERROR|error:|Traceback|exception)\b/i.test(t))
                           return { color: "#f87171", bold: false };

  // Heartbeat — dimmed
  if (t.startsWith("⋯"))  return { color: "#475569", bold: false };

  // Claude Code file operations — cyan (the most interesting lines)
  if (/\b(Creating|Writing|Updating|Editing|Reading|Deleting)\b/.test(t) && /\.(py|ts|tsx|js|jsx|json|md|sh|css|html|sql)/.test(t))
                           return { color: "#67e8f9", bold: false };

  // Claude Code tool calls (⏺ ● ◆ symbols it uses)
  if (t.startsWith("⏺") || t.startsWith("●") || t.startsWith("◆"))
                           return { color: "#fb923c", bold: false };

  // Shell commands being run
  if (/\b(Running|Executing|npm |pip |python |bash |curl )\b/.test(t))
                           return { color: "#fbbf24", bold: false };

  // Architecture / smoke / consistency check results
  if (t.startsWith("[PASS]")) return { color: "#4ade80", bold: false };
  if (t.startsWith("[FAIL]")) return { color: "#f87171", bold: false };
  if (t.startsWith("[WARN]")) return { color: "#fbbf24", bold: false };

  // Issue judgment / governance classification labels
  if (/\bCRITICAL\b/.test(t))  return { color: "#f87171", bold: true };
  if (/\bMAJOR\b/.test(t))     return { color: "#fb923c", bold: false };
  if (/\bMINOR\b/.test(t))     return { color: "#fbbf24", bold: false };
  if (/\bNOISE\b/.test(t))     return { color: "#64748b", bold: false };

  // Governance panel headings
  if (/\b(AppSec|Legal|Privacy|Infra|Governance)\b/.test(t) && /^#{1,3} /.test(t))
                               return { color: "#c4b5fd", bold: true };

  // Sprint plan rendering — make "Architecture Sprint Plan" readable, not random text
  if (t === "Architecture Sprint Plan")            return { color: "#fbbf24", bold: true };
  if (/^Complexity:/.test(t))                      return { color: "#c4b5fd", bold: true };
  if (/^Recommended sprint count:/.test(t))         return { color: "#c4b5fd", bold: true };
  if (/^Reason:/.test(t))                           return { color: "#a5b4fc", bold: false };
  if (/^Sprint \d+ of \d+:/.test(t))                return { color: "#67e8f9", bold: true };
  if (/^Goal:/.test(t))                             return { color: "#e2e8f0", bold: false };
  if (/^(Why first|Why now):/.test(t))              return { color: "#94a3b8", bold: false };
  if (/^Output:/.test(t))                           return { color: "#94a3b8", bold: false };
  if (/^Build now: yes/.test(t))                    return { color: "#4ade80", bold: true };
  if (/^Build now: no/.test(t))                     return { color: "#64748b", bold: false };
  if (/^Selected Sprint:/.test(t))                  return { color: "#fbbf24", bold: true };
  if (t === "Claude Code will build only this sprint.") return { color: "#fbbf24", bold: true };

  // File paths mentioned standalone
  if (/^[./].*\.(py|ts|tsx|js|jsx|json|md|css|html|sql)$/.test(t))
                           return { color: "#7dd3fc", bold: false };

  // DeepSeek / attack report headings
  if (/^#{1,3} /.test(t)) return { color: "#c4b5fd", bold: true };
  if (/^VERDICT:/.test(t)) return { color: "#f9a8d4", bold: true };

  // Default readable text
  return { color: "#94a3b8", bold: false };
}

// ── Live terminal ──────────────────────────────────────────────────────────────

// Sprint mode display-only transform: the backend log line still says "GPT-mini —
// writing ARCHITECTURE.md" (architecture generation itself is unchanged), but in sprint
// mode the dashboard presents that step as the advanced "Sprint Architecture" role for
// demo clarity — same treatment as the StepCard label swap in getStepsForRun.
function transformLogLine(line: string, sprintMode: boolean): string {
  if (!sprintMode) return line;
  return line.replace(/GPT-mini\s*—\s*writing ARCHITECTURE\.md/, "GPT-4o — writing Sprint Architecture");
}

function TerminalView({ runId, sprintMode }: { runId: string; sprintMode: boolean }) {
  const [log, setLog] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const bodyRef   = useRef<HTMLDivElement>(null);
  const pinned    = useRef(true); // true = follow tail

  useEffect(() => {
    const poll = () =>
      getArtifact(runId, "pipeline.log")
        .then(a => setLog(a.content))
        .catch(() => {});
    poll();
    const i = setInterval(poll, 1500);
    return () => clearInterval(i);
  }, [runId]);

  // Detect manual scroll-up → unpin; scroll to bottom → repin
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const onScroll = () => {
      pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // Auto-scroll when pinned
  useEffect(() => {
    if (pinned.current) bottomRef.current?.scrollIntoView();
  }, [log]);

  const lines = log.split("\n");

  return (
    <div className="terminal-view">
      <div className="terminal-header">
        <span className="tdot tdot-red" />
        <span className="tdot tdot-yellow" />
        <span className="tdot tdot-green" />
        <span className="terminal-title">pipeline.log — live</span>
        <span className="terminal-cursor" />
      </div>
      <div className="terminal-body" ref={bodyRef}>
        {log ? lines.map((line, i) => {
          const displayLine = transformLogLine(line, sprintMode);
          const s = lineStyle(displayLine);
          return (
            <div key={i} className="tline" style={{ color: s.color, fontWeight: s.bold ? 700 : 400 }}>
              {displayLine || " "}
            </div>
          );
        }) : (
          <div className="tline" style={{ color: "#2d3748" }}>Waiting for pipeline to start...</div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ── Status banner ──────────────────────────────────────────────────────────────

function NowBanner({ run, elapsed, sprintModeActive, selectedSprintNum }: {
  run: RunDetail | null; elapsed: number; sprintModeActive: boolean; selectedSprintNum: number;
}) {
  const isTerminal = TERMINAL.has(run?.status ?? "");
  const currentStepId = STEP_MAP[run?.current_step ?? ""];
  const currentStep  = [...PIPELINE_STEPS, ...SPRINT_ONLY_STEPS].find(s => s.id === currentStepId);
  const a = currentStep ? AGENTS[currentStep.agent] : null;

  if (!run || run.status === "queued") return (
    <div className="now-banner now-banner-queued">
      <div className="nb-icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round">
          <circle cx="12" cy="12" r="10"/><polyline points="12,6 12,12 16,14"/>
        </svg>
      </div>
      <div className="nb-content">
        <div className="nb-label">Starting up</div>
        <div className="nb-title">Pipeline initializing your run</div>
      </div>
      <div className="nb-timer">{fmtTime(elapsed)}</div>
    </div>
  );

  if (isTerminal) {
    // Exact wording per run mode — never a generic "all done" message that would
    // overstate what actually ran (e.g. claiming a build happened in a plan-only run).
    let title = "All steps finished successfully";
    let sub = "";
    if (run.status === "max_iterations_reached") {
      title = "Max fix cycles reached — review output";
    } else if (run.status === "blocked_consistency_violation") {
      title = "Blocked: planning consistency violation";
    } else if (run.status === "plan_only_done") {
      title = "Plan Complete";
      sub = "Requirements, spec, architecture and build prompt were generated. No build was run.";
    } else if (run.status === "sprint_plan_only_done") {
      title = "Sprint Planning Complete";
      sub = "Claude Code, DeepSeek, and Governance were not run.";
    } else if (sprintModeActive) {
      title = "Sprint Build Complete";
      sub = `Sprint ${selectedSprintNum} was built. Future sprints are planned but not built.`;
    }
    return (
      <div className="now-banner now-banner-done">
        <div className="nb-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.8" strokeLinecap="round">
            <polyline points="4,12 9,17 20,6"/>
          </svg>
        </div>
        <div className="nb-content">
          <div className="nb-label">Complete</div>
          <div className="nb-title">{title}</div>
          {sub && <div className="nb-sub">{sub}</div>}
        </div>
        {run.pipeline_elapsed_s != null && (
          <div className="nb-timer">{fmtTime(run.pipeline_elapsed_s)}</div>
        )}
      </div>
    );
  }

  if (!currentStep || !a) return (
    <div className="now-banner now-banner-queued">
      <div className="nb-content"><div className="nb-title">Preparing</div></div>
      <div className="nb-timer">{fmtTime(elapsed)}</div>
    </div>
  );

  return (
    <div className="now-banner now-banner-running" style={{ background: a.color } as React.CSSProperties}>
      <div className="nb-agent-logo">
        <AgentIcon agent={currentStep.agent} size={22} />
      </div>
      <div className="nb-content">
        <div className="nb-label">Now running</div>
        <div className="nb-title">{currentStep.label}</div>
        <div className="nb-sub">{currentStep.sub}</div>
      </div>
      <div className="nb-timer">{fmtTime(elapsed)}</div>
    </div>
  );
}

// ── Sprint mode banner ───────────────────────────────────────────────────────────
// Parsed from sprint_plan.json once it appears in the artifacts list (deterministic,
// already normalized server-side by apply_selected_sprint) — robust for both
// dashboard-triggered runs (which also carry run.sprint_plan/selected_sprint) and
// CLI-triggered runs (which don't).

interface SprintEntry {
  number: number;
  title: string;
  goal?: string;
  requirements_covered?: Array<{ id?: string; title?: string } | string>;
  build_items?: string[];
  not_included?: string[];
  user_visible_result?: string;
  completion_criteria?: string[];
  dependencies?: number[];
  independently_demoable?: boolean;
  build_now?: boolean;
}

interface SprintInfo {
  product_name?: string;
  complexity_level?: string;
  recommended_sprint_count?: number;
  reason_for_sprint_count?: string;
  total_sprints?: number;
  selected_sprint?: number;
  sprints?: SprintEntry[];
}

// Two distinct banners depending on what the run is actually doing:
//  - planOnly: a sprint-plan-only run — the architect produced a plan, but nothing is
//    being built. "Sprint Planning Mode."
//  - otherwise: a run that is actually building one selected sprint with Claude Code.
//    "Sprint Mode Enabled."
function SprintModeBanner({ info, fallbackSelected, planOnly }: {
  info: SprintInfo | null; fallbackSelected: number; planOnly: boolean;
}) {
  const total = info?.total_sprints ?? info?.sprints?.length;
  const selected = info?.selected_sprint ?? fallbackSelected;

  if (planOnly) {
    return (
      <div className="sprint-mode-banner sprint-mode-banner-planning">
        <span className="sprint-mode-pill sprint-mode-pill-planning">Sprint Planning Mode</span>
        <span className="sprint-mode-line">
          {total ? `Architect generated ${total} sprint${total === 1 ? "" : "s"}` : "Architect is generating the sprint plan"}
        </span>
        <span className="sprint-mode-line">No sprint is being built in this run</span>
        <span className="sprint-mode-line sprint-mode-future">Claude Code not run</span>
      </div>
    );
  }

  return (
    <div className="sprint-mode-banner">
      <span className="sprint-mode-pill">Sprint Mode Enabled</span>
      <span className="sprint-mode-line">Building Sprint {selected}{total ? ` of ${total}` : ""}</span>
      <span className="sprint-mode-line sprint-mode-future">Future sprints planned but not built</span>
    </div>
  );
}

// ── Sprint cards (Stage 2 entry point) ──────────────────────────────────────────
// Renders the generated sprint plan as actionable cards once sprint_plan.json exists,
// so the user sees the architect's actual plan before picking a sprint to build —
// rather than guessing a sprint number before the plan exists.

function SprintCards({ sprints, selected, onRun, launching, canRun, builtSprints }: {
  sprints: SprintEntry[];
  selected: number;
  onRun: (n: number) => void;
  launching: number | null;
  canRun: boolean;
  // Sprint numbers this dashboard has direct evidence (claude_build_output.txt) were
  // actually built — not just selected. Used to gate dependency locks: a sprint with
  // unmet dependencies is locked, not just discouraged, so a demo can't accidentally
  // "build" Sprint 3 before Sprint 1/2 actually exist.
  builtSprints: number[];
}) {
  if (!sprints.length) return null;
  const ordered = [...sprints].sort((a, b) => a.number - b.number);

  return (
    <div className="sprint-cards">
      <div className="sprint-cards-title">Generated Sprint Plan — choose a sprint to build next</div>
      <div className="sprint-cards-order">
        Recommended build order: {ordered.map(s => `Sprint ${s.number}`).join(" → ")}
      </div>
      <div className="sprint-cards-list">
        {ordered.map(s => {
          const deps = s.dependencies ?? [];
          const missingDeps = deps.filter(d => !builtSprints.includes(d));
          const locked = missingDeps.length > 0;
          const lockLabel = locked
            ? `Locked until Sprint${missingDeps.length > 1 ? "s" : ""} ${missingDeps.join(" and ")} ${missingDeps.length > 1 ? "are" : "is"} complete`
            : deps.length === 0 && s.build_now
            ? "Ready to run · Recommended first"
            : "Ready to run";
          const requirements = (s.requirements_covered ?? []).map(requirement =>
            typeof requirement === "string"
              ? { id: "", title: requirement }
              : { id: requirement.id ?? "", title: requirement.title ?? "" }
          );
          const requirementLabels = requirements.map(r => r.id || r.title).filter(Boolean);
          const visibleRequirementLabels = requirementLabels.slice(0, 4);
          const buildItems = (s.build_items ?? []).filter(Boolean).slice(0, 4);
          return (
            <div key={s.number} className={`sprint-card ${s.number === selected ? "sprint-card-selected" : ""}`}>
              <div className="sc2-top">
                <span className="sc2-number">Sprint {s.number}</span>
                {s.build_now && <span className="sc2-tag sc2-tag-buildnow">Recommended first</span>}
                {s.independently_demoable && <span className="sc2-tag sc2-tag-demo">Independently demoable</span>}
              </div>
              <div className="sc2-title">{s.title}</div>
              {s.goal && <div className="sc2-goal">{s.goal}</div>}
              {!!visibleRequirementLabels.length && (
                <div className="sc2-requirements">
                  <span className="sc2-detail-label">Requirements</span>
                  <div className="sc2-requirement-pills">
                    {visibleRequirementLabels.map((label, i) => <span key={`${label}-${i}`} className="sc2-requirement-pill">{label}</span>)}
                    {requirementLabels.length > 4 && <span className="sc2-requirement-more">+{requirementLabels.length - 4}</span>}
                  </div>
                </div>
              )}
              {!!buildItems.length && (
                <div className="sc2-builds">
                  <span className="sc2-detail-label">Builds</span>
                  <ul>{buildItems.map((item, i) => <li key={i}>{item}</li>)}</ul>
                </div>
              )}
              {s.user_visible_result && (
                <div className="sc2-output"><span>Output:</span> {s.user_visible_result}</div>
              )}
              {!!deps.length && (
                <div className="sc2-deps">Depends on: {deps.map(d => `Sprint ${d}`).join(", ")}</div>
              )}
              <div className={`sc2-status ${locked ? "sc2-status-locked" : "sc2-status-ready"}`}>{lockLabel}</div>
              <button
                className="sc2-run-btn"
                disabled={!canRun || launching !== null || locked}
                onClick={() => onRun(s.number)}
                title={
                  locked ? lockLabel :
                  canRun ? undefined :
                  "Original input not available for this run — start a new run to use this action"
                }
              >
                {launching === s.number ? "Starting…" : locked ? "Locked" : canRun ? `Run Sprint ${s.number}` : `Run Sprint ${s.number} (next step)`}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Sprint completion report ────────────────────────────────────────────────────
// Surfaces the build outcome explicitly instead of leaving it buried in the artifact
// tabs: either "no sprint was built" (sprint-plan-only, or a build that never produced
// proof it ran) or a real completion summary built from sprint_plan.json + the
// recommended next sprint's dependency chain.
function SprintCompletionReport({ sprintModeActive, sprintPlanOnlyActive, built, selectedSprintNum, sprintInfo }: {
  sprintModeActive: boolean;
  sprintPlanOnlyActive: boolean;
  built: boolean;
  selectedSprintNum: number;
  sprintInfo: SprintInfo | null;
}) {
  if (!sprintModeActive) return null;

  const sprints = sprintInfo?.sprints ?? [];
  const current = sprints.find(s => s.number === selectedSprintNum);
  const next = sprints.find(s => s.dependencies?.includes(selectedSprintNum)) ??
    sprints.find(s => s.number === selectedSprintNum + 1);

  if (!built) {
    return (
      <div className="sprint-completion-report sprint-completion-report-empty">
        <div className="sprint-completion-title">Sprint Completion Report</div>
        <div className="sprint-completion-line">No sprint was built in this run.</div>
        <div className="sprint-completion-line sprint-completion-muted">
          {sprintPlanOnlyActive
            ? "This was a sprint-plan-only run — Claude Code was not invoked and Sprint Requirements Check was not run."
            : "No build artifact (claude_build_output.txt) was produced, so no sprint build can be confirmed."}
        </div>
      </div>
    );
  }

  return (
    <div className="sprint-completion-report sprint-completion-report-built">
      <div className="sprint-completion-title">Sprint Completion Report</div>
      <div className="sprint-completion-line sprint-completion-headline">
        Sprint {selectedSprintNum} Complete{current?.title ? `: ${current.title}` : ""}
      </div>
      {current?.user_visible_result && (
        <div className="sprint-completion-line"><span>Output:</span> {current.user_visible_result}</div>
      )}
      <div className="sprint-completion-line sprint-completion-muted">
        Intentionally not built: everything in later sprints of this plan.
      </div>
      {next ? (
        <div className="sprint-completion-line">
          <span>Recommended next step:</span> Sprint {next.number}{next.title ? `: ${next.title}` : ""}
        </div>
      ) : (
        <div className="sprint-completion-line sprint-completion-muted">This was the last sprint in the plan.</div>
      )}
    </div>
  );
}

// ── Run timeline panel ──────────────────────────────────────────────────────────

function RunTimeline({ events }: { events: TimelineEvent[] }) {
  if (!events.length) return null;
  return (
    <div className="run-timeline">
      <div className="run-timeline-title">Run Timeline</div>
      <div className="run-timeline-list">
        {events.map(e => (
          <div key={e.key} className={`rt-event rt-event-${e.done ? "done" : "running"}`}>
            <span className="rt-dot" />
            <span className="rt-label">{e.label}</span>
            {e.elapsedS !== null && <span className="rt-time">{fmtTime(e.elapsedS)}</span>}
            {e.elapsedS === null && !e.done && <span className="rt-time rt-time-live">running…</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Six-section pipeline overview ───────────────────────────────────────────────
// The flat 12-15 card stepper (StepCard/steps-carousel above) is accurate but reads
// as one long undifferentiated list. This groups the same underlying step evidence
// into the six stages a person actually thinks in: Product Planning, Sprint Roadmap,
// Sprint Build, Review & Fix, Governance, Report & Deploy. It does not replace the
// step-level data — it's a second view over the same `statuses`/artifacts truth.

type SectionStatus = "done" | "active" | "not_running" | "upcoming";

interface SectionSubstep {
  label: string;
  status: StepStatus | "disabled"; // "disabled": structurally not part of this run mode
}

interface SectionDef {
  id: string;
  title: string;
  blurb: string;
  status: SectionStatus;
  substeps: SectionSubstep[];
}

function rollupStatus(substeps: SectionSubstep[], applicable: boolean): SectionStatus {
  if (!applicable) return "not_running";
  const active = substeps.filter(s => s.status !== "disabled");
  if (!active.length) return "not_running";
  if (active.every(s => s.status === "skipped" || s.status === "not_run")) return "not_running";
  if (active.some(s => s.status === "failed" || s.status === "blocked")) return "done";
  if (active.some(s => s.status === "running")) return "active";
  if (active.some(s => s.status === "pending")) return "upcoming";
  // No substep left pending or running: every remaining one is either "done" or
  // (deliberately, permanently) "skipped" — the section has run as far as it ever will.
  return "done";
}

function buildSections(opts: {
  statusById: Record<string, StepStatus>;
  runArtifacts: string[];
  sprintModeActive: boolean;
}): SectionDef[] {
  const { statusById, runArtifacts, sprintModeActive } = opts;
  const get = (id: string): StepStatus => statusById[id] ?? "pending";
  const hasRawInput = runArtifacts.includes("raw_input.md");

  // 1. Product Planning
  const planningSubsteps: SectionSubstep[] = [
    { label: "Raw Input",                                            status: hasRawInput ? "done" : "pending" },
    { label: "Requirements Normalization", status: get("requirements_normalization") },
    { label: "MVP Spec", status: get("mvp_spec") },
    { label: sprintModeActive ? "Sprint Architecture" : "Architecture", status: get("sprint_architecture") },
    { label: sprintModeActive ? "Selected Sprint Prompt" : "Build Prompt", status: get("selected_sprint_prompt") },
    { label: "Planning Consistency Check", status: get("planning_consistency_check") },
  ];

  // 3. Sprint Build — "Generated MVP Folder" has no tracked artifact of its own
  // (mvp/ is a directory, not a file in run_state.json's artifacts list), so it mirrors
  // the evidence we do have: claude_build_output.txt is only written from inside that
  // folder once Claude Code has run.
  const buildSubsteps: SectionSubstep[] = [
    { label: sprintModeActive ? "Build Selected Sprint" : "Build MVP", status: get("claude_build") },
  ];

  // 4. Review & Fix — not run at all in plan-only / sprint-plan-only modes
  const reviewSubsteps: SectionSubstep[] = [
    { label: "Smoke Checks", status: get("smoke_checks") },
    { label: "DeepSeek Red Team Review", status: get("deepseek_red_team") },
    { label: "Governance Review", status: get("governance_review") },
  ];

  // 5. Governance — each reviewer's report is its own artifact, checked directly so a
  // partially-run governance panel never reads as fully "done" or fully "not run".
  const governanceSubsteps: SectionSubstep[] = [
    { label: "Consolidated Fix Plan", status: get("consolidated_fix_plan") },
    { label: "Claude Fix Pass", status: get("claude_fix_pass") },
  ];

  // 6. Report & Deploy — Deploy to AWS is a permanently disabled placeholder; it is
  // never wired to any real evidence and must never read as "done" or "active".
  const reportSubsteps: SectionSubstep[] = [
    { label: "Final Smoke Checks", status: get("final_smoke_checks") },
    { label: "Sprint Requirements Check", status: get("sprint_requirements_check") },
    { label: sprintModeActive ? "Sprint Report" : "Final Report", status: get("sprint_report") },
  ];

  return [
    { id: "planning", title: "Phase 1 — Planning", blurb: "Creating and validating the selected sprint plan before any build.", status: rollupStatus(planningSubsteps, true), substeps: planningSubsteps },
    { id: "build", title: "Phase 2 — Build", blurb: "Building the selected sprint with Claude Code.", status: rollupStatus(buildSubsteps, true), substeps: buildSubsteps },
    { id: "review", title: "Phase 3 — Verification / Review", blurb: "Running smoke, red-team, and governance reviews in order.", status: rollupStatus(reviewSubsteps, true), substeps: reviewSubsteps },
    { id: "governance", title: "Phase 4 — Consolidated Fix", blurb: "Combining confirmed findings into one minimal fix pass.", status: rollupStatus(governanceSubsteps, true), substeps: governanceSubsteps },
    { id: "report", title: "Phase 5 — Final Acceptance", blurb: "Separately checking final smoke evidence and sprint requirements.", status: rollupStatus(reportSubsteps, true), substeps: reportSubsteps },
  ];
}

const SECTION_STATUS_LABEL: Record<SectionStatus, string> = {
  done: "Complete",
  active: "In progress",
  not_running: "Not being run",
  upcoming: "Planned",
};

function SectionCard({ section }: { section: SectionDef }) {
  return (
    <details className={`pipeline-section pipeline-section-${section.status}`} open={section.status === "active"}>
      <summary className="ps-top">
        <span className="ps-title">{section.title}</span>
        <span className={`ps-status ps-status-${section.status}`}>{SECTION_STATUS_LABEL[section.status]}</span>
      </summary>
      <div className="ps-blurb">{section.blurb}</div>
      <div className="ps-substeps">
        {section.substeps.map(s => {
          const selfExplanatory = s.label.includes("—");
          const suffix =
            !selfExplanatory && (s.status === "skipped" || s.status === "disabled") ? " — not being run" : "";
          return (
            <div key={s.label} className={`ps-substep ps-substep-${s.status}`}>
              <span className="ps-substep-dot" />
              <span className="ps-substep-label">{s.label}{suffix}</span>
            </div>
          );
        })}
      </div>
    </details>
  );
}

function PipelineSectionOverview({ sections }: { sections: SectionDef[] }) {
  return (
    <div className="pipeline-sections">
      {sections.map(s => <SectionCard key={s.id} section={s} />)}
    </div>
  );
}

// ── Pipeline view (main redesign) ──────────────────────────────────────────────

// ── Existing App Upgrade mode view ──────────────────────────────────────────────
// Minimal, read-only v1: a panel per planning/build artifact plus a feature sprint
// roadmap parsed from feature_sprint_plan.json. Scaffolding for a richer dashboard
// later (Gap Analysis / Additive Architecture as structured panels, sprint-card
// "Run next sprint" actions like normal sprint mode) — kept simple here on purpose.

interface FeatureSprintEntry {
  sprint_number: number;
  title: string;
  goal: string;
  features?: string[];
  depends_on?: number[];
  status?: string;
  buildable?: boolean;
  must_not_modify?: string[];
  overlap_warnings?: string[];
  overlap_matched_files?: string[];
}

// A sprint in either of these statuses must never expose a normal, active "Build" button —
// it overlaps with functionality the Existing Feature Overlap Check found already exists
// (fully or partially), so building it as scoped risks creating duplicate features.
const OVERLAP_BLOCKING_STATUSES = new Set(["needs_revision", "blocked_overlap"]);

interface FeatureSprintPlan {
  mode?: string;
  product_name?: string;
  reason_for_split?: string;
  baseline?: { sprint_number: number; title: string; status: string; buildable: boolean; description: string };
  sprints?: FeatureSprintEntry[];
  total_sprints?: number;
  selected_feature_sprint?: number;
}

const UPGRADE_ARTIFACT_PANELS: { file: string; label: string }[] = [
  { file: "git_sync_report.md", label: "Git Sync Report" },
  { file: "git_sync_state.json", label: "Git Sync State JSON" },
  { file: "git_pull_report.md", label: "Git Pull Report" },
  { file: "git_pull_state.json", label: "Git Pull State JSON" },
  { file: "git_sync_before_pull.json", label: "Git Sync — Before Pull" },
  { file: "git_sync_after_pull.json", label: "Git Sync — After Pull" },
  { file: "pr_delivery_plan.md", label: "PR Delivery Plan" },
  { file: "pr_state.json", label: "PR State JSON" },
  { file: "existing_app_inventory.md", label: "Existing App Inventory" },
  { file: "baseline_health_check.md", label: "Baseline Health Check" },
  { file: "baseline_behavior_checklist.md", label: "Baseline Behavior Checklist" },
  { file: "existing_app_summary.md", label: "Existing App Summary" },
  { file: "new_feature_requirements.md", label: "New Feature Requirements" },
  { file: "change_gap_analysis.md", label: "Gap Analysis" },
  { file: "additive_architecture.md", label: "Additive Architecture" },
  { file: "feature_sprint_plan.md", label: "Feature Sprint Plan" },
  { file: "feature_sprint_plan.json", label: "Feature Sprint Plan JSON" },
  { file: "selected_feature_sprint_scope.md", label: "Selected Feature Sprint Scope" },
  { file: "selected_feature_sprint_build_prompt.txt", label: "Selected Feature Sprint Build Prompt" },
  { file: "selected_feature_change_boundary.md", label: "Selected Feature Change Boundary" },
  { file: "selected_feature_change_boundary.json", label: "Selected Feature Change Boundary JSON" },
  { file: "changed_files_report.md", label: "Changed Files Report" },
  { file: "smoke_test_log.txt", label: "Smoke Test Log" },
  { file: "smoke_mutation_report.md", label: "Smoke Mutation Report" },
  { file: "smoke_mutation_report.json", label: "Smoke Mutation Report JSON" },
  { file: "review_finding_classification.md", label: "Review Finding Classification" },
  { file: "review_finding_classification.json", label: "Review Finding Classification JSON" },
  { file: "boundary_violation_report.md", label: "Boundary Violation Report" },
  { file: "regression_check.md", label: "Regression Check" },
  { file: "feature_completion_report.md", label: "Feature Completion Report" },
];

function FeatureSprintRoadmap({ plan, onBuild, launching }: {
  plan: FeatureSprintPlan; onBuild?: (n: number) => void; launching?: number | null;
}) {
  const selected = plan.selected_feature_sprint;
  const sprints = [...(plan.sprints ?? [])].sort((a, b) => a.sprint_number - b.sprint_number);
  return (
    <div className="upgrade-roadmap">
      <div className={`upgrade-sprint-card upgrade-sprint-baseline`}>
        <div className="upgrade-sprint-title">Sprint 0 — {plan.baseline?.title ?? "Baseline Existing App"}</div>
        <div className="upgrade-sprint-meta">Not buildable — regression target only</div>
      </div>
      {sprints.map(s => {
        const status = s.status ?? "ready";
        const hasOverlap = OVERLAP_BLOCKING_STATUSES.has(status) || (s.overlap_warnings?.length ?? 0) > 0;
        return (
          <div
            key={s.sprint_number}
            className={`upgrade-sprint-card${s.sprint_number === selected ? " upgrade-sprint-selected" : ""}${hasOverlap ? " upgrade-sprint-overlap" : ""}`}
          >
            <div className="upgrade-sprint-title">
              Sprint {s.sprint_number} — {s.title}
              {s.sprint_number === selected && <span className="upgrade-sprint-pill">SELECTED</span>}
              {hasOverlap && <span className="upgrade-sprint-pill upgrade-sprint-pill-warning">OVERLAP</span>}
            </div>
            <div className="upgrade-sprint-goal">{s.goal}</div>
            <div className="upgrade-sprint-meta">
              Depends on: {(s.depends_on ?? [0]).map(d => `Sprint ${d}`).join(", ")} · Status: {status}
            </div>
            {hasOverlap && (
              <div className="upgrade-sprint-overlap-warning">
                ⚠️ This feature appears partially/already implemented. Extend matched existing files
                instead of creating duplicate files.
                {(s.overlap_matched_files?.length ?? 0) > 0 && (
                  <ul>
                    {s.overlap_matched_files!.map(f => <li key={f}>{f}</li>)}
                  </ul>
                )}
              </div>
            )}
            {onBuild && (
              hasOverlap ? (
                <button className="submit-btn submit-btn-disabled" disabled title="This sprint needs roadmap revision before it can be safely built.">
                  Needs roadmap revision before build
                </button>
              ) : (
                <button className="submit-btn" onClick={() => onBuild(s.sprint_number)} disabled={launching !== null}>
                  {launching === s.sprint_number ? "Starting…" : `Build Feature Sprint ${s.sprint_number}`}
                </button>
              )
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Delivery & Git Safety card ──────────────────────────────────────────────
// Local Delivery + Optional Sandbox Push: makes it impossible to confuse "local
// commit" with "GitHub push". Repo path is never client-supplied — the backend
// reads it from the run's own state, so this UI can only ever act on the repo
// the run was actually created against.

const DELIVERY_ARTIFACT_LABELS: Record<string, string> = {
  "delivery_safety_check.md": "Delivery Safety Check",
  "delivery/delivery_safety_check.md": "Delivery Safety Check",
  "delivery_state.json": "Delivery State",
  "delivery/delivery_state.json": "Delivery State",
  "changed_files_report.md": "Changed Files Report",
  "github_delivery_plan.md": "GitHub Delivery Plan",
  "delivery/github_delivery_plan.md": "GitHub Delivery Plan",
  "local_commit_summary.md": "Local Commit Summary",
  "push_result.md": "Push Result",
  "repo_hygiene_report.md": "Repo Hygiene Report",
  "delivery/repo_hygiene_report.md": "Repo Hygiene Report",
  "repo_hygiene_report.json": "Repo Hygiene Report JSON",
  "delivery/repo_hygiene_report.json": "Repo Hygiene Report JSON",
};

const DELIVERY_CHECK_ORDER = [
  "target_repo_detected", "company_repo_protection", "current_branch_not_main",
  "working_tree_clean_before_delivery", "denied_files_not_staged",
  "local_commit_allowed", "github_push_allowed",
];

const DELIVERY_CHECK_LABELS: Record<string, string> = {
  target_repo_detected: "Target repo detected",
  company_repo_protection: "Company repo protection",
  current_branch_not_main: "Delivery branch is not main",
  working_tree_clean_before_delivery: "Working tree clean before delivery",
  denied_files_not_staged: "Denied files not staged",
  local_commit_allowed: "Local commit allowed",
  github_push_allowed: "GitHub push allowed",
};

function DeliveryChecklist({ precheck }: { precheck: DeliveryPrecheck | null }) {
  if (!precheck) return <div className="delivery-checklist-empty">Checking repo safety…</div>;
  return (
    <div className="delivery-checklist">
      {DELIVERY_CHECK_ORDER.filter(k => precheck.checks[k]).map(k => {
        const c = precheck.checks[k];
        const icon = c.status === "pass" ? "✓" : c.status === "warn" ? "!" : "✕";
        return (
          <div key={k} className={`delivery-check-row delivery-check-${c.status}`}>
            <span className="delivery-check-icon">{icon}</span>
            <span className="delivery-check-label">{DELIVERY_CHECK_LABELS[k] ?? k}</span>
            <span className="delivery-check-detail">{c.detail}</span>
          </div>
        );
      })}
    </div>
  );
}

function DeliveryStatusBadge({ decision }: { decision?: string | null }) {
  if (!decision) return <span className="delivery-badge delivery-badge-idle">Ready to create local commit</span>;
  const map: Record<string, { label: string; cls: string }> = {
    PASS_LOCAL_ONLY: { label: "Local only", cls: "ok" },
    PASS_SANDBOX_PUSH: { label: "Sandbox push allowed", cls: "ok" },
    BLOCKED: { label: "Blocked", cls: "fail" },
  };
  const v = map[decision] ?? { label: decision, cls: "warn" };
  return <span className={`delivery-badge delivery-badge-${v.cls}`}>{v.label}</span>;
}

// Selected Feature Change Boundary summary — derived directly from run_state.json
// fields written by pipeline_existing_app_upgrade. Shown above the Delivery card so
// it's clear BEFORE looking at delivery whether the build/fix pass stayed in scope.
function ChangeBoundaryBanner({ run }: { run: RunDetail | null }) {
  const status = run?.change_boundary_status;
  if (!status) return null;
  const violations = run?.boundary_violation_count ?? 0;
  const outOfScope = run?.out_of_scope_review_findings ?? 0;
  const blocked = !!run?.local_delivery_blocked_by_boundary;
  return (
    <div className={`boundary-banner boundary-banner-${status === "FAIL" ? "fail" : "pass"}`}>
      <div className="boundary-banner-row">
        <span className={`delivery-badge delivery-badge-${status === "FAIL" ? "fail" : "ok"}`}>
          Change Boundary: {status}
        </span>
        {blocked && <span className="delivery-badge delivery-badge-fail">Local Delivery blocked</span>}
      </div>
      <div className="boundary-banner-detail">
        {status === "FAIL"
          ? `${violations} file(s) outside the selected sprint were changed or deleted. See boundary_violation_report.md.`
          : "All build and fix-pass changes stayed inside the selected sprint's file boundary."}
        {outOfScope > 0 && ` ${outOfScope} review finding(s) were filtered out as out-of-scope and were not fixed.`}
      </div>
    </div>
  );
}

// Smoke Mutation — fields written by pipeline_existing_app_upgrade after smoke checks run.
// Detects whether the smoke-check commands themselves (e.g. `npm install`/`npm ci`) changed a
// tracked file — kept separate from ChangeBoundaryBanner so a lockfile rewrite caused by smoke
// is never read as "the build broke the boundary."
function SmokeMutationBanner({ run }: { run: RunDetail | null }) {
  const status = run?.smoke_mutation_status;
  if (!status) return null;
  const fileCount = run?.smoke_mutation_file_count ?? 0;
  const blocked = !!run?.smoke_mutation_blocked_delivery;
  const badgeClass = status === "FAIL" ? "fail" : status === "WARN" ? "warn" : "ok";
  return (
    <div className={`boundary-banner boundary-banner-${status === "FAIL" ? "fail" : "pass"}`}>
      <div className="boundary-banner-row">
        <span className={`delivery-badge delivery-badge-${badgeClass}`}>
          Smoke Mutation: {status}
        </span>
        {blocked && <span className="delivery-badge delivery-badge-fail">Local Delivery blocked</span>}
      </div>
      <div className="boundary-banner-detail">
        {status === "PASS"
          ? "Smoke checks (npm install / pip install, etc.) did not change any tracked file."
          : `Smoke checks changed ${fileCount} tracked file(s) after the build finished — not a Claude `
            + `build change. See smoke_mutation_report.md.`}
        {status === "FAIL" && " This is outside the selected feature boundary and blocks Local Delivery."}
      </div>
    </div>
  );
}

// Git Sync & Pull Safety — read-only foundation for collaborative existing app repos
// (e.g. OneHR/OneATS) where other developers are constantly pushing. Pulls full detail
// from git_sync_state.json (written by delivery.run_git_sync_check); falls back to the
// summary fields on run_state.json if the artifact hasn't loaded yet. Never offers a
// pull/update action here — this is fetch + status only, never push/reset/stash/pull.
function GitSyncCard({ runId, run }: { runId: string; run: RunDetail | null }) {
  const [state, setState] = useState<GitSyncState | null>(null);
  const [pull, setPull] = useState<GitPullState | null>(null);
  const hasArtifact = (run?.artifacts ?? []).includes("git_sync_state.json");
  const hasPullArtifact = (run?.artifacts ?? []).includes("git_pull_state.json");

  useEffect(() => {
    if (!hasArtifact) { setState(null); return; }
    getGitSyncState(runId).then(setState).catch(() => setState(null));
  }, [runId, hasArtifact]);

  useEffect(() => {
    if (!hasPullArtifact) { setPull(null); return; }
    getGitPullState(runId).then(setPull).catch(() => setPull(null));
  }, [runId, hasPullArtifact]);

  if (!run?.git_sync_status && !state) return null;

  // Pull action: not requested / blocked / succeeded / failed / no update needed.
  // NO_OP (already up to date) is a safe success state, not a failure — it must never
  // render with the "blocked"/fail styling.
  const pullDecision = pull?.decision ?? run?.git_pull_status ?? null;
  const pullAction: "not requested" | "blocked" | "succeeded" | "failed" | "no update needed" =
    pullDecision === "PULLED" ? "succeeded"
    : pullDecision === "NO_OP" ? "no update needed"
    : pullDecision === "FAILED" ? "failed"
    : pullDecision === "BLOCKED" ? "blocked"
    : "not requested";
  const pullBadgeClass =
    pullAction === "succeeded" || pullAction === "no update needed" ? "ok"
    : pullAction === "blocked" || pullAction === "failed" ? "fail"
    : "idle";

  const status = state?.sync_status ?? run?.git_sync_status ?? "unknown";
  const blocked = state?.pull_blocked ?? !!run?.git_sync_blocked;
  const buildProceed = state?.build_should_proceed
    ?? (run?.git_sync_blocked ? "no" : (status === "behind" || status === "diverged") ? "warn" : "yes");
  const badgeClass = blocked ? "fail" : (status === "behind" || status === "diverged") ? "warn" : "ok";

  return (
    <div className="delivery-card">
      <div className="delivery-card-header">
        <div>
          <div className="delivery-card-title">Git Sync &amp; Pull Safety</div>
          <div className="delivery-card-sub">
            {run?.git_sync_summary ?? "Read-only fetch + status check against the target repo's base branch."}
          </div>
        </div>
        <span className={`delivery-badge delivery-badge-${badgeClass}`}>{status.replace("_", " ")}</span>
      </div>

      <div className="delivery-repo-line">
        Current branch: <code>{state?.current_branch ?? "(unknown)"}</code>
        {" "}· Base branch: <code>{state?.base_branch ?? "main"}</code>
      </div>
      <div className="delivery-repo-line">
        Ahead: <code>{state?.commits_ahead ?? 0}</code> · Behind: <code>{state?.commits_behind ?? 0}</code>
        {" "}· Safe to pull (fast-forward): <code>{String(state?.fast_forward_safe ?? false)}</code>
        {" "}· Build should proceed: <code>{buildProceed}</code>
      </div>

      {state?.is_company_repo && (
        <div className="delivery-warning-panel">
          Company repo detected. Fetch/status checks are allowed, but pull/update must be explicitly
          approved. The pipeline will not discard, reset, stash, or push changes automatically.
        </div>
      )}

      {blocked && (state?.block_reasons?.length ?? 0) > 0 && (
        <div className="delivery-warning-panel delivery-warning-panel-severe">
          <strong>Pull blocked.</strong>
          <ul>{state!.block_reasons.map(r => <li key={r}>{r}</li>)}</ul>
        </div>
      )}

      <div className="delivery-command-preview">
        <div className="delivery-command-preview-label">
          {pullAction === "not requested" ? "Recommended command (run manually if you choose)"
            : pullAction === "no update needed" ? "Pull command (not run — nothing to pull)"
            : "Pull command run"}
        </div>
        <pre>{pull?.pull_command ?? state?.recommended_command ?? "No fast-forward pull is recommended right now."}</pre>
      </div>

      <div className="delivery-card-header">
        <div className="delivery-card-title" style={{ fontSize: "0.95em" }}>Pull action</div>
        <span className={`delivery-badge delivery-badge-${pullBadgeClass}`}>{pullAction}</span>
      </div>

      {pullAction === "not requested" ? (
        <div className="delivery-repo-line">
          No pull was requested for this run. Pass <code>--git-pull-ff-only</code> to run the guarded
          fast-forward pull shown above.
        </div>
      ) : pullAction === "no update needed" ? (
        <>
          <div className="delivery-repo-line">
            Local repo now up to date: <code>yes</code> — the repo was already up to date with{" "}
            <code>origin/{pull?.base_branch ?? "main"}</code>, so no pull command was run.
          </div>
          <div className="delivery-repo-line">
            No push/reset/stash performed: <code>true</code>
          </div>
        </>
      ) : (
        <>
          <div className="delivery-repo-line">
            Before status: <code>{pull?.now_up_to_date === false ? "not up to date" : "(see git_sync_before_pull.json)"}</code>
            {" "}· After status: <code>{pull ? (pull.now_up_to_date ? "up to date" : "not up to date") : "(unknown)"}</code>
          </div>
          <div className="delivery-repo-line">
            Local repo now up to date: <code>{String(pull?.now_up_to_date ?? false)}</code>
          </div>
          {pullAction === "blocked" && (pull?.block_reasons?.length ?? 0) > 0 && (
            <div className="delivery-warning-panel delivery-warning-panel-severe">
              <strong>Pull blocked.</strong>
              <ul>{pull!.block_reasons.map(r => <li key={r}>{r}</li>)}</ul>
            </div>
          )}
          {pullAction === "failed" && (
            <div className="delivery-warning-panel delivery-warning-panel-severe">
              <strong>Pull failed.</strong> Exit code {pull?.pull_exit_code}. See git_pull_report.md.
            </div>
          )}
          <div className="delivery-repo-line">
            No push/reset/stash performed: <code>{String(
              (pull?.no_push_performed ?? true) && (pull?.no_reset_performed ?? true) && (pull?.no_stash_performed ?? true)
            )}</code>
            {pull?.is_company_repo && " — pull-only local update, never published or merged."}
          </div>
        </>
      )}
    </div>
  );
}

// Pull Request Plan — planning layer for collaborative repos (e.g. OneHR/OneATS):
// sync now, branch/commit/push-branch/open-PR LATER. Pulls full detail from
// pr_state.json (written by delivery.run_pr_delivery_plan); falls back to the
// summary fields on run_state.json if the artifact hasn't loaded yet. This card
// never offers a branch/commit/push/PR action — it is plan only, always.
function PrPlanCard({ runId, run }: { runId: string; run: RunDetail | null }) {
  const [plan, setPlan] = useState<PrDeliveryPlanState | null>(null);
  const hasArtifact = (run?.artifacts ?? []).includes("pr_state.json");

  useEffect(() => {
    if (!hasArtifact) { setPlan(null); return; }
    getPrDeliveryPlanState(runId).then(setPlan).catch(() => setPlan(null));
  }, [runId, hasArtifact]);

  if (!run?.pr_plan_status && !plan) return null;

  const readiness = plan?.pr_readiness ?? run?.pr_plan_status ?? "blocked";
  const readinessBadgeClass =
    readiness === "ready" ? "ok"
    : readiness === "pr_workflow_required" ? "warn"
    : readiness === "warning" ? "warn"
    : "fail";
  const branchAction =
    readiness === "blocked" ? "blocked"
    : plan?.future_push_approval_required ? "future approval required"
    : "allowed later";

  return (
    <div className="delivery-card">
      <div className="delivery-card-header">
        <div>
          <div className="delivery-card-title">Pull Request Plan</div>
          <div className="delivery-card-sub">
            {run?.pr_plan_summary ?? "Read-only PR readiness plan — sync now, branch/commit/push/PR later."}
          </div>
        </div>
        <span className={`delivery-badge delivery-badge-${readinessBadgeClass}`}>{readiness.replace(/_/g, " ")}</span>
      </div>

      <div className="delivery-warning-panel">
        <strong>Plan only</strong> — no branch, commit, push, or PR was created.
      </div>

      <div className="delivery-repo-line">
        Base branch: <code>{plan?.base_branch ?? "main"}</code>
        {" "}· Suggested feature branch: <code>{plan?.suggested_branch ?? run?.pr_plan_branch ?? "(unknown)"}</code>
      </div>
      {plan?.pr_title && (
        <div className="delivery-repo-line">PR title: <code>{plan.pr_title}</code></div>
      )}
      <div className="delivery-repo-line">
        Repo type: <code>{plan?.repo_type ?? "unknown"}</code>
        {" "}· Main push blocked: <code>{String(plan?.direct_push_to_main_blocked ?? true)}</code>
        {" "}· Branch/PR action: <code>{branchAction}</code>
      </div>

      {plan?.is_company_repo && (
        <div className="delivery-warning-panel">
          Company repo detected. This plan prefers the PR workflow (feature branch + PR) over any
          direct push to main. Branch push / PR creation will require an explicit future
          approval/setup step — never performed automatically.
        </div>
      )}

      {(plan?.block_reasons?.length ?? 0) > 0 && (
        <div className="delivery-warning-panel delivery-warning-panel-severe">
          <strong>Blocker(s).</strong>
          <ul>{plan!.block_reasons.map(r => <li key={r}>{r}</li>)}</ul>
        </div>
      )}

      {(plan?.warnings?.length ?? 0) > 0 && (
        <div className="delivery-warning-panel">
          <strong>Warning(s).</strong>
          <ul>{plan!.warnings.map(w => <li key={w}>{w}</li>)}</ul>
        </div>
      )}

      <div className="delivery-command-preview">
        <div className="delivery-command-preview-label">Next safe step</div>
        <pre>{plan?.recommended_next_action ?? "Run --pr-delivery-plan to generate a recommendation."}</pre>
      </div>
    </div>
  );
}

function DeliveryCard({ runId, selectedArtifact, onSelectArtifact }: {
  runId: string;
  selectedArtifact?: string | null;
  onSelectArtifact: (artifact: string) => void;
}) {
  const [info, setInfo] = useState<DeliveryInfo | null>(null);
  const [branchName, setBranchName] = useState(`pipeline/${runId}-delivery`);
  const [commitMessage, setCommitMessage] = useState("Deliver pipeline changes locally");
  const [sandboxPush, setSandboxPush] = useState(false);
  const [precheck, setPrecheck] = useState<DeliveryPrecheck | null>(null);
  const [busy, setBusy] = useState<"commit" | "push" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getDeliveryInfo(runId).then(setInfo).catch(() => setInfo({ available: false, state: null }));
  }, [runId]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (info?.state?.branch_name) setBranchName(info.state.branch_name);
  }, [info?.state?.branch_name]);

  useEffect(() => {
    if (!info?.available || info.state?.plan_only || !branchName) { setPrecheck(null); return; }
    let cancelled = false;
    getDeliveryPrecheck(runId, branchName, sandboxPush)
      .then(p => { if (!cancelled) setPrecheck(p); })
      .catch(() => { if (!cancelled) setPrecheck(null); });
    return () => { cancelled = true; };
  }, [runId, branchName, sandboxPush, info?.available, info?.state?.plan_only]);

  if (!info) return null;
  if (!info.available) {
    return (
      <div className="delivery-card">
        <div className="delivery-card-header">
          <div>
            <div className="delivery-card-title">Delivery &amp; Git Safety</div>
            <div className="delivery-card-sub">{info.reason ?? "Delivery is not available for this run."}</div>
          </div>
        </div>
      </div>
    );
  }

  const isCompanyRepo = precheck?.repo_type === "company-protected";
  const canPushSandbox = !!precheck && precheck.decision === "PASS_SANDBOX_PUSH";
  const planOnly = !!info.state?.plan_only;
  const repoType = info.state?.repo_type ?? precheck?.repo_type;
  const smokeMutationBlocked = !!info.smoke_mutation?.blocked;
  const boundaryBlocked = !!info.boundary?.blocked || smokeMutationBlocked;
  // Repo hygiene — e.g. node_modules tracked/dirty in the TARGET repo. This is a
  // target-repo problem, not a generated-feature defect, so it gets its own panel
  // with a copyable (never auto-run) cleanup command instead of the boundary wording.
  const hygiene = info.state?.repo_hygiene ?? precheck?.repo_hygiene;
  const hygieneBlockReason = info.state?.block_reason ?? precheck?.block_reason;
  const nodeModulesHygieneBlocked = hygieneBlockReason === "DENIED_TRACKED_DEPENDENCY_FILES"
    && !!hygiene?.human_cleanup_recommended;
  const precheckBlocked = precheck?.decision === "BLOCKED";
  const deliveryBlocked = boundaryBlocked || precheckBlocked;

  const doCommit = async () => {
    setBusy("commit"); setError(null);
    try {
      await createDeliveryCommit(runId, branchName, commitMessage);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const doPush = async () => {
    setBusy("push"); setError(null);
    try {
      await pushDeliverySandbox(runId, branchName, commitMessage);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="delivery-card">
      <div className="delivery-card-header">
        <div>
          <div className="delivery-card-title">Delivery &amp; Git Safety</div>
          <div className="delivery-card-sub">
            {planOnly
              ? "Delivery plan was generated without creating a branch, commit, or push."
              : "Create a local branch/commit safely. Company repositories are never pushed unless explicitly allowed."}
          </div>
        </div>
        <DeliveryStatusBadge decision={deliveryBlocked ? "BLOCKED" : (info.state?.decision ?? precheck?.decision)} />
      </div>

      <div className="delivery-repo-line">
        Target repo: <code>{info.repo_path}</code>
        {repoType && <span className={`delivery-repo-type delivery-repo-type-${repoType}`}>{repoType}</span>}
      </div>
      {planOnly && (
        <div className="delivery-repo-line">
          Decision: <code>{info.state?.decision}</code>
          {info.state?.branch_name && <> Branch: <code>{info.state.branch_name}</code></>}
          {" "}Mode: <code>Plan only</code>
        </div>
      )}

      {boundaryBlocked && (
        <div className="delivery-warning-panel delivery-warning-panel-severe">
          <strong>Local Delivery is blocked.</strong>{" "}
          {info.boundary?.blocked && (
            <>
              The Selected Feature Change Boundary check failed for this run
              {info.boundary?.violation_count ? ` (${info.boundary.violation_count} violation(s))` : ""} —
              files outside the selected sprint were changed or deleted.{" "}
            </>
          )}
          {smokeMutationBlocked && (
            <>
              Smoke checks (e.g. <code>npm install</code>) mutated {info.smoke_mutation?.file_count ?? "some"} tracked
              file(s) outside the selected feature boundary after the build finished — see smoke_mutation_report.md.{" "}
            </>
          )}
          No branch, commit, or push can be created until this is resolved.
        </div>
      )}

      {nodeModulesHygieneBlocked && (
        <div className="delivery-warning-panel delivery-warning-panel-severe">
          <strong>Target repo hygiene issue: node_modules is tracked or dirty</strong>
          <p>
            The generated feature passed its change boundary, but local delivery is blocked because
            dependency files under <code>node_modules</code> are tracked/dirty in the target repo. The
            pipeline will not stage or commit dependency folders. Ask the repo owner before cleaning
            this up.
          </p>
          <p>
            <strong>No GitHub push was attempted.</strong>
          </p>
          <details>
            <summary>Recommended cleanup command (requires human approval — not run automatically)</summary>
            <div className="delivery-command-preview">
              <pre>{(hygiene?.recommended_commands ?? []).join("\n")}</pre>
            </div>
          </details>
        </div>
      )}

      {isCompanyRepo && (
        <div className="delivery-warning-panel">
          <strong>This repo is protected.</strong> The pipeline can create local commits for demo/review,
          but it will not publish branches to the company GitHub remote.
        </div>
      )}

      {planOnly ? (
        <div className="delivery-actions">
          <div className="delivery-action">
            <button className="submit-btn submit-btn-disabled" disabled>
              Create Local Commit
            </button>
            <div className="delivery-action-help">
              This run was created in delivery-plan-only mode. Rerun without --delivery-plan-only to create a commit.
            </div>
          </div>
        </div>
      ) : (
        <>
          <div className="delivery-form-row">
            <label>
              Branch name
              <input value={branchName} onChange={e => setBranchName(e.target.value)} placeholder="pipeline/my-change" />
            </label>
            <label>
              Commit message
              <input value={commitMessage} onChange={e => setCommitMessage(e.target.value)} placeholder="Describe the change" />
            </label>
          </div>

          <DeliveryChecklist precheck={precheck} />

          <div className="delivery-command-preview">
            <div className="delivery-command-preview-label">What will run</div>
            <pre>{`git checkout -b ${branchName || "<branch>"}\ngit add -A\ngit commit -m "${commitMessage || "<message>"}"${sandboxPush ? `\ngit push -u origin ${branchName || "<branch>"}` : ""}`}</pre>
          </div>

          <div className="delivery-actions">
            <div className="delivery-action">
              <button className="submit-btn" disabled={busy !== null || !branchName || !commitMessage || deliveryBlocked} onClick={doCommit}>
                {deliveryBlocked ? "Blocked — see warning above" : busy === "commit" ? "Creating…" : "Create Local Commit"}
              </button>
              <div className="delivery-action-help">Creates a branch and commit on your machine only. Nothing is published to GitHub.</div>
            </div>
            <div className="delivery-action">
              <label className="delivery-sandbox-toggle">
                <input type="checkbox" checked={sandboxPush} onChange={e => setSandboxPush(e.target.checked)} disabled={deliveryBlocked} />
                Enable sandbox push for this attempt
              </label>
              {isCompanyRepo ? (
                <>
                  <button className="submit-btn submit-btn-disabled" disabled title="Push disabled for company repo">
                    Push disabled for company repo
                  </button>
                  <div className="delivery-action-help">
                    This repo is protected. The pipeline can create local commits for demo/review, but it will not publish branches to the company GitHub remote.
                  </div>
                </>
              ) : (
                <>
                  <button
                    className="submit-btn"
                    disabled={busy !== null || !sandboxPush || !canPushSandbox || deliveryBlocked}
                    onClick={doPush}
                    title={deliveryBlocked ? "Blocked — see warning above" : !canPushSandbox ? (precheck?.push_blocked_reasons.join("; ") || "Not eligible for sandbox push") : ""}
                  >
                    {deliveryBlocked ? "Blocked — see warning above" : busy === "push" ? "Pushing…" : "Push Sandbox Demo Branch"}
                  </button>
                  <div className="delivery-action-help">Only enabled for allowlisted sandbox repos. Never pushes OneHR/OneATS company repos.</div>
                </>
              )}
            </div>
          </div>
        </>
      )}

      {error && <div className="delivery-error-panel">{error}</div>}

      {info.state && (
        <div className={`delivery-result-panel delivery-result-${info.state.decision === "BLOCKED" ? "fail" : "ok"}`}>
          {info.state.decision === "BLOCKED" ? (
            <>Delivery blocked: {info.state.blocked_reason ?? "see delivery_safety_check.md"}</>
          ) : info.state.plan_only ? (
            <>No branch, commit, or push was performed.</>
          ) : (
            <>
              Local commit created on <code>{info.state.branch_name}</code>
              {info.state.commit_hash && <> (<code>{info.state.commit_hash.slice(0, 10)}</code>)</>}.{" "}
              {info.state.push_attempted
                ? (info.state.push_succeeded ? "Pushed to sandbox remote." : "Push attempted but failed — see push_result.md.")
                : "Not pushed to GitHub."}
            </>
          )}
        </div>
      )}

      {(info.artifacts?.length ?? 0) > 0 && (
        <div className="delivery-artifacts">
          <div className="delivery-artifacts-label">Delivery reports</div>
          <div className="delivery-artifact-tabs">
            {info.artifacts!.map(f => {
              const artifactPath = `delivery/${f}`;
              return (
                <button
                  key={f}
                  className={`artifact-tab ${selectedArtifact === artifactPath ? "active" : ""}`}
                  onClick={() => onSelectArtifact(artifactPath)}
                >
                  {DELIVERY_ARTIFACT_LABELS[f] ?? f}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function ExistingAppUpgradeView({ runId, run, onBack, onNewRun }: {
  runId: string; run: RunDetail | null; onBack: () => void; onNewRun: (id: string) => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [plan, setPlan] = useState<FeatureSprintPlan | null>(null);
  const [launching, setLaunching] = useState<number | null>(null);
  const artifacts = run?.artifacts ?? [];

  useEffect(() => {
    if (!artifacts.includes("feature_sprint_plan.json")) return;
    getArtifact(runId, "feature_sprint_plan.json")
      .then(a => { try { setPlan(JSON.parse(a.content)); } catch { /* ignore */ } })
      .catch(() => {});
  }, [runId, artifacts]);

  useEffect(() => {
    if (!selected) return;
    getArtifact(runId, selected).then(a => setContent(a.content)).catch(() => setContent("(error loading content)"));
  }, [runId, selected]);

  const regressionStatus = (() => {
    const m = content.match(/\*\*Status:\*\*\s*(\w+)/);
    return selected === "regression_check.md" && m ? m[1] : null;
  })();

  const availablePanels = UPGRADE_ARTIFACT_PANELS.filter(p => artifacts.includes(p.file));
  const planReady = run?.status === "feature_plan_only_done";
  const buildFromPlan = async (n: number) => {
    setLaunching(n);
    try {
      const created = await createContinuationRun({
        continue_run: `runs/${runId}`, continue_feature_sprint: n,
        continue_plan_only: false, no_deepseek: true,
      });
      onNewRun(created.run_id);
    } finally {
      setLaunching(null);
    }
  };

  return (
    <div className="pipeline-view upgrade-view">
      <div className="pipeline-body">
        <div className="steps-panel">
          <div className="steps-panel-header">
            <button className="topbar-back" onClick={onBack}><IconBack /> MVP Pipeline</button>
          </div>
          <div className="steps-panel-scroll">
            <div className="sprint-mode-banner">
              <span className="sprint-mode-pill">Mode: Existing App Upgrade</span>
              <span className="sprint-mode-line">
                {plan?.product_name ? `${plan.product_name} — ` : ""}
                additive feature work on top of an existing app. Status: {run?.status ?? "running"}
              </span>
            </div>
            {planReady && <div className="sprint-mode-banner">Review the plan, then build exactly one selected feature sprint.</div>}
            {plan && <FeatureSprintRoadmap plan={plan} onBuild={planReady ? buildFromPlan : undefined} launching={launching} />}
            <ChangeBoundaryBanner run={run} />
            <SmokeMutationBanner run={run} />
            <GitSyncCard runId={runId} run={run} />
            <PrPlanCard runId={runId} run={run} />
            <DeliveryCard runId={runId} selectedArtifact={selected} onSelectArtifact={setSelected} />
          </div>
        </div>
        <div className="right-panel">
          <div className="artifact-panel">
            {availablePanels.length > 0 && (
              <div className="artifact-tabs">
                {availablePanels.map(p => (
                  <button
                    key={p.file}
                    className={`artifact-tab ${selected === p.file ? "active" : ""}`}
                    onClick={() => setSelected(p.file)}
                  >{p.label}</button>
                ))}
              </div>
            )}
            <div className="artifact-body">
              {selected ? (
                <>
                  <div className="artifact-filename">
                    {UPGRADE_ARTIFACT_PANELS.find(p => p.file === selected)?.label ?? artifactDisplayName(selected)}
                    {artifactDisplayName(selected) !== selected && (
                      <span className="artifact-filename-raw"> · {selected}</span>
                    )}
                    {regressionStatus && (
                      <span className={`upgrade-regression-badge upgrade-regression-${regressionStatus.toLowerCase()}`}>
                        Regression: {regressionStatus}
                      </span>
                    )}
                  </div>
                  <div className="artifact-content">
                    <pre key={selected}>{content}</pre>
                  </div>
                </>
              ) : (
                <div className="artifact-filename">Select an artifact on the left to view it.</div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Multi-Sprint Continuation mode view ─────────────────────────────────────────
// Minimal, read-only v1 mirroring ExistingAppUpgradeView's structure. The preserved
// sprint plan can be in either shape (normal sprint_plan.json keyed "number", or
// feature_sprint_plan.json keyed "sprint_number") since continuation mode works on
// top of both source modes — the roadmap below reads either key.

interface ContinuationPlanSprint {
  number?: number;
  sprint_number?: number;
  title: string;
  goal: string;
}

interface ContinuationPlan {
  mode?: string;
  product_name?: string;
  total_sprints?: number;
  selected_sprint?: number;
  selected_feature_sprint?: number;
  baseline?: { sprint_number: number; title: string };
  sprints?: ContinuationPlanSprint[];
}

const CONTINUATION_ARTIFACT_PANELS: { file: string; label: string }[] = [
  { file: "continuation_source.md", label: "Continuation Source" },
  { file: "preserved_sprint_plan.json", label: "Preserved Sprint Plan JSON" },
  { file: "preserved_sprint_plan.md", label: "Preserved Sprint Plan" },
  { file: "current_app_inventory.md", label: "Current App Inventory" },
  { file: "continuation_gap_analysis.md", label: "Continuation Gap Analysis" },
  { file: "selected_continuation_sprint_scope.md", label: "Selected Continuation Sprint Scope" },
  { file: "selected_continuation_sprint_build_prompt.txt", label: "Selected Continuation Build Prompt" },
  { file: "continuation_regression_check.md", label: "Continuation Regression Check" },
  { file: "continuation_completion_report.md", label: "Continuation Completion Report" },
];

function ContinuationRoadmap({ plan }: { plan: ContinuationPlan }) {
  const selected = plan.selected_feature_sprint ?? plan.selected_sprint;
  const sprints = [...(plan.sprints ?? [])].sort(
    (a, b) => (a.sprint_number ?? a.number ?? 0) - (b.sprint_number ?? b.number ?? 0)
  );
  return (
    <div className="upgrade-roadmap">
      {plan.baseline && (
        <div className="upgrade-sprint-card upgrade-sprint-baseline">
          <div className="upgrade-sprint-title">Sprint 0 — {plan.baseline.title ?? "Baseline"}</div>
          <div className="upgrade-sprint-meta">Not buildable — regression target only</div>
        </div>
      )}
      {sprints.map(s => {
        const n = s.sprint_number ?? s.number ?? 0;
        return (
          <div key={n} className={`upgrade-sprint-card${n === selected ? " upgrade-sprint-selected" : ""}`}>
            <div className="upgrade-sprint-title">
              Sprint {n} — {s.title}
              {n === selected && <span className="upgrade-sprint-pill">CONTINUING HERE</span>}
            </div>
            <div className="upgrade-sprint-goal">{s.goal}</div>
          </div>
        );
      })}
    </div>
  );
}

function ContinuationView({ runId, run, onBack }: {
  runId: string; run: RunDetail | null; onBack: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [plan, setPlan] = useState<ContinuationPlan | null>(null);
  const [sourceRun, setSourceRun] = useState<string | null>(null);
  const artifacts = run?.artifacts ?? [];

  useEffect(() => {
    if (!artifacts.includes("preserved_sprint_plan.json")) return;
    getArtifact(runId, "preserved_sprint_plan.json")
      .then(a => { try { setPlan(JSON.parse(a.content)); } catch { /* ignore */ } })
      .catch(() => {});
  }, [runId, artifacts]);

  useEffect(() => {
    if (!artifacts.includes("continuation_source.md")) return;
    getArtifact(runId, "continuation_source.md")
      .then(a => {
        const m = a.content.match(/\*\*Source run:\*\*\s*`([^`]+)`/);
        if (m) setSourceRun(m[1]);
      })
      .catch(() => {});
  }, [runId, artifacts]);

  useEffect(() => {
    if (!selected) return;
    getArtifact(runId, selected).then(a => setContent(a.content)).catch(() => setContent("(error loading content)"));
  }, [runId, selected]);

  const regressionStatus = (() => {
    const m = content.match(/\*\*Status:\*\*\s*(\w+)/);
    return selected === "continuation_regression_check.md" && m ? m[1] : null;
  })();

  const availablePanels = CONTINUATION_ARTIFACT_PANELS.filter(p => artifacts.includes(p.file));
  const selectedSprintNum = plan?.selected_feature_sprint ?? plan?.selected_sprint;

  return (
    <div className="pipeline-view upgrade-view">
      <div className="pipeline-body">
        <div className="steps-panel">
          <div className="steps-panel-header">
            <button className="topbar-back" onClick={onBack}><IconBack /> MVP Pipeline</button>
          </div>
          <div className="steps-panel-scroll">
            <div className="sprint-mode-banner">
              <span className="sprint-mode-pill">Mode: Sprint Continuation</span>
              <span className="sprint-mode-line">
                {sourceRun ? `Continuing from ${sourceRun.split("/").pop()}` : "Continuing a previous run"}
                {selectedSprintNum ? ` — Sprint ${selectedSprintNum}` : ""}. Status: {run?.status ?? "running"}
              </span>
            </div>
            {plan && <ContinuationRoadmap plan={plan} />}
          </div>
        </div>
        <div className="right-panel">
          <div className="artifact-panel">
            {availablePanels.length > 0 && (
              <div className="artifact-tabs">
                {availablePanels.map(p => (
                  <button
                    key={p.file}
                    className={`artifact-tab ${selected === p.file ? "active" : ""}`}
                    onClick={() => setSelected(p.file)}
                  >{p.label}</button>
                ))}
              </div>
            )}
            <div className="artifact-body">
              {selected ? (
                <>
                  <div className="artifact-filename">
                    {CONTINUATION_ARTIFACT_PANELS.find(p => p.file === selected)?.label ?? selected}
                    {regressionStatus && (
                      <span className={`upgrade-regression-badge upgrade-regression-${regressionStatus.toLowerCase()}`}>
                        Regression: {regressionStatus}
                      </span>
                    )}
                  </div>
                  <div className="artifact-content">
                    <pre key={selected}>{content}</pre>
                  </div>
                </>
              ) : (
                <div className="artifact-filename">Select an artifact on the left to view it.</div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function PipelineView({ runId, onBack, onNewRun }: { runId: string; onBack: () => void; onNewRun: (id: string) => void }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [selectedArtifact, setSelectedArtifact] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [contentLoading, setContentLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [activeStep, setActiveStep] = useState(0);
  const [sprintInfo, setSprintInfo] = useState<SprintInfo | null>(null);
  const [originalInput, setOriginalInput] = useState<string | null>(null);
  const [launchingSprint, setLaunchingSprint] = useState<number | null>(null);
  const carouselRef = useRef<HTMLDivElement>(null);
  const prevStep = useRef<string | null>(null);
  const prevStatus = useRef<string | null>(null);

  // Sprint-mode detection: works for both dashboard-triggered runs (run.sprint_plan /
  // run.sprint_plan_only set by backend) and CLI-triggered runs (no such fields — fall
  // back to current_step / artifact presence).
  const sprintModeActive = !!(run?.sprint_plan || run?.sprint_plan_only) ||
    run?.current_step === "sprint_plan" ||
    (run?.artifacts ?? []).some(a => a === "sprint_plan.json" || a === "sprint_plan.md" || a === "selected_sprint_scope.md");

  // Existing App Upgrade mode detection: presence of feature_sprint_plan.json is the
  // signal (set both for dashboard-triggered and CLI-triggered upgrade runs, since the
  // backend's generic artifact endpoint serves any filename written by the pipeline).
  const upgradeModeActive = !!run?.upgrade_mode ||
    (run?.artifacts ?? []).includes("feature_sprint_plan.json");
  // Multi-Sprint Continuation mode detection: presence of continuation_source.md (or a
  // "continuation_" status, for the brief window before that artifact lands) is the signal.
  // Checked independently of upgradeModeActive above — a continuation run never writes
  // feature_sprint_plan.json itself (it writes preserved_sprint_plan.json/.md instead).
  const continuationModeActive = !!run?.continue_run ||
    (run?.artifacts ?? []).includes("continuation_source.md") ||
    !!run?.status?.startsWith("continuation_");
  // Sprint-plan-only ("Stage 1") vs an actual selected-sprint build ("Stage 2") — these get
  // different banner copy and different step semantics ("Not being run" vs in-progress).
  const sprintPlanOnlyActive = !!run?.sprint_plan_only || run?.status === "sprint_plan_only_done";
  const selectedSprintNum = sprintInfo?.selected_sprint ?? run?.selected_sprint ?? 1;
  const steps = getStepsForRun(sprintModeActive, selectedSprintNum);

  // Original raw input, fetched once it exists, so "Run Sprint N" can launch a fresh run
  // against the same input without the user re-typing anything. Jira-sourced runs store a
  // placeholder in raw_input.md (not the real ticket text) — detect that and disable the
  // action rather than silently rerunning garbage input.
  useEffect(() => {
    if (originalInput !== null) return;
    if (!(run?.artifacts ?? []).includes("raw_input.md")) return;
    getArtifact(runId, "raw_input.md").then(a => setOriginalInput(a.content)).catch(() => {});
  }, [run?.artifacts, runId, originalInput]);
  const canRunSprint = !!originalInput && !originalInput.startsWith("[Jira ticket:");

  const runSprint = useCallback(async (n: number) => {
    if (!originalInput) return;
    setLaunchingSprint(n);
    try {
      const r = await fetch("http://127.0.0.1:5001/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raw_input: originalInput, sprint_plan: true, selected_sprint: n, no_deepseek: true }),
      });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      onNewRun(data.run_id);
    } catch {
      // Minimal error handling — surfaced via the button reverting; the new run, if any,
      // simply won't navigate. Keeping this lightweight per scope ("don't overbuild").
    } finally {
      setLaunchingSprint(null);
    }
  }, [originalInput, onNewRun]);

  const scrollToStep = useCallback((idx: number) => {
    setActiveStep(idx);
    const el = carouselRef.current;
    if (el) el.scrollTo({ left: idx * el.offsetWidth, behavior: "smooth" });
  }, []);

  // Sync active dot when user manually scrolls
  const handleScroll = useCallback(() => {
    const el = carouselRef.current;
    if (!el) return;
    const idx = Math.round(el.scrollLeft / el.offsetWidth);
    setActiveStep(idx);
  }, []);

  useEffect(() => {
    const el = carouselRef.current;
    el?.addEventListener("scroll", handleScroll, { passive: true });
    return () => el?.removeEventListener("scroll", handleScroll);
  }, [handleScroll]);

  // Auto-advance carousel whenever the active step changes
  useEffect(() => {
    if (!run) return;
    const runningId = STEP_MAP[run.current_step ?? ""];
    if (runningId && runningId !== prevStep.current) {
      const idx = steps.findIndex(s => s.id === runningId);
      if (idx >= 0) scrollToStep(idx);
      prevStep.current = runningId;
    }
  }, [run?.current_step, scrollToStep, steps]);

  // Fetch sprint_plan.json once it appears, to power the sprint-mode banner (complexity,
  // recommended sprint count, selected sprint, total sprints). Small + deterministic, so a
  // single one-shot fetch (not polled) is enough.
  useEffect(() => {
    if (sprintInfo) return;
    const hasPlan = (run?.artifacts ?? []).includes("sprint_plan.json");
    if (!hasPlan) return;
    getArtifact(runId, "sprint_plan.json")
      .then(a => { try { setSprintInfo(JSON.parse(a.content)); } catch { /* ignore parse errors */ } })
      .catch(() => {});
  }, [run?.artifacts, runId, sprintInfo]);

  // Poll run + detect terminal transition
  useEffect(() => {
    const poll = () =>
      getRun(runId).then(r => {
        setRun(r);
        const sorted = sortArtifacts(r.artifacts ?? []).filter(a => a !== "run_state.json");
        // On transition to terminal: auto-open final report
        if (TERMINAL.has(r.status) && prevStatus.current && !TERMINAL.has(prevStatus.current)) {
          const final = sorted.find(a => a === "final_mvp_report.md") ?? sorted[sorted.length - 1] ?? null;
          setSelectedArtifact(final);
        }
        prevStatus.current = r.status;
      }).catch(() => {});
    poll();
    const i = setInterval(poll, 2000);
    return () => clearInterval(i);
  }, [runId]);

  // Load artifact
  useEffect(() => {
    if (!selectedArtifact) return;
    setContentLoading(true);
    getArtifact(runId, selectedArtifact)
      .then(a => setContent(a.content))
      .catch(() => setContent("(error loading content)"))
      .finally(() => setContentLoading(false));
  }, [runId, selectedArtifact]);

  // Live timer
  useEffect(() => {
    const i = setInterval(() => setElapsed(e => e + 1), 1000);
    return () => clearInterval(i);
  }, []);

  const isTerminal = TERMINAL.has(run?.status ?? "");
  const timings = run?.step_timings ?? {};
  const sorted = sortArtifacts(run?.artifacts ?? []).filter(a => a !== "run_state.json");
  const fixCycle = run?.fix_iteration ?? 0;
  const timeline = buildTimeline(run);

  const statuses = steps.map(step =>
    stepStatus(step, run?.artifacts ?? [], run?.current_step ?? "", run?.status ?? "", run?.steps)
  );
  // Sprint numbers this run has direct evidence were actually built (claude_build_output.txt
  // exists, i.e. the "Build Selected Sprint" step is genuinely "done") — only the currently
  // selected sprint can ever be in this set for a single run, but it's what gates dependency
  // locks on the sprint cards below.
  const builtStepIdx = steps.findIndex(s => s.id === "claude_build");
  const builtSprints = builtStepIdx >= 0 && statuses[builtStepIdx] === "done" ? [selectedSprintNum] : [];

  // Six-section overview: same underlying step/artifact evidence as the detailed
  // carousel below, grouped into the stages a person actually thinks in.
  const statusById: Record<string, StepStatus> = {};
  steps.forEach((step, i) => { statusById[step.id] = statuses[i]; });
  const sections = buildSections({
    statusById,
    runArtifacts: run?.artifacts ?? [],
    sprintModeActive,
  });

  // Existing App Upgrade mode has a different artifact shape (existing_app_summary.md,
  // change_gap_analysis.md, feature_sprint_plan.json, regression_check.md, ...) than the
  // normal/sprint pipeline's step vocabulary, so it gets its own dedicated read-only view
  // instead of being forced through the six-section step rollup above. This early return
  // sits AFTER all hooks have run (rules-of-hooks safe) and never affects normal-mode or
  // sprint-mode runs, which fall through to the unchanged return below.
  if (upgradeModeActive) {
    return (
      <ExistingAppUpgradeView runId={runId} run={run} onBack={onBack} onNewRun={onNewRun} />
    );
  }

  // Multi-Sprint Continuation mode similarly gets its own dedicated read-only view —
  // its artifact shape (continuation_source.md, preserved_sprint_plan.*,
  // current_app_inventory.md, continuation_gap_analysis.md, continuation_regression_check.md,
  // continuation_completion_report.md) doesn't fit the six-section rollup either. Checked
  // after upgradeModeActive since the two are mutually exclusive in practice.
  if (continuationModeActive) {
    return (
      <ContinuationView runId={runId} run={run} onBack={onBack} />
    );
  }

  return (
    <div className="pipeline-view">
      <div className="pipeline-body">
        {/* ── LEFT: Six-section pipeline overview ─────────────────────────────── */}
        <div className="steps-panel">
          <div className="steps-panel-header">
            <button className="topbar-back" onClick={onBack}><IconBack /> MVP Pipeline</button>
          </div>
          {sprintModeActive && <SprintModeBanner info={sprintInfo} fallbackSelected={selectedSprintNum} planOnly={sprintPlanOnlyActive} />}

          <div className="steps-panel-scroll">
            <PipelineSectionOverview sections={sections} />

            {/* Run timeline — explicit history of loop cycles/rounds. */}
            <RunTimeline events={timeline} />

            {/* Detailed step-by-step view — the original flat stepper, preserved as an
                advanced disclosure rather than the main visual story. */}
            <details className="detailed-steps">
              <summary>Show detailed step view ({steps.length} steps)</summary>
              <div className="steps-carousel" ref={carouselRef}>
                {steps.map((step, i) => (
                  <StepCard
                    key={step.id}
                    step={step}
                    index={i}
                    total={steps.length}
                    status={statuses[i]}
                    elapsed={stepElapsed(step.id, timings)}
                    cycle={fixCycle}
                  />
                ))}
              </div>
              <div className="carousel-nav">
                <button
                  className="carousel-arrow"
                  onClick={() => scrollToStep(Math.max(0, activeStep - 1))}
                  disabled={activeStep === 0}
                >←</button>
                <div className="carousel-dots">
                  {steps.map((_, i) => (
                    <button
                      key={i}
                      className={`cdot cdot-${statuses[i]} ${i === activeStep ? "cdot-active" : ""}`}
                      onClick={() => scrollToStep(i)}
                      title={steps[i].label}
                    />
                  ))}
                </div>
                <button
                  className="carousel-arrow"
                  onClick={() => scrollToStep(Math.min(steps.length - 1, activeStep + 1))}
                  disabled={activeStep === steps.length - 1}
                >→</button>
              </div>
            </details>
          </div>
        </div>

        {/* ── RIGHT: Status banner + Terminal (running) or Artifacts (done) ──── */}
        <div className="right-panel">
          <NowBanner run={run} elapsed={elapsed} sprintModeActive={sprintModeActive} selectedSprintNum={selectedSprintNum} />

          {isTerminal ? (
            /* ── Artifact browser (run complete) ───────────────────────────── */
            <div className="artifact-panel">
              {sprintModeActive && sprintInfo?.sprints?.length ? (
                <SprintCards
                  sprints={sprintInfo.sprints}
                  selected={selectedSprintNum}
                  onRun={runSprint}
                  launching={launchingSprint}
                  canRun={canRunSprint}
                  builtSprints={builtSprints}
                />
              ) : null}
              <SprintCompletionReport
                sprintModeActive={sprintModeActive}
                sprintPlanOnlyActive={sprintPlanOnlyActive}
                built={builtStepIdx >= 0 && statuses[builtStepIdx] === "done"}
                selectedSprintNum={selectedSprintNum}
                sprintInfo={sprintInfo}
              />
              {sorted.length > 0 && (
                <div className="artifact-tabs">
                  {sorted.map(name => (
                    <button
                      key={name}
                      className={`artifact-tab ${selectedArtifact === name ? "active" : ""}`}
                      onClick={() => setSelectedArtifact(name)}
                    >{artifactDisplayName(name)}</button>
                  ))}
                </div>
              )}
              <div className="artifact-body">
                {selectedArtifact ? (
                  <>
                    <div className="artifact-filename">
                      {artifactDisplayName(selectedArtifact)}
                      {artifactDisplayName(selectedArtifact) !== selectedArtifact && (
                        <span className="artifact-filename-raw"> · {selectedArtifact}</span>
                      )}
                    </div>
                    <div className={`artifact-content ${contentLoading ? "loading" : ""}`}>
                      {contentLoading
                        ? <div className="artifact-shimmer" />
                        : <pre key={selectedArtifact}>{content}</pre>
                      }
                    </div>
                  </>
                ) : (
                  <div className="artifact-empty">Files will appear here as steps complete</div>
                )}
              </div>
            </div>
          ) : (
            /* ── Live terminal (run in progress) ───────────────────────────── */
            <TerminalView runId={runId} sprintMode={sprintModeActive} />
          )}
        </div>
      </div>
    </div>
  );
}

// ── Home view ──────────────────────────────────────────────────────────────────

type EntryMode = "idea" | "paste" | "file" | "jira";

const ENTRY_CARDS = [
  { mode: "idea"  as EntryMode, icon: <IconIdea />, title: "I Have an Idea", sub: "Tell us what you want to build in a sentence — we'll generate the full requirements", color: "#f59e0b", colorBg: "#fffbeb", hint: "Just a few words is enough" },
  { mode: "paste" as EntryMode, icon: <IconPen />,  title: "Write Requirements", sub: "Paste detailed requirements you've already written and let the pipeline build it", color: "#6366f1", colorBg: "#eef2ff", hint: "Plain text or Markdown" },
  { mode: "file"  as EntryMode, icon: <IconFile />, title: "Load File",         sub: "Import an existing spec or requirements doc from a .md or .txt file", color: "#0284c7", colorBg: "#e0f2fe", hint: ".md or .txt" },
  { mode: "jira"  as EntryMode, icon: <IconJira />, title: "Jira Ticket",       sub: "Pull a ticket directly from your Jira workspace by key", color: "#0052cc", colorBg: "#e8f0fb", hint: "e.g. MDP-1" },
];

type SecondaryCardMode = "upgrade" | "continuation" | "runs";

const SECONDARY_CARDS: { mode: SecondaryCardMode; icon: ReactElement; title: string; sub: string; color: string; colorBg: string; hint: string }[] = [
  { mode: "upgrade",      icon: <IconWrench />, title: "Existing App Upgrade",      sub: "Add features to an existing MVP without rebuilding it from scratch", color: "#0d9488", colorBg: "#f0fdfa", hint: "App path + feature request" },
  { mode: "continuation", icon: <IconRepeat />, title: "Continue Previous Sprint",  sub: "Use a previous run as the baseline and plan the next sprint",        color: "#7c3aed", colorBg: "#f5f3ff", hint: "Source run + sprint number" },
  { mode: "runs",         icon: <IconClock />,  title: "View Past Runs",            sub: "Open previous runs, artifacts, reports, and sprint plans",            color: "#64748b", colorBg: "#f8fafc", hint: "Run history" },
];

// ── Existing App Upgrade form panel ─────────────────────────────────────────────

function UpgradePanel({ onCreated, onCancel }: { onCreated: (id: string) => void; onCancel: () => void }) {
  const [existingApp, setExistingApp] = useState("");
  const [featureRequest, setFeatureRequest] = useState("");
  const [selectedSprint, setSelectedSprint] = useState(1);
  const [planOnly, setPlanOnly] = useState(true);
  const [noDeepseek, setNoDeepseek] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = existingApp.trim().length > 0 && featureRequest.trim().length > 0;

  const submit = async () => {
    setLoading(true); setError(null);
    try {
      const data = await createUpgradeRun({
        upgrade_mode: true,
        existing_app: existingApp.trim(),
        feature_request_text: featureRequest.trim(),
        feature_sprint_plan: true,
        selected_feature_sprint: selectedSprint,
        feature_plan_only: planOnly,
        no_deepseek: noDeepseek,
      });
      onCreated(data.run_id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally { setLoading(false); }
  };

  return (
    <div className="expand-panel" style={{ "--card-color": "#0d9488", "--card-bg": "#f0fdfa" } as React.CSSProperties}>
      <div className="expand-panel-header">
        <div className="expand-panel-icon"><IconWrench /></div>
        <span className="expand-panel-title">Existing App Upgrade</span>
        <button className="expand-panel-close" onClick={onCancel}>×</button>
      </div>
      <div className="expand-panel-body">
        <label className="expand-field">
          <span className="expand-field-label">Existing app path</span>
          <input className="expand-input" value={existingApp} onChange={e => setExistingApp(e.target.value)}
            placeholder="/path/to/app" disabled={loading} autoFocus />
        </label>
        <label className="expand-field">
          <span className="expand-field-label">Feature request</span>
          <textarea className="input-textarea expand-textarea" value={featureRequest} onChange={e => setFeatureRequest(e.target.value)}
            placeholder="Describe the features to add to this app..." rows={5} disabled={loading} />
        </label>
        {!planOnly && <label className="expand-field">
          <span className="expand-field-label">Selected feature sprint</span>
          <input className="expand-input expand-input-num" type="number" min={1} max={12} value={selectedSprint}
            onChange={e => setSelectedSprint(Math.min(12, Math.max(1, parseInt(e.target.value, 10) || 1)))} disabled={loading} />
          <span className="expand-field-help">Build only after reviewing a generated Feature Sprint Plan. For a prior plan, use its continuation command.</span>
        </label>}
        <div className="expand-checkboxes">
          <label className="expand-checkbox">
            <input type="checkbox" checked={planOnly} onChange={e => setPlanOnly(e.target.checked)} disabled={loading} />
            <span>Plan only <em>— no Claude Code build</em></span>
          </label>
          <label className="expand-checkbox">
            <input type="checkbox" checked={noDeepseek} onChange={e => setNoDeepseek(e.target.checked)} disabled={loading} />
            <span>Skip DeepSeek review</span>
          </label>
        </div>
        {error && <p className="input-error">{error}</p>}
        <button className="submit-btn" onClick={submit} disabled={loading || !canSubmit}>
          {loading ? "Starting…" : planOnly ? "Generate Feature Sprint Plan →" : "Build Feature Sprint →"}
        </button>
      </div>
    </div>
  );
}

// ── Continue Previous Sprint form panel ─────────────────────────────────────────

function ContinuationPanel({ onCreated, onCancel }: { onCreated: (id: string) => void; onCancel: () => void }) {
  const [sourceRun, setSourceRun] = useState("");
  const [continueSprint, setContinueSprint] = useState(2);
  const [planOnly, setPlanOnly] = useState(true);
  const [noDeepseek, setNoDeepseek] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = sourceRun.trim().length > 0;

  const submit = async () => {
    setLoading(true); setError(null);
    try {
      const data = await createContinuationRun({
        continue_run: sourceRun.trim(),
        continue_sprint: continueSprint,
        continue_plan_only: planOnly,
        no_deepseek: noDeepseek,
      });
      onCreated(data.run_id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally { setLoading(false); }
  };

  return (
    <div className="expand-panel" style={{ "--card-color": "#7c3aed", "--card-bg": "#f5f3ff" } as React.CSSProperties}>
      <div className="expand-panel-header">
        <div className="expand-panel-icon"><IconRepeat /></div>
        <span className="expand-panel-title">Continue Previous Sprint</span>
        <button className="expand-panel-close" onClick={onCancel}>×</button>
      </div>
      <div className="expand-panel-body">
        <label className="expand-field">
          <span className="expand-field-label">Source run</span>
          <input className="expand-input" value={sourceRun} onChange={e => setSourceRun(e.target.value)}
            placeholder="runs/run_049" disabled={loading} autoFocus />
        </label>
        <label className="expand-field">
          <span className="expand-field-label">Next sprint number</span>
          <input className="expand-input expand-input-num" type="number" min={1} max={12} value={continueSprint}
            onChange={e => setContinueSprint(Math.min(12, Math.max(1, parseInt(e.target.value, 10) || 1)))} disabled={loading} />
          <span className="expand-field-help">This uses the source run’s preserved sprint plan. If Sprint 1 is complete, choose 2.</span>
        </label>
        <div className="expand-checkboxes">
          <label className="expand-checkbox">
            <input type="checkbox" checked={planOnly} onChange={e => setPlanOnly(e.target.checked)} disabled={loading} />
            <span>Plan only <em>— no Claude Code build</em></span>
          </label>
          <label className="expand-checkbox">
            <input type="checkbox" checked={noDeepseek} onChange={e => setNoDeepseek(e.target.checked)} disabled={loading} />
            <span>Skip DeepSeek review</span>
          </label>
        </div>
        {error && <p className="input-error">{error}</p>}
        <button className="submit-btn" onClick={submit} disabled={loading || !canSubmit}>
          {loading ? "Starting…" : planOnly ? "Generate Continuation Plan →" : "Continue Sprint →"}
        </button>
      </div>
    </div>
  );
}

function HomeView({ onSelect, onRuns, onRunCreated }: { onSelect: (m: EntryMode) => void; onRuns: () => void; onRunCreated: (id: string) => void }) {
  const [expanded, setExpanded] = useState<"upgrade" | "continuation" | null>(null);

  const handleSecondaryClick = (mode: SecondaryCardMode) => {
    if (mode === "runs") { onRuns(); return; }
    setExpanded(prev => (prev === mode ? null : mode));
  };

  return (
    <div className="home-view">
      <div className="home-hero">
        <div className="home-wordmark">
          <div className="home-logo"><IconPipeline /></div>
          <span className="home-title">MVP Pipeline</span>
        </div>
        <p className="home-sub">Turn ideas into working applications, automatically.</p>
      </div>

      <div className="home-section">
        <div className="home-section-header">
          <div className="home-section-title">Start a New MVP</div>
        </div>
        <div className="entry-cards">
          {ENTRY_CARDS.map((card, i) => (
            <button
              key={card.mode}
              className="entry-card"
              style={{ "--card-color": card.color, "--card-bg": card.colorBg, animationDelay: `${i * 70 + 150}ms` } as React.CSSProperties}
              onClick={() => onSelect(card.mode)}
            >
              <div className="card-top-bar" />
              <div className="card-inner">
                <div className="card-icon-wrap">{card.icon}</div>
                <div className="card-text">
                  <div className="card-title">{card.title}</div>
                  <div className="card-sub">{card.sub}</div>
                </div>
              </div>
              <div className="card-footer">
                <span>{card.hint}</span>
                <span className="card-arrow">→</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="home-section">
        <div className="home-section-header">
          <div className="home-section-title">Continue or Upgrade</div>
          <div className="home-section-sub">Work from an existing app or continue a previous sprint.</div>
        </div>
        <div className="entry-cards">
          {SECONDARY_CARDS.map((card, i) => (
            <button
              key={card.mode}
              className={`entry-card ${expanded === card.mode ? "entry-card-active" : ""}`}
              style={{ "--card-color": card.color, "--card-bg": card.colorBg, animationDelay: `${i * 70 + 150}ms` } as React.CSSProperties}
              onClick={() => handleSecondaryClick(card.mode)}
            >
              <div className="card-top-bar" />
              <div className="card-inner">
                <div className="card-icon-wrap">{card.icon}</div>
                <div className="card-text">
                  <div className="card-title">{card.title}</div>
                  <div className="card-sub">{card.sub}</div>
                </div>
              </div>
              <div className="card-footer">
                <span>{card.hint}</span>
                <span className="card-arrow">→</span>
              </div>
            </button>
          ))}
        </div>
        {expanded === "upgrade" && <UpgradePanel onCreated={onRunCreated} onCancel={() => setExpanded(null)} />}
        {expanded === "continuation" && <ContinuationPanel onCreated={onRunCreated} onCancel={() => setExpanded(null)} />}
      </div>
    </div>
  );
}

// ── Input view ─────────────────────────────────────────────────────────────────

type RunMode = "full" | "plan_only" | "sprint_plan_only";

function InputView({ mode, onBack, onCreated }: { mode: EntryMode; onBack: () => void; onCreated: (id: string) => void }) {
  const [text, setText] = useState("");
  const [jiraKey, setJiraKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runMode, setRunMode] = useState<RunMode>("full");
  const [selectedSprintInput, setSelectedSprintInput] = useState(1);
  const fileRef = useRef<HTMLInputElement>(null);
  const card = ENTRY_CARDS.find(c => c.mode === mode)!;

  const submit = async () => {
    setLoading(true); setError(null);
    try {
      const base =
        mode === "jira" ? { jira_key: jiraKey.trim().toUpperCase() } :
        mode === "idea" ? { raw_input: text.trim(), mode: "idea" } :
                          { raw_input: text.trim() };
      // Plan-only / sprint-plan-only let the dashboard exercise the pipeline's cheap,
      // no-Claude-Code / no-DeepSeek paths (same as the CLI's --plan-only /
      // --sprint-plan --sprint-plan-only flags) for quick testing.
      const extra: Record<string, unknown> =
        runMode === "plan_only" ? { plan_only: true } :
        runMode === "sprint_plan_only" ? { sprint_plan: true, sprint_plan_only: true, selected_sprint: selectedSprintInput } :
        {};
      const payload = { ...base, ...extra };
      const r = await fetch("http://127.0.0.1:5001/api/runs", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      onCreated(data.run_id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally { setLoading(false); }
  };

  const canSubmit = mode === "jira" ? jiraKey.trim().length > 0 : text.trim().length > 0;

  return (
    <div className="input-view">
      <button className="back-btn" onClick={onBack}><IconBack /> Back</button>
      <div className="input-card" style={{ "--card-color": card.color, "--card-bg": card.colorBg } as React.CSSProperties}>
        <div className="input-card-header">
          <div className="input-header-icon">{card.icon}</div>
          <span className="input-title">{card.title}</span>
        </div>
        <div className="input-card-body">
          {mode === "idea" && (
            <div className="jira-input-wrap">
              <input className="jira-input" type="text" value={text} onChange={e => setText(e.target.value)}
                placeholder="e.g. A mood tracker app where users pick from 5 emotions"
                disabled={loading} autoFocus
                onKeyDown={e => e.key === "Enter" && canSubmit && submit()} />
              <p className="input-hint">One sentence is enough. The pipeline will interview your idea and generate full requirements, spec, and architecture before building.</p>
            </div>
          )}
          {mode === "paste" && (
            <textarea className="input-textarea" value={text} onChange={e => setText(e.target.value)}
              placeholder={"Paste your requirements. Example:\n\nBuild a Kanban board where users can create boards, add lists, and move cards between them. React frontend, Flask backend, PostgreSQL."}
              rows={10} disabled={loading} autoFocus />
          )}
          {mode === "file" && (
            <div className="file-drop" onClick={() => fileRef.current?.click()}>
              {text ? (
                <div>
                  <div className="file-ok">✓ File loaded ({text.length.toLocaleString()} chars)</div>
                  <pre className="file-sample">{text.slice(0, 280)}{text.length > 280 ? "…" : ""}</pre>
                </div>
              ) : (
                <div className="file-prompt"><IconFile /><span>Click to choose a .md or .txt file</span></div>
              )}
              <input ref={fileRef} type="file" accept=".md,.txt" style={{ display: "none" }}
                onChange={e => { const f = e.target.files?.[0]; if (!f) return; const r = new FileReader(); r.onload = ev => setText(ev.target?.result as string); r.readAsText(f); }} />
            </div>
          )}
          {mode === "jira" && (
            <div className="jira-input-wrap">
              <input className="jira-input" type="text" value={jiraKey} onChange={e => setJiraKey(e.target.value)}
                placeholder="MDP-1" disabled={loading} autoFocus onKeyDown={e => e.key === "Enter" && canSubmit && submit()} />
              <p className="input-hint">The pipeline will fetch this ticket from your Jira workspace and use it as the MVP input.</p>
            </div>
          )}
          <div className="run-mode-row">
            <div className="run-mode-label">Run mode</div>
            <div className="run-mode-options">
              <button type="button" className={`run-mode-opt ${runMode === "full" ? "active" : ""}`} onClick={() => setRunMode("full")} disabled={loading}>Full pipeline</button>
              <button type="button" className={`run-mode-opt ${runMode === "plan_only" ? "active" : ""}`} onClick={() => setRunMode("plan_only")} disabled={loading}>Plan only</button>
              <button type="button" className={`run-mode-opt ${runMode === "sprint_plan_only" ? "active" : ""}`} onClick={() => setRunMode("sprint_plan_only")} disabled={loading}>Sprint plan only</button>
            </div>
            <p className="run-mode-hint">
              {runMode === "full" && "Runs the complete pipeline, including the Claude Code build, smoke checks, review and governance."}
              {runMode === "plan_only" && "Generates requirements, spec, architecture and build prompt only — no Claude Code build, no DeepSeek."}
              {runMode === "sprint_plan_only" && "The architect reads your requirements and decides the full sprint plan — how many sprints, and what each one covers. No Claude Code build, no DeepSeek. Sprint selection happens after the plan is generated, on the results screen."}
            </p>
            {runMode === "sprint_plan_only" && (
              <details className="run-mode-advanced">
                <summary>Advanced: default sprint (optional)</summary>
                <div className="run-mode-sprint-pick">
                  <label htmlFor="selected-sprint-input">Sprint to build after planning</label>
                  <input
                    id="selected-sprint-input" type="number" min={1} max={12}
                    value={selectedSprintInput}
                    onChange={e => setSelectedSprintInput(Math.min(12, Math.max(1, parseInt(e.target.value, 10) || 1)))}
                    disabled={loading}
                  />
                </div>
                <p className="run-mode-hint">
                  The roadmap is generated first, then only this sprint is built or prepared.
                </p>
              </details>
            )}
          </div>
          {error && <p className="input-error">{error}</p>}
          <button className="submit-btn" onClick={submit} disabled={loading || !canSubmit}>
            {loading ? "Starting pipeline…" : "Start Pipeline →"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Runs view ──────────────────────────────────────────────────────────────────

function RunsView({ onSelect, onBack }: { onSelect: (id: string) => void; onBack: () => void }) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    getRuns().then(r => { setRuns(r); setLoading(false); }).catch(() => setLoading(false));
    const i = setInterval(() => getRuns().then(setRuns).catch(() => {}), 5000);
    return () => clearInterval(i);
  }, []);
  return (
    <div className="runs-view">
      <button className="back-btn" onClick={onBack}><IconBack /> Back</button>
      <div className="runs-header"><h2 className="runs-title">Past Runs</h2></div>
      {loading && <div className="runs-loading">Loading…</div>}
      {!loading && runs.length === 0 && <div className="runs-empty">No runs yet.</div>}
      <div className="runs-list">
        {[...runs].reverse().map(r => (
          <div key={r.run_id} className="run-card" onClick={() => onSelect(r.run_id)}>
            <div className="run-card-top">
              <span className="run-card-id">{r.run_id}</span>
              <StatusBadge status={r.status} />
            </div>
            <div className="run-card-meta">
              {r.created && <span>{new Date(r.created).toLocaleString()}</span>}
              {r.current_step && <span>Step: {r.current_step.replace(/_/g, " ")}</span>}
              {r.fix_iteration > 0 && <span>Fix cycles: {r.fix_iteration}</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── App root ───────────────────────────────────────────────────────────────────

type View = { type: "home" } | { type: "input"; mode: EntryMode } | { type: "pipeline"; runId: string } | { type: "runs" };

export default function App() {
  const [view, setView] = useState<View>({ type: "home" });
  return (
    <div className="app">
      {view.type === "home"     && <HomeView onSelect={mode => setView({ type: "input", mode })} onRuns={() => setView({ type: "runs" })} onRunCreated={id => setView({ type: "pipeline", runId: id })} />}
      {view.type === "input"    && <InputView mode={view.mode} onBack={() => setView({ type: "home" })} onCreated={id => setView({ type: "pipeline", runId: id })} />}
      {view.type === "pipeline" && <PipelineView runId={view.runId} onBack={() => setView({ type: "runs" })} onNewRun={id => setView({ type: "pipeline", runId: id })} />}
      {view.type === "runs"     && <RunsView onSelect={id => setView({ type: "pipeline", runId: id })} onBack={() => setView({ type: "home" })} />}
    </div>
  );
}

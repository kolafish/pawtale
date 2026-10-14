import { NARRATION_MODES, type DefaultNarrationMode } from "../domain/memoir-narration-mode";
import type { Project } from "../domain/memoir";
import type {
  MemoirGuidanceMode,
  MemoirGuidanceModule,
  MemoirInterviewType,
  MemoirSessionGoal,
} from "../domain/memoir-voice-session";
import type { MemoirNarratorGender } from "./memoir";

export interface GuidanceOption<T extends string> {
  value: T;
  label: string;
}

export const MEMOIR_GUIDANCE_MODULE_OPTIONS: Array<GuidanceOption<MemoirGuidanceModule>> = [
  { value: "person", label: "人物" },
  { value: "event", label: "事件" },
  { value: "place", label: "地点" },
  { value: "period", label: "时间段" },
  { value: "theme", label: "主题" },
];

export interface MemoirProjectProfile {
  narratorName: string;
  narratorGender: MemoirNarratorGender;
  narratorOrigin: string;
  narratorAge: number | null;
  defaultGuidanceMode: MemoirGuidanceMode;
  defaultInterviewType: MemoirInterviewType;
  defaultSessionGoal: MemoirSessionGoal;
  defaultNarrationMode: DefaultNarrationMode;
}

function metadataProfile(project?: Pick<Project, "metadata_json"> | null): Record<string, unknown> {
  const metadata = project?.metadata_json || {};
  const profile = metadata.memoir_profile;
  return profile && typeof profile === "object" && !Array.isArray(profile)
    ? profile as Record<string, unknown>
    : {};
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function genderValue(value: unknown): MemoirNarratorGender {
  return value === "male" || value === "female" || value === "unspecified" ? value : "unspecified";
}

function ageValue(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  const age = Math.round(value);
  return age >= 0 && age <= 130 ? age : null;
}

function interviewTypeValue(value: unknown): MemoirInterviewType {
  return value === "human_interview" ? "human_interview" : "ai_interview";
}

function sessionGoalValue(value: unknown): MemoirSessionGoal {
  return value === "book_completion" ? "book_completion" : "open_narration";
}

export function memoirProjectProfile(project?: Pick<Project, "metadata_json"> | null): MemoirProjectProfile {
  const profile = metadataProfile(project);
  // Free narration and guided interview are one AI interview now, whatever older projects stored.
  const defaultGuidanceMode: MemoirGuidanceMode = "guided_interview";
  const defaultInterviewType = interviewTypeValue(profile.default_interview_type);
  const defaultSessionGoal = sessionGoalValue(profile.default_session_goal);
  return {
    narratorName: stringValue(profile.narrator_name),
    narratorGender: genderValue(profile.narrator_gender),
    narratorOrigin: stringValue(profile.narrator_origin),
    narratorAge: ageValue(profile.narrator_age),
    defaultGuidanceMode,
    defaultInterviewType,
    defaultSessionGoal,
    defaultNarrationMode: defaultSessionGoal === "book_completion"
      ? "book_completion"
      : defaultInterviewType === "human_interview"
        ? "human_interview"
        : defaultGuidanceMode,
  };
}

export function defaultNarrationSessionFields(mode: DefaultNarrationMode): {
  guidanceMode: MemoirGuidanceMode;
  interviewType: MemoirInterviewType;
  sessionGoal: MemoirSessionGoal;
} {
  if (mode === "book_completion") {
    return {
      guidanceMode: "guided_interview",
      interviewType: "ai_interview",
      sessionGoal: "book_completion",
    };
  }
  return mode === "human_interview"
    ? { guidanceMode: "free_narration", interviewType: "human_interview", sessionGoal: "open_narration" }
    : { guidanceMode: "guided_interview", interviewType: "ai_interview", sessionGoal: "open_narration" };
}

export function guidanceModeLabel(mode: MemoirGuidanceMode): string {
  return NARRATION_MODES[mode].label;
}

export function normalizeGuidanceModule(value?: string | null): MemoirGuidanceModule {
  return MEMOIR_GUIDANCE_MODULE_OPTIONS.some((option) => option.value === value)
    ? value as MemoirGuidanceModule
    : "person";
}

export function guidanceModuleLabel(module?: string | null): string {
  const normalized = normalizeGuidanceModule(module);
  return MEMOIR_GUIDANCE_MODULE_OPTIONS.find((option) => option.value === normalized)?.label || "人物";
}

export function narratorGenderLabel(gender: MemoirNarratorGender): string {
  const labels: Record<MemoirNarratorGender, string> = {
    male: "男",
    female: "女",
    unspecified: "未填写",
  };
  return labels[gender];
}

export function projectProfileSummary(project: Project): string {
  const profile = memoirProjectProfile(project);
  const parts = [
    profile.narratorGender === "unspecified" ? "" : narratorGenderLabel(profile.narratorGender),
    profile.narratorOrigin,
    profile.narratorAge !== null ? `${profile.narratorAge} 岁` : "",
    NARRATION_MODES[profile.defaultNarrationMode].label,
  ].filter(Boolean);
  return parts.join(" · ");
}

/** What the narrator told 小余 about themselves, shown under the home title so a misheard name gets noticed. */
export function narratorProfileLine(project: Project): string {
  const profile = memoirProjectProfile(project);
  return [
    profile.narratorName && !project.title.includes(profile.narratorName) ? profile.narratorName : "",
    profile.narratorGender === "unspecified" ? "" : narratorGenderLabel(profile.narratorGender),
    profile.narratorAge !== null ? `${profile.narratorAge} 岁` : "",
    profile.narratorOrigin,
  ].filter(Boolean).join(" · ");
}

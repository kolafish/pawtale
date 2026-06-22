import type { Project } from "../domain/memoir";
import type { MemoirGuidanceMode, MemoirGuidanceModule } from "../domain/voice-chat";
import type { MemoirNarratorGender } from "./memoir";

export interface GuidanceOption<T extends string> {
  value: T;
  label: string;
}

export const MEMOIR_GUIDANCE_MODE_OPTIONS: Array<GuidanceOption<MemoirGuidanceMode>> = [
  { value: "free_narration", label: "自由讲述" },
  { value: "guided_interview", label: "AI 引导" },
];

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
}

const DEFAULT_PROFILE: MemoirProjectProfile = {
  narratorName: "",
  narratorGender: "unspecified",
  narratorOrigin: "",
  narratorAge: null,
  defaultGuidanceMode: "free_narration",
};

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

function guidanceModeValue(value: unknown): MemoirGuidanceMode {
  return value === "guided_interview" ? "guided_interview" : "free_narration";
}

export function memoirProjectProfile(project?: Pick<Project, "metadata_json"> | null): MemoirProjectProfile {
  const profile = metadataProfile(project);
  return {
    narratorName: stringValue(profile.narrator_name),
    narratorGender: genderValue(profile.narrator_gender),
    narratorOrigin: stringValue(profile.narrator_origin),
    narratorAge: ageValue(profile.narrator_age),
    defaultGuidanceMode: guidanceModeValue(profile.default_guidance_mode),
  };
}

export function guidanceModeLabel(mode: MemoirGuidanceMode): string {
  return mode === "guided_interview" ? "主题讲述" : "自由讲述";
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

export function voiceGuidanceLabel(mode: MemoirGuidanceMode, module?: string | null): string {
  return mode === "guided_interview" ? `AI 引导 · ${guidanceModuleLabel(module)}` : "自由讲述";
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
    narratorGenderLabel(profile.narratorGender),
    profile.narratorOrigin,
    profile.narratorAge !== null ? `${profile.narratorAge} 岁` : "",
    guidanceModeLabel(profile.defaultGuidanceMode),
  ].filter(Boolean);
  return parts.join(" · ");
}

export function defaultMemoirProjectProfile(): MemoirProjectProfile {
  return { ...DEFAULT_PROFILE };
}

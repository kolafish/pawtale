export type MemoirInterviewPromptVariant = "v3_improved" | "standalone" | "v3_plus";

export interface MemoirInterviewPromptOption {
  value: MemoirInterviewPromptVariant;
  label: string;
  description: string;
}

export interface MemoirInterviewPromptSettings {
  variant: MemoirInterviewPromptVariant;
}

export const DEFAULT_MEMOIR_INTERVIEW_PROMPT_VARIANT: MemoirInterviewPromptVariant = "v3_improved";
export const MEMOIR_INTERVIEW_PROMPT_OPTIONS: MemoirInterviewPromptOption[] = [
  {
    value: "v3_improved",
    label: "v3 改进版（默认）",
    description: "当前真机 baseline，保留产品访谈约束并加强事实边界",
  },
  {
    value: "standalone",
    label: "全新 Prompt",
    description: "更强调倾听、故事线索和自然节奏",
  },
  {
    value: "v3_plus",
    label: "v3_plus",
    description: "更完整地检查故事价值、事实关系和访谈边界",
  },
];

const STORAGE_KEY = "pawtale_memoir_interview_prompt_settings";
const VALUES = new Set(MEMOIR_INTERVIEW_PROMPT_OPTIONS.map((option) => option.value));

export function normalizeMemoirInterviewPromptVariant(value: unknown): MemoirInterviewPromptVariant {
  return typeof value === "string" && VALUES.has(value as MemoirInterviewPromptVariant)
    ? value as MemoirInterviewPromptVariant
    : DEFAULT_MEMOIR_INTERVIEW_PROMPT_VARIANT;
}

export function readMemoirInterviewPromptSettings(): MemoirInterviewPromptSettings {
  try {
    const stored = wx.getStorageSync<Partial<MemoirInterviewPromptSettings>>(STORAGE_KEY);
    return { variant: normalizeMemoirInterviewPromptVariant(stored?.variant) };
  } catch (error) {
    console.warn("read memoir interview prompt settings failed", error);
    return { variant: DEFAULT_MEMOIR_INTERVIEW_PROMPT_VARIANT };
  }
}

export function readSelectedMemoirInterviewPromptVariant(): MemoirInterviewPromptVariant {
  return readMemoirInterviewPromptSettings().variant;
}

export function saveMemoirInterviewPromptVariant(value: unknown): MemoirInterviewPromptVariant {
  const variant = normalizeMemoirInterviewPromptVariant(value);
  wx.setStorageSync(STORAGE_KEY, { variant });
  return variant;
}

export function memoirInterviewPromptVariantLabel(value: unknown): string {
  const variant = normalizeMemoirInterviewPromptVariant(value);
  return MEMOIR_INTERVIEW_PROMPT_OPTIONS.find((option) => option.value === variant)?.label || variant;
}

export interface AIModelOption {
  value: string;
  label: string;
  description: string;
}

export interface AIModelSettings {
  model: string;
}

const STORAGE_KEY = "pawtale_ai_model_settings";

export const DEFAULT_AI_MODEL = "deepseek-flash";

export const AI_MODEL_OPTIONS: AIModelOption[] = [
  {
    value: "deepseek-v4-pro",
    label: "DeepSeek V4 Pro",
    description: "质量优先，适合回忆录追问、整理和成文",
  },
  {
    value: "claude-opus-4-8",
    label: "Claude Opus 4.8",
    description: "旗舰推理模型，适合复杂结构和长文润色",
  },
  {
    value: "doubao-seed-2-0-pro-260215",
    label: "Doubao Seed 2.0 Pro",
    description: "豆包旗舰模型，适合中文表达和多模态任务",
  },
  {
    value: "deepseek-flash",
    label: "DeepSeek V4.1 Flash",
    description: "新一代高效模型，适合高频语音追问和整理",
  },
];

const AI_MODEL_VALUES = new Set(AI_MODEL_OPTIONS.map((option) => option.value));
const LEGACY_AI_MODEL_ALIASES: Record<string, string> = {
  "deepseek-v4-flash": "deepseek-flash",
};

export function normalizeAIModel(value: unknown): string {
  if (typeof value !== "string") {
    return DEFAULT_AI_MODEL;
  }
  const model = LEGACY_AI_MODEL_ALIASES[value.trim()] || value.trim();
  return AI_MODEL_VALUES.has(model) ? model : DEFAULT_AI_MODEL;
}

export function normalizeAIModelSettings(value: unknown): AIModelSettings {
  if (!value || typeof value !== "object") {
    return { model: DEFAULT_AI_MODEL };
  }
  const candidate = value as Partial<AIModelSettings>;
  return {
    model: normalizeAIModel(candidate.model),
  };
}

export function aiModelLabel(value: string): string {
  const model = normalizeAIModel(value);
  return (
    AI_MODEL_OPTIONS.find((option) => option.value === model)?.label
    || AI_MODEL_OPTIONS.find((option) => option.value === DEFAULT_AI_MODEL)?.label
    || value
  );
}

export function readAIModelSettings(): AIModelSettings {
  try {
    return normalizeAIModelSettings(wx.getStorageSync<AIModelSettings>(STORAGE_KEY));
  } catch (error) {
    console.warn("read AI model settings failed", error);
    return { model: DEFAULT_AI_MODEL };
  }
}

export function readSelectedAIModel(): string {
  return readAIModelSettings().model;
}

export function saveSelectedAIModel(model: string): string {
  const normalized = normalizeAIModel(model);
  wx.setStorageSync(STORAGE_KEY, { model: normalized });
  return normalized;
}

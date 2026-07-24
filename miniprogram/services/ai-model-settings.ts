export interface AIModelOption {
  value: string;
  label: string;
  description: string;
}

export interface AIModelSettings {
  model: string;
}

const STORAGE_KEY = "pawtale_ai_model_settings";

export const DEFAULT_AI_MODEL = "deepseek-v4-pro";

export const AI_MODEL_OPTIONS: AIModelOption[] = [
  {
    value: "deepseek-v4-pro",
    label: "DeepSeek V4 Pro",
    description: "质量优先，适合回忆录追问、整理和成文",
  },
  {
    value: "claude-fable-5",
    label: "Claude Fable 5",
    description: "高质量别名模型，适合深度追问、整理和成书",
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
    value: "deepseek-v4-flash",
    label: "DeepSeek V4 Flash",
    description: "速度优先，适合高频语音追问",
  },
];

const AI_MODEL_VALUES = new Set(AI_MODEL_OPTIONS.map((option) => option.value));

export function normalizeAIModel(value: unknown): string {
  if (typeof value !== "string") {
    return DEFAULT_AI_MODEL;
  }
  const model = value.trim();
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
  return AI_MODEL_OPTIONS.find((option) => option.value === value)?.label || AI_MODEL_OPTIONS[0].label;
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

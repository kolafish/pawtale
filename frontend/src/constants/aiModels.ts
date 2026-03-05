export type AiModelValue = 'deepseek' | 'claude-opus-4' | 'claude-sonnet-4';

export const AI_MODEL_OPTIONS: { value: AiModelValue; label: string }[] = [
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'claude-opus-4', label: 'Claude Opus 4.6' },
  { value: 'claude-sonnet-4', label: 'Claude Sonnet 4.6' },
];

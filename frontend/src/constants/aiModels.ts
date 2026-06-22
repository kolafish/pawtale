export type AiModelValue =
  | 'deepseek-v4-flash'
  | 'deepseek-v4-pro'
  | 'doubao-seed-2-0-pro-260215'
  | 'claude-opus-4'
  | 'claude-sonnet-4';

export const AI_MODEL_OPTIONS: { value: AiModelValue; label: string }[] = [
  { value: 'deepseek-v4-flash', label: 'DeepSeek V4 Flash' },
  { value: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro' },
  { value: 'doubao-seed-2-0-pro-260215', label: 'Doubao Seed 2.0 Pro' },
  { value: 'claude-opus-4', label: 'Claude Opus 4.6' },
  { value: 'claude-sonnet-4', label: 'Claude Sonnet 4.6' },
];

export type AiModelValue =
  | 'deepseek-flash'
  | 'deepseek-v4-pro'
  | 'doubao-seed-2-0-pro-260215'
  | 'claude-opus-4-8'
  | 'claude-sonnet-4';

export const AI_MODEL_OPTIONS: { value: AiModelValue; label: string }[] = [
  { value: 'deepseek-flash', label: 'DeepSeek V4.1 Flash' },
  { value: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro' },
  { value: 'doubao-seed-2-0-pro-260215', label: 'Doubao Seed 2.0 Pro' },
  { value: 'claude-opus-4-8', label: 'Claude Opus 4.8' },
  { value: 'claude-sonnet-4', label: 'Claude Sonnet 4.6' },
];

export const PUBLIC_AI_MODEL_OPTIONS = AI_MODEL_OPTIONS.filter(
  (option) => option.value === 'deepseek-flash',
);

export function getAIModelOptions(isAdmin: boolean) {
  return isAdmin ? AI_MODEL_OPTIONS : PUBLIC_AI_MODEL_OPTIONS;
}

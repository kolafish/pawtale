export type AiModelValue =
  | 'deepseek-flash'
  | 'deepseek-v4-pro'
  | 'doubao-seed-2-0-pro-260215';

export const AI_MODEL_OPTIONS: { value: AiModelValue; label: string }[] = [
  { value: 'deepseek-flash', label: 'DeepSeek V4.1 Flash' },
  { value: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro' },
  { value: 'doubao-seed-2-0-pro-260215', label: 'Doubao Seed 2.0 Pro' },
];

export const PUBLIC_AI_MODEL_OPTIONS = AI_MODEL_OPTIONS.filter(
  (option) => option.value === 'deepseek-flash',
);

export function getAIModelOptions(isAdmin: boolean) {
  return isAdmin ? AI_MODEL_OPTIONS : PUBLIC_AI_MODEL_OPTIONS;
}

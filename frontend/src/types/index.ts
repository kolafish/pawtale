export interface Character {
  id: string;
  name: string;
  gender?: string;
  age?: number;
  identity?: string;
  appearance?: string;
  personality: string[];
  speaking_style?: string;
  background?: string;
  inspiration?: string;
  avatar_url?: string;
  base_image_url?: string;
  is_preset: boolean;
  created_at: string;
  // 新增深度字段
  role_type?: string; // protagonist/supporting/antagonist
  core_trait?: string; // 底色 - 核心性格驱动力
  life_goal?: string; // 目标追求
  fatal_flaw?: string; // 致命弱点
  growth_hint?: string; // 成长弧线提示
  habits: string[]; // 行为习惯
  secret?: string; // 秘密
}

export interface CharacterAutoComplete {
  gender?: string;
  age?: number;
  identity?: string;
  appearance?: string;
  personality: string[];
  speaking_style?: string;
  background?: string;
  // 新增深度字段
  core_trait?: string;
  life_goal?: string;
  fatal_flaw?: string;
  growth_hint?: string;
  habits: string[];
  secret?: string;
}

export interface AIDebugInfo {
  request: {
    provider: string;
    model: string;
    temperature?: number;
    max_tokens?: number;
    system_prompt?: string;
    user_prompt?: string;
  };
  response: {
    raw_text?: string;
    raw_json?: Record<string, any> | any[] | string | null;
  };
  meta: {
    truncated: boolean;
    max_chars?: number;
    generated_at?: string;
  };
}

export interface Scene {
  id: string;
  name: string;
  category: string;
  description: string;
  location?: string;
  time_weather?: string;
  mood_keywords: string[];
  is_preset: boolean;
  created_at: string;
  // 新增增强字段
  dramatic_potential?: string; // 戏剧潜力
  sensory_details: { visual?: string; auditory?: string; olfactory?: string };
  props: string[]; // 可能的道具/元素
}

// AI 场景生成
export interface SceneAIGenerateRequest {
  category: string;
  mood_keywords: string[];
  character_context?: { character_ids: string[]; relationship?: string };
  custom_prompt?: string;
  count: number;
  ai_model?: string;
  debug?: boolean;
}

export interface SceneCandidate {
  name: string;
  description: string;
  location?: string;
  time_weather?: string;
  mood_keywords: string[];
  dramatic_potential?: string;
  sensory_details: { visual?: string; auditory?: string; olfactory?: string };
  props: string[];
}

export interface SceneAIGenerateResponse {
  candidates: SceneCandidate[];
  debug_info?: AIDebugInfo;
}

// 故事大纲
export interface OutlineCharacter {
  id: string;
  outline_id: string;
  character_id: string;
  role_type: string; // protagonist/deuteragonist/supporting/antagonist
  story_name?: string; // 故事中的称呼
  relationships: Record<string, string>; // 与其他角色的关系
  story_background?: string; // 故事中的特殊背景
  story_goal?: string; // 故事中的目标
  sort_order: number;
  character?: Character;
}

export interface OutlineScene {
  id: string;
  outline_id: string;
  scene_id: string;
  usage_hint?: string; // 用途提示
  is_starting_scene: boolean;
  sort_order: number;
  scene?: Scene;
}

export interface StoryOutline {
  id: string;
  title: string;
  mood?: string;
  writing_style?: string;
  chapter_length?: string;
  central_conflict?: string; // 核心冲突
  ending_type?: string; // HE/BE/OE
  plot_hints: string[]; // 剧情走向提示
  forbidden_elements: string[]; // 禁止元素
  ai_model?: string; // AI模型选择
  story_goal?: string; // 故事目标与读者感受
  character_tension?: string; // 角色关系与核心张力
  creation_stage?: string; // draft/confirmed
  is_active: boolean;
  created_at: string;
  updated_at: string;
  outline_characters: OutlineCharacter[];
  outline_scenes: OutlineScene[];
}

// 节点中出场的角色
export interface PresentCharacter {
  character_id: string;
  name: string;
  role_type?: string;
}

// 场景切换信息
export interface SceneTransition {
  from_scene?: string;
  to_scene?: string;
  transition_type?: string;
}

// 角色变动信息
export interface CharacterChanges {
  entered: string[];
  exited: string[];
}

export interface StoryNode {
  title?: string;
  content: string;
  word_count?: number;
  ai_model?: string;
  choices: { label: string; next_node: string | null }[];
  // 新增场景和角色信息
  scene_id?: string;
  scene_name?: string;
  present_characters?: PresentCharacter[];
  scene_transition?: SceneTransition;
  character_changes?: CharacterChanges;
}

export interface Story {
  id: string;
  title: string;
  outline_id?: string;
  scene_id?: string;
  mood?: string;
  writing_style?: string;
  chapter_length?: string;
  ai_model?: string; // AI模型选择
  nodes: Record<string, StoryNode>;
  current_node_id?: string;
  current_path: string[];
  is_favorite: boolean;
  created_at: string;
  updated_at: string;
  characters: Character[];
}

export interface StoryGenerateRequest {
  character_ids: string[];
  scene_id: string;
  mood: string;
  writing_style: string;
  chapter_length: string;
  title?: string;
  custom_direction?: string;
  outline_id?: string;
  ai_model?: string; // AI模型选择
  debug?: boolean;
}

export interface StoryBranchRequest {
  choice_index?: number;
  custom_direction?: string;
  ai_model?: string;
  // 新增场景切换和角色管理
  switch_scene_id?: string;
  add_character_ids?: string[];
  remove_character_ids?: string[];
  debug?: boolean;
}

export interface StoryGenerateResponse {
  story_id: string;
  node_id: string;
  title?: string;
  content: string;
  word_count?: number;
  ai_model?: string;
  choices: { label: string; next_node: string | null }[];
  // 新增场景和角色信息
  scene_id?: string;
  scene_name?: string;
  present_characters?: PresentCharacter[];
  debug_info?: AIDebugInfo;
}

export interface StorySwitchSceneRequest {
  scene_id: string;
  transition_type?: string;
  ai_model?: string;
  debug?: boolean;
}

export interface StoryUpdateCharactersRequest {
  add_character_ids: string[];
  remove_character_ids: string[];
}

export interface StoryNodeUpdateRequest {
  title?: string;
  content?: string;
}

export interface StoryNodeRegenerateRequest {
  ai_model?: string;
  debug?: boolean;
}

export interface StoryNodeReviseRequest {
  instruction: string;
  ai_model?: string;
  debug?: boolean;
}

// 重新生成分支选项
export interface RegenerateChoicesRequest {
  hint?: string;
  ai_model?: string;
  debug?: boolean;
}

export interface RegenerateChoicesResponse {
  choices: { label: string; next_node: string | null }[];
  debug_info?: AIDebugInfo;
}

// 润色章节
export interface PolishChaptersRequest {
  node_ids: string[];
  instruction?: string;  // 用户的润色意见（可选）
  ai_model?: string;
  debug?: boolean;
}

export interface PolishChaptersResponse {
  polished_contents: { node_id: string; content: string; ai_model?: string }[];
  debug_info?: AIDebugInfo;
}

export interface StoryExportResponse {
  title: string;
  chapters: {
    number: number;
    title: string;
    content: string;
    word_count: number;
  }[];
  total_words: number;
}

export interface ExtractedCharacter {
  name: string;
  role_hint?: string;
  traits: string[];
  summary?: string;
}

export interface ExtractedScene {
  name: string;
  description?: string;
  keywords: string[];
}

export interface ExtractedOutlinePoint {
  stage: string;
  summary: string;
}

export interface StoryWorkspace {
  story_id: string;
  title: string;
  mood?: string;
  writing_style?: string;
  chapter_length?: string;
  ai_model?: string;
  editor_content: string;
  extracted_characters: ExtractedCharacter[];
  extracted_scenes: ExtractedScene[];
  extracted_outline_points: ExtractedOutlinePoint[];
  extraction_notes: string[];
  created_at: string;
  updated_at: string;
}

export interface StoryWorkspaceCreateRequest {
  title?: string;
  content?: string;
  mood?: string;
  writing_style?: string;
  chapter_length?: string;
  ai_model?: string;
}

export interface StoryWorkspaceUpdateRequest {
  title?: string;
  content?: string;
  mood?: string;
  writing_style?: string;
  chapter_length?: string;
  ai_model?: string;
}

export interface StoryWorkspaceAIContinueRequest {
  instruction?: string;
  ai_model?: string;
  append?: boolean;
  debug?: boolean;
}

export interface StoryWorkspaceAIContinueResponse {
  story_id: string;
  editor_content: string;
  generated_chunk: string;
  choices: { label: string; next_node: string | null }[];
  ai_model: string;
  debug_info?: AIDebugInfo;
}

export interface StoryWorkspaceExtractRequest {
  content?: string;
  ai_model?: string;
  debug?: boolean;
}

export interface StoryWorkspaceExtractResponse {
  story_id: string;
  extracted_characters: ExtractedCharacter[];
  extracted_scenes: ExtractedScene[];
  extracted_outline_points: ExtractedOutlinePoint[];
  extraction_notes: string[];
  debug_info?: AIDebugInfo;
}

export interface StoryWorkspaceExtractionUpdateRequest {
  extracted_characters?: ExtractedCharacter[];
  extracted_scenes?: ExtractedScene[];
  extracted_outline_points?: ExtractedOutlinePoint[];
  extraction_notes?: string[];
}

// 大纲相关请求类型
export interface OutlineCharacterCreate {
  character_id: string;
  role_type: string;
  story_name?: string;
  relationships?: Record<string, string>;
  story_background?: string;
  story_goal?: string;
  sort_order?: number;
}

export interface OutlineCharacterUpdate {
  role_type?: string;
  story_name?: string;
  relationships?: Record<string, string>;
  story_background?: string;
  story_goal?: string;
  sort_order?: number;
}

export interface OutlineSceneCreate {
  scene_id: string;
  usage_hint?: string;
  is_starting_scene?: boolean;
  sort_order?: number;
}

export interface OutlineSceneUpdate {
  usage_hint?: string;
  is_starting_scene?: boolean;
  sort_order?: number;
}

export interface StoryOutlineCreate {
  title: string;
  mood?: string;
  writing_style?: string;
  chapter_length?: string;
  central_conflict?: string;
  ending_type?: string;
  plot_hints?: string[];
  forbidden_elements?: string[];
  ai_model?: string;
  story_goal?: string;
  character_tension?: string;
  creation_stage?: string;
}

export interface StoryOutlineUpdate {
  title?: string;
  mood?: string;
  writing_style?: string;
  chapter_length?: string;
  central_conflict?: string;
  ending_type?: string;
  plot_hints?: string[];
  forbidden_elements?: string[];
  is_active?: boolean;
  ai_model?: string;
  story_goal?: string;
  character_tension?: string;
  creation_stage?: string;
}

export interface StoryOutlineGenerateRequest {
  custom_direction?: string;
  starting_scene_id?: string;
  ai_model?: string;
}

// AI 增强大纲
export interface OutlineEnhanceRequest {
  ai_model?: string;
}

export interface OutlineEnhanceResponse {
  central_conflict: string;
  plot_hints: string[];
  suggested_mood?: string;
  suggested_ending?: string;
}

// 大纲模板
export interface OutlineTemplate {
  id: string;
  name: string;
  description: string;
  mood: string;
  writing_style: string;
  central_conflict: string;
  ending_type: string;
  plot_hints: string[];
  tags: string[];
}

export interface OutlineTemplatesResponse {
  templates: OutlineTemplate[];
}

// 引导式创建大纲
export interface OutlineGuidedCreateRequest {
  story_goal: string; // 故事目标与读者感受
  character_tension: string; // 角色关系与核心张力
  central_conflict: string; // 剧情核心冲突
  character_ids?: string[]; // 已有角色ID列表
  mood?: string; // 期望的情绪基调
  ai_model?: string; // 使用的AI模型
}

export interface OutlineGuidedCreateResponse {
  title: string; // AI生成的标题建议
  mood: string; // 情绪基调
  writing_style: string; // 文字风格建议
  central_conflict: string; // 核心冲突（整理后）
  ending_type: string; // 结局类型
  plot_hints: string[]; // 剧情提示
  forbidden_elements: string[]; // 禁止元素
  story_goal: string; // 原始故事目标
  character_tension: string; // 原始角色张力
}

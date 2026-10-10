export type MemoirTopicMapEvidenceState = "none" | "has_material";

export type MemoirTopicMapUserResolution =
  | "none"
  | "enough_for_now"
  | "not_applicable"
  | "do_not_discuss";

export type MemoirTopicMapDisplayState =
  | "not_started"
  | "touched"
  | "covered"
  | "enough_for_now"
  | "not_applicable"
  | "do_not_discuss";

export interface MemoirTopicMapItem {
  id: string;
  item_key: string;
  title: string;
  intent: string;
  evidence_state: MemoirTopicMapEvidenceState;
  user_resolution: MemoirTopicMapUserResolution;
  display_state: MemoirTopicMapDisplayState;
  state_text: string;
  state_detail: string;
  resolved_at: string | null;
}

export interface MemoirTopicMapModule {
  module_key: string;
  title: string;
  items: MemoirTopicMapItem[];
}

export interface MemoirTopicMap {
  project_id: string;
  template_key: string;
  template_version: string;
  headline: string;
  detail: string;
  modules: MemoirTopicMapModule[];
}

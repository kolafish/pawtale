export type AsrCorrectionRegexRuleConfig = {
  id: string;
  kind: "regex";
  chapter_edit_only?: boolean;
  description: string;
  pattern: string;
  replacement: string;
};

export type AsrCorrectionContextualHomophoneRuleConfig = {
  id: string;
  kind: "contextual_homophone";
  description: string;
  terms: string[];
  replacement: string;
  before_context_patterns: string[];
  after_context_patterns: string[];
};

export type AsrCorrectionRuleConfig =
  | AsrCorrectionRegexRuleConfig
  | AsrCorrectionContextualHomophoneRuleConfig;

export type AsrCorrectionRulesConfig = {
  version: number;
  rules: AsrCorrectionRuleConfig[];
};

export const ASR_CORRECTION_RULES_CONFIG = {
  version: 1,
  rules: [
    {
      id: "reorder_chapter_ordinals",
      kind: "regex",
      chapter_edit_only: true,
      description: "Correct paired chapter references in a directory reorder instruction.",
      pattern: "^(\\s*(?:请\\s*)?(?:(?:把|将)\\s*)?)第\\s*([0-9一二两三四五六七八九十]+)\\s*张(\\s*(?:和|跟|与)\\s*)第\\s*([0-9一二两三四五六七八九十]+)\\s*张(?=\\s*(?:换一下|对调|互换|交换)\\s*[。！？.!?]?\\s*$)",
      replacement: "{1}第{2}章{3}第{4}章",
    },
    {
      id: "move_chapter_relative_to_another",
      kind: "regex",
      chapter_edit_only: true,
      description: "Correct paired chapter references in a relative move instruction.",
      pattern: "^(\\s*(?:请\\s*)?(?:(?:把|将)\\s*)?)第\\s*([0-9一二两三四五六七八九十]+)\\s*张(\\s*(?:放|移|调)到\\s*)第\\s*([0-9一二两三四五六七八九十]+)\\s*张(?=\\s*(?:前面|后面|之前|之后)\\s*[。！？.!?]?\\s*$)",
      replacement: "{1}第{2}章{3}第{4}章",
    },
    {
      id: "chapter_ordinal_zhang_to_chapter",
      kind: "regex",
      chapter_edit_only: true,
      description: "Correct a chapter ordinal used as the target of an editing command.",
      pattern: "^(\\s*(?:请\\s*)?(?:(?:把|将)\\s*)?)第\\s*([0-9一二两三四五六七八九十]+)\\s*张(?=\\s*(?:改名|重命名|上移|下移))",
      replacement: "{1}第{2}章",
    },
    {
      id: "chapter_split_count",
      kind: "regex",
      chapter_edit_only: true,
      description: "Correct a standalone count following a chapter split or merge instruction.",
      pattern: "^(\\s*(?:拆|分|合并)成\\s*[0-9一二两三四五六七八九十]+)\\s*张(?=\\s*(?:吧|呢|吗)?\\s*[。！？.!?]?\\s*$)",
      replacement: "{1}章",
    },
    {
      id: "chapter_count_question",
      kind: "regex",
      chapter_edit_only: true,
      description: "Correct only a standalone chapter count question in the editor.",
      pattern: "^(\\s*(?:(?:这本书|本书|全书|目录|回忆录)(?:里)?\\s*)?(?:(?:现在|目前|一共|总共)\\s*)?有\\s*)(几|多少)\\s*张(?=\\s*(?:啊|呢|吗)?\\s*[。！？.!?]?\\s*$)",
      replacement: "{1}{2}章",
    },
    {
      id: "chapter_term_homophones",
      kind: "contextual_homophone",
      description: "Correct person-name-like homophones when the local context points to book chapters.",
      terms: ["张杰", "张洁", "张节", "章杰"],
      replacement: "章节",
      before_context_patterns: [
        "书的",
        "本书的",
        "成书的",
        "目录的",
        "回忆录的",
        "未\\s*收入",
        "未\\s*收录",
        "没\\s*收入",
        "没\\s*收录",
        "归\\s*到",
        "归\\s*入",
        "收\\s*录\\s*到",
        "收\\s*入\\s*到",
        "放\\s*到",
        "分\\s*到",
        "(?:现在有|一共有|总共有)?\\s*(?:几|几个|多少|哪些|哪个|每个|所有|全部)",
        "不是.{0,8}人名的?",
      ],
      after_context_patterns: [
        "规划",
        "结构",
        "顺序",
        "目录",
        "标题",
        "题目",
        "章名",
        "正文",
        "内容",
        "归属",
        "归入",
        "归到",
        "收录",
        "收入",
        "改名",
        "改成",
        "移动",
        "上移",
        "下移",
        "合并",
        "拆分",
        "确认",
      ],
    },
  ],
} satisfies AsrCorrectionRulesConfig;

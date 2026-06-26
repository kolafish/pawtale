const CHINESE_NUMBER = "0-9一二两三四五六七八九十";
const CHAPTER_HOMOPHONE_RE = /张杰|张洁|张节|章杰/g;
const CHAPTER_WORD_ASR_ORDINAL_RE = new RegExp(`第\\s*([${CHINESE_NUMBER}]+)\\s*张`, "g");
const CHAPTER_WORD_ASR_BARE_RE = new RegExp(
  `([${CHINESE_NUMBER}]+)\\s*张(?=(?:和|跟|与|的|换|调|移|顺序|章节|章|[，。,.!！?？、\\s]|$))`,
  "g",
);
const CHAPTER_HOMOPHONE_BEFORE_RE = new RegExp(
  [
    "(?:",
    "书的|本书的|成书的|目录的|回忆录的|",
    "未\\s*收入|未\\s*收录|没\\s*收入|没\\s*收录|",
    "归\\s*到|归\\s*入|收\\s*录\\s*到|收\\s*入\\s*到|放\\s*到|分\\s*到|",
    "(?:现在有|一共有|总共有)?\\s*(?:几|几个|多少|哪些|哪个|每个|所有|全部)|",
    "不是.{0,8}人名的?",
    ")\\s*$",
  ].join(""),
);
const CHAPTER_HOMOPHONE_AFTER_RE = new RegExp(
  [
    "^\\s*(?:",
    "规划|结构|顺序|目录|标题|题目|章名|正文|内容|归属|",
    "归入|归到|收录|收入|改名|改成|移动|上移|下移|合并|拆分|确认",
    ")",
  ].join(""),
);

export function normalizeMemoirAsrText(text: string): string {
  let normalized = text;
  normalized = normalized.replace(CHAPTER_WORD_ASR_ORDINAL_RE, "第$1章");
  normalized = normalized.replace(CHAPTER_WORD_ASR_BARE_RE, "$1章");
  normalized = normalized.replace(/几\s*张/g, "几章");
  normalized = normalized.replace(/多少\s*张/g, "多少章");
  return normalized.replace(CHAPTER_HOMOPHONE_RE, (match, offset) => (
    shouldReplaceChapterHomophone(normalized, offset, match.length) ? "章节" : match
  ));
}

function shouldReplaceChapterHomophone(text: string, start: number, length: number): boolean {
  const before = text.slice(Math.max(0, start - 18), start);
  const after = text.slice(start + length, start + length + 18);
  return CHAPTER_HOMOPHONE_BEFORE_RE.test(before) || CHAPTER_HOMOPHONE_AFTER_RE.test(after);
}

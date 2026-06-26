type AsrCorrectionRegexRuleConfig = {
  kind: "regex";
  pattern: string;
  replacement: string;
};

type AsrCorrectionContextualHomophoneRuleConfig = {
  kind: "contextual_homophone";
  terms: string[];
  replacement: string;
  before_context_patterns: string[];
  after_context_patterns: string[];
};

type AsrCorrectionRuleConfig =
  | AsrCorrectionRegexRuleConfig
  | AsrCorrectionContextualHomophoneRuleConfig;

type AsrCorrectionRulesConfig = {
  version: number;
  rules: AsrCorrectionRuleConfig[];
};

type RegexRule = {
  kind: "regex";
  pattern: RegExp;
  replacement: string;
};

type ContextualHomophoneRule = {
  kind: "contextual_homophone";
  pattern: RegExp;
  replacement: string;
  beforePatterns: RegExp[];
  afterPatterns: RegExp[];
};

type AsrCorrectionRule = RegexRule | ContextualHomophoneRule;

declare const require: (path: string) => AsrCorrectionRulesConfig;

const correctionRules = require("./asr-correction-rules.json");
const NORMALIZATION_RULES: AsrCorrectionRule[] = correctionRules.rules.map(compileRule);

export function normalizeMemoirAsrText(text: string): string {
  let normalized = text;
  NORMALIZATION_RULES.forEach((rule) => {
    if (rule.kind === "regex") {
      normalized = replaceRegexRule(normalized, rule);
      return;
    }
    normalized = replaceContextualHomophoneRule(normalized, rule);
  });
  return normalized;
}

function replaceRegexRule(text: string, rule: RegexRule): string {
  return text.replace(rule.pattern, (...args: Array<string | number>) => {
    const captures = args.slice(1, -2).map((value) => String(value || ""));
    return formatReplacement(rule.replacement, captures);
  });
}

function replaceContextualHomophoneRule(text: string, rule: ContextualHomophoneRule): string {
  return text.replace(rule.pattern, (match, offset) => (
    shouldReplaceContextualHomophone(text, offset, match.length, rule) ? rule.replacement : match
  ));
}

function shouldReplaceContextualHomophone(
  text: string,
  start: number,
  length: number,
  rule: ContextualHomophoneRule,
): boolean {
  const before = text.slice(Math.max(0, start - 18), start);
  const after = text.slice(start + length, start + length + 18);
  return rule.beforePatterns.some((pattern) => pattern.test(before))
    || rule.afterPatterns.some((pattern) => pattern.test(after));
}

function formatReplacement(template: string, captures: string[]): string {
  return captures.reduce(
    (result, capture, index) => result.replace(new RegExp(`\\{${index + 1}\\}`, "g"), capture),
    template,
  );
}

function compileRule(rule: AsrCorrectionRuleConfig): AsrCorrectionRule {
  if (rule.kind === "regex") {
    return {
      kind: "regex",
      pattern: new RegExp(rule.pattern, "g"),
      replacement: rule.replacement,
    };
  }
  return {
    kind: "contextual_homophone",
    pattern: new RegExp(rule.terms.map(escapeRegExp).join("|"), "g"),
    replacement: rule.replacement,
    beforePatterns: rule.before_context_patterns.map((pattern) => new RegExp(`(?:${pattern})\\s*$`)),
    afterPatterns: rule.after_context_patterns.map((pattern) => new RegExp(`^\\s*(?:${pattern})`)),
  };
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

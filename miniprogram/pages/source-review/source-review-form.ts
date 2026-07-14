import type {
  MemoirBookSourceReviewConfirmRequest,
  MemoirFollowupAnswerKind,
} from "../../domain/voice-chat";


export interface MixedAnswerParts {
  directNarrativeText: string;
  clarificationText: string;
  factResolutionText: string;
}


function occurrenceSpans(sourceText: string, excerpt: string): Array<[number, number]> {
  const spans: Array<[number, number]> = [];
  let start = sourceText.indexOf(excerpt);
  while (start >= 0) {
    spans.push([start, start + excerpt.length]);
    start = sourceText.indexOf(excerpt, start + 1);
  }
  return spans;
}


function hasNonOverlappingOccurrences(sourceText: string, excerpts: string[]): boolean {
  const options = excerpts.map((excerpt) => occurrenceSpans(sourceText, excerpt));
  const choose = (index: number, chosen: Array<[number, number]>): boolean => {
    if (index >= options.length) {
      return true;
    }
    return options[index].some(([start, end]) => {
      const overlaps = chosen.some(
        ([chosenStart, chosenEnd]) => start < chosenEnd && chosenStart < end,
      );
      return !overlaps && choose(index + 1, [...chosen, [start, end]]);
    });
  };
  return choose(0, []);
}


export function buildSourceReviewConfirmationRequest(
  sourceText: string,
  answerKind: MemoirFollowupAnswerKind | "",
  mixedParts: MixedAnswerParts,
): MemoirBookSourceReviewConfirmRequest {
  const source = sourceText.trim();
  if (!source) {
    throw new Error("这段回答没有可确认的文字");
  }
  if (!answerKind) {
    throw new Error("请先选择这段话的用途");
  }
  if (answerKind === "direct_narrative") {
    return { followup_answer_kind: answerKind, direct_narrative_text: source };
  }
  if (answerKind === "clarification") {
    return { followup_answer_kind: answerKind, clarification_text: source };
  }
  if (answerKind === "fact_resolution") {
    return { followup_answer_kind: answerKind, fact_resolution_text: source };
  }

  const directNarrativeText = mixedParts.directNarrativeText.trim();
  const clarificationText = mixedParts.clarificationText.trim();
  const factResolutionText = mixedParts.factResolutionText.trim();
  const excerpts = [directNarrativeText, clarificationText, factResolutionText].filter(Boolean);
  if (excerpts.length < 2) {
    throw new Error("请至少拆出两种不同用途的原话");
  }
  if (excerpts.some((excerpt) => !source.includes(excerpt))) {
    throw new Error("拆分内容必须完整保留在上方原话中");
  }
  if (!hasNonOverlappingOccurrences(source, excerpts)) {
    throw new Error("拆分的几段原话不能互相重叠");
  }
  return {
    followup_answer_kind: "mixed",
    direct_narrative_text: directNarrativeText,
    clarification_text: clarificationText,
    fact_resolution_text: factResolutionText,
  };
}

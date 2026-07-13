import type {
  MemoirChapterContractConstraints,
  MemoirChapterContractRevisionRequest,
  MemoirChapterCoreQuestion,
  MemoirChapterStructureSplitRequest,
  MemoirChapterWorkspaceContract,
} from "../../domain/chapter-workspace";
import type { MemoirBookPlan } from "../../domain/voice-chat";


export interface ContractEditorFields {
  objective: string;
  readerTakeaway: string;
  timeScopeText: string;
  questionsText: string;
}

export interface SplitEditorFields extends ContractEditorFields {
  title: string;
}

export interface ChapterMergeOption {
  id: string;
  title: string;
  label: string;
}

const EMPTY_CONSTRAINTS: MemoirChapterContractConstraints = {
  include_story_ids: [],
  exclude_story_ids: [],
  include_topics: [],
  exclude_topics: [],
};

function intentHash(value: string): string {
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ code, 0x85ebca6b);
  }
  return `${(first >>> 0).toString(16).padStart(8, "0")}${(second >>> 0).toString(16).padStart(8, "0")}`;
}

export function chapterStructureIntentKey(
  action: string,
  ...parts: unknown[]
): string {
  const normalizedAction = action.replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 40) || "edit";
  return `workspace:${normalizedAction}:${intentHash(JSON.stringify(parts))}`;
}

function questionLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function questionKeySuffix(idempotencyKey: string): string {
  return idempotencyKey.replace(/[^A-Za-z0-9_-]/g, "_").slice(-40) || "revision";
}

function versionedQuestions(
  text: string,
  existing: MemoirChapterCoreQuestion[],
  idempotencyKey: string,
): MemoirChapterCoreQuestion[] {
  const lines = questionLines(text);
  if (!lines.length) {
    throw new Error("至少填写一个章节核心问题");
  }
  const usedKeys = new Set<string>();
  const suffix = questionKeySuffix(idempotencyKey);
  return lines.map((question, index) => {
    const exact = existing.find((item) => (
      item.question === question && !usedKeys.has(item.key)
    ));
    const samePosition = existing[index];
    const key = exact?.key
      || (samePosition && !usedKeys.has(samePosition.key) ? samePosition.key : "")
      || `q_${index + 1}_${suffix}`.slice(0, 80);
    usedKeys.add(key);
    return { key, question };
  });
}

export function contractEditorFields(
  contract: MemoirChapterWorkspaceContract | null,
): ContractEditorFields {
  return {
    objective: contract?.objective || "",
    readerTakeaway: contract?.reader_takeaway || "",
    timeScopeText: contract?.time_scope_text || "",
    questionsText: (contract?.core_questions || [])
      .map((question) => question.question)
      .join("\n"),
  };
}

export function splitEditorFields(
  contract: MemoirChapterWorkspaceContract,
): SplitEditorFields {
  return {
    title: "",
    objective: "",
    readerTakeaway: "",
    timeScopeText: contract.time_scope_text,
    questionsText: "",
  };
}

export function buildContractRevisionRequest(
  contract: MemoirChapterWorkspaceContract | null,
  fields: ContractEditorFields,
  idempotencyKey: string,
): MemoirChapterContractRevisionRequest {
  const objective = fields.objective.trim();
  const readerTakeaway = fields.readerTakeaway.trim();
  if (!objective || !readerTakeaway) {
    throw new Error("章节目标和读者收获都不能为空");
  }
  return {
    idempotency_key: idempotencyKey,
    objective,
    reader_takeaway: readerTakeaway,
    time_scope_text: fields.timeScopeText.trim(),
    time_start: contract?.time_start || null,
    time_end: contract?.time_end || null,
    core_questions: versionedQuestions(
      fields.questionsText,
      contract?.core_questions || [],
      idempotencyKey,
    ),
    constraints: contract
      ? {
        include_story_ids: [...contract.constraints.include_story_ids],
        exclude_story_ids: [...contract.constraints.exclude_story_ids],
        include_topics: [...contract.constraints.include_topics],
        exclude_topics: [...contract.constraints.exclude_topics],
      }
      : { ...EMPTY_CONSTRAINTS },
    copy_from_contract_id: contract?.id || null,
    copy_nodes: Boolean(contract),
  };
}

export function buildSplitRequest(
  contract: MemoirChapterWorkspaceContract,
  nodeIds: string[],
  fields: SplitEditorFields,
  idempotencyKey: string,
): MemoirChapterStructureSplitRequest {
  const selectedIds = [...new Set(nodeIds.filter(Boolean))];
  if (!selectedIds.length) {
    throw new Error("请选择要移到新章节的结构节点");
  }
  if (selectedIds.length >= contract.nodes.length) {
    throw new Error("原章节至少需要保留一个结构节点");
  }
  const title = fields.title.trim();
  const objective = fields.objective.trim();
  const readerTakeaway = fields.readerTakeaway.trim();
  if (!title || !objective || !readerTakeaway) {
    throw new Error("新章节标题、目标和读者收获都不能为空");
  }
  return {
    idempotency_key: idempotencyKey,
    source_contract_id: contract.id,
    node_ids: selectedIds,
    target_title: title,
    target_objective: objective,
    target_reader_takeaway: readerTakeaway,
    target_time_scope_text: fields.timeScopeText.trim(),
    target_core_questions: versionedQuestions(
      fields.questionsText,
      [],
      idempotencyKey,
    ),
  };
}

export function chapterMergeOptions(
  plan: MemoirBookPlan,
  currentChapterId: string,
): ChapterMergeOption[] {
  return [...plan.chapters]
    .sort((left, right) => left.sort_order - right.sort_order)
    .filter((chapter) => chapter.id !== currentChapterId)
    .map((chapter) => ({
      id: chapter.id,
      title: chapter.title,
      label: `第 ${chapter.sort_order + 1} 章 · ${chapter.title}`,
    }));
}

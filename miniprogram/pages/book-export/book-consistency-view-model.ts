import type {
  MemoirBookOutput,
  MemoirBookReadiness,
  MemoirBookReadinessBlocker,
} from "../../domain/voice-chat";

export interface BookConsistencyItemView {
  id: string;
  title: string;
  detail: string;
}

export interface BookConsistencyData {
  bookReadiness: MemoirBookReadiness | null;
  bookConsistencyStatusText: string;
  bookConsistencyMetaText: string;
  bookConsistencyMustItems: BookConsistencyItemView[];
  bookConsistencySuggestedItems: BookConsistencyItemView[];
  bookConsistencyAcceptedItems: BookConsistencyItemView[];
  bookConsistencyCanFormalize: boolean;
}

function blockerView(item: MemoirBookReadinessBlocker): BookConsistencyItemView {
  return {
    id: item.gap_id || item.code,
    title: item.title,
    detail: item.detail || "",
  };
}

export function bookConsistencyData(
  readiness: MemoirBookReadiness | null,
  output: MemoirBookOutput | null,
  auditRunning = false,
): BookConsistencyData {
  const must = readiness?.groups.must_handle || [];
  const suggested = readiness?.groups.suggested || [];
  const accepted = readiness?.groups.accepted_incomplete || [];
  let statusText = "待检查";
  let metaText = "检查全书时间、人物关系、事实来源和跨章重复。";
  if (auditRunning) {
    statusText = "检查中";
    metaText = "正在后台审校，完成后会自动更新结果。";
  } else if (output?.status === "accepted" && readiness?.audit_is_current) {
    statusText = "正式书稿";
    metaText = "当前正式版本已通过全书一致性门禁。";
  } else if (readiness?.ready_for_formal) {
    statusText = "可以正式成书";
    metaText = suggested.length
      ? `没有阻塞问题，另有 ${suggested.length} 条改进建议。`
      : "当前书稿没有阻塞性问题。";
  } else if (readiness) {
    statusText = must.length ? `${must.length} 条必须处理` : "需要重新检查";
    metaText = readiness.audit_is_current
      ? "处理下列问题后，再标记为正式书稿。"
      : "书稿或事实已变化，需要重新运行全书审校。";
  }
  return {
    bookReadiness: readiness,
    bookConsistencyStatusText: statusText,
    bookConsistencyMetaText: metaText,
    bookConsistencyMustItems: must.map(blockerView),
    bookConsistencySuggestedItems: suggested.map(blockerView),
    bookConsistencyAcceptedItems: accepted.map(blockerView),
    bookConsistencyCanFormalize: Boolean(
      readiness?.ready_for_formal
      && readiness.latest_audit_job
      && readiness.output_version_id,
    ),
  };
}

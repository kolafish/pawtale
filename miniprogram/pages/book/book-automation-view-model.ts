import type { MemoirAutomationEvent } from "../../domain/voice-chat";
import { formatBeijingDateTimeShort } from "../../services/time";
import { createdAtTime, trimText } from "./book-view-model-utils";

export interface BookAutomationEventView {
  id: string;
  eventIds: string[];
  eventIdsText: string;
  eventType: MemoirAutomationEvent["event_type"];
  actionKind: "acknowledge" | "acknowledge_group" | "review_source" | "confirm_answer" | "none";
  primaryUtteranceId: string;
  titleText: string;
  summaryText: string;
  detailText: string;
  suggestedActionText: string;
  suggestionItems: BookAutomationEventSuggestionView[];
  hasSuggestions: boolean;
  detailItems: BookAutomationEventDetailView[];
  detailExpanded: boolean;
  detailToggleText: string;
  hasDetails: boolean;
  metaText: string;
  statusText: string;
  actionText: string;
  canAcknowledge: boolean;
  canAct: boolean;
  busy: boolean;
}

export interface BookAutomationEventSuggestionView {
  id: string;
  labelText: string;
  bodyText: string;
  chapterText: string;
}

export interface BookAutomationEventDetailView {
  id: string;
  titleText: string;
  chapterText: string;
  metaText: string;
  previewText: string;
}

function isBookGenerationEvent(event: MemoirAutomationEvent): boolean {
  return event.event_type === "book_editorial_copy_generated";
}

export function pendingBookGenerationEvents(events: MemoirAutomationEvent[]): MemoirAutomationEvent[] {
  return events.filter((event) => isBookGenerationEvent(event) && event.status === "pending");
}

export function mergeAutomationEvent(
  events: MemoirAutomationEvent[],
  event: MemoirAutomationEvent,
): MemoirAutomationEvent[] {
  return [event, ...events.filter((candidate) => candidate.id !== event.id)];
}

function automationEventTitle(event: MemoirAutomationEvent): string {
  if (event.event_type === "source_assigned") {
    return "已自动归章";
  }
  if (event.event_type === "source_held") {
    if (followupAnswerNeedsConfirmation(event)) {
      return "回答用途待确认";
    }
    return event.status === "needs_review" ? "待整理讲述" : "整理建议已处理";
  }
  if (event.event_type === "chapter_auto_revised") {
    return "已自动补写";
  }
  if (event.event_type === "audit_completed") {
    return stringValue(automationEventDisplay(event).title) || "成书整理建议";
  }
  if (event.event_type === "chapter_draft_generated") {
    return event.status === "pending" ? "章节生成中" : "章节已生成";
  }
  if (event.event_type === "book_output_generated") {
    if (event.status === "failed") {
      return "书稿整理失败";
    }
    return event.status === "pending" ? "书稿整理中" : "书稿整理完成";
  }
  if (event.event_type === "book_editorial_copy_generated") {
    if (event.status === "failed") {
      return "文案生成失败";
    }
    return event.status === "pending" ? "文案生成中" : "文案已更新";
  }
  return "自动整理";
}

function automationEventKindText(event: MemoirAutomationEvent): string {
  if (event.event_type === "source_assigned") {
    return "归章";
  }
  if (event.event_type === "source_held") {
    return "待整理";
  }
  if (event.event_type === "chapter_auto_revised") {
    return "补写";
  }
  if (event.event_type === "audit_completed") {
    return "审计";
  }
  if (event.event_type === "chapter_draft_generated") {
    return "章节生成";
  }
  if (event.event_type === "book_output_generated") {
    return "书稿整理";
  }
  if (event.event_type === "book_editorial_copy_generated") {
    return "成书文案";
  }
  return "自动处理";
}

function automationEventStatusText(event: MemoirAutomationEvent): string {
  if (event.status === "needs_review") {
    return "待确认";
  }
  if (event.status === "failed") {
    return "失败";
  }
  if (event.status === "pending") {
    return "处理中";
  }
  return "已完成";
}

function automationEventActionText(event: MemoirAutomationEvent): string {
  if (event.status === "pending") {
    return "处理中";
  }
  if (event.status === "needs_review" && event.event_type === "source_held") {
    if (followupAnswerNeedsConfirmation(event)) {
      return "去确认";
    }
    return "去整理";
  }
  if (event.status === "needs_review") {
    return "待处理";
  }
  return "收起";
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function followupAnswerNeedsConfirmation(event: MemoirAutomationEvent): boolean {
  if (event.event_type !== "source_held" || event.status !== "needs_review") {
    return false;
  }
  const metadata = objectValue(event.metadata_json);
  const route = Object.keys(objectValue(metadata.route)).length
    ? objectValue(metadata.route)
    : metadata;
  return stringValue(objectValue(route.followup_answer).status) === "needs_confirmation";
}

function objectList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object" && !Array.isArray(item)) as Record<string, unknown>[] : [];
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function numberValue(value: unknown): number {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? Math.floor(number) : 0;
}

function stringList(value: unknown, limit = 4): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const items: string[] = [];
  value.forEach((item) => {
    const text = stringValue(item);
    if (text && !items.includes(text) && items.length < limit) {
      items.push(text);
    }
  });
  return items;
}

function automationEventDisplay(event: MemoirAutomationEvent): Record<string, unknown> {
  const metadata = objectValue(event.metadata_json);
  const auditSnapshot = objectValue(metadata.audit_snapshot);
  const snapshotDisplay = objectValue(auditSnapshot.display);
  return Object.keys(snapshotDisplay).length ? snapshotDisplay : objectValue(metadata.display);
}

function automationEventPrimaryAction(event: MemoirAutomationEvent): Record<string, unknown> {
  const metadata = objectValue(event.metadata_json);
  const auditSnapshot = objectValue(metadata.audit_snapshot);
  const snapshotAction = objectValue(auditSnapshot.primary_action);
  return Object.keys(snapshotAction).length ? snapshotAction : objectValue(metadata.primary_action);
}

function automationEventSuggestedActionText(event: MemoirAutomationEvent): string {
  if (event.event_type !== "audit_completed") {
    return "";
  }
  return stringValue(automationEventPrimaryAction(event).cta_text);
}

function automationEventAuditSnapshot(event: MemoirAutomationEvent): Record<string, unknown> {
  return objectValue(objectValue(event.metadata_json).audit_snapshot);
}

function chapterTextFromAuditItem(item: Record<string, unknown>, fallback = ""): string {
  const title = stringValue(item.chapter_title) || fallback;
  return title ? `《${title}》` : "";
}

function actionSuggestionLabel(actionType: string): string {
  if (actionType === "open_chapter_workspace") {
    return "建议整理";
  }
  if (actionType === "continue_interview") {
    return "建议补问";
  }
  if (actionType === "review_sources") {
    return "待整理素材";
  }
  return "建议操作";
}

function pushAuditSuggestion(
  suggestions: BookAutomationEventSuggestionView[],
  suggestion: {
    labelText: string;
    bodyText: string;
    chapterText?: string;
  },
): void {
  const { labelText, bodyText, chapterText = "" } = suggestion;
  const body = trimText(bodyText);
  if (!body || suggestions.some((item) => item.labelText === labelText && item.bodyText === body)) {
    return;
  }
  suggestions.push({
    id: `suggestion-${suggestions.length + 1}`,
    labelText,
    bodyText: body.length > 120 ? `${body.slice(0, 120)}...` : body,
    chapterText,
  });
}

function auditSuggestionItems(event: MemoirAutomationEvent): BookAutomationEventSuggestionView[] {
  if (event.event_type !== "audit_completed") {
    return [];
  }
  const metadata = objectValue(event.metadata_json);
  const snapshot = automationEventAuditSnapshot(event);
  const suggestions: BookAutomationEventSuggestionView[] = [];
  const primaryAction = automationEventPrimaryAction(event);
  const primaryChapter = objectValue(metadata.primary_chapter);
  const primaryChapterTitle = stringValue(primaryChapter.title);
  const ctaText = stringValue(primaryAction.cta_text);
  if (ctaText) {
    pushAuditSuggestion(suggestions, {
      labelText: actionSuggestionLabel(stringValue(primaryAction.type)),
      bodyText: ctaText,
      chapterText: primaryChapterTitle ? `《${primaryChapterTitle}》` : "",
    });
  }

  const pendingCount = numberValue(metadata.pending_utterance_count);
  if (pendingCount) {
    pushAuditSuggestion(suggestions, {
      labelText: "待整理素材",
      bodyText: `还有 ${pendingCount} 条讲述需要归章或确认。`,
    });
  }

  const chapterCards = objectList(snapshot.chapter_cards);
  const chapterAudits = objectList(snapshot.chapter_audits);
  const pushQuestionSuggestions = (item: Record<string, unknown>) => {
    stringList(item.next_questions, 2).forEach((question) => {
      pushAuditSuggestion(suggestions, {
        labelText: "建议补问",
        bodyText: question,
        chapterText: chapterTextFromAuditItem(item, primaryChapterTitle),
      });
    });
  };
  const questionCount = suggestions.filter((item) => item.labelText === "建议补问").length;
  chapterCards.forEach(pushQuestionSuggestions);
  if (suggestions.filter((item) => item.labelText === "建议补问").length === questionCount) {
    chapterAudits.forEach(pushQuestionSuggestions);
  }
  [...chapterCards, ...chapterAudits].forEach((item) => {
    objectList(item.gaps).slice(0, 2).forEach((gap) => {
      const message = stringValue(gap.message);
      if (message) {
        pushAuditSuggestion(suggestions, {
          labelText: "建议补写",
          bodyText: message,
          chapterText: chapterTextFromAuditItem(item, primaryChapterTitle),
        });
      }
    });
  });

  return suggestions.slice(0, 5);
}

function quotedTitle(text: string): string {
  const match = text.match(/「([^」]+)」/);
  return match ? match[1].trim() : "";
}

function automationEventChapterTitle(event: MemoirAutomationEvent): string {
  const metadata = objectValue(event.metadata_json);
  const route = objectValue(metadata.route);
  return stringValue(route.target_chapter_title)
    || stringValue(route.suggested_title)
    || quotedTitle(event.summary_text);
}

function automationEventDetailViews(event: MemoirAutomationEvent): BookAutomationEventDetailView[] {
  const details = Array.isArray(event.utterance_details) ? event.utterance_details : [];
  return details.map((detail) => {
    const recordTitle = trimText(detail.record_title || "")
      || (detail.source_index ? `语音记录 ${detail.source_index}` : "讲述记录");
    const chapterTitle = trimText(detail.chapter_title || "") || automationEventChapterTitle(event);
    const createdAtText = detail.created_at ? formatBeijingDateTimeShort(detail.created_at) : "";
    const previewText = trimText(detail.preview_text || "");
    return {
      id: `${event.id}:${detail.utterance_id}`,
      titleText: recordTitle,
      chapterText: chapterTitle ? `归入「${chapterTitle}」` : "归入章节待确认",
      metaText: createdAtText,
      previewText: previewText.length > 90 ? `${previewText.slice(0, 90)}...` : previewText,
    };
  });
}

function sourceAssignmentGroupView(
  events: MemoirAutomationEvent[],
  busyTarget: string,
  expandedTargets: Record<string, boolean>,
): BookAutomationEventView | null {
  const sourceEvents = events.filter((event) => (
    event.event_type === "source_assigned" && event.status === "completed" && !event.metadata_json?.acknowledged_at
  ));
  if (!sourceEvents.length) {
    return null;
  }

  const eventIds = sourceEvents.map((event) => event.id);
  const groupId = `source_assigned:${eventIds.join("|")}`;
  const detailItems = sourceEvents.flatMap((event) => automationEventDetailViews(event));
  const utteranceCount = sourceEvents.reduce((count, event) => count + event.utterance_ids.length, 0)
    || detailItems.length
    || sourceEvents.length;
  const chapterTitles = Array.from(new Set(
    detailItems
      .map((item) => item.chapterText.match(/「([^」]+)」/)?.[1] || "")
      .concat(sourceEvents.map(automationEventChapterTitle))
      .filter(Boolean),
  ));
  const chapterText = chapterTitles.length
    ? `${chapterTitles.length} 个章节：${chapterTitles.slice(0, 2).map((title) => `「${title}」`).join("、")}${chapterTitles.length > 2 ? "等" : ""}`
    : "章节";
  const latestAt = sourceEvents
    .map((event) => event.updated_at || event.created_at)
    .sort((left, right) => createdAtTime(right) - createdAtTime(left))[0] || "";
  const expanded = Boolean(expandedTargets[groupId]);
  return {
    id: groupId,
    eventIds,
    eventIdsText: eventIds.join(","),
    eventType: "source_assigned",
    actionKind: "acknowledge_group",
    primaryUtteranceId: detailItems[0]?.id.split(":").pop() || "",
    titleText: "已自动归章",
    summaryText: `${utteranceCount} 条讲述已自动整理进 ${chapterText}。`,
    detailText: "",
    suggestedActionText: "",
    suggestionItems: [],
    hasSuggestions: false,
    detailItems,
    detailExpanded: expanded,
    detailToggleText: expanded ? "收起详情" : `查看 ${detailItems.length || utteranceCount} 条详情`,
    hasDetails: detailItems.length > 0,
    metaText: ["归章", latestAt ? formatBeijingDateTimeShort(latestAt) : "", `${utteranceCount} 条讲述`]
      .filter(Boolean)
      .join(" · "),
    statusText: "已完成",
    actionText: "收起",
    canAcknowledge: true,
    canAct: true,
    busy: busyTarget === groupId,
  };
}

function automationEventDetailText(event: MemoirAutomationEvent): string {
  const metadata = objectValue(event.metadata_json);
  const display = automationEventDisplay(event);
  if (event.event_type === "source_held") {
    const route = objectValue(metadata.route);
    const action = stringValue(route.action);
    const title = stringValue(route.target_chapter_title) || stringValue(route.suggested_title);
    const reason = stringValue(route.reason);
    const targetText = action === "suggest_new_chapter" && title
      ? `建议新建「${title}」`
      : (title ? `可能归入「${title}」` : "需要人工确认归属");
    return reason ? `${targetText}：${reason}` : targetText;
  }
  if (event.event_type === "audit_completed") {
    const detail = stringValue(display.detail);
    if (detail) {
      return detail;
    }
    const actions = Array.isArray(metadata.next_actions)
      ? metadata.next_actions.map((item) => stringValue(item)).filter(Boolean)
      : [];
    return actions.slice(0, 2).join("；");
  }
  if (event.event_type === "chapter_auto_revised") {
    const sourceIds = Array.isArray(metadata.pending_source_utterance_ids)
      ? metadata.pending_source_utterance_ids
      : [];
    return sourceIds.length ? `已把 ${sourceIds.length} 条新增讲述写入章节正文。` : "";
  }
  if (
    event.event_type === "chapter_draft_generated"
    || event.event_type === "book_output_generated"
    || event.event_type === "book_editorial_copy_generated"
  ) {
    return stringValue(metadata.error_message);
  }
  return "";
}

function automationEventPrimaryUtteranceId(event: MemoirAutomationEvent): string {
  const direct = event.utterance_ids.map((item) => trimText(item)).find(Boolean);
  if (direct) {
    return direct;
  }
  const metadata = objectValue(event.metadata_json);
  const route = objectValue(metadata.route);
  return stringValue(route.utterance_id);
}

function automationEventActionKind(event: MemoirAutomationEvent): BookAutomationEventView["actionKind"] {
  if (event.status === "pending") {
    return "none";
  }
  if (event.status === "needs_review") {
    if (followupAnswerNeedsConfirmation(event)) {
      return "confirm_answer";
    }
    return event.event_type === "source_held" && automationEventPrimaryUtteranceId(event)
      ? "review_source"
      : "none";
  }
  return "acknowledge";
}

function automationEventMetaText(event: MemoirAutomationEvent): string {
  const parts = [
    automationEventKindText(event),
    formatBeijingDateTimeShort(event.updated_at || event.created_at),
  ];
  if (event.utterance_ids.length) {
    parts.push(`${event.utterance_ids.length} 条讲述`);
  }
  if (typeof event.confidence === "number") {
    parts.push(`可信度 ${Math.round(event.confidence * 100)}%`);
  }
  return parts.filter(Boolean).join(" · ");
}

function automationEventSummaryText(event: MemoirAutomationEvent): string {
  if (event.event_type === "book_output_generated" && event.status === "completed") {
    return "书稿已经整理好，可以预览或继续修改。";
  }
  if (event.event_type === "audit_completed") {
    const summary = stringValue(automationEventDisplay(event).summary);
    if (summary) {
      return summary;
    }
  }
  return trimText(event.summary_text) || "小余已完成一次自动处理。";
}

export function buildAutomationEventViews(
  events: MemoirAutomationEvent[] = [],
  busyTarget = "",
  expandedTargets: Record<string, boolean> = {},
): BookAutomationEventView[] {
  const filteredEvents = events.filter((event) => !event.metadata_json?.acknowledged_at);
  const sourceGroup = sourceAssignmentGroupView(filteredEvents, busyTarget, expandedTargets);
  const regularViews = filteredEvents
    .filter((event) => !(event.event_type === "source_assigned" && event.status === "completed"))
    .filter((event) => !event.metadata_json?.acknowledged_at)
    .map((event) => {
      const actionKind = automationEventActionKind(event);
      const detailItems = automationEventDetailViews(event);
      const suggestionItems = auditSuggestionItems(event);
      const expanded = Boolean(expandedTargets[event.id]);
      return {
        id: event.id,
        eventIds: [event.id],
        eventIdsText: event.id,
        eventType: event.event_type,
        actionKind,
        primaryUtteranceId: automationEventPrimaryUtteranceId(event),
        titleText: automationEventTitle(event),
        summaryText: automationEventSummaryText(event),
        detailText: automationEventDetailText(event),
        suggestedActionText: automationEventSuggestedActionText(event),
        suggestionItems,
        hasSuggestions: suggestionItems.length > 0,
        detailItems,
        detailExpanded: expanded,
        detailToggleText: expanded ? "收起详情" : `查看 ${detailItems.length} 条详情`,
        hasDetails: detailItems.length > 0,
        metaText: automationEventMetaText(event),
        statusText: automationEventStatusText(event),
        actionText: automationEventActionText(event),
        canAcknowledge: actionKind === "acknowledge",
        canAct: actionKind !== "none",
        busy: event.id === busyTarget,
      };
    });
  return sourceGroup ? [sourceGroup, ...regularViews] : regularViews;
}

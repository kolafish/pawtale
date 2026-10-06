import type { MemoirAutomationEvent } from "../../domain/memoir-voice-session";
import { formatBeijingDateTimeShort } from "../../services/time";
import { trimText } from "./book-view-model-utils";

export interface BookAutomationEventView {
  id: string;
  eventIds: string[];
  eventIdsText: string;
  eventType: MemoirAutomationEvent["event_type"];
  actionKind: "acknowledge" | "none";
  primaryUtteranceId: string;
  titleText: string;
  summaryText: string;
  detailText: string;
  metaText: string;
  statusText: string;
  actionText: string;
  canAcknowledge: boolean;
  canAct: boolean;
  busy: boolean;
}

function automationEventTitle(event: MemoirAutomationEvent): string {
  if (event.event_type === "chapter_draft_generated") {
    if (event.status === "failed") {
      return "章节生成失败";
    }
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
  if (event.status === "needs_review") {
    return "待处理";
  }
  return "收起";
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function automationEventDetailText(event: MemoirAutomationEvent): string {
  return stringValue(event.metadata_json.error_message);
}

function automationEventPrimaryUtteranceId(event: MemoirAutomationEvent): string {
  const direct = event.utterance_ids.map((item) => trimText(item)).find(Boolean);
  if (direct) {
    return direct;
  }
  return "";
}

function automationEventActionKind(event: MemoirAutomationEvent): BookAutomationEventView["actionKind"] {
  return event.status === "completed" || event.status === "failed" ? "acknowledge" : "none";
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
  return trimText(event.summary_text) || "小余已完成一次自动处理。";
}

export function buildAutomationEventViews(
  events: MemoirAutomationEvent[] = [],
  busyTarget = "",
): BookAutomationEventView[] {
  const filteredEvents = events.filter((event) => !event.metadata_json?.acknowledged_at);
  return filteredEvents
    .map((event) => {
      const actionKind = automationEventActionKind(event);
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
        metaText: automationEventMetaText(event),
        statusText: automationEventStatusText(event),
        actionText: automationEventActionText(event),
        canAcknowledge: actionKind === "acknowledge",
        canAct: actionKind !== "none",
        busy: event.id === busyTarget,
      };
    });
}

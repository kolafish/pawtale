import type { MemoirBookEditorialCopyJobTarget, MemoirBookOutput } from "../../domain/memoir-book";
import {
  trimText,
} from "./book-view-model";

export interface BookDesignState {
  bookContent: string;
  bookViewingHistoricalVersion: boolean;
  bookTitleSaving: boolean;
  bookTitleChanged: boolean;
  bookMetadataChanged: boolean;
  bookSubtitleInput: string;
  bookNarratorNameInput: string;
  bookPreparedByInput: string;
  bookPrefaceInput: string;
  bookAfterwordInput: string;
  bookBackCoverQuoteInput: string;
}

export function latestBookTitle(output: MemoirBookOutput | null): string {
  return trimText(output?.latest_version?.manuscript?.profile?.title || output?.title || "");
}

function latestBookMetadata(output: MemoirBookOutput | null): Record<string, string> {
  const manuscript = output?.latest_version?.manuscript || null;
  const frontMatter = manuscript?.front_matter || [];
  const preface = frontMatter.find((section) => section.kind === "preface")?.content || "";
  return {
    subtitle: trimText(manuscript?.profile?.subtitle || ""),
    narrator_name: trimText(manuscript?.profile?.narrator_name || ""),
    prepared_by: trimText(manuscript?.profile?.prepared_by || "家人整理"),
    preface: trimText(preface),
    afterword: trimText(manuscript?.back_matter?.afterword?.content || ""),
    back_cover_quote: trimText(manuscript?.render_settings?.back_cover_quote || ""),
  };
}

export function bookMetadataChanged(
  data: BookDesignState,
  output: MemoirBookOutput | null,
): boolean {
  const stored = latestBookMetadata(output);
  return (
    trimText(data.bookSubtitleInput) !== stored.subtitle
    || trimText(data.bookNarratorNameInput) !== stored.narrator_name
    || trimText(data.bookPreparedByInput || "家人整理") !== stored.prepared_by
    || trimText(data.bookPrefaceInput) !== stored.preface
    || trimText(data.bookAfterwordInput) !== stored.afterword
    || trimText(data.bookBackCoverQuoteInput) !== stored.back_cover_quote
  );
}

export function currentBookMetadataPayload(data: BookDesignState, includeEmpty: boolean): Record<string, string> {
  const values: Record<string, string> = {
    subtitle: trimText(data.bookSubtitleInput),
    narrator_name: trimText(data.bookNarratorNameInput),
    prepared_by: trimText(data.bookPreparedByInput) || "家人整理",
    preface: trimText(data.bookPrefaceInput),
    afterword: trimText(data.bookAfterwordInput),
    back_cover_quote: trimText(data.bookBackCoverQuoteInput),
  };
  if (includeEmpty) {
    return values;
  }
  return Object.keys(values).reduce<Record<string, string>>((memo, key) => {
    if (values[key]) {
      memo[key] = values[key];
    }
    return memo;
  }, {});
}

export function bookDesignHasChanges(data: BookDesignState): boolean {
  return data.bookTitleChanged || data.bookMetadataChanged;
}

export function bookEditorialCopyBlockedMessage(
  data: BookDesignState,
  bookOutputAvailable: boolean,
): string {
  if (!bookOutputAvailable || !data.bookContent) {
    return "请先生成书稿";
  }
  if (data.bookViewingHistoricalVersion) {
    return "请切回最新版后再编辑成书文案";
  }
  return "";
}

export function isBookLevelEditorialCopyTarget(target: string): target is MemoirBookEditorialCopyJobTarget {
  return target === "preface" || target === "afterword" || target === "back_cover_quote";
}

export function bookDesignSaveStatusText(
  data: BookDesignState,
  bookOutputAvailable: boolean,
): string {
  if (!bookOutputAvailable) {
    return "生成时应用";
  }
  if (data.bookViewingHistoricalVersion) {
    return "切回最新版后自动保存";
  }
  if (data.bookTitleSaving) {
    return "保存中";
  }
  return bookDesignHasChanges(data) ? "待自动保存" : "已保存";
}

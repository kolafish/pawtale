import type {
  MemoirBookCoverStyle,
  MemoirBookEditorialCopyTarget,
  MemoirBookOutput,
  MemoirBookOutputGenerateRequest,
} from "../../domain/voice-chat";
import {
  BOOK_COVER_STYLE_OPTIONS,
  trimText,
} from "./book-view-model";

export interface BookDesignState {
  bookOutput: MemoirBookOutput | null;
  bookContent: string;
  bookViewingHistoricalVersion: boolean;
  bookTitleSaving: boolean;
  bookTitleChanged: boolean;
  bookMetadataChanged: boolean;
  bookCoverStyleChanged: boolean;
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

export function latestBookCoverStyle(output: MemoirBookOutput | null): MemoirBookCoverStyle {
  const style = output?.latest_version?.manuscript?.render_settings?.cover_style;
  const option = BOOK_COVER_STYLE_OPTIONS.find((candidate) => candidate.value === style);
  return option ? option.value : "classic_plain";
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

export function bookGenerationSettings(
  output: MemoirBookOutput | null,
  defaults: { title: string; narratorName: string },
): Omit<MemoirBookOutputGenerateRequest, "ai_model" | "force_regenerate"> {
  const stored = latestBookMetadata(output);
  const title = latestBookTitle(output) || trimText(defaults.title);
  const narratorName = stored.narrator_name || trimText(defaults.narratorName);
  return {
    ...(title ? { title } : {}),
    ...(stored.subtitle ? { subtitle: stored.subtitle } : {}),
    ...(narratorName ? { narrator_name: narratorName } : {}),
    prepared_by: stored.prepared_by || "家人整理",
    ...(stored.preface ? { preface: stored.preface } : {}),
    ...(stored.afterword ? { afterword: stored.afterword } : {}),
    ...(stored.back_cover_quote ? { back_cover_quote: stored.back_cover_quote } : {}),
    cover_style: latestBookCoverStyle(output),
  };
}

export function bookMetadataChanged(data: BookDesignState): boolean {
  const stored = latestBookMetadata(data.bookOutput);
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
  return data.bookTitleChanged || data.bookMetadataChanged || data.bookCoverStyleChanged;
}

export function bookEditorialCopyBlockedMessage(data: BookDesignState): string {
  if (!data.bookOutput?.latest_version || !data.bookContent) {
    return "请先生成书稿";
  }
  if (data.bookViewingHistoricalVersion) {
    return "请切回最新版后再编辑成书文案";
  }
  return "";
}

export function isBookLevelEditorialCopyTarget(target: string): target is MemoirBookEditorialCopyTarget {
  return target === "preface" || target === "afterword" || target === "back_cover_quote";
}

export function isChapterEditorialCopyTarget(target: string): target is MemoirBookEditorialCopyTarget {
  return target === "chapter_intros" || target === "pull_quotes";
}

export function bookDesignSaveStatusText(data: BookDesignState): string {
  if (!data.bookOutput?.latest_version) {
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

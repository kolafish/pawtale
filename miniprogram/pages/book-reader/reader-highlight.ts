import type { BookOutputChapterPreviewView } from "../book/book-view-model";

export interface ReaderParagraphRange {
  chapterIndex: number;
  sectionId: string;
  paragraphIndex: number;
  text: string;
  start: number;
  end: number;
}

const SENTENCE_ENDINGS = "。！？!?";
const SENTENCE_CLOSERS = "”’\"'」』】）)";

export function textLength(value: string): number {
  return Array.from(value).length;
}

// Locate the displayed text inside the actual audio source, before compacting
// the view model. Never guess offsets from heading or newline conventions.
export function readerParagraphRanges(
  source: string,
  chapters: BookOutputChapterPreviewView[],
  flatPages: string[],
  onlyChapter?: number,
): ReaderParagraphRange[] {
  const ranges: ReaderParagraphRange[] = [];
  let previousOffset = 0, previousChars = 0;
  const add = (text: string, offset: number, chapterIndex: number, sectionId = "", paragraphIndex = 0) => {
    const start = previousChars + textLength(source.slice(previousOffset, offset));
    const end = start + textLength(text);
    ranges.push({ chapterIndex, sectionId, paragraphIndex, text, start, end });
    previousOffset = offset + text.length;
    previousChars = end;
  };
  let chapterCursor = 0;
  if (!chapters.length) {
    flatPages.forEach((text, index) => {
      add(text, chapterCursor, index);
      chapterCursor += text.length;
    });
    return ranges;
  }
  chapters.forEach((chapter, chapterIndex) => {
    if (onlyChapter !== undefined && chapterIndex !== onlyChapter) return;
    const chapterStart = source.indexOf(chapter.content, chapterCursor);
    if (!chapter.content || chapterStart < 0) return;
    chapterCursor = chapterStart + chapter.content.length;
    if (!chapter.sections.length) {
      add(chapter.content, chapterStart, chapterIndex);
      return;
    }
    let sectionCursor = 0;
    chapter.sections.forEach(section => {
      const sectionStart = chapter.content.indexOf(section.content, sectionCursor);
      if (!section.content || sectionStart < 0) return;
      sectionCursor = sectionStart + section.content.length;
      let paragraphCursor = 0;
      section.paragraphs.forEach((paragraph, paragraphIndex) => {
        const paragraphStart = section.content.indexOf(paragraph, paragraphCursor);
        if (paragraphStart < 0) return;
        paragraphCursor = paragraphStart + paragraph.length;
        add(paragraph, chapterStart + sectionStart + paragraphStart, chapterIndex, section.id, paragraphIndex);
      });
    });
  });
  return ranges;
}

export function sentenceHighlight(
  range: ReaderParagraphRange,
  currentTextStart: number,
): { before: string; current: string; after: string } {
  const characters = Array.from(range.text);
  const currentIndex = currentTextStart - range.start;
  let sentenceStart = 0;
  for (let index = 0; index < characters.length; index += 1) {
    if (!SENTENCE_ENDINGS.includes(characters[index])) {
      continue;
    }
    let sentenceEnd = index + 1;
    while (
      sentenceEnd < characters.length
      && SENTENCE_CLOSERS.includes(characters[sentenceEnd])
    ) {
      sentenceEnd += 1;
    }
    if (currentIndex < sentenceEnd) {
      return {
        before: characters.slice(0, sentenceStart).join(""),
        current: characters.slice(sentenceStart, sentenceEnd).join(""),
        after: characters.slice(sentenceEnd).join(""),
      };
    }
    sentenceStart = sentenceEnd;
    index = sentenceEnd - 1;
  }
  return {
    before: characters.slice(0, sentenceStart).join(""),
    current: characters.slice(sentenceStart).join(""),
    after: "",
  };
}

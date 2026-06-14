export type MarkdownFormat =
  | 'heading-1'
  | 'heading-2'
  | 'bold'
  | 'italic'
  | 'quote'
  | 'unordered-list'
  | 'ordered-list'
  | 'divider';

export interface MarkdownSelection {
  start: number;
  end: number;
}

export interface MarkdownEditResult extends MarkdownSelection {
  text: string;
}

const INLINE_FORMATS: Partial<Record<MarkdownFormat, { marker: string; placeholder: string }>> = {
  bold: { marker: '**', placeholder: '加粗文字' },
  italic: { marker: '*', placeholder: '斜体文字' },
};

function lineBounds(text: string, start: number, end: number): MarkdownSelection {
  const lineStart = text.lastIndexOf('\n', Math.max(0, start - 1)) + 1;
  const nextBreak = text.indexOf('\n', end);
  return {
    start: lineStart,
    end: nextBreak === -1 ? text.length : nextBreak,
  };
}

function transformSelectedLines(
  text: string,
  selection: MarkdownSelection,
  transform: (line: string, index: number) => string,
): MarkdownEditResult {
  const bounds = lineBounds(text, selection.start, selection.end);
  const selected = text.slice(bounds.start, bounds.end);
  const lines = selected.length > 0 ? selected.split('\n') : [''];
  const replacement = lines.map(transform).join('\n');
  return {
    text: text.slice(0, bounds.start) + replacement + text.slice(bounds.end),
    start: bounds.start,
    end: bounds.start + replacement.length,
  };
}

function stripBlockPrefix(line: string): string {
  return line
    .replace(/^#{1,6}\s+/, '')
    .replace(/^>\s?/, '')
    .replace(/^[-*]\s+/, '')
    .replace(/^\d+\.\s+/, '');
}

function applyInlineFormat(
  text: string,
  selection: MarkdownSelection,
  marker: string,
  placeholder: string,
): MarkdownEditResult {
  const selected = text.slice(selection.start, selection.end);
  const content = selected || placeholder;
  const replacement = `${marker}${content}${marker}`;
  const nextStart = selection.start + marker.length;
  const nextEnd = nextStart + content.length;
  return {
    text: text.slice(0, selection.start) + replacement + text.slice(selection.end),
    start: nextStart,
    end: nextEnd,
  };
}

export function applyMarkdownFormat(
  text: string,
  selection: MarkdownSelection,
  format: MarkdownFormat,
): MarkdownEditResult {
  const inline = INLINE_FORMATS[format];
  if (inline) {
    return applyInlineFormat(text, selection, inline.marker, inline.placeholder);
  }

  if (format === 'divider') {
    const before = selection.start > 0 && text[selection.start - 1] !== '\n' ? '\n\n' : '';
    const after = selection.end < text.length && text[selection.end] !== '\n' ? '\n\n' : '\n';
    const replacement = `${before}---${after}`;
    const cursor = selection.start + replacement.length;
    return {
      text: text.slice(0, selection.start) + replacement + text.slice(selection.end),
      start: cursor,
      end: cursor,
    };
  }

  return transformSelectedLines(text, selection, (line, index) => {
    const body = stripBlockPrefix(line);
    if (format === 'heading-1') return `# ${body || '一级标题'}`;
    if (format === 'heading-2') return `## ${body || '二级标题'}`;
    if (format === 'quote') return `> ${body || '引用内容'}`;
    if (format === 'unordered-list') return `- ${body || '列表项'}`;
    if (format === 'ordered-list') return `${index + 1}. ${body || '列表项'}`;
    return line;
  });
}

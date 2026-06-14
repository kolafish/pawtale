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

export type MarkdownKeyboardCommand =
  | { type: 'format'; format: MarkdownFormat }
  | { type: 'indent'; direction: 'in' | 'out' }
  | { type: 'continue-line' };

export interface MarkdownKeyboardInput {
  key: string;
  code?: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
  isComposing?: boolean;
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

export function applyMarkdownIndent(
  text: string,
  selection: MarkdownSelection,
  direction: 'in' | 'out',
): MarkdownEditResult {
  return transformSelectedLines(text, selection, (line) => {
    if (!line.trim()) return line;
    if (direction === 'in') return `  ${line}`;
    if (line.startsWith('  ')) return line.slice(2);
    if (line.startsWith('\t')) return line.slice(1);
    return line;
  });
}

export function continueMarkdownLine(
  text: string,
  selection: MarkdownSelection,
): MarkdownEditResult | null {
  if (selection.start !== selection.end) return null;

  const cursor = selection.start;
  const start = text.lastIndexOf('\n', Math.max(0, cursor - 1)) + 1;
  const lineBeforeCursor = text.slice(start, cursor);
  const unordered = lineBeforeCursor.match(/^(\s*)([-*])\s+(.*)$/);
  const ordered = lineBeforeCursor.match(/^(\s*)(\d+)\.\s+(.*)$/);
  const quote = lineBeforeCursor.match(/^(\s*)>\s?(.*)$/);

  if (unordered) {
    const [, indent, marker, body] = unordered;
    if (!body.trim()) {
      return {
        text: text.slice(0, start) + indent + text.slice(cursor),
        start: start + indent.length,
        end: start + indent.length,
      };
    }
    const insert = `\n${indent}${marker} `;
    return {
      text: text.slice(0, cursor) + insert + text.slice(cursor),
      start: cursor + insert.length,
      end: cursor + insert.length,
    };
  }

  if (ordered) {
    const [, indent, number, body] = ordered;
    if (!body.trim()) {
      return {
        text: text.slice(0, start) + indent + text.slice(cursor),
        start: start + indent.length,
        end: start + indent.length,
      };
    }
    const nextNumber = Number(number) + 1;
    const insert = `\n${indent}${nextNumber}. `;
    return {
      text: text.slice(0, cursor) + insert + text.slice(cursor),
      start: cursor + insert.length,
      end: cursor + insert.length,
    };
  }

  if (quote) {
    const [, indent, body] = quote;
    if (!body.trim()) {
      return {
        text: text.slice(0, start) + indent + text.slice(cursor),
        start: start + indent.length,
        end: start + indent.length,
      };
    }
    const insert = `\n${indent}> `;
    return {
      text: text.slice(0, cursor) + insert + text.slice(cursor),
      start: cursor + insert.length,
      end: cursor + insert.length,
    };
  }

  return null;
}

export function getMarkdownKeyboardCommand(input: MarkdownKeyboardInput): MarkdownKeyboardCommand | null {
  if (input.isComposing) return null;

  if (input.key === 'Tab') {
    return { type: 'indent', direction: input.shiftKey ? 'out' : 'in' };
  }

  if (input.key === 'Enter') {
    return { type: 'continue-line' };
  }

  const hasMod = Boolean(input.metaKey || input.ctrlKey);
  if (!hasMod) return null;

  const key = input.key.toLowerCase();
  if (!input.altKey && !input.shiftKey && key === 'b') {
    return { type: 'format', format: 'bold' };
  }
  if (!input.altKey && !input.shiftKey && key === 'i') {
    return { type: 'format', format: 'italic' };
  }
  if (input.altKey && !input.shiftKey && (input.key === '1' || input.code === 'Digit1')) {
    return { type: 'format', format: 'heading-1' };
  }
  if (input.altKey && !input.shiftKey && (input.key === '2' || input.code === 'Digit2')) {
    return { type: 'format', format: 'heading-2' };
  }
  if (!input.altKey && input.shiftKey && (input.key === '7' || input.code === 'Digit7')) {
    return { type: 'format', format: 'ordered-list' };
  }
  if (!input.altKey && input.shiftKey && (input.key === '8' || input.code === 'Digit8')) {
    return { type: 'format', format: 'unordered-list' };
  }
  if (!input.altKey && input.shiftKey && (input.key === '.' || input.key === '>')) {
    return { type: 'format', format: 'quote' };
  }

  return null;
}

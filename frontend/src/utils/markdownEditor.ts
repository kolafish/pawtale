import type { ListItem, Root } from 'mdast';
import { fromMarkdown } from 'mdast-util-from-markdown';

export type MarkdownFormat =
  | 'heading-1'
  | 'heading-2'
  | 'heading-3'
  | 'heading-4'
  | 'heading-5'
  | 'heading-6'
  | 'bold'
  | 'italic'
  | 'strikethrough'
  | 'inline-code'
  | 'link'
  | 'image'
  | 'footnote'
  | 'hard-break'
  | 'quote'
  | 'unordered-list'
  | 'ordered-list'
  | 'task-list'
  | 'code-block'
  | 'table'
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
  strikethrough: { marker: '~~', placeholder: '删除文字' },
  'inline-code': { marker: '`', placeholder: '代码' },
};

const HEADINGS: Partial<Record<MarkdownFormat, readonly [string, string]>> = {
  'heading-1': ['#', '一级标题'],
  'heading-2': ['##', '二级标题'],
  'heading-3': ['###', '三级标题'],
  'heading-4': ['####', '四级标题'],
  'heading-5': ['#####', '五级标题'],
  'heading-6': ['######', '六级标题'],
};

const LINK_TEXT_PLACEHOLDER = '链接文字';
const LINK_URL_PLACEHOLDER = 'https://';
const FOOTNOTE_PLACEHOLDER = '脚注内容';
const CODE_PLACEHOLDER = '代码';
const TABLE_TEMPLATE = '| 列1 | 列2 |\n| --- | --- |\n| 内容 | 内容 |';
const LIST_PREFIX = /^([ \t]*)([-+*]|\d{1,9}[.)])([ \t]+|$)/;
const ORDERED_MARKER = /^(\d+)([.)])$/;

function lineBounds(text: string, start: number, end: number): MarkdownSelection {
  const lineStart = start === 0 ? 0 : text.lastIndexOf('\n', start - 1) + 1;
  const nextBreak = text.indexOf('\n', end > start ? end - 1 : end);
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
    .replace(/^[-+*][ \t]+\[[ xX]\][ \t]+/, '')
    .replace(/^(?:[-+*]|\d{1,9}[.)])[ \t]+/, '');
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

function applyLinkFormat(text: string, selection: MarkdownSelection, image: boolean): MarkdownEditResult {
  const selected = text.slice(selection.start, selection.end);
  const label = image
    ? selected.replace(/[\\[\]]/g, '\\$&') || '图片描述'
    : selected || LINK_TEXT_PLACEHOLDER;
  const prefix = image ? '![' : '[';
  const replacement = `${prefix}${label}](${LINK_URL_PLACEHOLDER})`;
  // 已选中文字时直接选中网址，否则先选中链接文字占位符。
  const nextStart = selected
    ? selection.start + prefix.length + label.length + 2
    : selection.start + prefix.length;
  const nextEnd = selected ? nextStart + LINK_URL_PLACEHOLDER.length : nextStart + label.length;
  return {
    text: text.slice(0, selection.start) + replacement + text.slice(selection.end),
    start: nextStart,
    end: nextEnd,
  };
}

function insertFootnote(text: string, offset: number): MarkdownEditResult {
  const labels = new Set(Array.from(text.matchAll(/\[\^(\d+)\]/g), (match) => match[1]));
  let number = 1;
  while (labels.has(String(number))) number += 1;

  const reference = `[^${number}]`;
  const body = text.slice(0, offset) + reference + text.slice(offset);
  const separator = body.endsWith('\n\n') ? '' : body.endsWith('\n') ? '\n' : '\n\n';
  const start = body.length + separator.length + reference.length + 2;
  return {
    text: `${body}${separator}${reference}: ${FOOTNOTE_PLACEHOLDER}`,
    start,
    end: start + FOOTNOTE_PLACEHOLDER.length,
  };
}

function insertHardBreak(text: string, offset: number): MarkdownEditResult {
  const insert = text[offset] === '\n' ? '  ' : '  \n';
  const cursor = offset + 3;
  return { text: text.slice(0, offset) + insert + text.slice(offset), start: cursor, end: cursor };
}

function wrapCodeBlock(text: string, selection: MarkdownSelection): MarkdownEditResult {
  const bounds = lineBounds(text, selection.start, selection.end);
  const content = text.slice(bounds.start, bounds.end) || CODE_PLACEHOLDER;
  const replacement = `\`\`\`\n${content}\n\`\`\``;
  return {
    text: text.slice(0, bounds.start) + replacement + text.slice(bounds.end),
    start: bounds.start + 4,
    end: bounds.start + 4 + content.length,
  };
}

function insertBlock(text: string, selection: MarkdownSelection, block: string): MarkdownEditResult {
  const before = selection.start > 0 && text[selection.start - 1] !== '\n' ? '\n\n' : '';
  const after = selection.end < text.length && text[selection.end] !== '\n' ? '\n\n' : '\n';
  const replacement = `${before}${block}${after}`;
  const cursor = selection.start + replacement.length;
  return {
    text: text.slice(0, selection.start) + replacement + text.slice(selection.end),
    start: cursor,
    end: cursor,
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

  if (format === 'link' || format === 'image') return applyLinkFormat(text, selection, format === 'image');
  if (format === 'footnote') return insertFootnote(text, selection.end);
  if (format === 'hard-break') return insertHardBreak(text, selection.end);
  if (format === 'code-block') return wrapCodeBlock(text, selection);
  if (format === 'divider') return insertBlock(text, selection, '---');
  if (format === 'table') return insertBlock(text, selection, TABLE_TEMPLATE);

  const heading = HEADINGS[format];
  return transformSelectedLines(text, selection, (line, index) => {
    const body = stripBlockPrefix(line);
    if (heading) return `${heading[0]} ${body || heading[1]}`;
    if (format === 'quote') return `> ${body || '引用内容'}`;
    if (format === 'unordered-list') return `- ${body || '列表项'}`;
    if (format === 'ordered-list') return `${index + 1}. ${body || '列表项'}`;
    if (format === 'task-list') return `- [ ] ${body || '待办事项'}`;
    return line;
  });
}

function findListItem(container: Root | ListItem, offset: number): {
  item: ListItem;
  previous: ListItem | undefined;
  parent: ListItem | undefined;
} | undefined {
  let previous: ListItem | undefined;
  for (const node of container.children) {
    if (node.type !== 'list') {
      previous = undefined;
      continue;
    }
    for (const item of node.children) {
      const start = item.position?.start.offset;
      const end = item.position?.end.offset;
      if (start === undefined || end === undefined) continue;
      if (start > offset) return;
      if (start === offset) return { item, previous, parent: container.type === 'listItem' ? container : undefined };
      if (end >= offset) return findListItem(item, offset);
      previous = item;
    }
  }
}

function indentColumns(whitespace: string, start = 0): number {
  let column = start;
  for (const character of whitespace) {
    column += character === '\t' ? 4 - column % 4 : 1;
  }
  return column;
}

function listPrefixAtLine(text: string, offset: number): RegExpMatchArray | null {
  const line = lineBounds(text, offset, offset);
  return text.slice(line.start, line.end).match(LIST_PREFIX);
}

export function applyMarkdownIndent(
  text: string,
  selection: MarkdownSelection,
  direction: 'in' | 'out',
): MarkdownEditResult {
  const bounds = lineBounds(text, selection.start, selection.end);
  const prefix = listPrefixAtLine(text, bounds.start);
  let shift: number | undefined;
  let separateOrderedList = false;

  if (prefix) {
    const root = fromMarkdown(text);
    const first = findListItem(root, bounds.start + prefix[1].length);
    const target = direction === 'in' ? first?.previous : first?.parent;
    const targetStart = target?.position?.start.offset;
    const targetPrefix = targetStart === undefined ? null : listPrefixAtLine(text, targetStart);

    if (first && !targetPrefix && direction === 'in') return { text, ...selection };
    if (targetPrefix) {
      const [, indentation, marker, spacing] = targetPrefix;
      const targetIndent = indentColumns(indentation);
      const markerEnd = targetIndent + marker.length;
      const padding = indentColumns(spacing, markerEnd) - markerEnd;
      const contentIndent = markerEnd + (padding >= 1 && padding <= 4 ? padding : 1);
      const nextIndent = direction === 'in' ? contentIndent : targetIndent;
      shift = nextIndent - indentColumns(prefix[1]);
      // Only an ordered list starting at 1 can interrupt a paragraph without a blank line.
      const ordered = prefix[2].match(ORDERED_MARKER);
      const previousLine = text.slice(text.lastIndexOf('\n', bounds.start - 2) + 1, bounds.start - 1);
      separateOrderedList = direction === 'in' && ordered !== null && Number(ordered[1]) !== 1 && Boolean(previousLine.trim());
    }

    if (first) {
      const lastLine = lineBounds(text, bounds.end, bounds.end);
      const lastPrefix = listPrefixAtLine(text, bounds.end);
      const last = lastPrefix && findListItem(root, lastLine.start + lastPrefix[1].length);
      bounds.end = Math.max(bounds.end, last?.item.position?.end.offset ?? 0, first.item.position?.end.offset ?? 0);
    }
  }

  let start = selection.start;
  let end = selection.end;
  let lineStart = bounds.start;
  const replacement = text.slice(bounds.start, bounds.end).split('\n').map((line, index) => {
    const offset = lineStart;
    lineStart += line.length + 1;
    if (!line.trim()) return line;

    const indentation = line.match(/^[ \t]*/)?.[0] ?? '';
    let nextIndentation = indentation;
    if (shift !== undefined) {
      const currentIndent = indentColumns(indentation);
      const nextIndent = Math.max(0, currentIndent + shift);
      if (currentIndent !== nextIndent) nextIndentation = ' '.repeat(nextIndent);
    } else if (direction === 'in') {
      nextIndentation = `  ${indentation}`;
    } else {
      nextIndentation = indentation.replace(/^(?: {2}|\t)/, '');
    }
    if (nextIndentation === indentation) return line;

    const replacementIndent = `${index === 0 && separateOrderedList ? '\n' : ''}${nextIndentation}`;
    const deltaAt = (position: number) => {
      if (position < offset || (position === offset && selection.start !== selection.end)) return 0;
      return replacementIndent.length - Math.min(indentation.length, position - offset);
    };
    start += deltaAt(selection.start);
    end += deltaAt(selection.end);
    return replacementIndent + line.slice(indentation.length);
  });
  return { text: text.slice(0, bounds.start) + replacement.join('\n') + text.slice(bounds.end), start, end };
}

export function continueMarkdownLine(
  text: string,
  selection: MarkdownSelection,
): MarkdownEditResult | null {
  if (selection.start !== selection.end) return null;

  const cursor = selection.start;
  const { start } = lineBounds(text, cursor, cursor);
  const lineBeforeCursor = text.slice(start, cursor);
  const list = lineBeforeCursor.match(LIST_PREFIX);
  const quote = lineBeforeCursor.match(/^([ \t]*)(>(?:[ \t]*>)*)[ \t]?/);
  const prefix = list?.[3] ? list : quote;
  if (!prefix) return null;

  const [markup, indent, marker] = prefix;
  let body = lineBeforeCursor.slice(markup.length);
  const ordered = marker.match(ORDERED_MARKER);
  const task = list?.[3] && !ordered ? body.match(/^\[[ xX]\][ \t]+/) : null;
  if (task) body = body.slice(task[0].length);

  if (!body.trim()) {
    const next = start + indent.length;
    return { text: text.slice(0, start) + indent + text.slice(cursor), start: next, end: next };
  }

  const nextMarker = ordered ? `${Number(ordered[1]) + 1}${ordered[2]}` : marker;
  const insert = `\n${indent}${nextMarker} ${task ? '[ ] ' : ''}`;
  const next = cursor + insert.length;
  return { text: text.slice(0, cursor) + insert + text.slice(cursor), start: next, end: next };
}

export function getMarkdownKeyboardCommand(input: MarkdownKeyboardInput): MarkdownKeyboardCommand | null {
  if (input.isComposing) return null;

  if (input.key === 'Tab') {
    return { type: 'indent', direction: input.shiftKey ? 'out' : 'in' };
  }

  if (input.key === 'Enter') {
    if (input.shiftKey && !input.metaKey && !input.ctrlKey && !input.altKey) {
      return { type: 'format', format: 'hard-break' };
    }
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
  if (!input.altKey && input.shiftKey && key === 'x') {
    return { type: 'format', format: 'strikethrough' };
  }
  if (!input.altKey && !input.shiftKey && key === 'e') {
    return { type: 'format', format: 'inline-code' };
  }
  if (!input.altKey && !input.shiftKey && key === 'k') {
    return { type: 'format', format: 'link' };
  }
  if (input.altKey && !input.shiftKey && (input.key === '1' || input.code === 'Digit1')) {
    return { type: 'format', format: 'heading-1' };
  }
  if (input.altKey && !input.shiftKey && (input.key === '2' || input.code === 'Digit2')) {
    return { type: 'format', format: 'heading-2' };
  }
  if (input.altKey && !input.shiftKey && (input.key === '3' || input.code === 'Digit3')) {
    return { type: 'format', format: 'heading-3' };
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

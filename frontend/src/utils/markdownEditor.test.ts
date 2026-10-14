import { describe, expect, it } from 'vitest';
import {
  applyMarkdownFormat,
  applyMarkdownIndent,
  continueMarkdownLine,
  getMarkdownKeyboardCommand,
} from './markdownEditor';

describe('applyMarkdownFormat', () => {
  it('wraps selected text with inline markdown markers', () => {
    const result = applyMarkdownFormat('一段文字', { start: 1, end: 3 }, 'bold');

    expect(result.text).toBe('一**段文**字');
    expect(result.start).toBe(3);
    expect(result.end).toBe(5);
  });

  it('inserts and selects placeholder text when inline selection is empty', () => {
    const result = applyMarkdownFormat('开头', { start: 1, end: 1 }, 'italic');

    expect(result.text).toBe('开*斜体文字*头');
    expect(result.start).toBe(2);
    expect(result.end).toBe(6);
  });

  it('wraps selected text with strikethrough markers', () => {
    const result = applyMarkdownFormat('删掉这句', { start: 0, end: 4 }, 'strikethrough');

    expect(result.text).toBe('~~删掉这句~~');
    expect(result.start).toBe(2);
    expect(result.end).toBe(6);
  });

  it('turns selected text into a link and selects the url', () => {
    const result = applyMarkdownFormat('官网', { start: 0, end: 2 }, 'link');

    expect(result.text).toBe('[官网](https://)');
    expect(result.text.slice(result.start, result.end)).toBe('https://');
  });

  it('wraps selected lines in a fenced code block', () => {
    const result = applyMarkdownFormat('前文\nconst a = 1;', { start: 3, end: 3 }, 'code-block');

    expect(result.text).toBe('前文\n```\nconst a = 1;\n```');
    expect(result.text.slice(result.start, result.end)).toBe('const a = 1;');
  });

  it('formats lines as task list items without duplicating list markers', () => {
    const result = applyMarkdownFormat('- 买菜\n洗碗', { start: 0, end: 7 }, 'task-list');

    expect(result.text).toBe('- [ ] 买菜\n- [ ] 洗碗');
  });

  it('inserts a table template on its own lines', () => {
    const result = applyMarkdownFormat('前文', { start: 2, end: 2 }, 'table');

    expect(result.text).toBe('前文\n\n| 列1 | 列2 |\n| --- | --- |\n| 内容 | 内容 |\n');
  });

  it('formats all selected lines as an ordered list', () => {
    const result = applyMarkdownFormat('第一行\n第二行\n第三行', { start: 0, end: 7 }, 'ordered-list');

    expect(result.text).toBe('1. 第一行\n2. 第二行\n第三行');
    expect(result.start).toBe(0);
    expect(result.end).toBe(13);
  });

  it('replaces an existing block prefix when applying a heading', () => {
    const result = applyMarkdownFormat('- 旧标题', { start: 0, end: 5 }, 'heading-2');

    expect(result.text).toBe('## 旧标题');
  });

  it('inserts a divider on its own line', () => {
    const result = applyMarkdownFormat('前文后文', { start: 2, end: 2 }, 'divider');

    expect(result.text).toBe('前文\n\n---\n\n后文');
  });
});

describe('applyMarkdownIndent', () => {
  it('indents selected non-empty lines', () => {
    const result = applyMarkdownIndent('- 第一项\n- 第二项', { start: 0, end: 11 }, 'in');

    expect(result.text).toBe('  - 第一项\n  - 第二项');
  });

  it('outdents selected lines by one markdown indent level', () => {
    const result = applyMarkdownIndent('  - 第一项\n\t- 第二项', { start: 0, end: 17 }, 'out');

    expect(result.text).toBe('- 第一项\n- 第二项');
  });
});

describe('continueMarkdownLine', () => {
  it('continues unordered list items', () => {
    const result = continueMarkdownLine('- 第一项', { start: 5, end: 5 });

    expect(result?.text).toBe('- 第一项\n- ');
    expect(result?.start).toBe(8);
  });

  it('increments ordered list items', () => {
    const result = continueMarkdownLine('2. 第二项', { start: 6, end: 6 });

    expect(result?.text).toBe('2. 第二项\n3. ');
  });

  it('exits an empty list item', () => {
    const result = continueMarkdownLine('- ', { start: 2, end: 2 });

    expect(result?.text).toBe('');
    expect(result?.start).toBe(0);
  });

  it('continues task list items unchecked and exits an empty one', () => {
    expect(continueMarkdownLine('- [x] 完成', { start: 8, end: 8 })?.text).toBe('- [x] 完成\n- [ ] ');
    expect(continueMarkdownLine('- [ ] ', { start: 6, end: 6 })?.text).toBe('');
  });

  it('continues blockquotes', () => {
    const result = continueMarkdownLine('> 引用', { start: 4, end: 4 });

    expect(result?.text).toBe('> 引用\n> ');
  });
});

describe('getMarkdownKeyboardCommand', () => {
  it('maps modifier shortcuts to markdown formats', () => {
    expect(getMarkdownKeyboardCommand({ key: 'b', metaKey: true })).toEqual({ type: 'format', format: 'bold' });
    expect(getMarkdownKeyboardCommand({ key: 'i', ctrlKey: true })).toEqual({ type: 'format', format: 'italic' });
    expect(getMarkdownKeyboardCommand({ key: '1', metaKey: true, altKey: true })).toEqual({ type: 'format', format: 'heading-1' });
    expect(getMarkdownKeyboardCommand({ key: '*', code: 'Digit8', metaKey: true, shiftKey: true })).toEqual({ type: 'format', format: 'unordered-list' });
    expect(getMarkdownKeyboardCommand({ key: 'X', metaKey: true, shiftKey: true })).toEqual({ type: 'format', format: 'strikethrough' });
    expect(getMarkdownKeyboardCommand({ key: 'k', ctrlKey: true })).toEqual({ type: 'format', format: 'link' });
  });

  it('maps structural editing keys and ignores composition', () => {
    expect(getMarkdownKeyboardCommand({ key: 'Tab' })).toEqual({ type: 'indent', direction: 'in' });
    expect(getMarkdownKeyboardCommand({ key: 'Tab', shiftKey: true })).toEqual({ type: 'indent', direction: 'out' });
    expect(getMarkdownKeyboardCommand({ key: 'Enter' })).toEqual({ type: 'continue-line' });
    expect(getMarkdownKeyboardCommand({ key: 'b', metaKey: true, isComposing: true })).toBeNull();
  });
});

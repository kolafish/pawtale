import { describe, expect, it } from 'vitest';
import { applyMarkdownFormat } from './markdownEditor';

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

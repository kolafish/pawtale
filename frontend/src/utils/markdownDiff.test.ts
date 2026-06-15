import { describe, expect, it } from 'vitest';
import { computeMarkdownDiff, parseMarkdownBlocks } from './markdownDiff';
import type { DiffChunk } from '../types';

function applyAccepted(chunks: DiffChunk[]): string {
  return chunks
    .map((chunk) => {
      if (chunk.type === 'equal') return chunk.text;
      if (chunk.type === 'insert') return chunk.text;
      return '';
    })
    .join('');
}

describe('parseMarkdownBlocks', () => {
  it('keeps markdown structures as separate blocks', () => {
    const blocks = parseMarkdownBlocks('# 标题\n\n- 第一项\n- 第二项\n\n> 引用\n');

    expect(blocks.map((block) => block.type)).toEqual([
      'heading',
      'blank',
      'list',
      'blank',
      'quote',
    ]);
  });
});

describe('computeMarkdownDiff', () => {
  it('keeps unchanged markdown block boundaries equal and diffs inside changed paragraphs', () => {
    const original = '# 第一章\n\n雨下着。\n\n## 场景\n旧句。';
    const revised = '# 第一章\n\n雨还下着。\n\n## 场景\n新句。';
    const chunks = computeMarkdownDiff(original, revised);

    expect(chunks).toContainEqual(expect.objectContaining({ type: 'equal', text: '# 第一章\n\n雨' }));
    expect(chunks).toContainEqual(expect.objectContaining({ type: 'insert', text: '还' }));
    expect(chunks).toContainEqual(expect.objectContaining({ type: 'equal', text: '下着。\n\n## 场景\n' }));
    expect(applyAccepted(chunks)).toBe(revised);
  });

  it('treats inserted markdown blocks as insertions between matched blocks', () => {
    const original = '# 第一章\n\n开场。';
    const revised = '# 第一章\n\n## 新场景\n\n开场。';
    const chunks = computeMarkdownDiff(original, revised);

    expect(chunks.find((chunk) => chunk.type === 'insert')?.text).toContain('## 新场景');
    expect(applyAccepted(chunks)).toBe(revised);
  });

  it('diffs list edits inside the list block instead of replacing the whole document', () => {
    const original = '# 清单\n\n- 旧地点\n- 人物';
    const revised = '# 清单\n\n- 新地点\n- 人物';
    const chunks = computeMarkdownDiff(original, revised);

    expect(chunks.filter((chunk) => chunk.type === 'delete').map((chunk) => chunk.text).join('')).toBe('旧');
    expect(chunks.filter((chunk) => chunk.type === 'insert').map((chunk) => chunk.text).join('')).toBe('新');
    expect(applyAccepted(chunks)).toBe(revised);
  });

  it('falls back to delete and insert chunks for unrelated markdown', () => {
    const chunks = computeMarkdownDiff('# 标题\n\n正文', '- 完全\n- 不同');

    expect(chunks.some((chunk) => chunk.type === 'delete')).toBe(true);
    expect(chunks.some((chunk) => chunk.type === 'insert')).toBe(true);
    expect(applyAccepted(chunks)).toBe('- 完全\n- 不同');
  });
});

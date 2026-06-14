import DiffMatchPatch from 'diff-match-patch';
import type { DiffChunk } from '../types';

type MarkdownBlockType =
  | 'blank'
  | 'code'
  | 'heading'
  | 'quote'
  | 'list'
  | 'divider'
  | 'paragraph';

interface MarkdownBlock {
  type: MarkdownBlockType;
  text: string;
  normalized: string;
}

const dmp = new DiffMatchPatch();

function splitLines(text: string): string[] {
  if (!text) return [];
  const lines = text.match(/.*(?:\n|$)/g) ?? [];
  if (lines[lines.length - 1] === '') lines.pop();
  return lines;
}

function lineBody(line: string): string {
  return line.replace(/\n$/, '');
}

function classifyLine(line: string): Exclude<MarkdownBlockType, 'code'> {
  const body = lineBody(line);
  if (!body.trim()) return 'blank';
  if (/^#{1,6}\s+/.test(body)) return 'heading';
  if (/^\s*>\s?/.test(body)) return 'quote';
  if (/^\s*(?:[-*+]\s+|\d+\.\s+)/.test(body)) return 'list';
  if (/^\s{0,3}(?:[-*_]\s*){3,}$/.test(body)) return 'divider';
  return 'paragraph';
}

function normalizeBlockText(type: MarkdownBlockType, text: string): string {
  return text
    .split('\n')
    .map((line) => {
      if (type === 'heading') return line.replace(/^#{1,6}\s+/, '');
      if (type === 'quote') return line.replace(/^\s*>\s?/, '');
      if (type === 'list') return line.replace(/^\s*(?:[-*+]\s+|\d+\.\s+)/, '');
      return line;
    })
    .join('\n')
    .replace(/\s+/g, ' ')
    .trim();
}

function tokenizeSimilarityText(text: string): string[] {
  return text.match(/[\u4e00-\u9fff]|[a-zA-Z0-9]+/g) ?? [];
}

function quickTokenSimilarity(a: string, b: string): number {
  const aTokens = tokenizeSimilarityText(a);
  const bTokens = tokenizeSimilarityText(b);
  if (aTokens.length === 0 && bTokens.length === 0) return 1;
  if (aTokens.length === 0 || bTokens.length === 0) return 0;

  const bCounts = new Map<string, number>();
  for (const token of bTokens) {
    bCounts.set(token, (bCounts.get(token) ?? 0) + 1);
  }

  let overlap = 0;
  for (const token of aTokens) {
    const count = bCounts.get(token) ?? 0;
    if (count > 0) {
      overlap += 1;
      bCounts.set(token, count - 1);
    }
  }

  return (2 * overlap) / (aTokens.length + bTokens.length);
}

export function parseMarkdownBlocks(text: string): MarkdownBlock[] {
  const lines = splitLines(text);
  const blocks: MarkdownBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const body = lineBody(line);

    if (/^\s*```/.test(body)) {
      const start = index;
      index += 1;
      while (index < lines.length && !/^\s*```/.test(lineBody(lines[index]))) {
        index += 1;
      }
      if (index < lines.length) index += 1;
      const textBlock = lines.slice(start, index).join('');
      blocks.push({
        type: 'code',
        text: textBlock,
        normalized: normalizeBlockText('code', textBlock),
      });
      continue;
    }

    const type = classifyLine(line);
    const start = index;
    index += 1;

    if (type === 'paragraph' || type === 'quote' || type === 'list' || type === 'blank') {
      while (index < lines.length && classifyLine(lines[index]) === type) {
        index += 1;
      }
    }

    const textBlock = lines.slice(start, index).join('');
    blocks.push({
      type,
      text: textBlock,
      normalized: normalizeBlockText(type, textBlock),
    });
  }

  return blocks;
}

function blockSimilarity(a: MarkdownBlock, b: MarkdownBlock): number {
  if (a.text === b.text) return 1;
  if (a.type !== b.type) return 0;
  if (!a.normalized && !b.normalized) return 1;
  if (!a.normalized || !b.normalized) return 0;
  if (quickTokenSimilarity(a.normalized, b.normalized) < 0.2) return 0;

  const diffs = dmp.diff_main(a.normalized, b.normalized);
  const distance = dmp.diff_levenshtein(diffs);
  return 1 - distance / Math.max(a.normalized.length, b.normalized.length);
}

function matchBlocks(original: MarkdownBlock[], revised: MarkdownBlock[]): [number, number][] {
  const rows = original.length;
  const cols = revised.length;
  const scores = Array.from({ length: rows + 1 }, () => Array<number>(cols + 1).fill(0));
  const moves = Array.from({ length: rows + 1 }, () => Array<'up' | 'left' | 'diag' | null>(cols + 1).fill(null));

  for (let row = 1; row <= rows; row += 1) {
    for (let col = 1; col <= cols; col += 1) {
      const similarity = blockSimilarity(original[row - 1], revised[col - 1]);
      const matchScore = similarity >= 0.45 ? scores[row - 1][col - 1] + similarity : Number.NEGATIVE_INFINITY;
      const deleteScore = scores[row - 1][col];
      const insertScore = scores[row][col - 1];

      if (matchScore >= deleteScore && matchScore >= insertScore) {
        scores[row][col] = matchScore;
        moves[row][col] = 'diag';
      } else if (deleteScore >= insertScore) {
        scores[row][col] = deleteScore;
        moves[row][col] = 'up';
      } else {
        scores[row][col] = insertScore;
        moves[row][col] = 'left';
      }
    }
  }

  const pairs: [number, number][] = [];
  let row = rows;
  let col = cols;
  while (row > 0 || col > 0) {
    if (row === 0) {
      col -= 1;
      continue;
    }
    if (col === 0) {
      row -= 1;
      continue;
    }
    const move = moves[row][col];
    if (move === 'diag') {
      pairs.push([row - 1, col - 1]);
      row -= 1;
      col -= 1;
    } else if (move === 'up') {
      row -= 1;
    } else {
      col -= 1;
    }
  }

  return pairs.reverse();
}

function pushChunk(chunks: Omit<DiffChunk, 'id'>[], type: DiffChunk['type'], text: string): void {
  if (!text) return;
  const last = chunks[chunks.length - 1];
  if (last?.type === type && last.accepted === null) {
    last.text += text;
    return;
  }
  chunks.push({ type, text, accepted: null });
}

function pushTextDiff(chunks: Omit<DiffChunk, 'id'>[], original: string, revised: string): void {
  if (original === revised) {
    pushChunk(chunks, 'equal', original);
    return;
  }
  const diffs = dmp.diff_main(original, revised);
  dmp.diff_cleanupSemantic(diffs);
  for (const [op, text] of diffs) {
    pushChunk(chunks, op === 0 ? 'equal' : op === 1 ? 'insert' : 'delete', text);
  }
}

export function computeMarkdownDiff(originalText: string, revisedText: string): DiffChunk[] {
  const original = parseMarkdownBlocks(originalText);
  const revised = parseMarkdownBlocks(revisedText);
  const pairs = matchBlocks(original, revised);
  const chunks: Omit<DiffChunk, 'id'>[] = [];
  let originalIndex = 0;
  let revisedIndex = 0;

  for (const [nextOriginal, nextRevised] of pairs) {
    for (; originalIndex < nextOriginal; originalIndex += 1) {
      pushChunk(chunks, 'delete', original[originalIndex].text);
    }
    for (; revisedIndex < nextRevised; revisedIndex += 1) {
      pushChunk(chunks, 'insert', revised[revisedIndex].text);
    }
    pushTextDiff(chunks, original[nextOriginal].text, revised[nextRevised].text);
    originalIndex = nextOriginal + 1;
    revisedIndex = nextRevised + 1;
  }

  for (; originalIndex < original.length; originalIndex += 1) {
    pushChunk(chunks, 'delete', original[originalIndex].text);
  }
  for (; revisedIndex < revised.length; revisedIndex += 1) {
    pushChunk(chunks, 'insert', revised[revisedIndex].text);
  }

  return chunks.map((chunk, index) => ({
    ...chunk,
    id: `chunk-${index}`,
  }));
}

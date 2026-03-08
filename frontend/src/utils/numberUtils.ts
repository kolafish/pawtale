/**
 * Convert number to Chinese ordinal (1 → 一, 2 → 二, etc.)
 */
export function toChineseNumber(num: number): string {
  const chars = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十'];

  if (num <= 0) return '零';
  if (num <= 10) return chars[num];
  if (num < 20) return '十' + (num === 10 ? '' : chars[num - 10]);
  if (num < 100) {
    const tens = Math.floor(num / 10);
    const ones = num % 10;
    return chars[tens] + '十' + (ones === 0 ? '' : chars[ones]);
  }

  // For larger numbers, just use Arabic numerals
  return num.toString();
}

/**
 * Generate group label (第一卷, 第二卷, etc.)
 */
export function getGroupLabel(index: number): string {
  return `第${toChineseNumber(index + 1)}卷`;
}

/**
 * Generate document label (第一章, 第二章, etc.)
 */
export function getDocumentLabel(index: number): string {
  return `第${toChineseNumber(index + 1)}章`;
}

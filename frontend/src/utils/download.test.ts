import { describe, expect, it } from 'vitest';
import { parseContentDispositionFilename } from './download';

describe('parseContentDispositionFilename', () => {
  it('prefers RFC 5987 encoded filenames', () => {
    const filename = parseContentDispositionFilename(
      'attachment; filename="ZIP .zip"; filename*=UTF-8\'\'ZIP%20%E5%AF%BC%E5%87%BA.zip',
    );

    expect(filename).toBe('ZIP 导出.zip');
  });

  it('falls back to quoted filenames', () => {
    const filename = parseContentDispositionFilename('attachment; filename="draft.txt"');

    expect(filename).toBe('draft.txt');
  });

  it('returns null when no filename is present', () => {
    expect(parseContentDispositionFilename('attachment')).toBeNull();
  });
});

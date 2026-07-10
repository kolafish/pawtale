import { describe, expect, it } from 'vitest';

import type { MemoirBookOutput } from '../../types';
import { bookOutputForm, bookOutputFormsEqual } from './memoirBookOutputView';


describe('memoir book output form', () => {
  it('preserves saved manuscript metadata when reopening the editor', () => {
    const output = {
      id: 'output-1',
      title: '院子里的往事',
      latest_version: {
        manuscript: {
          profile: {
            subtitle: '余仲尤口述回忆录',
            narrator_name: '余仲尤',
            prepared_by: '家人整理',
          },
          front_matter: [
            { kind: 'preface', content: '写在前面的话。' },
          ],
          back_matter: {
            afterword: { content: '写在最后的话。' },
          },
          render_settings: {
            back_cover_quote: '人走得再远，也记得回家的路。',
            cover_style: 'navy_gilt',
          },
        },
      },
    } as MemoirBookOutput;

    const form = bookOutputForm(output);

    expect(form).toEqual({
      title: '院子里的往事',
      subtitle: '余仲尤口述回忆录',
      narrator_name: '余仲尤',
      prepared_by: '家人整理',
      preface: '写在前面的话。',
      afterword: '写在最后的话。',
      back_cover_quote: '人走得再远，也记得回家的路。',
      cover_style: 'navy_gilt',
    });
    expect(bookOutputFormsEqual(form, { ...form })).toBe(true);
    expect(bookOutputFormsEqual(form, { ...form, cover_style: 'classic_plain' })).toBe(false);
  });
});

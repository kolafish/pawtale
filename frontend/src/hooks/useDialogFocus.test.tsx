// @vitest-environment happy-dom
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { useDialogFocus } from './useDialogFocus';

afterEach(cleanup);

function Panel({ open, onClose }: { open: boolean; onClose?: () => void }) {
  const ref = useDialogFocus({ open, onClose });
  return <>
    <button>打开面板</button>
    {open && <section ref={ref} tabIndex={-1} role="dialog" aria-label="参考">
      <button>第一个操作</button>
      <div hidden><button>隐藏操作</button></div>
      <button disabled>不可用操作</button>
      <button>最后一个操作</button>
    </section>}
  </>;
}

test('modal keyboard navigation stays inside and closing restores the opener', () => {
  const onClose = vi.fn();
  const page = render(<Panel open={false} onClose={onClose} />);
  const opener = page.getByText('打开面板');
  opener.focus();
  page.rerender(<Panel open onClose={onClose} />);
  const first = page.getByText('第一个操作');
  const last = page.getByText('最后一个操作');
  expect(document.activeElement).toBe(first);
  fireEvent.keyDown(first, { key: 'Tab', shiftKey: true });
  expect(document.activeElement).toBe(last);
  fireEvent.keyDown(last, { key: 'Tab' });
  expect(document.activeElement).toBe(first);
  opener.focus();
  expect(document.activeElement).toBe(first);
  fireEvent.keyDown(first, { key: 'Escape' });
  expect(onClose).toHaveBeenCalledOnce();
  page.rerender(<Panel open={false} onClose={onClose} />);
  expect(document.activeElement).toBe(opener);
});

test('changing the close handler does not steal focus from an ongoing edit', () => {
  const previousClose = vi.fn();
  const nextClose = vi.fn();
  const page = render(<Panel open onClose={previousClose} />);
  const last = page.getByText('最后一个操作');
  last.focus();
  page.rerender(<Panel open onClose={nextClose} />);
  expect(document.activeElement).toBe(last);
  fireEvent.keyDown(last, { key: 'Escape' });
  expect(previousClose).not.toHaveBeenCalled();
  expect(nextClose).toHaveBeenCalledOnce();
});

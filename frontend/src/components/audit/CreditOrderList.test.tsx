// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import type { AdminCreditOrdersResponse } from '../../types';
import { CreditOrderList } from './CreditOrderList';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it('shows paid totals and opens the buyer credit panel or audit', () => {
  vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-11T07:00:00Z'));
  const response: AdminCreditOrdersResponse = {
    totals: { product_id: '', pack_name: '', paid_count: 1, paid_rmb: '9.90', refunded_rmb: '0', net_rmb: '9.90' },
    products: [{ product_id: '9_9_0', pack_name: 'AI 额度 9.9 元', paid_count: 1, paid_rmb: '9.90', refunded_rmb: '0', net_rmb: '9.90' }],
    pending_count: 2,
    orders: [{
      order_no: '13532bc9cebc4ad88bd7000971524c30',
      user: { id: 'buyer', display_name: 'Kola', email: 'kola@example.test' },
      product_id: '9_9_0', pack_name: 'AI 额度 9.9 元', price_rmb: '9.90', credit_rmb: '9.9',
      refunded_rmb: '0', status: 'paid', wx_order_id: 'wx-1',
      created_at: '2026-10-11T06:58:24', paid_at: '2026-10-11T06:58:35',
    }],
    truncated: false,
  };
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  client.setQueryData(['billing', 'admin-credit-orders', '2026-09-12', '2026-10-11', false], response);
  client.setQueryData(['billing', 'admin-credit', 'buyer'], {
    summary: {
      currency: 'CNY', free_credit_rmb: '10', manual_grant_rmb: '10', recharged_credit_rmb: '9.9',
      total_credit_rmb: '29.9', used_rmb: '13.58', remaining_rmb: '16.32', unpriced_success_count: 0,
    },
    credits: [],
  });
  const onOpenAudit = vi.fn();
  render(<QueryClientProvider client={client}><CreditOrderList onOpenAudit={onOpenAudit} /></QueryClientProvider>);

  expect(screen.getAllByText('¥9.90').length).toBeGreaterThan(0);
  expect(screen.getByText('2')).toBeTruthy();
  expect(screen.getByText('10-11 14:58')).toBeTruthy();
  expect(screen.getByText('已付款')).toBeTruthy();

  fireEvent.click(screen.getByText('Kola'));
  expect(screen.getByText('AI 额度')).toBeTruthy();
  expect(screen.getByText('¥16.32')).toBeTruthy();

  fireEvent.click(screen.getByRole('button', { name: '查看审计' }));
  expect(onOpenAudit).toHaveBeenCalledWith('buyer');
  client.clear();
});

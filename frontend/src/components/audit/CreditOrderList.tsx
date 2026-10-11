import { Fragment, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, CircleDollarSign, Clock3, RefreshCw, Undo2, Wallet } from 'lucide-react';
import { billingAdminApi } from '../../api';
import { AdminCreditPanel } from './AdminCreditPanel';
import { Metric } from './AuditMetric';
import { formatDateTime } from './auditFormat';
import type { CreditOrderStatus } from '../../types';
import { formatBeijingDate } from '../../utils/time';

const STATUS_LABELS: Record<CreditOrderStatus, { label: string; className: string }> = {
  pending: { label: '未付款', className: 'bg-gray-100 text-gray-600' },
  paid: { label: '已付款', className: 'bg-green-50 text-green-700' },
  refunded: { label: '已退款', className: 'bg-amber-50 text-amber-700' },
};

function money(value: string | number): string {
  return `¥${Number(value).toFixed(2)}`;
}

function defaultDateRange() {
  const now = Date.now();
  return { from: formatBeijingDate(now - 29 * 24 * 60 * 60 * 1000), to: formatBeijingDate(now) };
}

/** Lists AI credit top-up orders paid through WeChat virtual payment, with income and refund totals. */
export function CreditOrderList({ onOpenAudit }: { onOpenAudit: (userId: string) => void }) {
  const [range, setRange] = useState(defaultDateRange);
  const [includePending, setIncludePending] = useState(false);
  const [expandedOrder, setExpandedOrder] = useState('');
  const ordersQuery = useQuery({
    queryKey: ['billing', 'admin-credit-orders', range.from, range.to, includePending],
    queryFn: () => billingAdminApi.creditOrders({ ...range, include_pending: includePending }),
    enabled: Boolean(range.from && range.to && range.from <= range.to),
    staleTime: 30_000,
  });
  const data = ordersQuery.data;
  const orders = data?.orders ?? [];

  return (
    <div className="flex-1 overflow-y-auto bg-gray-50">
      <div className="mx-auto flex max-w-7xl flex-col gap-5 px-5 py-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">充值订单</h1>
            <p className="mt-1 text-sm text-gray-500">小程序虚拟支付的额度充值，按下单日期（北京时间）筛选。退款计入原订单当天。</p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <input
              type="date"
              aria-label="开始日期"
              value={range.from}
              max={range.to}
              onChange={(event) => setRange((current) => ({ ...current, from: event.target.value }))}
              className="rounded-md border border-gray-200 bg-white px-2 py-1.5 text-gray-700"
            />
            <span className="text-gray-400">至</span>
            <input
              type="date"
              aria-label="结束日期"
              value={range.to}
              min={range.from}
              onChange={(event) => setRange((current) => ({ ...current, to: event.target.value }))}
              className="rounded-md border border-gray-200 bg-white px-2 py-1.5 text-gray-700"
            />
            <label className="flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-gray-700">
              <input
                type="checkbox"
                checked={includePending}
                onChange={(event) => setIncludePending(event.target.checked)}
              />
              显示未付款订单
            </label>
            <button
              type="button"
              onClick={() => ordersQuery.refetch()}
              disabled={ordersQuery.isFetching}
              className="inline-flex items-center gap-2 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-gray-700 transition-colors hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw size={15} className={ordersQuery.isFetching ? 'animate-spin' : ''} />
              刷新
            </button>
          </div>
        </div>

        {ordersQuery.isError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">充值订单加载失败，请刷新重试。</div>
        )}

        <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Metric icon={<CircleDollarSign size={18} />} label="实收" value={money(data?.totals.paid_rmb ?? 0)} subLabel={`${data?.totals.paid_count ?? 0} 笔已付款`} />
          <Metric icon={<Undo2 size={18} />} label="退款" value={money(data?.totals.refunded_rmb ?? 0)} />
          <Metric icon={<Wallet size={18} />} label="净收入" value={money(data?.totals.net_rmb ?? 0)} subLabel="未扣苹果和微信的手续费" />
          <Metric icon={<Clock3 size={18} />} label="未付款订单" value={`${data?.pending_count ?? 0}`} subLabel="下单后取消或没付成，不加额度" />
        </section>

        {(data?.products.length ?? 0) > 0 && (
          <section className="rounded-lg border border-gray-200 bg-white">
            <div className="border-b border-gray-100 px-4 py-3">
              <h2 className="text-base font-semibold text-gray-900">按档位</h2>
            </div>
            <div className="divide-y divide-gray-100 text-sm">
              {data?.products.map((item) => (
                <div key={item.product_id} className="grid grid-cols-4 gap-3 px-4 py-2.5">
                  <span className="text-gray-900">{item.pack_name}</span>
                  <span className="text-gray-700">{item.paid_count} 笔 · {money(item.paid_rmb)}</span>
                  <span className="text-gray-700">退款 {money(item.refunded_rmb)}</span>
                  <span className="text-right font-medium text-gray-900">净 {money(item.net_rmb)}</span>
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="rounded-lg border border-gray-200 bg-white">
          <div className="border-b border-gray-100 px-4 py-3">
            <h2 className="text-base font-semibold text-gray-900">订单列表</h2>
            <p className="mt-0.5 text-xs text-gray-500">最新的在前。点一行展开该用户的 AI 额度，可查看余额和手动加额度。</p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1080px] divide-y divide-gray-100 text-sm">
              <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="w-32 px-4 py-3 text-left font-medium">下单时间</th>
                  <th className="w-56 px-4 py-3 text-left font-medium">用户</th>
                  <th className="w-36 px-4 py-3 text-left font-medium">档位</th>
                  <th className="w-24 px-4 py-3 text-right font-medium">金额</th>
                  <th className="w-24 px-4 py-3 text-left font-medium">状态</th>
                  <th className="w-24 px-4 py-3 text-right font-medium">已退款</th>
                  <th className="px-4 py-3 text-left font-medium">微信订单号 / 我方订单号</th>
                  <th className="w-28 px-4 py-3 text-right font-medium">审计</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {orders.map((order) => {
                  const expanded = expandedOrder === order.order_no;
                  const status = STATUS_LABELS[order.status] ?? { label: order.status, className: 'bg-gray-100 text-gray-600' };
                  return (
                    <Fragment key={order.order_no}>
                      <tr
                        className="cursor-pointer hover:bg-pink-50/40"
                        aria-expanded={expanded}
                        onClick={() => setExpandedOrder(expanded ? '' : order.order_no)}
                      >
                        <td className="whitespace-nowrap px-4 py-3 text-gray-700">
                          <span className="inline-flex items-center gap-1">
                            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                            {formatDateTime(order.created_at)}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <div className="font-medium text-gray-900">{order.user.display_name || order.user.email}</div>
                          <div className="text-xs text-gray-500">{order.user.email}</div>
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-gray-700">{order.pack_name}</td>
                        <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-gray-900">{money(order.price_rmb)}</td>
                        <td className="px-4 py-3">
                          <span className={`rounded-md px-2 py-0.5 text-xs font-medium ${status.className}`}>{status.label}</span>
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-gray-700">
                          {Number(order.refunded_rmb) > 0 ? money(order.refunded_rmb) : '-'}
                        </td>
                        <td className="px-4 py-3 font-mono text-xs text-gray-500">
                          <div>{order.wx_order_id || '-'}</div>
                          <div>{order.order_no}</div>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              onOpenAudit(order.user.id);
                            }}
                            className="inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-sm text-pink-600 transition-colors hover:bg-pink-50"
                          >
                            查看审计
                            <ChevronRight size={14} />
                          </button>
                        </td>
                      </tr>
                      {expanded && (
                        <tr>
                          <td colSpan={8} className="bg-gray-50/60 p-0">
                            <AdminCreditPanel userId={order.user.id} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
                {!ordersQuery.isLoading && orders.length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-4 py-8 text-center text-gray-500">这段时间没有充值订单。</td>
                  </tr>
                )}
                {ordersQuery.isLoading && (
                  <tr>
                    <td colSpan={8} className="px-4 py-8 text-center text-gray-500">加载中...</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {data?.truncated && (
            <p className="border-t border-gray-100 px-4 py-2 text-xs text-gray-500">只显示最新 {orders.length} 笔，缩小日期范围可以看到更早的订单。上面的汇总包含全部订单。</p>
          )}
        </section>
      </div>
    </div>
  );
}

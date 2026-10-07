import { useState } from 'react';
import type { FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { billingAdminApi } from '../../api';
import type { AICreditKind } from '../../types';

function money(value: string | number): string {
  return Number(value).toFixed(2);
}

function errorMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : '保存失败，请重试。';
}

/** Lets an administrator see a user's AI credit balance and add recharge or grant credit. */
export function AdminCreditPanel({ userId }: { userId: string }) {
  const queryClient = useQueryClient();
  const queryKey = ['billing', 'admin-credit', userId];
  const creditQuery = useQuery({
    queryKey,
    queryFn: () => billingAdminApi.userCredit(userId),
  });
  const [amount, setAmount] = useState('');
  const [kind, setKind] = useState<AICreditKind>('recharge');
  const [note, setNote] = useState('');
  const addCredit = useMutation({
    mutationFn: () => billingAdminApi.addCredit(userId, { amount_rmb: amount.trim(), kind, note: note.trim() }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKey, data);
      setAmount('');
      setNote('');
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!amount.trim() || Number(amount) === 0 || addCredit.isPending) {
      return;
    }
    addCredit.mutate();
  };

  if (creditQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 px-4 py-4 text-sm text-gray-500">
        <RefreshCw size={14} className="animate-spin" />
        加载 AI 额度...
      </div>
    );
  }
  if (creditQuery.isError || !creditQuery.data) {
    return <div className="px-4 py-4 text-sm text-red-600">AI 额度加载失败。</div>;
  }

  const { summary, credits } = creditQuery.data;
  const remaining = Number(summary.remaining_rmb);
  return (
    <div className="border-b border-gray-100 px-4 py-4">
      <h3 className="text-sm font-semibold text-gray-900">AI 额度</h3>
      <dl className="mt-2 grid grid-cols-2 gap-2 text-sm md:grid-cols-5">
        <div><dt className="text-xs text-gray-500">剩余</dt><dd className={remaining < 0 ? 'text-red-600' : 'text-gray-900'}>¥{money(remaining)}</dd></div>
        <div><dt className="text-xs text-gray-500">已用</dt><dd className="text-gray-900">¥{money(summary.used_rmb)}</dd></div>
        <div><dt className="text-xs text-gray-500">充值</dt><dd className="text-gray-900">¥{money(summary.recharged_credit_rmb)}</dd></div>
        <div><dt className="text-xs text-gray-500">赠送（含免费 ¥{money(summary.free_credit_rmb)}）</dt><dd className="text-gray-900">¥{money(Number(summary.manual_grant_rmb) + Number(summary.free_credit_rmb))}</dd></div>
        <div><dt className="text-xs text-gray-500">未定价调用</dt><dd className={summary.unpriced_success_count > 0 ? 'text-amber-600' : 'text-gray-900'}>{summary.unpriced_success_count} 次</dd></div>
      </dl>

      <form onSubmit={submit} className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <select
          value={kind}
          onChange={(event) => setKind(event.target.value as AICreditKind)}
          className="rounded-md border border-gray-300 px-2 py-1"
        >
          <option value="recharge">充值</option>
          <option value="grant">赠送</option>
        </select>
        <input
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
          inputMode="decimal"
          placeholder="增加金额（元），负数为冲正"
          className="w-48 rounded-md border border-gray-300 px-2 py-1"
        />
        <input
          value={note}
          onChange={(event) => setNote(event.target.value)}
          maxLength={255}
          placeholder="备注，例如微信转账"
          className="w-56 rounded-md border border-gray-300 px-2 py-1"
        />
        <button
          type="submit"
          disabled={addCredit.isPending || !amount.trim() || Number(amount) === 0}
          className="rounded-md bg-gray-900 px-3 py-1 text-white disabled:opacity-40"
        >
          {addCredit.isPending ? '保存中...' : '增加额度'}
        </button>
        {addCredit.isError && <span className="text-red-600">{errorMessage(addCredit.error)}</span>}
      </form>

      {credits.length > 0 && (
        <ul className="mt-3 divide-y divide-gray-100 text-sm">
          {credits.map((entry) => (
            <li key={entry.id} className="flex items-center justify-between gap-3 py-1.5">
              <span className="text-gray-700">
                {new Date(entry.created_at).toLocaleString('zh-CN')} · {entry.label}
                {entry.note ? ` · ${entry.note}` : ''}
              </span>
              <span className="tabular-nums text-gray-900">{Number(entry.amount_rmb) < 0 ? '-' : '+'}¥{money(Math.abs(Number(entry.amount_rmb)))}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

import { Fragment, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronRight, Clock3, Mic, RefreshCw, Search, Users } from 'lucide-react';
import { auditApi } from '../../api';
import { AdminCreditPanel } from './AdminCreditPanel';
import { Metric } from './AuditMetric';
import { formatDateTime, formatDuration } from './auditFormat';
import type { LaoyuUserUsage } from '../../types';
import { formatBeijingDate, timestampMs } from '../../utils/time';

const RECENT_DAYS = 7;

function isRecent(value: string | null, days: number): boolean {
  if (!value) return false;
  return Date.now() - timestampMs(value) <= days * 24 * 60 * 60 * 1000;
}

function matchesKeyword(row: LaoyuUserUsage, keyword: string): boolean {
  if (!keyword) return true;
  return [row.user.display_name, row.user.email, row.user.id].some((value) => value.toLowerCase().includes(keyword));
}

/** Lists everyone who signed in to or used 老余口述, with their memoir recording and app usage totals. */
export function LaoyuUserList({ onOpenAudit }: { onOpenAudit: (userId: string) => void }) {
  const [keyword, setKeyword] = useState('');
  const [creditUserId, setCreditUserId] = useState('');
  const usersQuery = useQuery({
    queryKey: ['audit', 'laoyu-users'],
    queryFn: () => auditApi.laoyuUsers(),
    staleTime: 30_000,
  });
  const users = useMemo(() => usersQuery.data?.users ?? [], [usersQuery.data?.users]);
  const normalizedKeyword = keyword.trim().toLowerCase();
  const visibleUsers = useMemo(
    () => users.filter((row) => matchesKeyword(row, normalizedKeyword)),
    [normalizedKeyword, users],
  );
  const recentCount = users.filter((row) => isRecent(row.last_active_at, RECENT_DAYS)).length;
  const recordedUsers = users.filter((row) => row.utterance_count > 0).length;
  const recordedSeconds = users.reduce((total, row) => total + row.recorded_seconds, 0);
  const activeMs = users.reduce((total, row) => total + row.miniprogram_active_ms, 0);

  return (
    <div className="flex-1 overflow-y-auto bg-gray-50">
      <div className="mx-auto flex max-w-7xl flex-col gap-5 px-5 py-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">老余口述用户</h1>
            <p className="mt-1 text-sm text-gray-500">登录过或使用过老余口述小程序的用户，以及他们的讲述录音和小程序使用时长。</p>
          </div>
          <button
            type="button"
            onClick={() => usersQuery.refetch()}
            disabled={usersQuery.isFetching}
            className="inline-flex w-fit items-center gap-2 rounded-md border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 transition-colors hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw size={15} className={usersQuery.isFetching ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>

        {usersQuery.isError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">老余口述用户加载失败，请刷新重试。</div>
        )}

        <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Metric icon={<Users size={18} />} label="用户数" value={`${users.length}`} subLabel={`近 ${RECENT_DAYS} 天活跃 ${recentCount} 人`} />
          <Metric icon={<Mic size={18} />} label="有讲述的用户" value={`${recordedUsers}`} subLabel="至少录过一段讲述" />
          <Metric icon={<Clock3 size={18} />} label="讲述录音总时长" value={formatDuration(recordedSeconds * 1000)} />
          <Metric icon={<Clock3 size={18} />} label="小程序活跃总时长" value={formatDuration(activeMs)} subLabel="来自小程序使用审计" />
        </section>

        <section className="rounded-lg border border-gray-200 bg-white">
          <div className="flex flex-col gap-3 border-b border-gray-100 px-4 py-3 md:flex-row md:items-center md:justify-between">
            <div>
              <h2 className="text-base font-semibold text-gray-900">用户列表</h2>
              <p className="mt-0.5 text-xs text-gray-500">按最近活跃排序，点击查看该用户的小程序使用审计。</p>
            </div>
            <label className="flex items-center gap-2 rounded-md border border-gray-200 px-3 py-2 text-sm focus-within:ring-2 focus-within:ring-pink-500">
              <Search size={15} className="text-gray-400" />
              <input
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="搜索昵称、邮箱或用户 ID"
                className="w-56 bg-transparent text-gray-900 focus:outline-none"
              />
            </label>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1080px] divide-y divide-gray-100 text-sm">
              <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="w-56 px-4 py-3 text-left font-medium">用户</th>
                  <th className="w-28 px-4 py-3 text-left font-medium">注册</th>
                  <th className="w-24 px-4 py-3 text-left font-medium">回忆录</th>
                  <th className="w-32 px-4 py-3 text-left font-medium">讲述会话/段数</th>
                  <th className="w-32 px-4 py-3 text-left font-medium">录音时长</th>
                  <th className="w-40 px-4 py-3 text-left font-medium">小程序活跃</th>
                  <th className="w-32 px-4 py-3 text-left font-medium">最近活跃</th>
                  <th className="w-48 px-4 py-3 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {visibleUsers.map((row) => (
                  <Fragment key={row.user.id}>
                    <tr
                      className="cursor-pointer hover:bg-pink-50/40"
                      title="点击查看小程序使用审计"
                      onClick={() => onOpenAudit(row.user.id)}
                    >
                      <td className="px-4 py-3">
                        <div className="font-medium text-gray-900">{row.user.display_name || row.user.email}</div>
                        <div className="text-xs text-gray-500">
                          {row.wechat_bound ? '微信登录' : '邮箱账号'} · {row.user.email}
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-gray-700">{formatBeijingDate(row.created_at) || '-'}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-gray-700">{row.memoir_project_count}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-gray-700">
                        {row.voice_session_count} / {row.utterance_count}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-gray-900">{formatDuration(row.recorded_seconds * 1000)}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-gray-700">
                        <div>{formatDuration(row.miniprogram_active_ms)}</div>
                        <div className="text-xs text-gray-500">{row.miniprogram_active_days} 天</div>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-gray-700">{formatDateTime(row.last_active_at)}</td>
                      <td className="px-4 py-3 text-right">
                        <button
                          type="button"
                          aria-expanded={creditUserId === row.user.id}
                          onClick={(event) => {
                            event.stopPropagation();
                            setCreditUserId(creditUserId === row.user.id ? '' : row.user.id);
                          }}
                          className="mr-1 inline-flex items-center justify-center whitespace-nowrap rounded-md px-2 py-1 text-sm text-gray-600 transition-colors hover:bg-gray-100"
                        >
                          AI 额度
                        </button>
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            onOpenAudit(row.user.id);
                          }}
                          className="inline-flex items-center justify-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-sm text-pink-600 transition-colors hover:bg-pink-50"
                        >
                          查看审计
                          <ChevronRight size={14} />
                        </button>
                      </td>
                    </tr>
                    {creditUserId === row.user.id && (
                      <tr>
                        <td colSpan={8} className="bg-gray-50/60 p-0">
                          <AdminCreditPanel userId={row.user.id} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
                {!usersQuery.isLoading && visibleUsers.length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-4 py-8 text-center text-gray-500">
                      {users.length === 0 ? '还没有老余口述用户。' : '没有匹配的用户。'}
                    </td>
                  </tr>
                )}
                {usersQuery.isLoading && (
                  <tr>
                    <td colSpan={8} className="px-4 py-8 text-center text-gray-500">加载中...</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

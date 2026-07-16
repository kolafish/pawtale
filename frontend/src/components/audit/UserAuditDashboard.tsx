import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  ChevronRight,
  Clock3,
  DoorOpen,
  Eye,
  Filter,
  MousePointerClick,
  RefreshCw,
  Users,
  X,
} from 'lucide-react';
import { auditApi } from '../../api';
import type {
  AuditDashboardFilters,
  AuditDailyFeatureUsage,
  AuditDailyUserUsage,
  AuditEvent,
  AuditPlatform,
  AuditSession,
} from '../../types';

const PLATFORM_LABELS: Record<string, string> = {
  miniprogram: '小程序',
  web: 'Web',
};

const EVENT_TYPE_LABELS: Record<string, string> = {
  page_view: '进入页面',
  page_leave: '离开页面',
  feature_start: '开始功能',
  feature_end: '结束功能',
  action: '功能动作',
  heartbeat: '停留记录',
};

const EVENT_TYPE_ORDER = [
  'page_view',
  'page_leave',
  'action',
  'feature_start',
  'feature_end',
  'heartbeat',
];

const EXIT_REASON_LABELS: Record<string, string> = {
  app_background: '退到后台',
  page_unload: '页面卸载',
  logout: '退出登录',
  timeout: '超时',
  navigate: '跳转',
  unknown: '未知',
};

const PAGE_PATH_LABELS: Record<string, string> = {
  '/pages/home/index': '小程序首页',
  '/pages/index/index': '旧版首页',
  '/pages/login/index': '登录页',
  '/pages/voice-authorization/index': '语音授权页',
  '/pages/voice-chat/index': '语音访谈页',
  '/pages/transcripts/index': '讲述记录页',
  '/pages/book/index': '成书页',
  '/pages/voice-progress/index': '访谈进度页',
  '/pages/projects/index': '项目列表页',
  '/pages/project-edit/index': '项目编辑页',
  '/pages/me/index': '我的页面',
};

const FEATURE_LABELS: Record<string, string> = {
  'home.view': '首页浏览',
  'page.view': '页面浏览',
  'voice_chat.session': '语音访谈页停留',
  'voice_chat.start': '开始语音访谈',
  'voice_chat.recording_start': '开始录音',
  'voice_chat.asr_final': '保存一段转写',
  'voice_chat.finish': '结束访谈',
  'transcript.view': '查看讲述记录',
  'transcript.voice_search': '语音搜索记录',
  'transcript.voice_edit_preview': '预览语音修正',
  'transcript.voice_edit_save': '保存语音修正',
  'transcript.edit_save': '保存文字修正',
  'book.view': '查看成书页',
  'book.plan_generate': '生成成书目录',
  'book.plan_confirm': '确认成书目录',
  'book.chapter_title_generate': '生成章节标题',
  'book.chapter_draft_generate': '生成章节草稿',
  'book.chapter_draft_confirm': '确认章节草稿',
  'book.output_generate': '生成全书',
  'book.voice_command_start': '开始成书语音指令',
  'book.voice_command_execute': '执行成书语音指令',
  'project.create': '新建项目',
  'project.update': '更新项目',
  'project.activate': '切换项目',
  'project.delete': '删除项目',
  'admin.project.delete': '管理员删除项目',
};

function toDateInput(date: Date): string {
  const localDate = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return localDate.toISOString().slice(0, 10);
}

function defaultDateRange() {
  const to = new Date();
  const from = new Date();
  from.setDate(to.getDate() - 6);
  return { from: toDateInput(from), to: toDateInput(to) };
}

function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '0 分钟';
  const totalSeconds = Math.round(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分 ${seconds} 秒`;
  return `${seconds} 秒`;
}

function formatDateTime(value?: string | null): string {
  if (!value) return '-';
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function labelOrDash(value?: string | null): string {
  const trimmed = value?.trim();
  return trimmed || '-';
}

function platformLabel(platform: string): string {
  return PLATFORM_LABELS[platform] || platform || '-';
}

function exitReasonLabel(reason: string): string {
  return EXIT_REASON_LABELS[reason] || reason || '-';
}

function normalizedPagePath(pagePath: string): string {
  if (!pagePath) return '';
  return pagePath.startsWith('/') ? pagePath : `/${pagePath}`;
}

function pagePathLabel(pagePath: string): string {
  const normalized = normalizedPagePath(pagePath);
  return PAGE_PATH_LABELS[normalized] || pagePath || '未记录页面';
}

function featureLabel(featureCode: string): string {
  return FEATURE_LABELS[featureCode] || featureCode || '未归类功能';
}

function featureOptionLabel(featureCode: string): string {
  const label = featureLabel(featureCode);
  return label === featureCode ? featureCode : `${label}（${featureCode}）`;
}

function userDisplay(row: Pick<AuditDailyUserUsage, 'user'> | Pick<AuditDailyFeatureUsage, 'user'>): string {
  return row.user.display_name || row.user.email;
}

interface DetailEventFilters {
  eventType: string;
  featureCode: string;
  keyword: string;
  showHeartbeat: boolean;
}

function eventTypeLabel(eventType: string): string {
  return EVENT_TYPE_LABELS[eventType] || eventType || '未知事件';
}

function sortedSessionEvents(session: AuditSession): AuditEvent[] {
  return [...session.events].sort(
    (a, b) => new Date(a.occurred_at).getTime() - new Date(b.occurred_at).getTime(),
  );
}

function eventMatchesDetailFilters(event: AuditEvent, filters: DetailEventFilters): boolean {
  if (filters.eventType && event.event_type !== filters.eventType) return false;
  if (!filters.showHeartbeat && event.event_type === 'heartbeat') return false;
  if (filters.featureCode && event.feature_code !== filters.featureCode) return false;

  const keyword = filters.keyword.trim().toLowerCase();
  if (!keyword) return true;

  const searchableText = [
    event.event_type,
    eventTypeLabel(event.event_type),
    event.page_path,
    pagePathLabel(event.page_path),
    event.feature_code,
    featureLabel(event.feature_code),
    event.project_id,
    event.voice_session_id,
    event.exit_reason,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return searchableText.includes(keyword);
}

function flattenEvents(sessions: AuditSession[]): AuditEvent[] {
  return sessions.flatMap((session) => sortedSessionEvents(session));
}

function countEventsByType(events: AuditEvent[]): Record<string, number> {
  return events.reduce<Record<string, number>>((counts, event) => {
    counts[event.event_type] = (counts[event.event_type] || 0) + 1;
    return counts;
  }, {});
}

function featureCodeOptions(events: AuditEvent[]): string[] {
  return Array.from(new Set(events.map((event) => event.feature_code).filter(Boolean))).sort((a, b) => a.localeCompare(b));
}

interface FeatureTotal {
  key: string;
  feature_code: string;
  platform: string;
  active_ms: number;
  event_count: number;
  user_count: number;
  last_seen_at?: string | null;
}

function aggregateFeatures(rows: AuditDailyFeatureUsage[]): FeatureTotal[] {
  const byFeature = new Map<string, FeatureTotal & { userIds: Set<string> }>();
  for (const row of rows) {
    const key = `${row.platform}:${row.feature_code}`;
    const current = byFeature.get(key) ?? {
      key,
      feature_code: row.feature_code,
      platform: row.platform,
      active_ms: 0,
      event_count: 0,
      user_count: 0,
      last_seen_at: row.last_seen_at,
      userIds: new Set<string>(),
    };
    current.active_ms += row.active_ms;
    current.event_count += row.event_count;
    current.userIds.add(row.user.id);
    if (!current.last_seen_at || (row.last_seen_at && row.last_seen_at > current.last_seen_at)) {
      current.last_seen_at = row.last_seen_at;
    }
    byFeature.set(key, current);
  }
  return Array.from(byFeature.values())
    .map(({ userIds, ...row }) => ({ ...row, user_count: userIds.size }))
    .sort((a, b) => b.active_ms - a.active_ms || b.event_count - a.event_count)
    .slice(0, 20);
}

interface MetricProps {
  icon: ReactNode;
  label: string;
  value: string;
  subLabel?: string;
}

function Metric({ icon, label, value, subLabel }: MetricProps) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between text-gray-500">
        <span className="text-sm">{label}</span>
        {icon}
      </div>
      <div className="text-2xl font-semibold text-gray-900">{value}</div>
      {subLabel && <div className="mt-1 text-xs text-gray-500">{subLabel}</div>}
    </div>
  );
}

interface SelectedUserDay {
  userId: string;
  date: string;
  name: string;
}

export function UserAuditDashboard() {
  const defaultRange = useMemo(() => defaultDateRange(), []);
  const [fromDate, setFromDate] = useState(defaultRange.from);
  const [toDate, setToDate] = useState(defaultRange.to);
  const [platform, setPlatform] = useState<'' | AuditPlatform>('');
  const [userId, setUserId] = useState('');
  const [featureCode, setFeatureCode] = useState('');
  const [selectedDay, setSelectedDay] = useState<SelectedUserDay | null>(null);
  const [detailEventType, setDetailEventType] = useState('');
  const [detailFeatureCode, setDetailFeatureCode] = useState('');
  const [detailKeyword, setDetailKeyword] = useState('');
  const [detailShowHeartbeat, setDetailShowHeartbeat] = useState(false);
  const invalidRange = Boolean(fromDate && toDate && fromDate > toDate);
  const detailSectionRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!selectedDay) return undefined;

    const timer = window.setTimeout(() => {
      detailSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 0);

    return () => window.clearTimeout(timer);
  }, [selectedDay]);

  const dashboardFilters = useMemo<AuditDashboardFilters>(() => ({
    from: fromDate,
    to: toDate,
    ...(platform ? { platform } : {}),
    ...(userId.trim() ? { user_id: userId.trim() } : {}),
    ...(featureCode.trim() ? { feature_code: featureCode.trim() } : {}),
  }), [featureCode, fromDate, platform, toDate, userId]);

  const dashboardQuery = useQuery({
    queryKey: ['audit', 'dashboard', dashboardFilters],
    queryFn: () => auditApi.dashboard(dashboardFilters),
    enabled: !invalidRange,
    staleTime: 30_000,
  });

  const dayQuery = useQuery({
    queryKey: ['audit', 'user-day', selectedDay?.userId, selectedDay?.date],
    queryFn: () => auditApi.userDay(selectedDay?.userId || '', selectedDay?.date || ''),
    enabled: Boolean(selectedDay),
    staleTime: 30_000,
  });

  const dashboard = dashboardQuery.data;
  const featureTotals = useMemo(
    () => aggregateFeatures(dashboard?.feature_usage ?? []),
    [dashboard?.feature_usage],
  );
  const daySessions = useMemo(() => dayQuery.data?.sessions ?? [], [dayQuery.data?.sessions]);
  const dayEvents = useMemo(() => flattenEvents(daySessions), [daySessions]);
  const detailFilters = useMemo<DetailEventFilters>(() => ({
    eventType: detailEventType,
    featureCode: detailFeatureCode,
    keyword: detailKeyword,
    showHeartbeat: detailShowHeartbeat,
  }), [detailEventType, detailFeatureCode, detailKeyword, detailShowHeartbeat]);
  const detailEventCounts = useMemo(() => countEventsByType(dayEvents), [dayEvents]);
  const detailFeatureOptions = useMemo(() => featureCodeOptions(dayEvents), [dayEvents]);
  const filteredDayEventCount = useMemo(
    () => dayEvents.filter((event) => eventMatchesDetailFilters(event, detailFilters)).length,
    [dayEvents, detailFilters],
  );

  const clearFilters = () => {
    setFromDate(defaultRange.from);
    setToDate(defaultRange.to);
    setPlatform('');
    setUserId('');
    setFeatureCode('');
  };

  const clearDetailFilters = () => {
    setDetailEventType('');
    setDetailFeatureCode('');
    setDetailKeyword('');
    setDetailShowHeartbeat(false);
  };

  const openUserDay = (row: AuditDailyUserUsage) => {
    clearDetailFilters();
    setSelectedDay({ userId: row.user.id, date: row.date, name: userDisplay(row) });
  };

  return (
    <div className="flex-1 overflow-y-auto bg-gray-50">
      <div className="mx-auto flex max-w-7xl flex-col gap-5 px-5 py-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">用户审计</h1>
            <p className="mt-1 text-sm text-gray-500">按用户、日期、平台和功能查看使用时长、行为和离开位置。</p>
          </div>
          <button
            type="button"
            onClick={() => dashboardQuery.refetch()}
            disabled={dashboardQuery.isFetching || invalidRange}
            className="inline-flex w-fit items-center gap-2 rounded-md border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 transition-colors hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw size={15} className={dashboardQuery.isFetching ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>

        <section className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-medium text-gray-700">
            <Filter size={15} />
            筛选
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
            <label className="flex flex-col gap-1 text-sm text-gray-600">
              <span>开始日期</span>
              <input
                type="date"
                value={fromDate}
                onChange={(event) => setFromDate(event.target.value)}
                className="rounded-md border border-gray-200 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-pink-500"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-gray-600">
              <span>结束日期</span>
              <input
                type="date"
                value={toDate}
                onChange={(event) => setToDate(event.target.value)}
                className="rounded-md border border-gray-200 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-pink-500"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-gray-600">
              <span>平台</span>
              <select
                value={platform}
                onChange={(event) => setPlatform(event.target.value as '' | AuditPlatform)}
                className="rounded-md border border-gray-200 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-pink-500"
              >
                <option value="">全部</option>
                <option value="miniprogram">小程序</option>
                <option value="web">Web</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm text-gray-600">
              <span>用户 ID</span>
              <input
                value={userId}
                onChange={(event) => setUserId(event.target.value)}
                placeholder="按用户精确筛选"
                className="rounded-md border border-gray-200 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-pink-500"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-gray-600">
              <span>功能编码</span>
              <input
                value={featureCode}
                onChange={(event) => setFeatureCode(event.target.value)}
                placeholder="如 voice_chat.start"
                className="rounded-md border border-gray-200 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-pink-500"
              />
            </label>
            <div className="flex items-end">
              <button
                type="button"
                onClick={clearFilters}
                className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-gray-200 px-3 py-2 text-sm text-gray-600 transition-colors hover:bg-gray-50"
              >
                <X size={15} />
                重置
              </button>
            </div>
          </div>
          {invalidRange && <p className="mt-3 text-sm text-red-600">开始日期不能晚于结束日期。</p>}
        </section>

        {dashboardQuery.isError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            审计数据加载失败，请确认当前账号有管理员权限。
          </div>
        )}

        <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Metric
            icon={<Clock3 size={18} />}
            label="总活跃时长"
            value={formatDuration(dashboard?.summary.active_ms ?? 0)}
            subLabel={dashboard?.summary.top_exit_page_path ? `最多离开：${pagePathLabel(dashboard.summary.top_exit_page_path)}` : '暂无离开位置'}
          />
          <Metric
            icon={<Users size={18} />}
            label="活跃用户"
            value={`${dashboard?.summary.user_count ?? 0}`}
            subLabel={`${dashboard?.summary.session_count ?? 0} 个会话`}
          />
          <Metric
            icon={<MousePointerClick size={18} />}
            label="事件数"
            value={`${dashboard?.summary.event_count ?? 0}`}
            subLabel={`${dashboard?.feature_usage.length ?? 0} 条功能明细`}
          />
          <Metric
            icon={<DoorOpen size={18} />}
            label="最多离开功能"
            value={dashboard?.summary.top_exit_feature_code ? featureLabel(dashboard.summary.top_exit_feature_code) : '-'}
            subLabel="来自每日最后离开位置"
          />
        </section>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(360px,0.9fr)]">
          <section className="rounded-lg border border-gray-200 bg-white">
            <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
              <div>
                <h2 className="text-base font-semibold text-gray-900">每日用户使用</h2>
                <p className="mt-0.5 text-xs text-gray-500">按天统计每个用户的活跃时长、会话数和最后离开位置。</p>
              </div>
              {dashboardQuery.isFetching && <RefreshCw size={16} className="animate-spin text-gray-400" />}
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-[1120px] divide-y divide-gray-100 text-sm">
                <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                  <tr>
                    <th className="w-56 px-4 py-3 text-left font-medium">用户</th>
                    <th className="w-32 px-4 py-3 text-left font-medium">日期</th>
                    <th className="w-24 px-4 py-3 text-left font-medium">平台</th>
                    <th className="w-36 px-4 py-3 text-left font-medium">活跃时长</th>
                    <th className="w-28 px-4 py-3 text-left font-medium">会话/事件</th>
                    <th className="px-4 py-3 text-left font-medium">最后离开</th>
                    <th className="w-32 px-4 py-3 text-right font-medium">详情</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {(dashboard?.daily_users ?? []).map((row) => (
                    <tr
                      key={`${row.user.id}:${row.date}:${row.platform}`}
                      className="cursor-pointer hover:bg-pink-50/40"
                      title="点击查看当天详情"
                      onClick={() => openUserDay(row)}
                    >
                      <td className="px-4 py-3">
                        <div className="font-medium text-gray-900">{userDisplay(row)}</div>
                        <div className="text-xs text-gray-500">{row.user.email}</div>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-gray-700">{row.date}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-gray-700">{platformLabel(row.platform)}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-gray-900">{formatDuration(row.active_ms)}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-gray-700">
                        {row.session_count} / {row.event_count}
                      </td>
                      <td className="px-4 py-3">
                        <div className="max-w-72 truncate text-gray-800">{pagePathLabel(row.last_exit_page_path)}</div>
                        <div className="text-xs text-gray-500">
                          {row.last_exit_feature_code ? featureLabel(row.last_exit_feature_code) : '-'} · {exitReasonLabel(row.last_exit_reason)}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            openUserDay(row);
                          }}
                          className="inline-flex min-w-24 items-center justify-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-sm text-pink-600 transition-colors hover:bg-pink-50"
                        >
                          查看详情
                          <ChevronRight size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!dashboardQuery.isLoading && (dashboard?.daily_users.length ?? 0) === 0 && (
                    <tr>
                      <td colSpan={7} className="px-4 py-10 text-center text-gray-500">
                        当前筛选范围内暂无用户审计数据。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <div className="flex flex-col gap-5">
            <section className="rounded-lg border border-gray-200 bg-white">
              <div className="border-b border-gray-100 px-4 py-3">
                <h2 className="text-base font-semibold text-gray-900">功能使用排行</h2>
                <p className="mt-0.5 text-xs text-gray-500">同平台同功能聚合后的活跃时长和事件数。</p>
              </div>
              <div className="divide-y divide-gray-100">
                {featureTotals.map((row) => (
                  <div key={row.key} className="px-4 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate font-medium text-gray-900">{featureLabel(row.feature_code)}</div>
                        <div className="mt-1 text-xs text-gray-500">
                          {row.feature_code} · {platformLabel(row.platform)} · {row.user_count} 位用户 · 最近 {formatDateTime(row.last_seen_at)}
                        </div>
                      </div>
                      <div className="shrink-0 text-right">
                        <div className="text-sm font-semibold text-gray-900">{formatDuration(row.active_ms)}</div>
                        <div className="text-xs text-gray-500">{row.event_count} 事件</div>
                      </div>
                    </div>
                  </div>
                ))}
                {!dashboardQuery.isLoading && featureTotals.length === 0 && (
                  <div className="px-4 py-8 text-center text-sm text-gray-500">暂无功能使用数据。</div>
                )}
              </div>
            </section>

            <section className="rounded-lg border border-gray-200 bg-white">
              <div className="border-b border-gray-100 px-4 py-3">
                <h2 className="text-base font-semibold text-gray-900">离开位置</h2>
                <p className="mt-0.5 text-xs text-gray-500">按每日最后离开位置聚合。</p>
              </div>
              <div className="divide-y divide-gray-100">
                {(dashboard?.exit_locations ?? []).map((row) => (
                  <div key={`${row.page_path}:${row.feature_code}:${row.reason}`} className="px-4 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate font-medium text-gray-900">{pagePathLabel(row.page_path)}</div>
                        <div className="mt-1 text-xs text-gray-500">
                          {row.feature_code ? featureLabel(row.feature_code) : '-'} · {exitReasonLabel(row.reason)}
                        </div>
                      </div>
                      <span className="rounded-md bg-gray-100 px-2 py-1 text-xs font-medium text-gray-700">
                        {row.count}
                      </span>
                    </div>
                  </div>
                ))}
                {!dashboardQuery.isLoading && (dashboard?.exit_locations.length ?? 0) === 0 && (
                  <div className="px-4 py-8 text-center text-sm text-gray-500">暂无离开位置数据。</div>
                )}
              </div>
            </section>
          </div>
        </div>

        {selectedDay && (
          <section ref={detailSectionRef} className="rounded-lg border border-gray-200 bg-white">
            <div className="flex flex-col gap-3 border-b border-gray-100 px-4 py-3 md:flex-row md:items-center md:justify-between">
              <div>
                <h2 className="text-base font-semibold text-gray-900">{selectedDay.name} 的当天详情</h2>
                <p className="mt-0.5 text-xs text-gray-500">{selectedDay.date} 的 session、事件、项目和语音会话线索。</p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedDay(null)}
                className="inline-flex w-fit items-center gap-1 rounded-md px-2 py-1 text-sm text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700"
              >
                <X size={14} />
                关闭
              </button>
            </div>

            {dayQuery.isLoading ? (
              <div className="flex items-center justify-center gap-2 px-4 py-10 text-sm text-gray-500">
                <RefreshCw size={16} className="animate-spin" />
                加载当天详情...
              </div>
            ) : dayQuery.isError ? (
              <div className="px-4 py-8 text-sm text-red-600">当天详情加载失败。</div>
            ) : (
              <>
                {dayEvents.length > 0 && (
                  <div className="border-b border-gray-100 px-4 py-4">
                    <div className="mb-3 flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          onClick={() => setDetailEventType('')}
                          className={`rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                            !detailEventType
                              ? 'bg-pink-500 text-white'
                              : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                          }`}
                        >
                          全部 {dayEvents.length}
                        </button>
                        {EVENT_TYPE_ORDER.filter((eventType) => detailEventCounts[eventType]).map((eventType) => (
                          <button
                            key={eventType}
                            type="button"
                            onClick={() => {
                              setDetailEventType(eventType);
                              if (eventType === 'heartbeat') {
                                setDetailShowHeartbeat(true);
                              }
                            }}
                            className={`rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                              detailEventType === eventType
                                ? 'bg-pink-500 text-white'
                                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                            }`}
                          >
                            {eventTypeLabel(eventType)} {detailEventCounts[eventType]}
                          </button>
                        ))}
                      </div>
                      <div className="text-sm text-gray-500">
                        显示 {filteredDayEventCount} / {dayEvents.length} 事件
                      </div>
                    </div>
                    <div className="grid gap-3 md:grid-cols-[minmax(180px,240px)_minmax(220px,1fr)_auto_auto]">
                      <label className="flex flex-col gap-1 text-sm text-gray-600">
                        <span>功能编码</span>
                        <select
                          value={detailFeatureCode}
                          onChange={(event) => setDetailFeatureCode(event.target.value)}
                          className="rounded-md border border-gray-200 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-pink-500"
                        >
                          <option value="">全部功能</option>
                          {detailFeatureOptions.map((option) => (
                            <option key={option} value={option}>
                              {featureOptionLabel(option)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="flex flex-col gap-1 text-sm text-gray-600">
                        <span>搜索</span>
                        <input
                          value={detailKeyword}
                          onChange={(event) => setDetailKeyword(event.target.value)}
                          placeholder="页面、功能、项目或语音会话"
                          className="rounded-md border border-gray-200 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-pink-500"
                        />
                      </label>
                      <div className="flex items-end">
                        <button
                          type="button"
                          onClick={() => setDetailShowHeartbeat((value) => !value)}
                          className={`inline-flex w-full items-center justify-center rounded-md border px-3 py-2 text-sm transition-colors ${
                            detailShowHeartbeat
                              ? 'border-pink-200 bg-pink-50 text-pink-700'
                              : 'border-gray-200 text-gray-600 hover:bg-gray-50'
                          }`}
                        >
                          {detailShowHeartbeat ? '隐藏停留记录' : '显示停留记录'}
                        </button>
                      </div>
                      <div className="flex items-end">
                        <button
                          type="button"
                          onClick={clearDetailFilters}
                          className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-gray-200 px-3 py-2 text-sm text-gray-600 transition-colors hover:bg-gray-50"
                        >
                          <X size={15} />
                          重置
                        </button>
                      </div>
                    </div>
                  </div>
                )}
                <div className="divide-y divide-gray-100">
                  {daySessions.map((session) => (
                    <SessionDetail key={session.id} session={session} filters={detailFilters} />
                  ))}
                  {daySessions.length === 0 && (
                    <div className="px-4 py-8 text-center text-sm text-gray-500">当天没有 session 记录。</div>
                  )}
                  {daySessions.length > 0 && filteredDayEventCount === 0 && (
                    <div className="px-4 py-8 text-center text-sm text-gray-500">当前筛选下没有匹配事件。</div>
                  )}
                </div>
              </>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

function SessionDetail({ session, filters }: { session: AuditSession; filters: DetailEventFilters }) {
  const allEvents = sortedSessionEvents(session);
  const events = allEvents.filter((event) => eventMatchesDetailFilters(event, filters));

  if (events.length === 0) {
    return null;
  }

  return (
    <div className="px-4 py-4">
      <div className="mb-3 grid gap-3 text-sm md:grid-cols-5">
        <div>
          <div className="text-xs text-gray-500">平台</div>
          <div className="font-medium text-gray-900">{platformLabel(session.platform)}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500">开始</div>
          <div className="font-medium text-gray-900">{formatDateTime(session.started_at)}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500">最后活跃</div>
          <div className="font-medium text-gray-900">{formatDateTime(session.last_seen_at)}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500">活跃时长</div>
          <div className="font-medium text-gray-900">{formatDuration(session.active_ms)}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500">离开</div>
          <div className="truncate font-medium text-gray-900">{pagePathLabel(session.exit_page_path)}</div>
        </div>
      </div>
      <div className="mb-3 text-xs text-gray-500">
        筛选后事件 {events.length} / {allEvents.length}
      </div>
      {(session.app_version || session.device_summary) && (
        <div className="mb-3 flex flex-wrap gap-2 text-xs text-gray-500">
          {session.app_version && <span className="rounded-md bg-gray-100 px-2 py-1">版本 {session.app_version}</span>}
          {session.device_summary && <span className="rounded-md bg-gray-100 px-2 py-1">{session.device_summary}</span>}
        </div>
      )}
      <div className="overflow-x-auto rounded-lg border border-gray-100">
        <table className="min-w-full divide-y divide-gray-100 text-sm">
          <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
            <tr>
              <th className="px-3 py-2 text-left font-medium">时间</th>
              <th className="px-3 py-2 text-left font-medium">事件</th>
              <th className="px-3 py-2 text-left font-medium">页面/功能</th>
              <th className="px-3 py-2 text-left font-medium">时长</th>
              <th className="px-3 py-2 text-left font-medium">关联</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {events.map((event) => (
              <tr key={event.id}>
                <td className="px-3 py-2 text-gray-700">{formatDateTime(event.occurred_at)}</td>
                <td className="px-3 py-2">
                  <span className="inline-flex items-center gap-1 rounded-md bg-gray-100 px-2 py-1 text-xs font-medium text-gray-700">
                    {event.event_type === 'page_view' ? <Eye size={12} /> : <Activity size={12} />}
                    {EVENT_TYPE_LABELS[event.event_type] || event.event_type}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <div className="max-w-80 truncate text-gray-900">{pagePathLabel(event.page_path)}</div>
                  <div className="text-xs text-gray-500">
                    {featureLabel(event.feature_code)}
                    {event.feature_code && FEATURE_LABELS[event.feature_code] ? ` · ${event.feature_code}` : ''}
                  </div>
                </td>
                <td className="px-3 py-2 text-gray-700">{formatDuration(event.active_ms || event.duration_ms)}</td>
                <td className="px-3 py-2 text-xs text-gray-500">
                  <div className="max-w-64 truncate">项目：{labelOrDash(event.project_id)}</div>
                  <div className="max-w-64 truncate">语音：{labelOrDash(event.voice_session_id)}</div>
                </td>
              </tr>
            ))}
            {events.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-gray-500">
                  这个 session 没有当天事件。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

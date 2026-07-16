import { useEffect, useState } from 'react';
import { AlertTriangle, RefreshCw, Trash2, X } from 'lucide-react';
import { isProjectDeletionConfirmationValid } from '../domain/projectDeletion';
import type { ProjectListItem } from '../types';

interface ProjectDeleteDialogProps {
  project: ProjectListItem;
  isDeleting: boolean;
  errorMessage: string;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ProjectDeleteDialog({
  project,
  isDeleting,
  errorMessage,
  onCancel,
  onConfirm,
}: ProjectDeleteDialogProps) {
  const [confirmation, setConfirmation] = useState('');
  const ownerLabel = project.owner_display_name || project.owner_email || '未记录所有者';
  const isConfirmed = isProjectDeletionConfirmationValid(project.title, confirmation);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !isDeleting) onCancel();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isDeleting, onCancel]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/35 p-4 backdrop-blur-[2px]"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !isDeleting) onCancel();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="project-delete-title"
        aria-describedby="project-delete-description"
        className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-[0_0_0_1px_rgba(0,0,0,0.06),0_12px_32px_-12px_rgba(17,24,39,0.28),0_24px_64px_-24px_rgba(17,24,39,0.22)]"
      >
        <div className="flex items-start gap-4">
          <div className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-red-50 text-red-600">
            <AlertTriangle size={22} aria-hidden="true" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 id="project-delete-title" className="text-balance text-lg font-semibold text-gray-900">
              永久删除项目及全部数据？
            </h2>
            <p id="project-delete-description" className="mt-1 text-pretty text-sm leading-6 text-gray-600">
              将删除数据库记录、文稿、音频、转写、图片和导出文件。此操作无法撤销。
            </p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={isDeleting}
            className="flex size-10 shrink-0 items-center justify-center rounded-lg text-gray-400 transition-[color,background-color,scale] duration-150 ease-out hover:bg-gray-100 hover:text-gray-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="关闭删除确认"
          >
            <X size={19} aria-hidden="true" />
          </button>
        </div>

        <dl className="mt-5 rounded-xl bg-gray-50 px-4 py-3 text-sm">
          <div className="flex gap-3 py-1">
            <dt className="w-16 shrink-0 text-gray-500">项目</dt>
            <dd className="min-w-0 break-words font-medium text-gray-900">{project.title}</dd>
          </div>
          <div className="flex gap-3 py-1">
            <dt className="w-16 shrink-0 text-gray-500">所有者</dt>
            <dd className="min-w-0 break-words text-gray-700">{ownerLabel}</dd>
          </div>
          <div className="flex gap-3 py-1">
            <dt className="w-16 shrink-0 text-gray-500">项目 ID</dt>
            <dd className="min-w-0 break-all font-mono text-xs text-gray-600">{project.id}</dd>
          </div>
        </dl>

        <form
          className="mt-5"
          onSubmit={(event) => {
            event.preventDefault();
            if (isConfirmed && !isDeleting) onConfirm();
          }}
        >
          <label htmlFor="project-delete-confirmation" className="text-sm font-medium text-gray-800">
            输入项目名称 <span className="font-semibold text-red-600">{project.title}</span> 以确认
          </label>
          <input
            id="project-delete-confirmation"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            autoFocus
            autoComplete="off"
            disabled={isDeleting}
            className="mt-2 min-h-10 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 outline-none transition-[border-color,box-shadow] duration-150 focus:border-red-400 focus:ring-2 focus:ring-red-100 disabled:cursor-not-allowed disabled:bg-gray-50"
          />
          {errorMessage && (
            <p className="mt-2 text-pretty text-sm text-red-600" role="alert">
              {errorMessage}
            </p>
          )}

          <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            <button
              type="button"
              onClick={onCancel}
              disabled={isDeleting}
              className="min-h-10 rounded-lg px-4 text-sm font-medium text-gray-700 shadow-[0_0_0_1px_rgba(0,0,0,0.10)] transition-[background-color,box-shadow,scale] duration-150 ease-out hover:bg-gray-50 hover:shadow-[0_0_0_1px_rgba(0,0,0,0.16)] active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={!isConfirmed || isDeleting}
              className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-red-600 pl-4 pr-3.5 text-sm font-semibold text-white transition-[background-color,scale] duration-150 ease-out hover:bg-red-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-45 disabled:active:scale-100"
            >
              {isDeleting ? <RefreshCw size={16} className="animate-spin" aria-hidden="true" /> : <Trash2 size={16} aria-hidden="true" />}
              {isDeleting ? '正在删除全部数据…' : '永久删除'}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

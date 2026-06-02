import { useEffect, useRef, useState, type ReactNode } from 'react';
import { FileText } from 'lucide-react';

const DEFAULT_BINDER_WIDTH = 250;
const MIN_BINDER_WIDTH = 220;
const MAX_BINDER_WIDTH = 420;
const MIN_EDITOR_WIDTH = 360;

interface WorkspaceShellProps {
  renderBinder: (closeBinder: () => void) => ReactNode;
  workspacePanel?: ReactNode;
  sidePanel?: ReactNode;
  dialogs?: ReactNode;
  children: ReactNode;
}

export function WorkspaceShell({
  renderBinder,
  workspacePanel,
  sidePanel,
  dialogs,
  children,
}: WorkspaceShellProps) {
  const [mobileBinderOpen, setMobileBinderOpen] = useState(false);
  const [binderWidth, setBinderWidth] = useState(DEFAULT_BINDER_WIDTH);
  const [isResizingBinder, setIsResizingBinder] = useState(false);
  const shellRef = useRef<HTMLDivElement>(null);
  const closeBinder = () => setMobileBinderOpen(false);

  useEffect(() => {
    if (!isResizingBinder) return;

    const clampWidth = (width: number) => {
      const shellWidth = shellRef.current?.getBoundingClientRect().width ?? window.innerWidth;
      const maxWidth = Math.max(MIN_BINDER_WIDTH, Math.min(MAX_BINDER_WIDTH, shellWidth - MIN_EDITOR_WIDTH));
      return Math.min(Math.max(width, MIN_BINDER_WIDTH), maxWidth);
    };

    const handleMouseMove = (event: MouseEvent) => {
      const shellLeft = shellRef.current?.getBoundingClientRect().left ?? 0;
      setBinderWidth(clampWidth(event.clientX - shellLeft));
    };

    const handleMouseUp = () => setIsResizingBinder(false);
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isResizingBinder]);

  const resizeBinderBy = (delta: number) => {
    const shellWidth = shellRef.current?.getBoundingClientRect().width ?? window.innerWidth;
    const maxWidth = Math.max(MIN_BINDER_WIDTH, Math.min(MAX_BINDER_WIDTH, shellWidth - MIN_EDITOR_WIDTH));
    setBinderWidth((width) => Math.min(Math.max(width + delta, MIN_BINDER_WIDTH), maxWidth));
  };

  return (
    <div ref={shellRef} className="h-full w-full flex relative">
      {mobileBinderOpen && (
        <div
          className="md:hidden fixed inset-0 bg-black/30 z-40"
          onClick={closeBinder}
        />
      )}
      <div className={`
        ${mobileBinderOpen ? 'fixed inset-y-0 left-0 z-50 w-[82vw] max-w-[320px]' : 'hidden'}
        md:relative md:block md:max-w-none md:flex-shrink-0
      `}
        style={mobileBinderOpen ? undefined : { width: binderWidth }}
      >
        {renderBinder(closeBinder)}
      </div>
      <div
        role="separator"
        aria-label="调整目录宽度"
        aria-orientation="vertical"
        aria-valuemin={MIN_BINDER_WIDTH}
        aria-valuemax={MAX_BINDER_WIDTH}
        aria-valuenow={binderWidth}
        tabIndex={0}
        onMouseDown={(event) => {
          event.preventDefault();
          setIsResizingBinder(true);
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowLeft') {
            event.preventDefault();
            resizeBinderBy(-16);
          }
          if (event.key === 'ArrowRight') {
            event.preventDefault();
            resizeBinderBy(16);
          }
        }}
        className={`hidden md:block w-1.5 flex-shrink-0 cursor-col-resize bg-gray-100 hover:bg-pink-100 focus:outline-none focus:ring-2 focus:ring-pink-300 ${
          isResizingBinder ? 'bg-pink-200' : ''
        }`}
      />
      <div className="flex-1 min-w-0 relative flex flex-col">
        <button
          type="button"
          aria-label="打开项目目录"
          onClick={() => setMobileBinderOpen(true)}
          className="md:hidden fixed bottom-4 left-4 z-30 w-12 h-12 rounded-full bg-pink-500 text-white shadow-lg flex items-center justify-center"
        >
          <FileText size={20} />
        </button>
        {workspacePanel && (
          <div className="flex-shrink-0 border-b border-pink-100 bg-white">
            {workspacePanel}
          </div>
        )}
        {children}
      </div>
      {sidePanel}
      {dialogs}
    </div>
  );
}

import { useState, type ReactNode } from 'react';
import { FileText } from 'lucide-react';

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
  const closeBinder = () => setMobileBinderOpen(false);

  return (
    <div className="h-full w-full flex relative">
      {mobileBinderOpen && (
        <div
          className="md:hidden fixed inset-0 bg-black/30 z-40"
          onClick={closeBinder}
        />
      )}
      <div className={`
        ${mobileBinderOpen ? 'fixed inset-y-0 left-0 z-50' : 'hidden'}
        md:relative md:block
      `}>
        {renderBinder(closeBinder)}
      </div>
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

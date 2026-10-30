import { useEffect } from 'react';

import { useRegisterDraftSave } from '../../hooks/project/draftSaves';

export function useMemoirEditGuard({ dirty, saving, save, onUnsavedChange }: {
  dirty: boolean;
  saving: boolean;
  save: () => Promise<void>;
  onUnsavedChange?: (dirty: boolean) => void;
}) {
  useRegisterDraftSave(async () => {
    if (saving) throw new Error('正在保存，请稍后再离开');
    if (dirty) await save();
  }, () => dirty || saving);

  useEffect(() => {
    onUnsavedChange?.(dirty || saving);
  }, [dirty, saving, onUnsavedChange]);
  useEffect(() => () => onUnsavedChange?.(false), [onUnsavedChange]);

  return () => !saving && (!dirty || window.confirm('当前编辑内容尚未保存，确定放弃修改吗？'));
}

import { useCallback, useRef, useState } from 'react';

export interface TextSelection {
  start: number;
  end: number;
}

export interface TextSnapshot {
  text: string;
  selection: TextSelection;
}

export interface TextEditOptions {
  before?: TextSelection;
  after?: TextSelection;
  group?: string;
}

const HISTORY_LIMIT = 100;
const TYPING_GROUP_MS = 1000;

function snapshot(text: string, selection = { start: text.length, end: text.length }): TextSnapshot {
  return { text, selection };
}

export function useTextHistory() {
  const history = useRef({
    past: [] as TextSnapshot[],
    present: snapshot(''),
    future: [] as TextSnapshot[],
    group: undefined as { name: string; time: number } | undefined,
  });
  const [view, setView] = useState({ text: '', canUndo: false, canRedo: false });

  const publish = useCallback(() => {
    const { past, present, future } = history.current;
    setView({ text: present.text, canUndo: past.length > 0, canRedo: future.length > 0 });
  }, []);

  const breakGroup = useCallback(() => { history.current.group = undefined; }, []);

  const setSelection = useCallback((selection: TextSelection) => {
    const current = history.current;
    if (selection.start !== current.present.selection.start || selection.end !== current.present.selection.end) {
      if (current.group?.name !== 'composition') current.group = undefined;
      current.present = { ...current.present, selection };
    }
  }, []);

  const setText = useCallback((text: string, options: TextEditOptions = {}) => {
    const current = history.current;
    if (options.before) setSelection(options.before);
    if (text === current.present.text) return;
    const now = Date.now();
    const merge = options.group && current.group?.name === options.group
      && (options.group === 'composition' || now - current.group.time < TYPING_GROUP_MS);
    if (!merge) current.past = [...current.past.slice(-(HISTORY_LIMIT - 1)), current.present];
    current.present = snapshot(text, options.after);
    current.future = [];
    current.group = options.group ? { name: options.group, time: now } : undefined;
    publish();
  }, [publish, setSelection]);

  const reset = useCallback((text: string) => {
    history.current = { past: [], present: snapshot(text), future: [], group: undefined };
    publish();
  }, [publish]);

  const undo = useCallback(() => {
    const current = history.current;
    const previous = current.past.pop();
    if (!previous) return null;
    current.future.push(current.present);
    current.present = previous;
    current.group = undefined;
    publish();
    return previous;
  }, [publish]);

  const redo = useCallback(() => {
    const current = history.current;
    const next = current.future.pop();
    if (!next) return null;
    current.past.push(current.present);
    current.present = next;
    current.group = undefined;
    publish();
    return next;
  }, [publish]);

  return { ...view, setText, reset, undo, redo, setSelection, breakGroup };
}

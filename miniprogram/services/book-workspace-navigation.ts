export type BookWorkspaceNavigationTarget = "chapters" | "tools";

let pendingTarget: BookWorkspaceNavigationTarget | null = null;

export function writeBookWorkspaceNavigationTarget(target: BookWorkspaceNavigationTarget): void {
  pendingTarget = target;
}

export function takeBookWorkspaceNavigationTarget(): BookWorkspaceNavigationTarget | null {
  const target = pendingTarget;
  pendingTarget = null;
  return target;
}

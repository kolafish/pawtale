export function isProjectDeletionConfirmationValid(projectTitle: string, confirmation: string): boolean {
  return projectTitle.trim().length > 0 && confirmation.trim() === projectTitle.trim();
}

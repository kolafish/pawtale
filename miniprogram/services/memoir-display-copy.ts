export function memoirStoryTitle(projectTitle: string): string {
  const normalized = projectTitle.trim();
  if (!normalized) {
    return "";
  }
  if (normalized.endsWith("主题")) {
    const subject = normalized.slice(0, -"主题".length).trim();
    return subject ? `${subject}的故事` : "这段回忆";
  }
  return normalized;
}

export function memoirHomepageTitle(
  formalBookTitle: string,
  projectTitle: string,
): string {
  return formalBookTitle.trim() || projectTitle.trim() || "还没有回忆录";
}

export function memoirStorySubject(projectTitle: string): string {
  const displayTitle = memoirStoryTitle(projectTitle);
  if (displayTitle.endsWith("的故事")) {
    return displayTitle.slice(0, -"的故事".length).trim();
  }
  if (displayTitle.endsWith("的回忆")) {
    return displayTitle.slice(0, -"的回忆".length).trim();
  }
  return "";
}

export function memoirHomePrompt(projectTitle: string): string {
  const subject = memoirStorySubject(projectTitle);
  if (subject) {
    return `不用讲完整，可以先说一件和${subject}有关的小事。`;
  }
  return "不用讲完整，先说一件您想起来的事。";
}

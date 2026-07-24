const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

function pageTemplate(page) {
  return fs.readFileSync(
    path.join(__dirname, `../pages/${page}/index.wxml`),
    "utf8",
  );
}

function assertViewAction(template, className) {
  const escapedClass = className.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const viewPattern = new RegExp(
    `<view\\b(?=[^>]*class="[^"]*\\b${escapedClass}\\b[^"]*")(?=[^>]*aria-role="button")[^>]*>`,
  );
  const buttonPattern = new RegExp(
    `<button\\b[^>]*class="[^"]*\\b${escapedClass}\\b[^"]*"`,
  );

  assert.match(template, viewPattern, `${className} should use a semantic view action`);
  assert.doesNotMatch(template, buttonPattern, `${className} should not use a native button shell`);
}

test("layout-critical row actions avoid native button shrink wrapping", () => {
  const expectedViewActions = {
    login: [
      "primary-button",
      "mode-button",
      "secondary-button",
      "skip-profile-button",
    ],
    transcripts: [
      "login-button",
      "empty-secondary-button",
      "load-more",
    ],
    "topic-map": [
      "topic-start-action",
      "topic-choice-action",
    ],
    "book-completion": [
      "book-ready-action",
      "recheck-book-action",
    ],
    "book-reader": [
      "toc-row",
      "reader-top-action",
    ],
    "book-export": [
      "state-action",
      "settings-toggle",
      "save-design-button",
    ],
    projects: [
      "primary-button",
    ],
    me: [
      "primary-button",
      "overview-button",
      "logout-button",
    ],
    "image-assets": [
      "asset-details-toggle",
    ],
    "transcript-detail": [
      "login-entry-action",
      "edit-reference-toggle",
      "repair-button",
      "danger-button",
    ],
    "voice-chat": [
      "completion-issue-continue",
      "history-toggle",
    ],
  };

  for (const [page, classNames] of Object.entries(expectedViewActions)) {
    const template = pageTemplate(page);
    for (const className of classNames) {
      assertViewAction(template, className);
    }
  }
});

test("native-only WeChat controls stay native buttons", () => {
  const loginTemplate = pageTemplate("login");
  const homeTemplate = pageTemplate("home");

  assert.match(loginTemplate, /<button\b[^>]*open-type="chooseAvatar"/);
  assert.match(homeTemplate, /<button\b[^>]*open-type="share"/);
});

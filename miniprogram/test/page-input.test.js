require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  datasetChoice,
  datasetNumber,
  datasetString,
  pageUrl,
  pickerIndex,
  queryChoice,
  queryParam,
  safeDecodeURIComponent,
} = require("../services/page-input.ts");

test("page input decodes query values without throwing on malformed encoding", () => {
  assert.equal(safeDecodeURIComponent("project%2F1"), "project/1");
  assert.equal(safeDecodeURIComponent("%E0%A4%A"), "%E0%A4%A");
  assert.equal(queryParam({ projectId: "project%2F1" }, "projectId"), "project/1");
  assert.equal(queryParam({}, "projectId"), "");
});

test("page input constrains query and dataset enum choices", () => {
  assert.equal(queryChoice({ tab: "sources" }, "tab", ["content", "sources"], "content"), "sources");
  assert.equal(queryChoice({ tab: "other" }, "tab", ["content", "sources"], "content"), "content");
  assert.equal(
    datasetChoice({ currentTarget: { dataset: { direction: "up" } } }, "direction", ["up", "down"], "down"),
    "up",
  );
  assert.equal(
    datasetChoice({ currentTarget: { dataset: { direction: "left" } } }, "direction", ["up", "down"], "down"),
    "down",
  );
});

test("page input reads dataset values and picker indexes consistently", () => {
  assert.equal(datasetString({ currentTarget: { dataset: { id: 123 } } }, "id"), "123");
  assert.equal(datasetString({ currentTarget: { dataset: {} } }, "id"), "");
  assert.equal(datasetNumber({ currentTarget: { dataset: { speed: "1.25" } } }, "speed"), 1.25);
  assert.equal(datasetNumber({ currentTarget: { dataset: { speed: "" } } }, "speed", 1), 1);
  assert.equal(datasetNumber({ currentTarget: { dataset: { speed: "fast" } } }, "speed", 1), 1);
  assert.equal(pickerIndex({ currentTarget: { dataset: {} }, detail: { value: "2" } }), 2);
  assert.equal(pickerIndex({ currentTarget: { dataset: {} }, detail: { value: "" } }), -1);
  assert.equal(pickerIndex({ currentTarget: { dataset: {} }, detail: { value: -1 } }), -1);
  assert.equal(pickerIndex({ currentTarget: { dataset: {} }, detail: { value: "bad" } }), -1);
});

test("pageUrl builds encoded mini program page URLs", () => {
  assert.equal(
    pageUrl("/pages/book-chapter/index", { projectId: "project/1", chapterId: "chapter 1", empty: "" }),
    "/pages/book-chapter/index?projectId=project%2F1&chapterId=chapter%201",
  );
  assert.equal(pageUrl("/pages/project-edit/index"), "/pages/project-edit/index");
});

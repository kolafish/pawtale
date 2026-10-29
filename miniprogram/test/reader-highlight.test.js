require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const { readerParagraphRanges, sentenceHighlight } = require("../pages/book-reader/reader-highlight.ts");

test("audio offsets map repeated paragraphs within their source chapter, with Unicode and CRLF intact", () => {
  const paragraph = "她说：‘记得🌿吗？’我点点头。";
  const content = `开头\r\n\r\n${paragraph}\r\n${paragraph}`;
  const chapters = [0, 1].map(index => ({ content, sections: [{ id: `s${index}`, content: `${paragraph}\r\n${paragraph}`, paragraphs: [paragraph, paragraph] }] }));
  const source = `🌸书名\n\n${content}\n\n${content}`;
  const ranges = readerParagraphRanges(source, chapters, []);
  assert.deepEqual(ranges.map(range => [range.chapterIndex, range.sectionId, range.paragraphIndex]),
    [[0, "s0", 0], [0, "s0", 1], [1, "s1", 0], [1, "s1", 1]]);
  for (const range of ranges) assert.equal(Array.from(source).slice(range.start, range.end).join(""), paragraph);
  const highlight = sentenceHighlight(ranges[3], ranges[3].start + 8);
  assert.equal(highlight.current, "她说：‘记得🌿吗？’");
  assert.equal(highlight.before + highlight.current + highlight.after, paragraph);
  const scoped = readerParagraphRanges(content, chapters, [], 1);
  assert.deepEqual(scoped.map(range => range.chapterIndex), [1, 1]);
  assert.equal(scoped[0].start, Array.from("开头\r\n\r\n").length);
});

test("legacy unsectioned chapters and flat pages highlight their existing text without inventing structure", () => {
  const pages = ["🌿第一句。", "第二页的句子。"];
  const source = pages.join("");
  const ranges = readerParagraphRanges(source, [], pages);
  assert.equal(ranges[1].start, Array.from(pages[0]).length);
  assert.equal(sentenceHighlight(ranges[1], ranges[1].start).current, pages[1]);
  const chapter = { content: source, sections: [] };
  const plain = readerParagraphRanges(source, [chapter], []);
  assert.equal(sentenceHighlight(plain[0], ranges[1].start).current, pages[1]);
  assert.deepEqual(readerParagraphRanges("另一个版本。", [chapter], []), []);
});

const { after } = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function fileSystemWx() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "pawtale-export-test-"));
  after(() => fs.rmSync(root, { recursive: true, force: true }));
  return {
    env: { USER_DATA_PATH: root },
    getFileSystemManager: () => ({
      statSync: (file) => { const stat = fs.statSync(file); return { size: stat.size, lastModifiedTime: stat.mtimeMs }; },
      readdirSync: fs.readdirSync,
      mkdirSync: (file, recursive) => fs.mkdirSync(file, { recursive }),
      copyFileSync: (source, dest) => fs.writeFileSync(dest, fs.existsSync(source) ? fs.readFileSync(source) : "%PDF-test\n%%EOF\n"),
      renameSync: fs.renameSync,
      unlinkSync: fs.unlinkSync,
      rmdirSync: (file, recursive) => fs.rmSync(file, { recursive }),
    }),
  };
}
module.exports = { fileSystemWx };

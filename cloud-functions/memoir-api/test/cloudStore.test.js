const assert = require("node:assert/strict");
const test = require("node:test");

const { CloudStore } = require("../src/store");

class FakeQuery {
  constructor(rows, offset = 0, count = null) {
    this.rows = rows;
    this.offset = offset;
    this.count = count;
  }

  skip(offset) {
    return new FakeQuery(this.rows, offset, this.count);
  }

  limit(count) {
    return new FakeQuery(this.rows, this.offset, count);
  }

  orderBy() {
    throw new Error("CloudStore should sort rows in memory");
  }

  async get() {
    const end = this.count ? this.offset + this.count : undefined;
    return {
      data: this.rows.slice(this.offset, end).map((row) => ({ ...row })),
    };
  }
}

class FakeCollection extends FakeQuery {
  constructor(rows) {
    super(rows);
    this.whereCalls = [];
  }

  where(filters) {
    if (Object.keys(filters).length === 0) {
      throw new Error("empty where should not be called");
    }
    this.whereCalls.push(filters);
    return new FakeQuery(this.rows.filter((row) => Object.entries(filters).every(([key, value]) => {
      if (key === "_id") {
        return row._id === value || row.id === value;
      }
      return row[key] === value;
    })));
  }
}

function fakeDatabase(seed) {
  const collections = new Map(
    Object.entries(seed).map(([name, rows]) => [name, new FakeCollection(rows)]),
  );
  return {
    collection(name) {
      if (!collections.has(name)) {
        collections.set(name, new FakeCollection([]));
      }
      return collections.get(name);
    },
  };
}

test("CloudStore.findMany avoids empty where and sorts in memory", async () => {
  const db = fakeDatabase({
    things: Array.from({ length: 105 }, (_, index) => ({
      _id: `row_${index}`,
      openid: "openid_a",
      score: index,
    })),
  });
  const store = new CloudStore(db);

  const rows = await store.findMany("things", {}, {
    orderBy: [{ field: "score", direction: "desc" }],
    limit: 2,
  });

  assert.deepEqual(rows.map((row) => row.id), ["row_104", "row_103"]);
  assert.deepEqual(db.collection("things").whereCalls, []);
});

test("CloudStore.findMany sanitizes filters and normalizes ids", async () => {
  const db = fakeDatabase({
    things: [
      { _id: "row_a", openid: "openid_a", created_at: "2026-01-02T00:00:00.000Z" },
      { _id: "row_b", openid: "openid_b", created_at: "2026-01-01T00:00:00.000Z" },
      { _id: "row_c", openid: "openid_a", created_at: "2026-01-01T00:00:00.000Z" },
    ],
  });
  const store = new CloudStore(db);

  const rows = await store.findMany("things", {
    openid: "openid_a",
    ignored: undefined,
  }, {
    orderBy: [{ field: "created_at", direction: "asc" }],
  });

  assert.deepEqual(db.collection("things").whereCalls, [{ openid: "openid_a" }]);
  assert.deepEqual(rows.map((row) => row.id), ["row_c", "row_a"]);
});

const assert = require("node:assert/strict");
const test = require("node:test");

const { CloudStore } = require("../src/store");

class FakeQuery {
  constructor(rows, tracker = null, orderBy = [], count = null, offset = 0) {
    this.rows = rows;
    this.tracker = tracker || { orderByCalls: [], limitCalls: [], skipCalls: [] };
    this.orderByRules = orderBy;
    this.count = count;
    this.offset = offset;
  }

  limit(count) {
    this.tracker.limitCalls.push(count);
    return new FakeQuery(this.rows, this.tracker, this.orderByRules, count, this.offset);
  }

  skip(offset) {
    this.tracker.skipCalls.push(offset);
    return new FakeQuery(this.rows, this.tracker, this.orderByRules, this.count, offset);
  }

  orderBy(field, direction = "asc") {
    this.tracker.orderByCalls.push({ field, direction });
    return new FakeQuery(this.rows, this.tracker, [
      ...this.orderByRules,
      { field, direction },
    ], this.count, this.offset);
  }

  async get() {
    const sorted = [...this.rows].sort((left, right) => {
      for (const order of this.orderByRules) {
        if (left[order.field] === right[order.field]) {
          continue;
        }
        const result = left[order.field] > right[order.field] ? 1 : -1;
        return order.direction === "desc" ? -result : result;
      }
      return 0;
    });
    return {
      data: sorted.slice(this.offset, this.offset + (this.count || 100)).map((row) => ({ ...row })),
    };
  }
}

class FakeCollection extends FakeQuery {
  constructor(rows) {
    const tracker = { orderByCalls: [], limitCalls: [], skipCalls: [] };
    super(rows, tracker);
    this.whereCalls = [];
    this.tracker = tracker;
  }

  where(filters) {
    if (Object.keys(filters).length === 0) {
      throw new Error("empty where should not be called");
    }
    this.whereCalls.push(filters);
    const filtered = this.rows.filter((row) => Object.entries(filters).every(([key, value]) => {
      if (key === "_id") {
        return row._id === value || row.id === value;
      }
      return row[key] === value;
    }));
    return new FakeQuery(filtered, this.tracker);
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

test("CloudStore.findMany avoids empty where and delegates order and limit", async () => {
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
  assert.deepEqual(db.collection("things").tracker.orderByCalls, [{ field: "score", direction: "desc" }]);
  assert.deepEqual(db.collection("things").tracker.limitCalls, [2]);
  assert.deepEqual(db.collection("things").tracker.skipCalls, []);
});

test("CloudStore.findMany paginates unbounded reads using database order", async () => {
  const db = fakeDatabase({
    things: Array.from({ length: 205 }, (_, index) => ({
      _id: `row_${index}`,
      score: index,
    })),
  });
  const store = new CloudStore(db);

  const rows = await store.findMany("things", {}, {
    orderBy: [{ field: "score", direction: "desc" }],
  });

  assert.equal(rows.length, 205);
  assert.equal(rows[0].id, "row_204");
  assert.equal(rows[204].id, "row_0");
  assert.deepEqual(db.collection("things").tracker.orderByCalls, [{ field: "score", direction: "desc" }]);
  assert.deepEqual(db.collection("things").tracker.limitCalls, [100, 100, 100]);
  assert.deepEqual(db.collection("things").tracker.skipCalls, [100, 200]);
});

test("CloudStore.findMany filters and normalizes ids", async () => {
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
  }, {
    orderBy: [{ field: "created_at", direction: "asc" }],
  });

  assert.deepEqual(db.collection("things").whereCalls, [{ openid: "openid_a" }]);
  assert.deepEqual(rows.map((row) => row.id), ["row_c", "row_a"]);
});

test("CloudStore.findMany rejects undefined filter values", async () => {
  const db = fakeDatabase({
    things: [
      { _id: "row_a", openid: "openid_a" },
    ],
  });
  const store = new CloudStore(db);

  await assert.rejects(
    () => store.findMany("things", { openid: "openid_a", _id: undefined }),
    /undefined filter value for "_id"/,
  );
  assert.deepEqual(db.collection("things").whereCalls, []);
});

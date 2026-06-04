class MemoryStore {
  constructor(seed = {}) {
    this.rows = new Map();
    this.nextId = 1;
    for (const [collection, rows] of Object.entries(seed)) {
      this.rows.set(collection, rows.map((row) => ({ ...row })));
    }
  }

  collectionRows(collection) {
    if (!this.rows.has(collection)) {
      this.rows.set(collection, []);
    }
    return this.rows.get(collection);
  }

  matches(row, filters) {
    return Object.entries(filters).every(([key, value]) => {
      if (key === "_id") {
        return row._id === value || row.id === value;
      }
      return row[key] === value;
    });
  }

  async findMany(collection, filters = {}, options = {}) {
    let rows = this.collectionRows(collection)
      .filter((row) => this.matches(row, filters))
      .map((row) => ({ ...row, id: row.id || row._id }));

    for (const order of [...(options.orderBy || [])].reverse()) {
      const direction = order.direction === "desc" ? -1 : 1;
      rows = rows.sort((left, right) => {
        if (left[order.field] === right[order.field]) {
          return 0;
        }
        return left[order.field] > right[order.field] ? direction : -direction;
      });
    }

    return options.limit ? rows.slice(0, options.limit) : rows;
  }

  async findOne(collection, filters = {}, options = {}) {
    const rows = await this.findMany(collection, filters, {
      ...options,
      limit: 1,
    });
    return rows[0] || null;
  }

  async getById(collection, id) {
    return this.findOne(collection, { _id: id });
  }

  async insert(collection, data) {
    const id = `mem_${this.nextId++}`;
    const row = {
      ...data,
      _id: id,
      id,
    };
    this.collectionRows(collection).push(row);
    return { ...row };
  }

  async updateById(collection, id, data) {
    const rows = this.collectionRows(collection);
    const index = rows.findIndex((row) => row._id === id || row.id === id);
    if (index === -1) {
      return null;
    }
    rows[index] = {
      ...rows[index],
      ...data,
    };
    return { ...rows[index], id: rows[index].id || rows[index]._id };
  }
}

module.exports = {
  MemoryStore,
};

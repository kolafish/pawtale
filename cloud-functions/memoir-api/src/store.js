function normalizeDoc(doc) {
  if (!doc) {
    return null;
  }

  const id = doc.id || doc._id;
  return {
    ...doc,
    id,
  };
}

const CLOUD_PAGE_SIZE = 100;

function cleanFilters(filters = {}) {
  const entries = Object.entries(filters || {});
  const undefinedFilter = entries.find(([, value]) => value === undefined);
  if (undefinedFilter) {
    throw new Error(`CloudStore received undefined filter value for "${undefinedFilter[0]}"`);
  }
  return Object.fromEntries(entries);
}

function compareValues(left, right) {
  if (left === right) {
    return 0;
  }
  return left > right ? 1 : -1;
}

function isMissing(value) {
  return value === undefined || value === null;
}

function sortRows(rows, orderBy = []) {
  if (!orderBy.length) {
    return rows;
  }

  return [...rows].sort((left, right) => {
    for (const order of orderBy) {
      const direction = order.direction === "desc" ? -1 : 1;
      const leftValue = left[order.field];
      const rightValue = right[order.field];
      const leftMissing = isMissing(leftValue);
      const rightMissing = isMissing(rightValue);
      if (leftMissing && rightMissing) {
        continue;
      }
      if (leftMissing) {
        return 1;
      }
      if (rightMissing) {
        return -1;
      }
      const result = compareValues(leftValue, rightValue);
      if (result !== 0) {
        return result * direction;
      }
    }
    return 0;
  });
}

class CloudStore {
  constructor(database) {
    this.database = database;
  }

  collection(name) {
    return this.database.collection(name);
  }

  async findMany(name, filters = {}, options = {}) {
    const sanitizedFilters = cleanFilters(filters);
    const collection = this.collection(name);
    const hasFilters = Object.keys(sanitizedFilters).length > 0;
    const baseQuery = hasFilters ? collection.where(sanitizedFilters) : collection;
    let rows = [];
    let offset = 0;

    while (true) {
      let query = baseQuery;
      if (offset > 0 && typeof query.skip === "function") {
        query = query.skip(offset);
      }
      if (typeof query.limit === "function") {
        query = query.limit(CLOUD_PAGE_SIZE);
      }

      const result = await query.get();
      const page = result.data || [];
      rows = rows.concat(page.map(normalizeDoc));

      if (page.length < CLOUD_PAGE_SIZE || typeof baseQuery.skip !== "function") {
        break;
      }
      offset += CLOUD_PAGE_SIZE;
    }

    rows = sortRows(rows, options.orderBy || []);
    return options.limit ? rows.slice(0, options.limit) : rows;
  }

  async findOne(name, filters = {}, options = {}) {
    const rows = await this.findMany(name, filters, {
      ...options,
      limit: 1,
    });
    return rows[0] || null;
  }

  async getById(name, id) {
    try {
      const result = await this.collection(name).doc(id).get();
      return normalizeDoc(result.data);
    } catch {
      return null;
    }
  }

  async insert(name, data) {
    const result = await this.collection(name).add({ data });
    return normalizeDoc({
      ...data,
      _id: result._id,
    });
  }

  async updateById(name, id, data) {
    await this.collection(name).doc(id).update({ data });
    return this.getById(name, id);
  }
}

module.exports = {
  CloudStore,
  normalizeDoc,
};

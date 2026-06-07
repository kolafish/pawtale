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

function cleanFilters(filters = {}) {
  const entries = Object.entries(filters || {});
  const undefinedFilter = entries.find(([, value]) => value === undefined);
  if (undefinedFilter) {
    throw new Error(`CloudStore received undefined filter value for "${undefinedFilter[0]}"`);
  }
  return Object.fromEntries(entries);
}

const CLOUD_PAGE_SIZE = 100;

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
    let query = hasFilters ? collection.where(sanitizedFilters) : collection;
    for (const order of options.orderBy || []) {
      query = query.orderBy(order.field, order.direction || "asc");
    }

    const requestedLimit = Number.isInteger(options.limit) && options.limit > 0
      ? options.limit
      : null;
    const pageSize = requestedLimit ? Math.min(requestedLimit, CLOUD_PAGE_SIZE) : CLOUD_PAGE_SIZE;
    const rows = [];
    let offset = 0;

    while (true) {
      let pageQuery = query;
      if (offset > 0) {
        pageQuery = pageQuery.skip(offset);
      }
      const result = await pageQuery.limit(pageSize).get();
      const pageRows = result.data || [];
      rows.push(...pageRows);

      if (pageRows.length < pageSize || (requestedLimit && rows.length >= requestedLimit)) {
        break;
      }
      offset += pageRows.length;
    }

    return rows.slice(0, requestedLimit || rows.length).map(normalizeDoc);
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

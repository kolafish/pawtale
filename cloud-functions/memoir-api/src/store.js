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

class CloudStore {
  constructor(database) {
    this.database = database;
  }

  collection(name) {
    return this.database.collection(name);
  }

  async findMany(name, filters = {}, options = {}) {
    let query = this.collection(name).where(filters);
    for (const order of options.orderBy || []) {
      query = query.orderBy(order.field, order.direction || "asc");
    }
    if (options.limit) {
      query = query.limit(options.limit);
    }
    const result = await query.get();
    return (result.data || []).map(normalizeDoc);
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

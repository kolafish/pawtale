function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function withTimeout(promise, timeoutMs, description) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new Error(`${description}超时`)),
        timeoutMs,
      );
    }),
  ]).finally(() => clearTimeout(timer));
}

async function waitForValue(readValue, predicate, options = {}) {
  const timeoutMs = options.timeoutMs ?? 20000;
  const intervalMs = options.intervalMs ?? 250;
  const description = options.description || "条件";
  const deadline = Date.now() + timeoutMs;
  let lastValue;
  while (Date.now() < deadline) {
    lastValue = await readValue();
    if (predicate(lastValue)) {
      return lastValue;
    }
    await sleep(intervalMs);
  }
  const describedValue = options.describeValue?.(lastValue);
  const suffix = describedValue === undefined ? "" : `；最后状态：${describedValue}`;
  throw new Error(`等待${description}超时${suffix}`);
}

async function readCurrentPage(miniProgram) {
  try {
    return await miniProgram.currentPage();
  } catch (error) {
    // Developer Tools can announce App.initialized before it has registered the
    // first simulator webview. Retry only that known startup race.
    if (String(error?.message || "").includes("getPageMetaByWebviewId")) {
      return null;
    }
    throw error;
  }
}

async function waitForCurrentPageReady(miniProgram, timeoutMs = 30000) {
  return waitForValue(
    () => readCurrentPage(miniProgram),
    (page) => Boolean(page?.path),
    {
      timeoutMs,
      description: "开发者工具注册首个页面",
      describeValue: (page) => page?.path || "无页面",
    },
  );
}

async function waitForCurrentPage(miniProgram, expectedPath, timeoutMs = 20000) {
  return waitForValue(
    () => readCurrentPage(miniProgram),
    (page) => page?.path === expectedPath,
    {
      timeoutMs,
      description: `页面 ${expectedPath}`,
      describeValue: (page) => page?.path || "无页面",
    },
  );
}

async function waitForPageData(page, predicate, description, timeoutMs = 20000) {
  return waitForValue(
    () => page.data(),
    predicate,
    {
      timeoutMs,
      description,
      describeValue: (data) => JSON.stringify({
        busy: data?.busy,
        error: data?.error,
        loading: data?.loading,
      }),
    },
  );
}

module.exports = {
  sleep,
  waitForCurrentPage,
  waitForCurrentPageReady,
  waitForPageData,
  waitForValue,
  withTimeout,
};

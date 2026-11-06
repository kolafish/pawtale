type PageMethods<TDefinition extends { data: object }> = Omit<TDefinition, "data">;

type PageContext<TData extends object, TDefinition extends { data: TData }> = PageMethods<TDefinition> & {
  data: TData;
  setData(data: Partial<TData>, callback?: () => void): void;
  selectComponent<T>(selector: string): T | null;
};

type PageDefinition<TData extends object, TDefinition extends { data: TData }> = {
  data: TData;
} & TDefinition & ThisType<PageContext<TData, TDefinition>>;

type PageRuntimeFactory = () => unknown;

const pageRuntimeFactories = new WeakMap<object, PageRuntimeFactory>();

export function pageRuntime<T>(factory: () => T): T {
  const placeholder = {};
  pageRuntimeFactories.set(placeholder, factory);
  return placeholder as T;
}

export function definePage<TData extends object>() {
  return function <TDefinition extends { data: TData }>(
    definition: PageDefinition<TData, TDefinition>,
  ): void {
    const registeredDefinition = { ...definition };
    const registeredRecord = registeredDefinition as unknown as Record<string, unknown>;
    // Keep page content outside the navigation component so selector queries and
    // existing component lifecycles retain their original page ownership.
    registeredRecord.onNavigationLayout = function (
      this: { setData(data: { navigationHeight: number }): void },
      event: { detail: { height: number } },
    ) {
      this.setData({ navigationHeight: event.detail.height });
    };
    const runtimeFields: Array<[string, PageRuntimeFactory]> = [];
    for (const [key, value] of Object.entries(definition)) {
      const factory = value && typeof value === "object"
        ? pageRuntimeFactories.get(value)
        : undefined;
      if (!factory) {
        continue;
      }
      runtimeFields.push([key, factory]);
      delete registeredRecord[key];
    }
    if (runtimeFields.length) {
      const originalOnLoad = registeredRecord.onLoad;
      const initializePageRuntime = function (this: Record<string, unknown>) {
        runtimeFields.forEach(([key, factory]) => {
          this[key] = factory();
        });
      };
      registeredRecord.__initializePageRuntime = initializePageRuntime;
      registeredRecord.onLoad = function (
        this: Record<string, unknown>,
        ...args: unknown[]
      ) {
        initializePageRuntime.call(this);
        if (typeof originalOnLoad === "function") {
          return originalOnLoad.apply(this, args);
        }
        return undefined;
      };
    }
    Page<TData, PageMethods<TDefinition>>(registeredDefinition);
  };
}

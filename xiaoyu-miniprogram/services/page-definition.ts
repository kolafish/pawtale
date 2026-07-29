type PageMethods<TDefinition extends { data: object }> = Omit<TDefinition, "data">;

type PageContext<TData extends object, TDefinition extends { data: TData }> =
  PageMethods<TDefinition> & {
    data: TData;
    setData(data: Partial<TData>): void;
  };

type PageDefinition<TData extends object, TDefinition extends { data: TData }> = {
  data: TData;
} & TDefinition & ThisType<PageContext<TData, TDefinition>>;

export function definePage<TData extends object>() {
  return function <TDefinition extends { data: TData }>(
    definition: PageDefinition<TData, TDefinition>,
  ): void {
    Page<TData, PageMethods<TDefinition>>(definition);
  };
}

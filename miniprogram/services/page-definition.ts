type PageContext<TData extends object, TMethods extends object> = TMethods & {
  data: TData;
  setData(data: Partial<TData>): void;
};

type PageDefinition<TData extends object, TMethods extends object> = TMethods & {
  data: TData;
} & ThisType<PageContext<TData, TMethods>>;

export function definePage<TData extends object>() {
  return function <TMethods extends object>(
    definition: PageDefinition<TData, TMethods>,
  ): void {
    Page<TData, TMethods>(definition);
  };
}

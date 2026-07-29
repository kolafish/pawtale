declare const wx: any;

declare function App<T extends object>(options: T): void;
declare function Page<TData extends object, TMethods extends object>(
  options: unknown,
): void;

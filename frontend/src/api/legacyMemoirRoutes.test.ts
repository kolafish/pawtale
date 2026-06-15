import { describe, expect, it } from 'vitest';

const removedMobileMemoirRoutes = [
  '/mobile/memoir/sessions/current',
  '/mobile/memoir/sessions/',
  '/mobile/memoir/memories/',
  '/mobile/memoir/open-questions',
  '/mobile/memoir/coverage',
  '/mobile/memoir/question-candidates',
  '/mobile/memoir/outputs',
];

describe('frontend memoir API boundary', () => {
  it('does not call removed legacy mobile memoir endpoints', () => {
    const sourceModules = import.meta.glob('../**/*.{ts,tsx}', {
      query: '?raw',
      import: 'default',
      eager: true,
    }) as Record<string, string>;

    const offenders = Object.entries(sourceModules)
      .filter(([path]) => !/\.test\.(ts|tsx)$/.test(path))
      .flatMap(([path, source]) => (
        removedMobileMemoirRoutes
          .filter((route) => source.includes(route))
          .map((route) => `${path} -> ${route}`)
      ));

    expect(offenders).toEqual([]);
  });
});

export const watchKeys = {
  all: ["watch-results"] as const,
  results: (query: object) => ["watch-results", query] as const,
};

// Vitest component-test setup: extends `expect` with jest-dom matchers
// (toBeInTheDocument, toHaveTextContent, ...) for every test file. The
// `/vitest` subpath imports `expect` from "vitest" directly and augments its
// TS types via module declaration merging, so this works regardless of the
// `globals` setting in vite.config.ts.
import "@testing-library/jest-dom/vitest";

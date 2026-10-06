import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// antd's responsive helpers need matchMedia, which jsdom doesn't provide.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

afterEach(cleanup);

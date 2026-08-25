import { defineConfig } from "vitest/config";
import TestDriver from "testdriverai/vitest";

export default defineConfig({
  test: {
    testTimeout: 900000,
    hookTimeout: 900000,
    maxConcurrency: 1,
    reporters: ["verbose", TestDriver()],
    setupFiles: ["testdriverai/vitest/setup"],
  },
});

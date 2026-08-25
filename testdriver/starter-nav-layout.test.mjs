import { expect, test } from "vitest";
import { TestDriver } from "testdriverai/vitest/hooks";

test("Starter navigation and production-line layout", async (context) => {
  const testdriver = TestDriver(context);

  await testdriver.provision.chrome({ url: "http://127.0.0.1:3000" });

  const shellIsStable = await testdriver.assert(
    "The OmniAgentOS Starter page has a top navigation landmark with an Agents link, followed by the goal form and the Planner, Workers, Critic, Verifier, and Deliverable production-line layout; ignore all changing status or metric values",
  );
  expect(shellIsStable).toBeTruthy();
});

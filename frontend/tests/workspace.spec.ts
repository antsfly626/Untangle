import { test, expect, type Page } from "@playwright/test";
import { consensus, session, withRisk } from "./fixtures";
import type { Session } from "../src/lib/contracts";
async function backend(page: Page, initial: Session) {
  let snapshot = initial;
  await page.route("http://localhost:8000/sessions**", async route => {
    const url = route.request().url();
    if (url.endsWith("/stream")) {
      await route.fulfill({ contentType: "text/event-stream", body: `event: session\ndata: ${JSON.stringify(snapshot)}\n\n` });
    } else if (url.endsWith("/messages")) {
      snapshot = consensus(); await route.fulfill({ json: snapshot });
    } else await route.fulfill({ json: snapshot });
  });
  await page.goto("/");
  await page.getByLabel("Existing session ID").fill(initial.id);
  await page.getByRole("button", { name: "Load session" }).click();
  await expect(page.getByRole("heading", { name: initial.plan.idea, exact: true })).toBeVisible();
  return { update: (next: Session) => { snapshot = next; } };
}
test("illustrative project intelligence session is shown by default without backend requests", async ({ page }) => {
  const sessionRequests: string[] = [];
  page.on("request", request => {
    if (request.url().includes("/sessions")) sessionRequests.push(request.url());
  });
  await page.goto("/");
  await expect(page.getByRole("note").filter({ hasText: "Example session · illustrative data." })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Create a collaborative project intelligence", exact: true })).toBeVisible();
  await expect(page.locator(".project-node")).toHaveCount(12);
  await expect(page.locator(".project-node").filter({ hasText: "Constraints" })).toBeVisible();
  await expect(page.locator(".project-node").filter({ hasText: "Budget: $2500" })).toBeVisible();
  await expect(page.getByText("Private team workspace", { exact: true })).toBeVisible();
  await expect(page.getByText("This sample is shown locally and is not connected to the backend.", { exact: false })).toBeVisible();
  await expect(page.getByText("Live updates are connecting or reconnecting.", { exact: false })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Knowledge Management and Knowledge Management Systems/ })).toBeVisible();
  await expect(page.getByRole("region", { name: "Research evidence" }).getByText(/Illustrative static example; no live retrieval or review was performed/)).toBeVisible();
  await expect(page.getByText("Example follow-up", { exact: true })).toBeVisible();
  const board = page.getByRole("region", { name: "Task board" });
  await expect(board.getByRole("note")).toContainText("Illustrative example tasks only");
  await expect(board.getByRole("heading", { name: "Define the shared project context" })).toBeVisible();
  await expect(board.getByRole("heading", { name: "Build the decision history" })).toBeVisible();
  await expect(board.getByRole("heading", { name: "Add private workspace access" })).toBeVisible();
  await expect(board.getByRole("heading", { name: "Prototype contextual project search" })).toBeVisible();
  await board.getByLabel("Status for Build the decision history").selectOption("Done");
  await expect(board.locator(".task-column").nth(3).locator(".task").filter({ hasText: "Build the decision history" })).toBeVisible();
  await page.locator(".agent-decisions > summary").click();
  await page.locator(".agent-decisions summary").filter({ hasText: "Round 1 · evidence · critique · abstain" }).click();
  await expect(page.getByText("Proposal: example-proposal-1", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Answer or revise constraints")).toHaveCount(0);
  expect(sessionRequests).toEqual([]);
});
test("initial project renders structured proposed nodes and supports creation", async ({ page }) => {
  await page.route("http://localhost:8000/sessions**", route => route.fulfill({ json: session() }));
  await page.goto("/");
  await page.getByLabel("Project idea and constraints").fill("Study assistant for two developers, one week");
  await page.getByRole("button", { name: "Start project" }).click();
  await expect(page.locator(".project-node").filter({ hasText: "Document uploads" })).toBeVisible();
  await expect(page.getByText("No approved tasks yet.", { exact: false })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath("workspace.png"), fullPage: true });
});
test("mind map shows user topics without agent discussion results", async ({ page }) => {
  await backend(page, withRisk());
  await page.locator(".project-node").filter({ hasText: "Document uploads" }).click();
  const details = page.getByRole("region", { name: "Node details" });
  await expect(details.getByRole("heading", { name: "Document uploads" })).toBeVisible();
  await expect(details).not.toContainText("proposal-1");
  await expect(page.locator(".project-node")).toHaveCount(2);
  await expect(page.locator(".map-section")).not.toContainText(/critique|escalated|reject|decision details/);
  await expect(page.getByRole("link", { name: /Mock retrieved paper/ })).toHaveAttribute("href", "https://example.org/test-paper");
  await expect(page.getByText("Mixed", { exact: true })).toBeVisible();
  await expect(page.getByText("No approved tasks yet.", { exact: false })).toBeVisible();
});
test("revision shows approved tasks without adding debate nodes to the map", async ({ page }) => {
  await backend(page, withRisk());
  await page.getByLabel("Answer or revise constraints").fill("Delete uploads after processing");
  await page.getByRole("button", { name: "Send update" }).click();
  await expect(page.getByRole("heading", { name: "Implement temporary uploads" })).toBeVisible();
  await page.getByLabel("Status for Implement temporary uploads").selectOption("Done");
  await expect(page.locator(".task-column").filter({ has: page.getByRole("heading", { name: "Done", exact: true }) }).getByText("Implement temporary uploads")).toBeVisible();
  await expect(page.locator(".project-node")).toHaveCount(2);
});
test("named session SSE updates the map without manual refresh", async ({ page }) => {
  const mock = await backend(page, session());
  const next = withRisk();
  next.plan.nodes.push({ ...next.plan.nodes[0], id: "registration", label: "Workshop registration" });
  mock.update(next);
  await expect(page.locator(".project-node").filter({ hasText: "Workshop registration" })).toBeVisible({ timeout: 15000 });
  await expect(page.locator(".project-node")).toHaveCount(3);
});
test("unavailable research remains a warning without invented papers", async ({ page }) => {
  const s = withRisk(); s.plan.negotiation[0].evidence = { bad: "metadata" };
  await backend(page, s);
  await expect(page.getByText(/Invalid or incomplete Evidence metadata/)).toBeVisible();
  await expect(page.getByRole("link", { name: /Mock retrieved paper/ })).toHaveCount(0);
});
test("API failures are recoverable and do not display fake results", async ({ page }) => {
  await page.route("http://localhost:8000/sessions**", route => route.fulfill({ status: 503 }));
  await page.goto("/"); await page.getByLabel("Existing session ID").fill("missing");
  await page.getByRole("button", { name: "Load session" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Backend request failed" })).toContainText("503");
  await expect(page.getByRole("button", { name: "Load session" })).toBeEnabled();
});
test("dragged node positions survive refreshed project snapshots", async ({ page }) => {
  await backend(page, session());
  const feature = page.locator(".react-flow__node").filter({ hasText: "Document uploads" });
  await expect(feature).toBeVisible();
  await feature.scrollIntoViewIfNeeded();
  const before = await feature.getAttribute("style");
  const box = (await feature.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down(); await page.mouse.move(box.x + box.width / 2 + 45, box.y + box.height / 2 + 30, { steps: 8 }); await page.mouse.up();
  await expect(feature).not.toHaveAttribute("style", before!);
  const moved = await feature.getAttribute("style");
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(feature).toHaveAttribute("style", moved!);
});

test("agent decisions expand separately from the idea map", async ({ page }) => {
  await backend(page, withRisk());
  const box = page.locator(".agent-decisions");
  await expect(box).not.toHaveAttribute("open", "");
  await box.locator(":scope > summary").click();
  await expect(box.getByRole("heading", { name: "Plan: unresolved" })).toBeVisible();
  await box.locator("summary").filter({ hasText: "Round 1 · evidence · critique · reject" }).click();
  await expect(box.getByText("Delete uploads after processing", { exact: true })).toBeVisible();
  await expect(box.getByText("Proposal: proposal-1", { exact: true })).toBeVisible();
  await expect(page.locator(".map-section")).not.toContainText(/critique|Plan: unresolved/);
  await expect(page.locator(".project-node")).toHaveCount(2);
  await page.getByLabel("Answer or revise constraints").fill("Delete notes after use");
  await page.getByRole("button", { name: "Send update" }).click();
  await expect(box.getByRole("heading", { name: "Plan: consensus" })).toBeVisible();
  await expect(box.getByText("Proposal: proposal-1", { exact: true })).toBeVisible();
  await expect(box.locator("summary").filter({ hasText: "Round 2 · evidence · critique · accept" })).toBeVisible();
});

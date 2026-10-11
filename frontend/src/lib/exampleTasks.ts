import type { WorkspaceTask } from "./graphAdapter";

export const exampleTasks: WorkspaceTask[] = [
  {
    id: "example-task-context-schema",
    title: "Define the shared project context",
    description: "Choose the first project fields for goals, updates, resources, and open questions.",
    nodeId: JSON.stringify(["node", "living-context"]),
    dependencies: [],
    estimateHours: 4,
    priority: "Unspecified",
    source: "Illustrative example — not a backend-approved task",
  },
  {
    id: "example-task-decision-log",
    title: "Build the decision history",
    description: "Record decisions with their rationale, date, contributors, and assumptions to revisit.",
    nodeId: JSON.stringify(["node", "decision-history"]),
    dependencies: ["example-task-context-schema"],
    estimateHours: 6,
    priority: "Unspecified",
    source: "Illustrative example — not a backend-approved task",
  },
  {
    id: "example-task-workspace-access",
    title: "Add private workspace access",
    description: "Start with invite-only membership and a clear owner-managed access model.",
    nodeId: JSON.stringify(["node", "workspace-access"]),
    dependencies: [],
    estimateHours: 5,
    priority: "Unspecified",
    source: "Illustrative example — not a backend-approved task",
  },
  {
    id: "example-task-search",
    title: "Prototype contextual project search",
    description: "Search the shared project context and show the source for each result.",
    nodeId: JSON.stringify(["node", "project-search"]),
    dependencies: ["example-task-context-schema"],
    estimateHours: 8,
    priority: "Unspecified",
    source: "Illustrative example — not a backend-approved task",
  },
];

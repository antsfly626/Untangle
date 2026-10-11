"use client";

import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
} from "@xyflow/react";

import "@xyflow/react/dist/style.css";

const initialNodes: Node[] = [
  {
    id: "idea",
    position: { x: 250, y: 40 },
    data: { label: "AI Study Assistant" },
    style: {
      background: "#334155",
      color: "white",
      borderRadius: 12,
    },
  },
  {
    id: "feature",
    position: { x: 80, y: 180 },
    data: { label: "Personalized Quizzes" },
    style: {
      background: "#166534",
      color: "white",
      borderRadius: 12,
    },
  },
  {
    id: "risk",
    position: { x: 400, y: 180 },
    data: { label: "Limited Development Time" },
    style: {
      background: "#9a3412",
      color: "white",
      borderRadius: 12,
    },
  },
];

const initialEdges: Edge[] = [
  { id: "e1", source: "idea", target: "feature" },
  { id: "e2", source: "idea", target: "risk" },
];

export default function ConceptMap() {
  return (
    <div className="h-[600px] w-full rounded-xl border">
      <ReactFlow
        nodes={initialNodes}
        edges={initialEdges}
        fitView
      >
        <Background />
        <Controls />
        <MiniMap />
      </ReactFlow>
    </div>
  );
}

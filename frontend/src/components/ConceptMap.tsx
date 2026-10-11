"use client";

import { useEffect, useState } from "react";
import { ReactFlow, Background, Controls, MiniMap, Handle, Position, useReactFlow, applyNodeChanges, type NodeProps, type Node } from "@xyflow/react";
import type { GraphData, WorkspaceGraph } from "@/lib/graphAdapter";
import "@xyflow/react/dist/style.css";

function ProjectNode({ data, selected }: NodeProps<Node<GraphData>>) {
  return <div className={`project-node kind-${data.kind} ${selected ? "selected" : ""}`}>
    <Handle type="target" position={Position.Left} />
    <span className="eyebrow">{data.kind}</span>
    <strong>{data.label}</strong>
    <Handle type="source" position={Position.Right} />
  </div>;
}
const nodeTypes = { project: ProjectNode };
function FocusSelection({ id }: { id: string | null }) {
  const { fitView } = useReactFlow();
  useEffect(() => { if (id) void fitView({ nodes: [{ id }], maxZoom: 1, duration: 250, padding: 1 }); }, [id, fitView]);
  return null;
}
export default function ConceptMap({ graph, selectedId, onSelect }: { graph: WorkspaceGraph; selectedId: string | null; onSelect: (id: string) => void }) {
  const [canvas, setCanvas] = useState<{ source: WorkspaceGraph["nodes"]; nodes: Node<GraphData>[] }>(() => ({ source: graph.nodes, nodes: graph.nodes.map(n => ({ ...n, type: "project" })) }));
  let nodes = canvas.nodes;
  if (canvas.source !== graph.nodes) {
    nodes = graph.nodes.map(n => {
      const previous = canvas.nodes.find(old => old.id === n.id);
      return { ...previous, ...n, type: "project", position: previous?.position ?? n.position };
    });
    setCanvas({ source: graph.nodes, nodes });
  }
  return <div className="map-canvas" aria-label="Project concept map">
    <ReactFlow nodes={nodes.map(n => ({ ...n, selected: n.id === selectedId }))} edges={graph.edges} nodeTypes={nodeTypes} fitView minZoom={0.08}
      nodesConnectable={false} onNodeClick={(_, n) => onSelect(n.id)}
      onNodesChange={changes => setCanvas(previous => ({ ...previous, nodes: applyNodeChanges(changes, previous.nodes) }))}>
      <FocusSelection id={selectedId} /><Background gap={24} color="#ccd6d2" /><Controls showInteractive={false} /><MiniMap pannable zoomable nodeColor="#78998a" />
    </ReactFlow>
  </div>;
}

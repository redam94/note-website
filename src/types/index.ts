export interface GraphNode {
  id: number;
  title: string;
  slug: string;
  level: number;
  tags: string[];
  degree: number;
  documentId: number | null;
  nodeType?: "note" | "subgraph";
  summary?: string | null;
  clusterLabel?: string | null;
}

export interface GraphEdgeData {
  source: number;
  target: number;
  relationship: string;
  confidence: number;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdgeData[];
}

export interface SearchResult {
  id: number;
  title: string;
  slug: string;
  excerpt: string;
  tags: string[];
  matchType: "keyword" | "semantic" | "cluster";
  score: number;
}

export interface Space {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  createdAt: string;
}

export interface NoteWithLinks {
  id: number;
  title: string;
  content: string;
  slug: string;
  tags: string[];
  level: number;
  documentId: number | null;
  createdAt: string;
  source?: string | null;
  chapter?: string | null;
  page?: number | null;
  summary?: string | null;
  backlinks: Array<{ id: number; title: string; slug: string; relationship: string }>;
  outlinks: Array<{ id: number; title: string; slug: string; relationship: string }>;
}

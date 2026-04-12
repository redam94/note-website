import { sqliteTable, text, integer, real } from "drizzle-orm/sqlite-core";

export const documents = sqliteTable("documents", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  filename: text("filename").notNull(),
  originalName: text("original_name").notNull(),
  mimeType: text("mime_type").notNull(),
  contentRaw: text("content_raw"),
  status: text("status", { enum: ["pending", "processing", "done", "error"] })
    .notNull()
    .default("pending"),
  error: text("error"),
  createdAt: text("created_at")
    .notNull()
    .$defaultFn(() => new Date().toISOString()),
});

export const notes = sqliteTable("notes", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  documentId: integer("document_id").references(() => documents.id, {
    onDelete: "cascade",
  }),
  parentId: integer("parent_id"),
  title: text("title").notNull(),
  content: text("content").notNull(),
  slug: text("slug").notNull().unique(),
  tags: text("tags", { mode: "json" }).$type<string[]>().default([]),
  level: integer("level").notNull().default(1),
  embedding: text("embedding", { mode: "json" }).$type<number[]>(),
  createdAt: text("created_at")
    .notNull()
    .$defaultFn(() => new Date().toISOString()),
});

export const graphEdges = sqliteTable("graph_edges", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  sourceId: integer("source_id")
    .notNull()
    .references(() => notes.id, { onDelete: "cascade" }),
  targetId: integer("target_id")
    .notNull()
    .references(() => notes.id, { onDelete: "cascade" }),
  relationshipType: text("relationship_type", {
    enum: [
      "supports",
      "contradicts",
      "defines",
      "example_of",
      "part_of",
      "references",
    ],
  }).notNull(),
  confidence: real("confidence").notNull().default(0.5),
  createdBy: text("created_by", { enum: ["llm", "user"] })
    .notNull()
    .default("llm"),
  createdAt: text("created_at")
    .notNull()
    .$defaultFn(() => new Date().toISOString()),
});

export type Document = typeof documents.$inferSelect;
export type NewDocument = typeof documents.$inferInsert;
export type Note = typeof notes.$inferSelect;
export type NewNote = typeof notes.$inferInsert;
export type GraphEdge = typeof graphEdges.$inferSelect;
export type NewGraphEdge = typeof graphEdges.$inferInsert;

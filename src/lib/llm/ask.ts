import { getClient, getModel } from "./client";
import type { Note } from "../db/schema";

export async function* askKnowledgeBase(
  question: string,
  relevantNotes: Note[]
): AsyncGenerator<string> {
  const client = getClient();

  const notesContext = relevantNotes
    .map(
      (n, i) =>
        `[Note ${i + 1}: "${n.title}" (ID: ${n.id})]\n${n.content}\n`
    )
    .join("\n---\n");

  const stream = await client.messages.stream({
    model: getModel(),
    max_tokens: 4096,
    system: `You are a knowledgeable assistant answering questions based on a personal knowledge base.
Use the provided notes to answer the question. Cite specific notes by their title when referencing them.
If the notes don't contain enough information to fully answer, say so clearly.
Format your answer in markdown.`,
    messages: [
      {
        role: "user",
        content: `Here are the relevant notes from the knowledge base:\n\n${notesContext}\n\nQuestion: ${question}`,
      },
    ],
  });

  for await (const event of stream) {
    if (
      event.type === "content_block_delta" &&
      event.delta.type === "text_delta"
    ) {
      yield event.delta.text;
    }
  }
}

import { getClient, getModel } from "./client";
import type { Note } from "../db/schema";

interface LinkResult {
  related: boolean;
  relationship:
    | "supports"
    | "contradicts"
    | "defines"
    | "example_of"
    | "part_of"
    | "references";
  confidence: number;
  reason: string;
}

export async function detectLink(
  noteA: Note,
  noteB: Note
): Promise<LinkResult | null> {
  const client = getClient();

  const response = await client.messages.create({
    model: getModel(),
    max_tokens: 512,
    system: `You determine if two notes are semantically related. Return ONLY valid JSON:
If related: {"related": true, "relationship": "supports|contradicts|defines|example_of|part_of|references", "confidence": 0.0-1.0, "reason": "brief explanation"}
If not related: {"related": false}`,
    messages: [
      {
        role: "user",
        content: `Note A - "${noteA.title}": ${noteA.content}\n\nNote B - "${noteB.title}": ${noteB.content}\n\nAre these notes related?`,
      },
    ],
  });

  const text =
    response.content[0].type === "text" ? response.content[0].text : "";
  const jsonMatch = text.match(/```(?:json)?\s*([\s\S]*?)```/) || [null, text];
  const parsed = JSON.parse(jsonMatch[1]!.trim());

  if (!parsed.related) return null;
  return parsed as LinkResult;
}

export async function detectLinksForNote(
  newNote: Note,
  existingNotes: Note[]
): Promise<
  Array<{
    sourceId: number;
    targetId: number;
    relationship: string;
    confidence: number;
  }>
> {
  const links: Array<{
    sourceId: number;
    targetId: number;
    relationship: string;
    confidence: number;
  }> = [];

  // Process in batches of 5 to avoid rate limits
  const batchSize = 5;
  for (let i = 0; i < existingNotes.length; i += batchSize) {
    const batch = existingNotes.slice(i, i + batchSize);
    const results = await Promise.all(
      batch.map((existing) => detectLink(newNote, existing))
    );

    for (let j = 0; j < results.length; j++) {
      const result = results[j];
      if (result) {
        links.push({
          sourceId: newNote.id,
          targetId: batch[j].id,
          relationship: result.relationship,
          confidence: result.confidence,
        });
      }
    }
  }

  return links;
}

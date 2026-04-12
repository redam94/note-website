import { getClient, getModel } from "./client";
import slugify from "slugify";

interface ExtractedNote {
  title: string;
  content: string;
  tags: string[];
  level: number;
  children: ExtractedNote[];
}

interface ExtractionResult {
  notes: ExtractedNote[];
}

export async function extractNotes(
  documentText: string
): Promise<ExtractionResult> {
  const client = getClient();

  const response = await client.messages.create({
    model: getModel(),
    max_tokens: 8192,
    system: `You are a knowledge extraction expert. Given a document, extract a hierarchical set of notes.

Return ONLY valid JSON with this structure:
{
  "notes": [
    {
      "title": "Topic Title",
      "content": "A self-contained explanation of this topic. Should be understandable on its own.",
      "tags": ["tag1", "tag2"],
      "level": 1,
      "children": [
        {
          "title": "Subtopic",
          "content": "Detailed content...",
          "tags": ["tag1"],
          "level": 2,
          "children": [
            {
              "title": "Atomic Note",
              "content": "Specific detail...",
              "tags": ["tag1"],
              "level": 3,
              "children": []
            }
          ]
        }
      ]
    }
  ]
}

Guidelines:
- Create up to 3 levels of hierarchy: topic → subtopic → atomic note
- Each note should be atomic and self-contained
- Content should be rich enough to understand without the source document
- Tags should be specific and reusable across documents
- Focus on key concepts, definitions, arguments, and evidence`,
    messages: [
      {
        role: "user",
        content: `Extract hierarchical notes from this document:\n\n${documentText}`,
      },
    ],
  });

  const text =
    response.content[0].type === "text" ? response.content[0].text : "";

  // Parse JSON from the response, handling potential markdown code blocks
  const jsonMatch = text.match(/```(?:json)?\s*([\s\S]*?)```/) || [null, text];
  const parsed: ExtractionResult = JSON.parse(jsonMatch[1]!.trim());
  return parsed;
}

export interface FlatNote {
  title: string;
  content: string;
  slug: string;
  tags: string[];
  level: number;
  parentSlug: string | null;
}

export function flattenNotes(
  notes: ExtractedNote[],
  parentSlug: string | null = null
): FlatNote[] {
  const result: FlatNote[] = [];

  for (const note of notes) {
    const slug = slugify(note.title, { lower: true, strict: true });
    result.push({
      title: note.title,
      content: note.content,
      slug,
      tags: note.tags,
      level: note.level,
      parentSlug,
    });
    if (note.children?.length) {
      result.push(...flattenNotes(note.children, slug));
    }
  }

  return result;
}

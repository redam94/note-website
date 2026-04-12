import fs from "fs/promises";
import path from "path";

export async function extractText(
  filePath: string,
  mimeType: string
): Promise<string> {
  switch (mimeType) {
    case "application/pdf":
      return extractPdf(filePath);
    case "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
      return extractDocx(filePath);
    case "text/markdown":
    case "text/plain":
      return extractPlainText(filePath);
    default:
      return extractPlainText(filePath);
  }
}

async function extractPdf(filePath: string): Promise<string> {
  const { PDFParse } = await import("pdf-parse");
  const buffer = await fs.readFile(filePath);
  const pdf = new PDFParse({ data: new Uint8Array(buffer) });
  const result = await pdf.getText();
  return result.text;
}

async function extractDocx(filePath: string): Promise<string> {
  const mammoth = await import("mammoth");
  const buffer = await fs.readFile(filePath);
  const result = await mammoth.extractRawText({ buffer });
  return result.value;
}

async function extractPlainText(filePath: string): Promise<string> {
  return fs.readFile(filePath, "utf-8");
}

export function getMimeType(filename: string): string {
  const ext = path.extname(filename).toLowerCase();
  const mimeMap: Record<string, string> = {
    ".pdf": "application/pdf",
    ".docx":
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".txt": "text/plain",
  };
  return mimeMap[ext] || "text/plain";
}

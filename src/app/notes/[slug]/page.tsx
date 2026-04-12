import NoteViewer from "@/components/NoteViewer/NoteViewer";

export default async function NotePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <NoteViewer slug={slug} />;
}

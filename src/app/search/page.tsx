import SearchPanel from "@/components/Search/SearchPanel";

export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q } = await searchParams;
  return <SearchPanel initialQuery={q || ""} />;
}

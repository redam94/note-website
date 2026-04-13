export function apiUrl(
  path: string,
  spaceSlug: string,
  params?: Record<string, string>
): string {
  const searchParams = new URLSearchParams({ space: spaceSlug });
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      searchParams.set(key, value);
    }
  }
  return `${path}?${searchParams.toString()}`;
}

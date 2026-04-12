import aiofiles


async def parse_text(file_path: str) -> tuple[str, list[dict]]:
    """Parse a plain text or markdown file."""
    async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
        content = await f.read()
    return content, [{"page": 1, "text": content}]

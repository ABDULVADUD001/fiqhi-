
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urljoin

URL = (
    "https://fatvo.uz/uz/answers/"
    "08175646-0d54-4de2-9601-3bae5f1c5c11"
)

async def main():
    headers = {"User-Agent": "Mozilla/5.0"}
    timeout = aiohttp.ClientTimeout(total=25)

    async with aiohttp.ClientSession(
        headers=headers, timeout=timeout
    ) as session:
        async with session.get(URL) as response:
            print("HTTP:", response.status)
            response.raise_for_status()
            html = await response.text()

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()

    text = soup.get_text("\n", strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    def extract_between(start, end):
        start_index = next(
            (i for i, line in enumerate(lines)
             if line.lower().rstrip(":") == start.lower()),
            None
        )
        if start_index is None:
            return "Ajratilmadi"

        end_index = next(
            (i for i in range(start_index + 1, len(lines))
             if lines[i].lower().rstrip(":") == end.lower()),
            None
        )
        if end_index is None:
            return "Ajratilmadi"

        return "\n".join(lines[start_index + 1:end_index])

    question = extract_between("Savol", "Javob")
    answer = extract_between(
        "Javob", "Ulashish"
    )

    print("\n=== SAVOL ===\n")
    print(question)

    print("\n=== JAVOB ===\n")
    print(answer)

    print("\n=== ASL MANBA ===\n")
    print(urljoin(URL, ""))

asyncio.run(main())

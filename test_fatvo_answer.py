
import asyncio
import aiohttp
from bs4 import BeautifulSoup

URL = "https://fatvo.uz/uz/answers/08175646-0d54-4de2-9601-3bae5f1c5c11"

async def main():
    async with aiohttp.ClientSession(
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=aiohttp.ClientTimeout(total=25)
    ) as session:
        async with session.get(URL) as response:
            print("HTTP:", response.status)
            response.raise_for_status()
            html = await response.text()

    soup = BeautifulSoup(html, "html.parser")

    # Faqat asosiy matnni qoldiramiz
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()

    lines = [
        line.strip()
        for line in soup.get_text("\n").splitlines()
        if line.strip()
    ]

    def find_label(label):
        for i, line in enumerate(lines):
            if line.rstrip(":").strip().lower() == label.lower():
                return i
        return None

    q = find_label("Savol")
    a = find_label("Javob")
    share = find_label("Ulashish")

    print("\n=== SAVOL ===")
    if q is not None and a is not None:
        print("\n".join(lines[q + 1:a]))
    else:
        print("Savol bo‘limi topilmadi.")

    print("\n=== JAVOB ===")
    if a is not None:
        end = share if share is not None and share > a else len(lines)
        print("\n".join(lines[a + 1:end]))
    else:
        print("Javob bo‘limi topilmadi.")

    print("\n=== MANBA ===")
    print(URL)

asyncio.run(main())

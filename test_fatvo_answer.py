
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE_URL = "https://fatvo.uz"
QUESTIONS_URL = f"{BASE_URL}/uz/questions"

async def main():
    headers = {"User-Agent": "Mozilla/5.0"}
    timeout = aiohttp.ClientTimeout(total=25)

    async with aiohttp.ClientSession(
        headers=headers, timeout=timeout
    ) as session:
        async with session.get(QUESTIONS_URL) as response:
            print("Savollar sahifasi:", response.status)
            html = await response.text()

        soup = BeautifulSoup(html, "html.parser")

        link = None
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(" ", strip=True).lower()

            if "/uz/answers/" in href and (
                "tahorat" in text or "chekish" in text
            ):
                link = urljoin(BASE_URL, href)
                break

        if not link:
            print("Savol havolasi topilmadi.")
            return

        print("\nTopilgan manba:", link)

        async with session.get(link) as response:
            print("Javob sahifasi:", response.status)
            page_html = await response.text()

        detail = BeautifulSoup(page_html, "html.parser")

        for tag in detail(["script", "style", "nav", "footer"]):
            tag.decompose()

        title = detail.title.get_text(" ", strip=True) if detail.title else ""
        text = detail.get_text("\n", strip=True)

        print("\n=== SAHIFA NOMI ===")
        print(title)

        print("\n=== SAHIFA MATNI ===")
        print(text[:10000])

asyncio.run(main())

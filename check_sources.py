
import asyncio
import aiohttp
from bs4 import BeautifulSoup

SOURCES = {
    "Fatvo.uz": "https://fatvo.uz/uz/questions",
    "Shāmila Hanafiy fiqhi": "https://shamela.ws/category/14",
    "Shāmila qidiruv": "https://shamela.ws/search",
}

async def check_source(session, name, url):
    print(f"\n{'=' * 45}\n{name}\n{url}")

    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=25),
            allow_redirects=True
        ) as response:
            print("HTTP holati:", response.status)

            if response.status != 200:
                print("Sahifa ochilmadi.")
                return

            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")

            title = soup.title.get_text(" ", strip=True) if soup.title else "Sarlavha topilmadi"
            text = soup.get_text(" ", strip=True)

            print("Sarlavha:", title)
            print("Matn uzunligi:", len(text), "belgi")
            print("Havolalar soni:", len(soup.find_all("a")))
            print("Sahifadan namuna:")
            print(text[:700])

    except Exception as error:
        print("Ulanish xatosi:", str(error))

async def main():
    headers = {
        "User-Agent": "HanafiyFiqhResearchBot/1.0"
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        for name, url in SOURCES.items():
            await check_source(session, name, url)

if __name__ == "__main__":
    asyncio.run(main())

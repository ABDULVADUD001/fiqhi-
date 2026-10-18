
import asyncio
import aiohttp
from bs4 import BeautifulSoup

URL = "https://fatvo.uz/uz/questions"

async def main():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
    }

    try:
        timeout = aiohttp.ClientTimeout(total=25)

        async with aiohttp.ClientSession(
            headers=headers,
            timeout=timeout
        ) as session:
            async with session.get(URL) as response:
                print("HTTP holati:", response.status)
                print("Sahifa:", response.url)

                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")

                print("\n=== QIDIRUV FORMALARI ===")

                forms = soup.find_all("form")

                if not forms:
                    print("HTML ichida form topilmadi.")

                for number, form in enumerate(forms, 1):
                    print(f"\nForma #{number}")
                    print("Action:", form.get("action"))
                    print("Method:", form.get("method"))

                    for item in form.find_all(
                        ["input", "select", "button", "textarea"]
                    ):
                        print({
                            "element": item.name,
                            "name": item.get("name"),
                            "type": item.get("type"),
                            "placeholder": item.get("placeholder"),
                            "text": item.get_text(" ", strip=True)[:100]
                        })

                print("\n=== SAVOL SAHIFALARIGA HAVOLALAR ===")

                count = 0
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    title = link.get_text(" ", strip=True)

                    if "/uz/answers/" in href:
                        print(title[:150], "->", href)
                        count += 1

                    if count >= 10:
                        break

                print("\nTekshiruv tugadi.")

    except Exception as error:
        print("Xatolik:", repr(error))

asyncio.run(main())

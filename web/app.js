
const tg = window.Telegram?.WebApp;

if (tg) {
  tg.ready();
  tg.expand();
  tg.setHeaderColor("#102a24");
  tg.setBackgroundColor("#0b1714");
}

const searchInput = document.getElementById("search");
const searchButton = document.getElementById("searchBtn");
const searchMessage = document.getElementById("searchMessage");

function showMessage(text) {
  searchMessage.textContent = text;
}

function searchTopic() {
  const query = searchInput.value.trim();

  if (!query) {
    showMessage("Avval qidiriladigan mavzuni yozing.");
    searchInput.focus();
    return;
  }

  showMessage("🔎 «" + query + "» mavzusi tanlandi.");

  // Telegram botga qidiruv so‘rovini yuborish.
  if (tg && tg.initData) {
    tg.sendData(JSON.stringify({
      action: "search",
      query: query
    }));
    return;
  }

  showMessage(
    "Qidiruv so‘rovi tayyor. Haqiqiy natijalarni olish uchun " +
    "Mini App backend va Telegram bot bilan ulanishi kerak."
  );
}

searchButton.addEventListener("click", searchTopic);

searchInput.addEventListener("keydown", function (event) {
  if (event.key === "Enter") {
    searchTopic();
  }
});

document.querySelectorAll(".category").forEach(function (button) {
  button.addEventListener("click", function () {
    const topic = button.dataset.topic;

    searchInput.value = topic;
    showMessage("📚 «" + topic + "» bo‘limi tanlandi.");

    searchInput.focus();
  });
});

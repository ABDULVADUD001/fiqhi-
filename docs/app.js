
const tg = window.Telegram?.WebApp;

const searchInput = document.getElementById("search");
const searchButton = document.getElementById("searchBtn");
const searchMessage = document.getElementById("searchMessage");
const askQuestionButton = document.getElementById("askQuestionBtn");

if (tg) {
  tg.ready();
  tg.expand();

  try {
    tg.setHeaderColor("#102a24");
    tg.setBackgroundColor("#0b1714");
  } catch (error) {
    console.log("Telegram rang sozlamasi mavjud emas.");
  }
}

function showMessage(message) {
  searchMessage.textContent = message;
}

function sendToBot(action, query) {
  if (tg && tg.initData) {
    tg.sendData(JSON.stringify({
      action: action,
      query: query
    }));
    showMessage("So‘rovingiz Telegram botga yuborildi.");
    return true;
  }

  showMessage(
    "Bu funksiya Telegram ichida ochilganda ishlaydi. " +
    "Hozircha Telegram bot orqali /start buyrug‘ini yuboring."
  );

  return false;
}

function searchTopic() {
  const query = searchInput.value.trim();

  if (!query) {
    showMessage("Avval fiqh mavzusini yozing.");
    searchInput.focus();
    return;
  }

  sendToBot("search", query);
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
    showMessage("Tanlangan bo‘lim: " + topic);
    searchInput.focus();
  });
});

askQuestionButton.addEventListener("click", function () {
  if (tg && tg.initData) {
    sendToBot("question", "");
    return;
  }

  showMessage(
    "Savol yuborish uchun Telegram botini oching va /start buyrug‘ini yuboring."
  );
});

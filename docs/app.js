
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
  if (tg && typeof tg.sendData === "function") {
    try {
      tg.sendData(JSON.stringify({
        action: action,
        query: query
      }));
      return true;
    } catch (error) {
      showMessage("Yuborishda xato yuz berdi. Telegram bot orqali urinib ko‘ring.");
      return false;
    }
  }

  showMessage("Mini App’ni bot klaviaturasidagi tugma orqali oching.");
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

searchInput.addEventListener("keydown", function(event) {
  if (event.key === "Enter") {
    searchTopic();
  }
});

document.querySelectorAll(".category").forEach(function(button) {
  button.addEventListener("click", function() {
    searchInput.value = button.dataset.topic;
    showMessage("Tanlangan bo‘lim: " + button.dataset.topic);
    searchInput.focus();
  });
});

askQuestionButton.addEventListener("click", function() {
  showMessage(
    "Savol yuborish uchun Telegram bot chatiga qayting va savolingizni yozing."
  );
});

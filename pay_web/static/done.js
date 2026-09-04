// Shows the "close" button only when the page really is inside Telegram.
// Rendering it unconditionally would leave a dead button in a normal browser.
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp;
  var button = document.getElementById("close-app");
  if (!tg || !button) {
    return;
  }
  tg.ready();
  button.hidden = false;
  button.addEventListener("click", function () {
    tg.close();
  });
})();

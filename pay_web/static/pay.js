// Sends the user straight through to the gateway.
//
// The form has to be a POST — WayForPay's purchase endpoint takes no GET, and
// the invoice API that does return a plain link cannot carry a recurring
// schedule. So the page exists, but the user should not have to tap on it:
// this submits it for them, and the visible button is what remains if
// scripting is off or this file fails to load.
//
// External rather than inline so the page keeps script-src 'self'.
(function () {
  "use strict";

  var form = document.getElementById("pay-form");
  if (!form) {
    return; // an "expired" or "already paid" page — nothing to submit
  }

  // Telegram's in-app browser restores a page from cache when the user backs
  // out of the gateway. Submitting again on that restore would bounce them
  // straight back out, so a page shown from cache stays put.
  var restored = false;
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) {
      restored = true;
    }
  });

  window.setTimeout(function () {
    if (!restored) {
      form.submit();
    }
  }, 150);
})();

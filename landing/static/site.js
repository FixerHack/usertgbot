// Theme toggle. External rather than inline so the page keeps
// script-src 'self' — the same rule the Mini App is held to.
(function () {
  "use strict";

  var KEY = "moozuku-theme";
  var root = document.documentElement;

  function apply(theme) {
    root.setAttribute("data-theme", theme);
  }

  function stored() {
    // Private windows and blocked site data make this throw rather than
    // return null, so a failure here must not take the page with it.
    try {
      return localStorage.getItem(KEY);
    } catch (e) {
      return null;
    }
  }

  function remember(theme) {
    try {
      localStorage.setItem(KEY, theme);
    } catch (e) {
      /* nothing to do — the toggle still works for this visit */
    }
  }

  var saved = stored();
  if (saved === "light" || saved === "dark") {
    apply(saved);
  } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
    // The markup ships dark; only a light system preference needs acting on.
    apply("light");
  }

  var button = document.getElementById("theme-toggle");
  if (button) {
    button.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      apply(next);
      remember(next);
    });
  }
})();

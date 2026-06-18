// DISHA AI · Screener MCP — landing page behaviour.
// Reads /health to show live status + whether the auth gate is on.

(function () {
  "use strict";

  // Banner: fall back to a text placeholder if the image isn't configured.
  var banner = document.getElementById("banner");
  if (banner) {
    banner.addEventListener("error", function () {
      var box = document.getElementById("bannerBox");
      box.classList.add("placeholder");
      box.innerHTML = "<span>DISHA AI</span>";
    });
  }

  var AUTH_HTML =
    '<div class="creds">' +
    '  <div class="cred-group">' +
    '    <span class="cred-label">Sign in <em>(auth server)</em></span>' +
    '    <div class="chips"><code>SSO_USERNAME</code><code>SSO_PASSWORD</code></div>' +
    "  </div>" +
    '  <div class="cred-group">' +
    '    <span class="cred-label">Screener account</span>' +
    '    <div class="chips"><code>SCREENER_USERNAME</code><code>SCREENER_PASSWORD</code>' +
    '      <span class="muted">or server&nbsp;.env</span></div>' +
    "  </div>" +
    "</div>" +
    '<p class="note">Connections must sign in against the DISHA AI auth server. ' +
    "Screener data is fetched with the configured Screener account.</p>";

  var OPEN_HTML =
    '<div class="chips"><code>SCREENER_USERNAME</code><code>SCREENER_PASSWORD</code>' +
    '  <span class="muted">request headers or query params</span></div>' +
    '<p class="note">Credentials are scoped per connection.</p>';

  function setBadge(text, cls) {
    var badge = document.getElementById("badge");
    badge.textContent = text;
    badge.className = "badge " + cls;
  }

  function showAuth(html) {
    var block = document.getElementById("authBlock");
    document.getElementById("authBody").innerHTML = html;
    block.hidden = false;
  }

  fetch("/health", { headers: { Accept: "application/json" } })
    .then(function (r) {
      if (!r.ok) throw new Error("health " + r.status);
      return r.json();
    })
    .then(function (h) {
      var authOn = h.auth === "enabled";
      setBadge(authOn ? "Auth required" : "Open", authOn ? "badge-auth" : "badge-open");
      showAuth(authOn ? AUTH_HTML : OPEN_HTML);
    })
    .catch(function () {
      document.getElementById("dot").classList.add("offline");
      document.getElementById("statusTitle").textContent = "Screener MCP server — unreachable";
      setBadge("Offline", "badge-offline");
    });
})();

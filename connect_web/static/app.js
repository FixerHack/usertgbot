"use strict";

const dataEl = document.getElementById("connect-data");
const DATA = dataEl.dataset;
const TOKEN = DATA.token;
const PHONE = DATA.phone;
const EXPIRES_AT = DATA.expiresAt ? new Date(DATA.expiresAt) : null;
const EXPIRED_ON_LOAD = DATA.expired === "true";
// Server-supplied so the ring's "how full is full" matches the real TTL
// instead of a magic 15 that would drift the day the TTL changes.
const TTL_MS = (parseInt(DATA.ttlSeconds, 10) || 900) * 1000;

// --- localized strings, injected server-side per the owner's language ------
// `{placeholder}` substitution mirrors shared/i18n.py's str.format keys, so
// the same catalogue entry works on both sides. Values interpolated in are
// escaped first: `err` can carry raw text straight from Telegram, and these
// land in innerHTML.
function esc(v) {
  return String(v == null ? "" : v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function tr(key, vars) {
  let out = DATA["s" + key] || "";
  if (vars) for (const k of Object.keys(vars)) out = out.split("{" + k + "}").join(esc(vars[k]));
  return out;
}

const tg = window.Telegram && window.Telegram.WebApp;

// console only — no visible on-page log in production (it was a diagnostic
// aid for the prototype, not something an end user should see).
function log(...parts) {
  console.log(...parts);
}

function show(stepId) {
  document.querySelectorAll(".step").forEach(s => s.classList.remove("active"));
  document.getElementById(stepId).classList.add("active");
}

// innerHTML, not textContent: the localized catalogue entries legitimately
// carry inline markup (<b>), and everything interpolated INTO them goes
// through esc() first — see s().
function status(id, html, kind) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = html;
  el.className = "status" + (kind ? " " + kind : "");
}

function haptic(style) {
  try { tg && tg.HapticFeedback && tg.HapticFeedback.impactOccurred(style); } catch (e) {}
}

// --- gramjs bundle: same defensive resolution as the prototype ------------
const G = window.telegram || {};
log("gramjs globals:", Object.keys(G).join(", ") || "(none — bundle failed to load?)");
const TelegramClient = G.TelegramClient;
const Api = G.Api;
const StringSession = G.StringSession || (G.sessions && G.sessions.StringSession);
const computeCheck =
  G.computeCheck || (G.Password && G.Password.computeCheck) || (G.password && G.password.computeCheck) || null;
const AuthKey = G.AuthKey;

// --- byte <-> base64 helpers (used both for the final handoff to the
// backend and for saving/restoring a pending auth key across a resume) -----
function bytesToBase64(bytes) {
  let binary = "";
  for (const b of new Uint8Array(bytes)) binary += String.fromCharCode(b);
  return btoa(binary);
}
function base64ToBytes(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return window.Buffer ? window.Buffer.from(bytes) : bytes;
}

// GramJS's OWN browser-mode DC hostnames (node_modules/telegram/client/
// TelegramClient.js::getDC(), the `!platform_1.isNode` branch) — NOT the
// server's shared/session_convert.py table, which holds raw production TCP
// IPs for Telethon's plain-socket use. A browser can only open a `wss://`
// WebSocket to a named host with a matching TLS cert; GramJS's own
// PromisedWebSockets.js builds `wss://${ip}:${port}/apiws` literally, so a
// raw IP here just hangs forever with no error surfaced anywhere (this was
// the actual cause of a real resume-flow bug: reconnecting a pinned client
// during resume silently never completed the handshake). Needed here to PIN
// a freshly-opened connection to the DC that answered the original sendCode
// when resuming a saved attempt: phone_code_hash is only valid within the DC
// that issued it, and a fresh client without this would connect to GramJS's
// default DC instead.
const DC_IPS = {
  1: "pluto.web.telegram.org",
  2: "venus.web.telegram.org",
  3: "aurora.web.telegram.org",
  4: "vesta.web.telegram.org",
  5: "flora.web.telegram.org",
};

if (tg) {
  tg.ready();
  tg.expand();
  try { tg.setHeaderColor("secondary_bg_color"); tg.setBackgroundColor("secondary_bg_color"); } catch (e) {}
  log("WebApp platform:", tg.platform, "version:", tg.version);
  log("initData present:", !!tg.initData, "len:", (tg.initData || "").length);
} else {
  log("WARNING: window.Telegram.WebApp missing — opened outside Telegram?");
}

// --- resumable state --------------------------------------------------------
// A half-finished login is a live GramJS client + phone_code_hash, which only
// exist in THIS browser tab. Closing the Mini App destroys them. To let the
// user reopen the same bot button and continue instead of starting over, the
// pending attempt (not the finished session — that never touches storage) is
// kept in localStorage, scoped to this token, cleared the moment it's no
// longer needed (success, or the token turning out to be dead).
const STORAGE_KEY = "connect_pending_" + TOKEN;

function savePending(state) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (e) {}
}
function loadPending() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); } catch (e) { return null; }
}
function clearPending() {
  try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
}

let client = null;
let phoneCodeHash = "";
let code = "";
let API_ID = null;
let API_HASH = null;

async function ensureConfig() {
  if (API_ID !== null) return;
  const cfg = await fetch("/webapp-config").then(r => r.json());
  API_ID = cfg.api_id;
  API_HASH = cfg.api_hash;
}

const codeBoxes = document.querySelectorAll("#code-boxes span");
function renderCode() {
  codeBoxes.forEach((box, i) => { box.textContent = code[i] || ""; box.classList.toggle("filled", i < code.length); });
}

// --- countdown ring ----------------------------------------------------------
const ringWrap = document.getElementById("ring-wrap");
const ringArc = document.getElementById("ring-arc");
const ringLabel = document.getElementById("ring-label");
const RING_C = 2 * Math.PI * 22;  // r=22 in the SVG

const DANGER_RGB = [236, 57, 66];
const OK_RGB = [77, 205, 94];
// Telegram hands the real theme accent over in themeParams; the constant is
// only the fallback for the (blocked) out-of-Telegram case.
const ACCENT_RGB = hexToRgb((tg && tg.themeParams && tg.themeParams.button_color) || "#5288c1") || [82, 136, 193];

function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function mix(a, b, t) {
  return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * t)).join(",")})`;
}

let ringTimer = null;

function ringCountdown(leftMs) {
  const fraction = Math.max(0, Math.min(1, leftMs / TTL_MS));
  ringWrap.classList.add("on");
  ringWrap.classList.remove("settled");
  ringArc.style.strokeDasharray = RING_C;
  ringArc.style.strokeDashoffset = RING_C * (1 - fraction);
  // Bleeds toward red as the time goes, so "running out" is legible at a
  // glance without a label saying so.
  ringArc.style.stroke = mix(DANGER_RGB, ACCENT_RGB, fraction);
  const m = Math.floor(leftMs / 60000);
  const s = Math.floor((leftMs % 60000) / 1000);
  ringLabel.classList.remove("badge");
  ringLabel.textContent = `${m}:${String(s).padStart(2, "0")}`;
}

/** Stop counting; the ring becomes the outcome badge. */
function ringSettle(kind) {
  if (ringTimer) { clearInterval(ringTimer); ringTimer = null; }
  ringWrap.classList.add("on", "settled");
  ringArc.style.strokeDasharray = RING_C;
  ringArc.style.strokeDashoffset = 0;
  ringArc.style.stroke = kind === "ok" ? `rgb(${OK_RGB})` : `rgb(${DANGER_RGB})`;
  ringLabel.classList.add("badge");
  ringLabel.textContent = kind === "ok" ? "✓" : "✕";
}

function expire(reason) {
  log("expired:", reason);
  clearPending();
  ringSettle("fail");
  show("step-expired");
}

function startTimer() {
  if (!EXPIRES_AT) return;
  const tick = () => {
    const leftMs = EXPIRES_AT.getTime() - Date.now();
    if (leftMs <= 0) {
      expire("countdown reached zero on the page");
      return;
    }
    ringCountdown(leftMs);
  };
  ringTimer = setInterval(tick, 1000);
  tick();
}

// --- client setup, optionally pinned to a DC (+ auth key) when resuming -----
// Resuming isn't just "reconnect to the same DC": phone_code_hash is bound to
// the specific MTProto auth key that requested it, not merely to the DC. A
// fresh StringSession("") makes GramJS negotiate a BRAND NEW auth key on
// connect() (MTProtoSender only reuses one if session.getAuthKey() already
// has a key set) — so without restoring the original key, signIn always fails
// with PHONE_CODE_EXPIRED even seconds later, confirmed live. Restoring the
// raw key via AuthKey.setKey()/session.setAuthKey() before connect() makes
// GramJS skip the handshake and reuse the exact same pre-auth session.
async function ensureClient(pinDcId, pinAuthKeyB64) {
  if (client) return client;
  if (!TelegramClient || !StringSession) throw new Error("GramJS did not load — check /gramjs.js");
  await ensureConfig();

  const session = new StringSession("");
  if (pinDcId && DC_IPS[pinDcId]) {
    session.setDC(pinDcId, DC_IPS[pinDcId], 443);
    log("pinning to dcId =", String(pinDcId), "(resuming a saved attempt)");
    if (pinAuthKeyB64 && AuthKey) {
      const authKey = new AuthKey();
      await authKey.setKey(base64ToBytes(pinAuthKeyB64));
      session.setAuthKey(authKey, pinDcId);
      log("restored saved auth key — connect() should reuse it, not generate a new one");
    } else if (pinAuthKeyB64 && !AuthKey) {
      log("WARNING: have a saved auth key but AuthKey is missing from the bundle — resume will fail");
    }
  }
  client = new TelegramClient(session, API_ID, API_HASH, {
    connectionRetries: 3,
    deviceModel: "User Agent Bot",
    systemVersion: "1.0",
    appVersion: "1.0",
  });
  log("connecting to Telegram…");
  await client.connect();
  log("connected. dcId =", String(client.session.dcId));
  return client;
}

// --- step: code --------------------------------------------------------------
// There is no "confirm your phone" step: the number came from the contact the
// user already shared with the bot in the chat, so asking them to confirm it
// again was a tap that told them nothing. The code request fires on open and
// the number stays visible here purely as context.
const pad = document.getElementById("pad");
const retryBtn = document.getElementById("retry-code");
// The phone is no longer a separate #phone-display element: it's interpolated
// straight into the localized subtitle (tr("CodeSending"/"CodePhone")), since
// where the number sits inside the sentence differs by language.

function padEnabled(on) {
  pad.classList.toggle("disabled", !on);
}

async function sendCodeNow() {
  retryBtn.style.display = "none";
  padEnabled(false);
  document.getElementById("code-sub").innerHTML = tr("CodeSending", { phone: PHONE });
  status("code-status", "");
  try {
    const c = await ensureClient();
    const res = await c.sendCode({ apiId: API_ID, apiHash: API_HASH }, PHONE);
    phoneCodeHash = res.phoneCodeHash;
    log("sendCode ok. isCodeViaApp =", String(res.isCodeViaApp));
    // Save the auth key too, not just dcId — see the comment on ensureClient
    // for why a resume without it always fails.
    const authKeyB64 = bytesToBase64(client.session.authKey.getKey());
    savePending({ phoneCodeHash, dcId: client.session.dcId, authKeyB64 });
    code = "";
    renderCode();
    document.getElementById("code-sub").innerHTML = tr("CodeSent");
    padEnabled(true);
  } catch (e) {
    const err = e.errorMessage || e.message || String(e);
    log("sendCode FAILED:", err);
    document.getElementById("code-sub").innerHTML = tr("CodePhone", { phone: PHONE });
    status("code-status", tr("CodeSendFailed", { error: err }), "err");
    // Without the old confirm step there'd be no way back from a failed
    // send, so the retry lives here instead.
    retryBtn.style.display = "block";
  }
}

retryBtn.onclick = sendCodeNow;

document.querySelectorAll(".pad button[data-d]").forEach(b => {
  b.onclick = () => { if (code.length < 5) { code += b.dataset.d; renderCode(); haptic("light"); } };
});
document.getElementById("backspace").onclick = () => { code = code.slice(0, -1); renderCode(); haptic("soft"); };

document.getElementById("submit-code").onclick = async () => {
  if (code.length < 5) { status("code-status", tr("CodeTooShort"), "err"); return; }
  status("code-status", tr("CodeChecking"));
  try {
    await client.invoke(new Api.auth.SignIn({ phoneNumber: PHONE, phoneCodeHash, phoneCode: code }));
    log("signIn ok (no 2FA)");
    await finish();
  } catch (e) {
    const err = e.errorMessage || e.message || String(e);
    log("signIn ->", err);
    if (err === "SESSION_PASSWORD_NEEDED") { show("step-2fa"); return; }
    if (err === "PHONE_CODE_EXPIRED") {
      // Telegram's OWN code expiry (independent of our token TTL) — the saved
      // phone_code_hash is dead, so resuming it can't work. Drop the pending
      // state and offer a fresh code right here.
      clearPending();
      code = ""; renderCode();
      padEnabled(false);
      status("code-status", tr("CodeExpired"), "err");
      retryBtn.style.display = "block";
      return;
    }
    code = ""; renderCode();
    status("code-status", tr("Error", { error: err }), "err");
  }
};

// --- step: 2FA via SRP (password never leaves the device) -------------------
document.getElementById("send-password").onclick = async () => {
  const btn = document.getElementById("send-password");
  const password = document.getElementById("password").value;
  if (!password) return;
  btn.disabled = true;
  status("pw-status", tr("2faChecking"));
  try {
    if (!computeCheck) throw new Error("computeCheck not found in the GramJS bundle — see log");
    const pwInfo = await client.invoke(new Api.account.GetPassword());
    const srp = await computeCheck(pwInfo, password);
    await client.invoke(new Api.auth.CheckPassword({ password: srp }));
    log("2FA ok");
    document.getElementById("password").value = "";
    await finish();
  } catch (e) {
    const err = e.errorMessage || e.message || String(e);
    log("2FA ->", err);
    status("pw-status", tr("Error", { error: err }), "err");
    btn.disabled = false;
  }
};

// --- step: hand the session components to the backend ------------------------
async function finish() {
  const dcId = client.session.dcId;
  const keyBytes = client.session.authKey.getKey();
  const authKey = bytesToBase64(keyBytes);
  log("exporting session: dcId =", String(dcId), "authKey bytes =", String(keyBytes.length));

  const res = await fetch(`/connect/${TOKEN}/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initData: (tg && tg.initData) || "", dcId, authKey }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    log("backend REJECTED:", body.detail || res.status);
    const msg = tr("ServerRejected", { error: body.detail || res.status });
    status("code-status", msg, "err");
    status("pw-status", msg, "err");
    if (res.status === 410) {
      // token died between opening the page and finishing (e.g. someone else
      // finished the same resumed attempt first) — nothing to retry here.
      expire("backend returned 410 on handoff");
    }
    return;
  }
  // Success: the pending attempt is no longer meaningful — before this point
  // it's a pre-auth key, after it the account is already linked, so there is
  // nothing left worth keeping in the browser.
  clearPending();
  log("backend accepted. account =", body.account);
  ringSettle("ok");
  show("step-done");
  // status() writes innerHTML now, so escape the server-supplied value
  status("done-status", "@" + esc(body.account.username || body.account.user_id));
  if (tg) tg.HapticFeedback && tg.HapticFeedback.notificationOccurred("success");
}

// --- entry point: fresh attempt, or resume one already in progress ----------
(async function start() {
  // Refuse to show anything to whoever did not arrive through the bot. The
  // backend rejects a save without valid initData regardless — this just stops
  // the page existing as a bare, Telegram-branded login form for anyone who
  // finds the URL, which is exactly the shape of a phishing site.
  if (!tg || !tg.initData) {
    log("blocked: no initData — nothing shown");
    show("step-outside");
    return;
  }

  if (EXPIRED_ON_LOAD) {
    log("token already expired/used server-side");
    expire("server said the token is expired or used");
    return;
  }

  startTimer();

  const pending = loadPending();
  show("step-code");

  if (pending && pending.phoneCodeHash) {
    // The code is already in the user's Telegram from the earlier visit —
    // don't request a second one, just re-attach to the same login.
    log("resuming a saved attempt for this token");
    phoneCodeHash = pending.phoneCodeHash;
    document.getElementById("code-sub").innerHTML = tr("CodeSent");
    padEnabled(false);
    status("code-status", tr("Resuming"));
    try {
      await ensureClient(pending.dcId, pending.authKeyB64);
      status("code-status", "");
      padEnabled(true);
    } catch (e) {
      log("resume failed, requesting a fresh code:", e.message || String(e));
      clearPending();
      client = null;
      await sendCodeNow();
    }
    return;
  }

  await sendCodeNow();
})();

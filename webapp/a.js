/* Зооконсьерж: собственный счётчик посещений. Без сторонних сервисов, без имени, телефона и содержимого полей.
   Что считаем: просмотры страниц, нажатия на кнопки и ссылки, глубину прокрутки, какие поля формы начали заполнять,
   сколько секунд человек был на странице. Уважает «Не отслеживать» в браузере. Администратора сервер не считает. */
(function () {
  "use strict";
  if (navigator.doNotTrack === "1" || window.doNotTrack === "1") return;
  var KEY = "zc_a", mem = {};
  function load() { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return mem; } }
  function save(o) { mem = o; try { localStorage.setItem(KEY, JSON.stringify(o)); } catch (e) {} }
  function rnd() { var a = ""; while (a.length < 16) a += Math.random().toString(36).slice(2); return a.slice(0, 16); }

  var S = load(), now = Date.now();
  if (!S.v) S.v = rnd();
  if (!S.s || !S.t || now - S.t > 1800000) S.s = rnd();   // новая «сессия» после 30 минут тишины
  S.t = now; save(S);

  var path = location.pathname, Q = [], act = 0, last = now, seen = {}, maxScroll = 0, left = false;
  function ev(t, o) { o = o || {}; o.t = t; o.p = path; Q.push(o); if (Q.length >= 20) flush(); }
  function flush() {
    if (!Q.length) return;
    var body = JSON.stringify({ v: S.v, s: S.s, d: Q.splice(0, 50) });
    try { if (navigator.sendBeacon && navigator.sendBeacon("/api/a", new Blob([body], { type: "text/plain" }))) return; } catch (e) {}
    try { fetch("/api/a", { method: "POST", body: body, keepalive: true, headers: { "Content-Type": "text/plain" } }); } catch (e) {}
  }
  function touch() { last = Date.now(); }

  // просмотр страницы: откуда пришли (только сайт-источник, без адресов страниц) и метки utm_source
  var ref = "";
  try { var u = new URL(document.referrer); if (u.host && u.host !== location.host) ref = u.host.replace(/^www\./, ""); } catch (e) {}
  var utm = ""; try { utm = new URLSearchParams(location.search).get("utm_source") || ""; } catch (e) {}
  ev("pv", { r: ref || utm.slice(0, 40), w: window.innerWidth, ti: (document.title || "").slice(0, 80) });

  // нажатия: текст кнопки или ссылки и куда она ведёт; содержимое полей не читаем
  document.addEventListener("click", function (e) {
    touch();
    var el = e.target && e.target.closest ? e.target.closest("a,button,[role=button],input[type=submit],summary,label,select") : null;
    if (!el) return;
    var txt = (el.getAttribute("aria-label") || el.innerText || el.value || el.id || el.tagName || "").replace(/\s+/g, " ").trim().slice(0, 50);
    var href = el.tagName === "A" ? (el.getAttribute("href") || "") : "";
    if (href && href.charAt(0) !== "/" && href.indexOf("tel:") !== 0 && href.indexOf("mailto:") !== 0) { try { href = new URL(href).host; } catch (x) { href = ""; } }
    ev("click", { g: txt + (href ? " → " + href.slice(0, 60) : "") });
  }, true);

  // глубина прокрутки: 25 / 50 / 75 / 100 %
  window.addEventListener("scroll", function () {
    touch();
    var h = document.documentElement, total = Math.max(h.scrollHeight, 1);
    var pct = Math.min(100, Math.round((window.scrollY + window.innerHeight) / total * 100));
    if (pct > maxScroll) maxScroll = pct;
    [25, 50, 75, 100].forEach(function (n) { if (pct >= n - (n === 100 ? 2 : 0) && !seen["s" + n]) { seen["s" + n] = 1; ev("scroll", { n: n }); } });
  }, { passive: true });

  // поля формы: какое поле начали заполнять (только его имя) и отправили ли форму
  document.addEventListener("focusin", function (e) {
    touch();
    var el = e.target;
    if (!el || !/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName) || el.type === "password") return;
    var name = (el.name || el.id || "").slice(0, 40);
    if (name && !seen["f" + name]) { seen["f" + name] = 1; ev("focus", { g: name }); }
  });
  document.addEventListener("submit", function (e) { var f = e.target; ev("submit", { g: ((f && f.getAttribute && f.getAttribute("action")) || path).slice(0, 60) }); flush(); }, true);
  ["keydown", "touchstart", "mousemove"].forEach(function (n) { document.addEventListener(n, touch, { passive: true }); });

  // время на странице: секунды, пока вкладка открыта и человек что-то делал за последние 3 минуты
  setInterval(function () { if (document.visibilityState === "visible" && Date.now() - last < 180000) act++; }, 1000);
  setInterval(function () {
    if (document.visibilityState !== "visible" || Date.now() - last >= 180000) return;
    S.t = Date.now(); save(S); ev("ping", { n: act }); flush();
  }, 15000);
  function leave() { if (left) return; left = true; ev("leave", { n: act, m: maxScroll }); flush(); }
  window.addEventListener("pagehide", leave);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") { left = false; leave(); left = false; }
  });
})();

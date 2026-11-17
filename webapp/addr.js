/* Подбор адреса для сайта и чата: подсказки как в картографических сервисах, определение места, выбор точки на карте.
   Поиск идёт из браузера клиента (Photon по данным OpenStreetMap); если он недоступен — через наш сервер (/api/addr). */
(function () {
  var BBOX = [35.1, 54.2, 40.3, 56.97];                     // Москва и область: lon_min, lat_min, lon_max, lat_max
  var STATES = ['москва', 'московская область', 'moscow', 'moscow oblast'];
  function inBox(lat, lon) { return lon >= BBOX[0] && lon <= BBOX[2] && lat >= BBOX[1] && lat <= BBOX[3]; }
  function label(p) {
    var parts = [p.street || p.name || '', p.housenumber, p.district || p.suburb, p.city || p.town || p.village || p.locality || p.county], seen = [];
    parts.forEach(function (x) { if (x && seen.indexOf(x) < 0) seen.push(x); });
    return seen.join(', ');
  }
  function photon(q) {
    var url = 'https://photon.komoot.io/api/?limit=10&lang=default&lat=55.75&lon=37.62&bbox=' + BBOX.join(',') + '&q=' + encodeURIComponent(q);
    return fetch(url).then(function (r) { if (!r.ok) throw new Error('photon'); return r.json(); }).then(function (j) {
      var items = [], street = false;
      (j.features || []).forEach(function (f) {
        var p = f.properties || {}, c = f.geometry && f.geometry.coordinates;
        if (!c || !inBox(c[1], c[0])) return;
        if (p.state && STATES.indexOf(String(p.state).toLowerCase()) < 0) return;
        if (!p.housenumber) { street = true; return; }
        var l = label(p);
        if (l && !items.some(function (i) { return i.label === l; })) items.push({ label: l, lat: c[1], lon: c[0] });
      });
      return { items: items.slice(0, 5), street_only: street && !items.length };
    });
  }
  function server(q) {
    return fetch('/api/addr?q=' + encodeURIComponent(q)).then(function (r) { return r.json(); }).then(function (j) {
      return { items: (j.items || []).map(function (l) { return { label: l }; }), street_only: !!j.street_only, down: !!j.down };
    });
  }
  // Photon не нашёл дом (бывает с «16/23», «стр. 2») — спрашиваем второй источник (Nominatim через наш сервер) и берём лучшее
  function search(q) {
    return photon(q).then(function (d) {
      if (d.items.length) return d;
      return server(q).then(function (s) {
        return s.items.length ? s : { items: [], street_only: d.street_only || s.street_only, down: s.down && !d.street_only };
      }, function () { return d; });
    }, function () { return server(q); });
  }

  // Адрес по точке: сначала Photon, затем Nominatim. house=false — нашли только улицу
  function reverse(lat, lon) {
    return fetch('https://photon.komoot.io/reverse?lang=default&lat=' + lat + '&lon=' + lon).then(function (r) { return r.json(); }).then(function (j) {
      var f = (j.features || [])[0], p = f && f.properties;
      if (!p) throw new Error('empty');
      return { label: label(p), house: !!p.housenumber };
    }).catch(function () {
      return fetch('https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=18&addressdetails=1&accept-language=ru&lat=' + lat + '&lon=' + lon)
        .then(function (r) { return r.json(); }).then(function (j) {
          var a = (j && j.address) || {}, town = a.city || a.town || a.village || a.hamlet || a.municipality || '';
          var parts = [a.road || a.pedestrian || j.name, a.house_number, a.suburb !== town ? a.suburb : '', town].filter(Boolean);
          return { label: parts.join(', '), house: !!a.house_number };
        });
    });
  }

  // Геопозиция: сначала GPS, потом обычная точность. Ошибка: {code:1} — запретили, иначе не определилось
  function locate() {
    return new Promise(function (resolve, reject) {
      if (!navigator.geolocation) { reject({ code: 0 }); return; }
      function ok(p) { resolve({ lat: p.coords.latitude, lon: p.coords.longitude, acc: p.coords.accuracy }); }
      navigator.geolocation.getCurrentPosition(ok, function (e) {
        if (e && e.code === 1) { reject(e); return; }
        navigator.geolocation.getCurrentPosition(ok, reject, { enableHighAccuracy: false, timeout: 20000, maximumAge: 300000 });
      }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
    });
  }
  function locateError(e) {
    if (e && e.code === 1) return 'Браузер не дал доступ к геопозиции. Разрешите её в настройках или выберите адрес из подсказок.';
    return 'Не удалось определить место. Впишите адрес или отметьте точку на карте.';
  }

  // Список подсказок под (или над) полем. opts: {box, mode, min, enabled(), onPick(item), onChange()}
  function bind(inp, opts) {
    var box = opts.box, timer = null, seq = 0, cur = [], active = -1, min = opts.min || 4;
    function clear() { box.innerHTML = ''; cur = []; active = -1; box.classList.remove('open'); }
    function note(text) { var n = document.createElement('div'); n.className = 'sug-note'; n.textContent = text; box.appendChild(n); box.classList.add('open'); }
    function mark() {
      var bs = box.querySelectorAll('.sug');
      for (var i = 0; i < bs.length; i++) bs[i].classList.toggle('on', i === active);
    }
    function draw(d) {
      box.innerHTML = ''; cur = d.items || []; active = -1;
      cur.forEach(function (it, i) {
        var parts = it.label.split(', '), b = document.createElement('button');
        b.type = 'button'; b.className = 'sug'; b.setAttribute('data-pick', it.label); b.setAttribute('data-for', inp.id);
        var main = document.createElement('span'); main.className = 'sug-main'; main.textContent = parts.slice(0, 2).join(', ');
        b.appendChild(main);
        if (parts.length > 2) { var sub = document.createElement('span'); sub.className = 'sug-sub'; sub.textContent = parts.slice(2).join(', '); b.appendChild(sub); }
        b.addEventListener('mousedown', function (e) { e.preventDefault(); });          // поле не теряет фокус
        b.addEventListener('click', function () { pick(i); });
        box.appendChild(b);
      });
      if (!cur.length && !d.down) note(d.street_only ? 'Нашли улицу. Допишите номер дома, например: Ленина 5.' : 'Не нашли такой адрес. Проверьте написание, добавьте город и номер дома или отметьте точку на карте.');
      else if (!cur.length) note('Подсказки сейчас недоступны. Впишите адрес полностью или отметьте точку на карте.');
      box.classList.add('open');
    }
    function pick(i) { var it = cur[i]; if (!it) return; clear(); opts.onPick(it); }
    inp.addEventListener('input', function () {
      if (opts.onChange) opts.onChange();
      clearTimeout(timer); var q = inp.value.trim(), my = ++seq;
      if (opts.enabled && !opts.enabled()) { clear(); return; }
      if (q.length < min) { clear(); return; }
      timer = setTimeout(function () { search(q).then(function (d) { if (my === seq) draw(d); }).catch(function () { if (my === seq) draw({ items: [], down: true }); }); }, 350);
    });
    inp.addEventListener('keydown', function (e) {
      if (!cur.length) return;
      if (e.key === 'ArrowDown') { active = (active + 1) % cur.length; mark(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { active = (active - 1 + cur.length) % cur.length; mark(); e.preventDefault(); }
      else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); e.stopPropagation(); pick(active); }
      else if (e.key === 'Escape') clear();
    });
    return { clear: clear, note: function (t) { clear(); note(t); } };
  }

  // Карта во всплывающем окне: булавку можно сдвинуть, адрес по точке определяется заново
  function openMap(o) {
    var wrap = document.createElement('div'); wrap.className = 'zc-modal';
    var q = '?embed=1' + (o.pt ? '&pt=' + o.pt : '') + (o.lat != null ? '&lat=' + o.lat + '&lon=' + o.lon : '') + (o.label ? '&label=' + encodeURIComponent(o.label) : '') + (o.house === 0 ? '&h=0' : '');
    wrap.innerHTML = '<div class="zc-modal-box"><div class="zc-modal-bar"><b>Отметьте место на карте</b><button type="button" class="zc-modal-x">✕ Закрыть</button></div><iframe title="Карта" src="/map' + q + '"></iframe></div>';
    function close() { window.removeEventListener('message', onMsg); wrap.remove(); document.documentElement.style.overflow = ''; }
    function onMsg(e) {
      if (e.origin !== location.origin || !e.data || !e.data.zcPick) return;
      close(); o.onPick(e.data.zcPick);
    }
    window.addEventListener('message', onMsg);
    wrap.querySelector('.zc-modal-x').onclick = close;
    wrap.addEventListener('click', function (e) { if (e.target === wrap) close(); });
    document.documentElement.style.overflow = 'hidden';
    document.body.appendChild(wrap);
  }

  window.zcAddr = { search: search, reverse: reverse, locate: locate, locateError: locateError, bind: bind, openMap: openMap, inBox: inBox };
})();

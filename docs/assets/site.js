// The SEL documentation site: tabs, theme, menu, copy buttons, and which
// heading of the page is in view. Everything works without it -- the first tab
// is shown and the rest hidden, so this only adds switching.
(function () {
  'use strict';

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, value);
    } catch (e) { /* private mode: remember nothing */ }
    return null;
  }

  // --- tabs: one choice of language for every group, remembered across pages
  function selectTab(name) {
    document.querySelectorAll('.tabs').forEach(function (group) {
      var buttons = group.querySelectorAll('.tab-list button');
      var has = Array.prototype.some.call(buttons, function (b) { return b.dataset.tab === name; });
      if (!has) return;
      buttons.forEach(function (b) { b.setAttribute('aria-selected', String(b.dataset.tab === name)); });
      group.querySelectorAll('.tab-panel').forEach(function (p) { p.hidden = p.dataset.tab !== name; });
    });
  }
  document.addEventListener('click', function (event) {
    var button = event.target.closest('.tab-list button');
    if (!button) return;
    var top = button.getBoundingClientRect().top;
    selectTab(button.dataset.tab);
    store('sel-lang', button.dataset.tab);
    // Keep the clicked tab where it was: other groups above it may change height.
    window.scrollBy(0, button.getBoundingClientRect().top - top);
  });
  var remembered = store('sel-lang');
  if (remembered) selectTab(remembered);

  // --- theme
  var theme = document.querySelector('.theme');
  if (theme) {
    theme.addEventListener('click', function () {
      var root = document.documentElement;
      var dark = root.dataset.theme
        ? root.dataset.theme === 'dark'
        : window.matchMedia('(prefers-color-scheme: dark)').matches;
      root.dataset.theme = dark ? 'light' : 'dark';
      store('sel-theme', root.dataset.theme);
    });
  }

  // --- menu on narrow screens
  var menu = document.querySelector('.menu');
  if (menu) {
    menu.addEventListener('click', function () {
      var open = document.body.classList.toggle('nav-open');
      menu.setAttribute('aria-expanded', String(open));
    });
  }

  // --- copy buttons
  document.addEventListener('click', function (event) {
    var button = event.target.closest('.copy');
    if (!button) return;
    var code = button.parentElement.querySelector('code');
    if (!code || !navigator.clipboard) return;
    navigator.clipboard.writeText(code.innerText).then(function () {
      button.textContent = 'Copied';
      setTimeout(function () { button.textContent = 'Copy'; }, 1200);
    });
  });

  // --- the heading in view, marked in "On this page"
  var links = document.querySelectorAll('.toc a');
  if (links.length && 'IntersectionObserver' in window) {
    var byId = {};
    links.forEach(function (a) { byId[a.getAttribute('href').slice(1)] = a; });
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        links.forEach(function (a) { a.classList.remove('active'); });
        var link = byId[entry.target.id];
        if (link) link.classList.add('active');
      });
    }, { rootMargin: '-70px 0px -70% 0px' });
    document.querySelectorAll('.doc h2[id], .doc h3[id]').forEach(function (h) { observer.observe(h); });
  }
}());

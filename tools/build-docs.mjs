#!/usr/bin/env node
// Builds the documentation site: every page docs/nav.json lists, from the same
// Markdown GitHub renders, into styled HTML under site/.
//
//   node tools/build-docs.mjs                 build into site/
//   node tools/build-docs.mjs --out DIR       build into DIR
//   node tools/build-docs.mjs --check         build nothing; fail on a broken link or anchor
//   node tools/build-docs.mjs --serve [PORT]  build, then serve site/ on 127.0.0.1:PORT (8080)
//
// ONE SOURCE, TWO RENDERINGS. The Markdown is written for GitHub first, and the
// site is a rendering of it rather than a second copy, so nothing here may need
// syntax GitHub does not show sensibly:
//
//   - A code sample in five languages is a run of <details> blocks between
//     <!-- tabs --> and <!-- /tabs -->. GitHub shows collapsible sections, the
//     first one open; the site shows tabs, and remembers the language a reader
//     picked across pages.
//   - Heading anchors are GitHub's (github-slugger), so a link to #some-heading
//     works in both places, and the check below proves every one resolves.
//   - A relative link to another page becomes a link to its .html; a link to
//     anything else in the repository -- an example, a spec file not in the
//     nav, a directory -- becomes a link to it on GitHub.
//   - GitHub's alert blockquotes (> [!NOTE]) become styled callouts.
//
// Every relative link and every #anchor is checked, in the Markdown sense: the
// target file exists, and the heading exists on the target page. A broken link
// is broken on GitHub too, so --check is a gate layer as well as a build step.

import { readFileSync, writeFileSync, mkdirSync, existsSync, statSync, cpSync, rmSync, readdirSync } from 'node:fs';
import { dirname, join, relative, resolve, posix, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:http';
import { Marked } from 'marked';
import hljs from 'highlight.js';
import GithubSlugger from 'github-slugger';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const CHECK = args.includes('--check');
const SERVE = args.includes('--serve');
const OUT = resolve(ROOT, args.includes('--out') ? args[args.indexOf('--out') + 1] : 'site');

const NAV = JSON.parse(readFileSync(join(ROOT, 'docs/nav.json'), 'utf8'));
const SITE = NAV.site;
// A nav entry is a page path, or { page, title } when the page's own heading is
// not what the sidebar should say (the README has none; the spec's is long).
const entryPage = (e) => (typeof e === 'string' ? e : e.page);
const NAV_TITLES = new Map(NAV.sections.flatMap((s) => s.pages).filter((e) => typeof e !== 'string').map((e) => [e.page, e.title]));
const PAGES = NAV.sections.flatMap((s) => s.pages.map(entryPage));
const PAGE_SET = new Set(PAGES);

// --- SEL for highlight.js ---------------------------------------------------------
// Keywords from the lexer, builtins from the manifest, and one thing no other
// language has: in a documentation block, `expr  => result` shows the result as
// a dimmed annotation, since that is what the doc checker reads it as.

const BUILTINS = Object.keys(JSON.parse(readFileSync(join(ROOT, 'spec/builtins.json'), 'utf8')).builtins ?? {});
function selLanguage(h) {
  const interpolation = { scope: 'subst', begin: /\{/, end: /\}/, contains: [] };
  const quoted = {
    scope: 'string', begin: /"/, end: /"/,
    contains: [{ scope: 'char.escape', begin: /\\(u\{[0-9A-Fa-f]+\}|.)/ }, interpolation],
  };
  const raw = { scope: 'string', begin: /'/, end: /'/, contains: [{ begin: /''/ }] };
  const number = { scope: 'number', begin: /\b\d+(\.\d+)?\b/ };
  const result = { scope: 'comment', begin: /\s=>\s/, end: /$/, excludeBegin: false };
  const operator = { scope: 'operator', begin: /\.>|\?\?\?|\?\?|\$(==|!=|<=|>=|<|>)|==|!=|<=|>=|[-+*/%&=<>;,]/ };
  const binder = { scope: 'variable.language', begin: /\b_K\b|\b_\b|\b_[12]\b/ };
  const keywords = {
    $pattern: /[A-Za-z_][A-Za-z0-9_]*/,
    literal: ['TRUE', 'FALSE', 'NULL'],
    keyword: ['AND', 'OR', 'NOT', 'XOR', 'EQL', 'IN', 'BAND', 'BOR', 'BXOR'],
    built_in: BUILTINS,
  };
  interpolation.contains = [quoted, raw, number, operator, binder];
  interpolation.keywords = keywords;
  return {
    name: 'SEL', aliases: ['sel'], case_insensitive: true, keywords,
    contains: [h.COMMENT(/#/, /$/), result, quoted, raw, number, operator, binder],
  };
}
hljs.registerLanguage('sel', selLanguage);

// --- rendering ----------------------------------------------------------------------

const outPath = (page) => (page === 'README.md' ? 'index.html'
  : page.endsWith('/README.md') ? page.replace(/README\.md$/, 'index.html')
    : page.replace(/\.md$/, '.html'));
const githubUrl = (path) => {
  const isDir = existsSync(join(ROOT, path)) && statSync(join(ROOT, path)).isDirectory();
  return `${SITE.repo}/${isDir ? 'tree' : 'blob'}/${SITE.branch}/${path.replace(/\/$/, '')}`;
};
const escapeHtml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const problems = [];
const anchorsOf = new Map();          // page -> Set of heading ids
const pendingAnchors = [];            // [fromPage, targetPage, anchor]

// A link as written in `page`, resolved: the href the site uses, after checking
// the target exists. Absolute URLs and mailto pass through.
function resolveLink(page, href, what = 'link') {
  if (!href || /^[a-z][a-z0-9+.-]*:/i.test(href)) return href;
  const [pathPart, anchor] = href.split('#');
  if (pathPart === '') {
    if (anchor) pendingAnchors.push([page, page, anchor]);
    return href;
  }
  const target = posix.normalize(posix.join(posix.dirname(page), decodeURI(pathPart)));
  if (!existsSync(join(ROOT, target))) {
    problems.push(`${page}: ${what} to ${href}, which does not exist (${target})`);
    return href;
  }
  if (PAGE_SET.has(target)) {
    if (anchor) pendingAnchors.push([page, target, anchor]);
    const rel = posix.relative(posix.dirname(outPath(page)), outPath(target)) || posix.basename(outPath(target));
    return rel + (anchor ? `#${anchor}` : '');
  }
  if (target.startsWith('docs/assets/')) {
    return posix.relative(posix.dirname(outPath(page)), target);
  }
  if (target.endsWith('.md') && target.startsWith('docs/') && !PAGE_SET.has(target)) {
    problems.push(`${page}: ${what} to ${target}, a docs page docs/nav.json does not list`);
  }
  return githubUrl(target) + (anchor ? `#${anchor}` : '');
}

function rewriteHtmlLinks(page, html) {
  return html
    .replace(/(\s(?:href|src))="([^"]*)"/g, (_, attr, v) => `${attr}="${escapeHtml(resolveLink(page, v, 'html link'))}"`)
    .replace(/(\ssrcset)="([^"]*)"/g, (_, attr, v) => `${attr}="${v.split(',').map((part) => {
      const [url, ...rest] = part.trim().split(/\s+/);
      return [resolveLink(page, url, 'srcset'), ...rest].join(' ');
    }).join(', ')}"`);
}

const ALERTS = { NOTE: 'Note', TIP: 'Tip', IMPORTANT: 'Important', WARNING: 'Warning', CAUTION: 'Caution' };

function makeMarked(page, slugger, toc) {
  const marked = new Marked({ gfm: true });
  marked.use({
    renderer: {
      heading({ tokens, depth, text }) {
        const inner = this.parser.parseInline(tokens);
        const plain = text.replace(/<[^>]+>/g, '');
        const id = slugger.slug(plain);
        anchorsOf.get(page).add(id);
        if (depth === 2 || depth === 3) toc.push({ depth, id, html: inner.replace(/<a [^>]*>|<\/a>/g, '') });
        return `<h${depth} id="${id}"><a class="anchor" href="#${id}" aria-hidden="true">#</a>${inner}</h${depth}>\n`;
      },
      code({ text, lang }) {
        const language = (lang || '').trim().split(/\s+/)[0].toLowerCase();
        const known = language && hljs.getLanguage(language);
        const body = known ? hljs.highlight(text, { language, ignoreIllegals: true }).value : escapeHtml(text);
        const label = language ? `<span class="code-lang">${escapeHtml(language)}</span>` : '';
        return `<div class="code">${label}<button class="copy" type="button" aria-label="Copy">Copy</button>`
          + `<pre><code class="hljs${known ? ` language-${language}` : ''}">${body}</code></pre></div>\n`;
      },
      link({ href, title, tokens }) {
        const inner = this.parser.parseInline(tokens);
        const url = resolveLink(page, href);
        const external = /^https?:/.test(url) ? ' rel="noopener"' : '';
        return `<a href="${escapeHtml(url)}"${title ? ` title="${escapeHtml(title)}"` : ''}${external}>${inner}</a>`;
      },
      image({ href, title, text }) {
        return `<img src="${escapeHtml(resolveLink(page, href, 'image'))}" alt="${escapeHtml(text)}"${title ? ` title="${escapeHtml(title)}"` : ''}>`;
      },
      html({ text }) {
        if (/^\s*<!--/.test(text) && /-->\s*$/.test(text) && !text.includes('data-tabs')) return '';
        return rewriteHtmlLinks(page, text);
      },
      blockquote({ tokens }) {
        const first = tokens[0];
        const m = first && first.type === 'paragraph' && /^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/.exec(first.text);
        if (!m) return `<blockquote>${this.parser.parse(tokens)}</blockquote>\n`;
        const kind = m[1];
        const rest = first.text.slice(m[0].length);
        const body = marked.parse(rest + '\n\n') + this.parser.parse(tokens.slice(1));
        return `<div class="callout callout-${kind.toLowerCase()}"><p class="callout-title">${ALERTS[kind]}</p>${body}</div>\n`;
      },
    },
  });
  return marked;
}
// <!-- tabs --> ... <!-- /tabs --> around <details><summary>Lang</summary>...</details>
// runs become tab widgets. They are cut out before Markdown sees the page and
// put back after, each panel rendered on its own.
function extractTabs(source, render) {
  const groups = [];
  const text = source.replace(/<!-- tabs -->([\s\S]*?)<!-- \/tabs -->/g, (_, inner) => {
    const panels = [...inner.matchAll(/<details(?:\s+open)?>\s*<summary>([\s\S]*?)<\/summary>([\s\S]*?)<\/details>/g)]
      .map(([, label, body]) => ({ label: label.replace(/<[^>]+>/g, '').trim(), html: render(body).replace(/<table>/g, '<div class="table-wrap"><table>').replace(/<\/table>/g, '</table></div>') }));
    groups.push(panels);
    return `\n\n<div data-tabs="${groups.length - 1}"></div>\n\n`;
  });
  return { text, groups };
}

function tabsHtml(panels) {
  const id = (label) => label.toLowerCase().replace(/\+/g, 'p').replace(/[^a-z0-9]+/g, '-');
  const buttons = panels.map((p, i) => `<button role="tab" type="button" data-tab="${id(p.label)}" aria-selected="${i === 0}">${escapeHtml(p.label)}</button>`).join('');
  const bodies = panels.map((p, i) => `<div class="tab-panel" role="tabpanel" data-tab="${id(p.label)}"${i === 0 ? '' : ' hidden'}>${p.html}</div>`).join('');
  return `<div class="tabs"><div class="tab-list" role="tablist">${buttons}</div>${bodies}</div>`;
}

function renderPage(page) {
  const source = readFileSync(join(ROOT, page), 'utf8');
  anchorsOf.set(page, new Set());
  const slugger = new GithubSlugger();
  const toc = [];
  const marked = makeMarked(page, slugger, toc);
  const panelMarked = makeMarked(page, new GithubSlugger(), []);
  const { text, groups } = extractTabs(source, (md) => panelMarked.parse(md));
  let html = marked.parse(text).replace(/<table>/g, '<div class="table-wrap"><table>').replace(/<\/table>/g, '</table></div>');
  html = html.replace(/<div data-tabs="(\d+)"><\/div>/g, (_, n) => tabsHtml(groups[Number(n)]));
  const title = NAV_TITLES.get(page) ?? (/^#\s+(.+)$/m.exec(source)?.[1] ?? page).replace(/[`*_]/g, '');
  return { page, title, html, toc };
}

// --- the page shell ----------------------------------------------------------------

function shell({ page, title, html, toc }, prev, next, titles) {
  const out = outPath(page);
  const root = posix.relative(posix.dirname(out), '.') || '.';
  const up = (p) => `${root}/${p}`;
  const link = (target) => posix.relative(posix.dirname(out), outPath(target)) || posix.basename(outPath(target));
  const nav = NAV.sections.map((section) => `<div class="nav-section"><p class="nav-title">${escapeHtml(section.title)}</p><ul>${section.pages.map(entryPage).map((p) => `<li${p === page ? ' class="current"' : ''}><a href="${link(p)}">${escapeHtml(titles.get(p))}</a></li>`).join('')}</ul></div>`).join('');
  const tocHtml = toc.length > 1 ? `<nav class="toc" aria-label="On this page"><p class="toc-title">On this page</p><ul>${toc.map((t) => `<li class="toc-${t.depth}"><a href="#${t.id}">${t.html}</a></li>`).join('')}</ul></nav>` : '';
  const pager = `<nav class="pager">${prev ? `<a class="prev" href="${link(prev)}"><span>Previous</span>${escapeHtml(titles.get(prev))}</a>` : '<span></span>'}${next ? `<a class="next" href="${link(next)}"><span>Next</span>${escapeHtml(titles.get(next))}</a>` : ''}</nav>`;
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(page === 'README.md' ? `${SITE.title} — ${SITE.tagline}` : `${title} — ${SITE.title}`)}</title>
<link rel="icon" href="${up('docs/assets/logo-mark.svg')}" type="image/svg+xml">
<link rel="stylesheet" href="${up('docs/assets/site.css')}">
<script>try{var t=localStorage.getItem('sel-theme');if(t)document.documentElement.dataset.theme=t;}catch(e){}</script>
</head>
<body>
<header class="topbar">
  <button class="menu" type="button" aria-label="Menu" aria-expanded="false">☰</button>
  <a class="brand" href="${link('README.md')}"><img src="${up('docs/assets/logo-mark.svg')}" alt="" width="28" height="28"><span>${escapeHtml(SITE.title)}</span><small>${escapeHtml(SITE.tagline)}</small></a>
  <div class="topbar-end">
    <button class="theme" type="button" aria-label="Toggle dark mode">◐</button>
    <a class="repo" href="${SITE.repo}" rel="noopener">GitHub</a>
  </div>
</header>
<div class="layout">
  <aside class="sidebar"><nav aria-label="Documentation">${nav}</nav></aside>
  <main class="content">
    <article class="doc">${html}</article>
    <p class="source"><a href="${githubUrl(page)}" rel="noopener">View this page's Markdown on GitHub</a></p>
    ${pager}
  </main>
  ${tocHtml}
</div>
<script src="${up('docs/assets/site.js')}"></script>
</body>
</html>
`;
}

// --- main ----------------------------------------------------------------------------

for (const page of PAGES) {
  if (!existsSync(join(ROOT, page))) problems.push(`docs/nav.json lists ${page}, which does not exist`);
}
const rendered = PAGES.filter((p) => existsSync(join(ROOT, p))).map(renderPage);
for (const [from, target, anchor] of pendingAnchors) {
  const ids = anchorsOf.get(target);
  if (ids && !ids.has(decodeURIComponent(anchor))) problems.push(`${from}: link to ${target}#${anchor}, which has no such heading`);
}

if (problems.length) {
  for (const p of problems) process.stderr.write(`docs: ${p}\n`);
  process.stderr.write(`docs: ${problems.length} broken link(s)\n`);
  process.exit(1);
}
if (CHECK) {
  process.stdout.write(`docs: ${rendered.length} pages, every link and anchor resolves\n`);
  process.exit(0);
}

rmSync(OUT, { recursive: true, force: true });
const titles = new Map(rendered.map((r) => [r.page, r.title]));
rendered.forEach((r, i) => {
  const file = join(OUT, outPath(r.page));
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, shell(r, rendered[i - 1]?.page, rendered[i + 1]?.page, titles));
});
cpSync(join(ROOT, 'docs/assets'), join(OUT, 'docs/assets'), { recursive: true });
// Served for any missing path, at any depth, so its relative links are anchored
// at the site root.
writeFileSync(join(OUT, '404.html'), shell({ page: 'README.md', title: 'Not found', html: '<h1>Not found</h1><p>There is no page here. <a href="index.html">Back to the start</a>.</p>', toc: [] }, null, null, titles)
  .replace('<head>', '<head>\n<base href="/">'));
process.stdout.write(`docs: ${rendered.length} pages written to ${relative(ROOT, OUT) || '.'}\n`);

if (SERVE) {
  const port = Number(args[args.indexOf('--serve') + 1]) || 8080;
  const types = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.png': 'image/png' };
  createServer((req, res) => {
    let path = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    if (path.endsWith('/')) path += 'index.html';
    const file = join(OUT, path);
    if (!file.startsWith(OUT) || !existsSync(file) || statSync(file).isDirectory()) {
      res.writeHead(404, { 'content-type': types['.html'] });
      res.end(readFileSync(join(OUT, '404.html')));
      return;
    }
    res.writeHead(200, { 'content-type': types[extname(file)] ?? 'application/octet-stream' });
    res.end(readFileSync(file));
  }).listen(port, '127.0.0.1', () => process.stdout.write(`docs: serving on http://127.0.0.1:${port}/\n`));
}

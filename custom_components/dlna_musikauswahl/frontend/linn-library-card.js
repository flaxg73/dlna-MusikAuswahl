/*
 * Linn Library Card – browse the DLNA music servers of the "DLNA Musikauswahl"
 * integration and load music into the playlist of a Linn DS/DSM.
 *
 *   type: custom:linn-library-card
 *   entity: media_player.wohnzimmer
 *   title: Musik            # optional
 *   track_action: play      # click on a track: play | next | add | replace
 *   view: auto              # auto | grid | list
 *   max_height: 520px       # optional, height of the scrolling area
 */

(() => {
const DOMAIN = "dlna_musikauswahl";
const CARD_VERSION = "0.3.0";

const STRINGS = {
  de: {
    library: "Musik",
    servers: "Musikserver",
    back: "Zurück",
    home: "Start",
    search: "Suchen",
    searchPlaceholder: "Titel, Album oder Interpret …",
    searchIn: (s) => `in „${s}“`,
    noResults: "Nichts gefunden.",
    empty: "Dieser Ordner ist leer.",
    play: "Abspielen",
    playNow: "Jetzt abspielen",
    replace: "Playlist ersetzen",
    next: "Als Nächstes",
    add: "Hinzufügen",
    open: "Öffnen",
    loadMore: (n, total) => `Mehr laden (${n} von ${total})`,
    tracks: (n) => (n === 1 ? "1 Titel" : `${n} Titel`),
    items: (n) => (n === 1 ? "1 Eintrag" : `${n} Einträge`),
    added: {
      play: (n) => `▶ ${n === 1 ? "1 Titel wird" : `${n} Titel werden`} abgespielt`,
      replace: (n) => `▶ Playlist ersetzt (${n} Titel)`,
      next: (n) => `⤒ ${n === 1 ? "1 Titel" : `${n} Titel`} als Nächstes`,
      add: (n) => `+ ${n === 1 ? "1 Titel" : `${n} Titel`} hinzugefügt`,
    },
    loading: "Wird geladen …",
    notFound: "Entität gehört nicht zur Integration DLNA Musikauswahl",
    noEntity: "Bitte eine Linn-Player-Entität auswählen",
    error: "Fehler",
  },
  en: {
    library: "Music",
    servers: "Music servers",
    back: "Back",
    home: "Home",
    search: "Search",
    searchPlaceholder: "Track, album or artist …",
    searchIn: (s) => `in “${s}”`,
    noResults: "Nothing found.",
    empty: "This folder is empty.",
    play: "Play",
    playNow: "Play now",
    replace: "Replace playlist",
    next: "Play next",
    add: "Add to playlist",
    open: "Open",
    loadMore: (n, total) => `Load more (${n} of ${total})`,
    tracks: (n) => (n === 1 ? "1 track" : `${n} tracks`),
    items: (n) => (n === 1 ? "1 item" : `${n} items`),
    added: {
      play: (n) => `▶ Playing ${n === 1 ? "1 track" : `${n} tracks`}`,
      replace: (n) => `▶ Playlist replaced (${n} tracks)`,
      next: (n) => `⤒ ${n === 1 ? "1 track" : `${n} tracks`} up next`,
      add: (n) => `+ ${n === 1 ? "1 track" : `${n} tracks`} added`,
    },
    loading: "Loading …",
    notFound: "Entity does not belong to the DLNA Musikauswahl integration",
    noEntity: "Please select a Linn player entity",
    error: "Error",
  },
};

const KIND_ICON = {
  server: "mdi:server-network",
  album: "mdi:album",
  artist: "mdi:account-music",
  genre: "mdi:tag-music",
  playlist: "mdi:playlist-music",
  directory: "mdi:folder-music",
  track: "mdi:music-note",
  music: "mdi:music-note",
};

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const fmtDuration = (seconds) => {
  if (seconds == null || isNaN(seconds)) return "";
  seconds = Math.round(seconds);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
};

class LinnLibraryCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._stack = [{ id: null, title: null }];
    this._cache = new Map();
    this._page = null;
    this._items = [];
    this._nextStart = null;
    this._loading = false;
    this._error = null;
    this._searchOpen = false;
    this._searchQuery = "";
    this._results = null;
    this._menu = null;
    this._toast = null;
    this._onOutside = (ev) => {
      if (this._menu && !ev.composedPath().some((el) => el.classList?.contains("menu"))) {
        this._menu = null;
        this._render();
      }
    };
  }

  // ------------------------------------------------------------ HA API
  static getConfigForm() {
    return {
      schema: [
        { name: "entity", required: true, selector: { entity: { domain: "media_player", integration: DOMAIN } } },
        { name: "title", selector: { text: {} } },
        {
          name: "track_action",
          selector: {
            select: {
              mode: "dropdown",
              options: [
                { value: "play", label: "Jetzt abspielen / Play now" },
                { value: "next", label: "Als Nächstes / Play next" },
                { value: "add", label: "Hinzufügen / Add" },
                { value: "replace", label: "Playlist ersetzen / Replace" },
              ],
            },
          },
        },
        {
          name: "view",
          selector: { select: { mode: "dropdown", options: ["auto", "grid", "list"] } },
        },
        { name: "max_height", selector: { text: {} } },
      ],
    };
  }

  static getStubConfig(hass) {
    const entities = hass?.entities || {};
    const entity = Object.keys(entities).find((id) => entities[id].platform === DOMAIN && id.startsWith("media_player."));
    return { entity: entity || "" };
  }

  setConfig(config) {
    const entityChanged = this._config?.entity !== config.entity;
    this._config = { track_action: "play", view: "auto", max_height: "520px", ...config };
    if (entityChanged) {
      this._cache.clear();
      this._stack = this._restoreStack();
      this._page = null;
      if (this._hass) this._open(this._current.id, false);
    }
    this._render();
  }

  getCardSize() {
    return 9;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: 9, min_rows: 5 };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first && this._config) this._open(this._current.id, false);
  }

  connectedCallback() {
    document.addEventListener("click", this._onOutside, true);
  }

  disconnectedCallback() {
    document.removeEventListener("click", this._onOutside, true);
  }

  get _t() {
    const lang = (this._hass?.locale?.language || this._hass?.language || "de").slice(0, 2);
    return STRINGS[lang] || STRINGS.en;
  }

  get _current() {
    return this._stack[this._stack.length - 1];
  }

  // -------------------------------------------------------- persistence
  get _storageKey() {
    return `linn-library-card:${this._config?.entity}`;
  }

  _restoreStack() {
    try {
      const stack = JSON.parse(localStorage.getItem(this._storageKey) || "null");
      if (Array.isArray(stack) && stack.length && stack[0].id === null) return stack;
    } catch (err) {
      /* storage not available */
    }
    return [{ id: null, title: null }];
  }

  _saveStack() {
    try {
      localStorage.setItem(this._storageKey, JSON.stringify(this._stack.map(({ id, title, skip }) => ({ id, title, skip }))));
    } catch (err) {
      /* storage not available */
    }
  }

  // ------------------------------------------------------------- data
  async _ws(type, data = {}) {
    return this._hass.callWS({ type: `${DOMAIN}/${type}`, entity_id: this._config.entity, ...data });
  }

  async _open(id, push = true, title = null) {
    if (!this._hass || !this._config?.entity) return;
    const scroll = this.shadowRoot.querySelector(".scroll")?.scrollTop || 0;
    if (push) {
      this._current.scroll = scroll;
      this._stack.push({ id, title });
    }
    this._searchOpen = false;
    this._results = null;
    this._menu = null;
    this._saveStack();

    const cached = this._cache.get(id ?? "");
    if (cached) {
      this._page = cached.page;
      this._items = cached.items;
      this._nextStart = cached.nextStart;
      this._error = null;
      this._render(this._current.scroll || 0);
      return;
    }
    this._loading = true;
    this._page = null;
    this._render();
    try {
      const page = await this._ws("browse", { media_content_id: id });
      // a single server: jump straight into it
      if (id === null && page.children.length === 1 && this._stack.length === 1) {
        this._cache.set("", { page, items: page.children, nextStart: null });
        this._loading = false;
        this._stack = [{ id: null, title: null, skip: true }];
        return this._open(page.children[0].id, true, page.children[0].title);
      }
      this._page = page;
      this._items = page.children;
      this._nextStart = page.next_start;
      this._current.title = page.title;
      this._error = null;
      this._cache.set(id ?? "", { page, items: this._items, nextStart: this._nextStart });
      this._saveStack();
    } catch (err) {
      this._error = err?.code === "not_found" ? this._t.notFound : err?.message || String(err);
      // stored path no longer valid: start from the top
      if (!push && this._stack.length > 1) {
        this._stack = [{ id: null, title: null }];
        this._saveStack();
        this._loading = false;
        return this._open(null, false);
      }
    } finally {
      this._loading = false;
    }
    this._render(0);
  }

  async _loadMore() {
    if (this._loading || this._nextStart == null || !this._page) return;
    this._loading = true;
    this._render();
    try {
      const page = await this._ws("browse", { media_content_id: this._page.id, start: this._nextStart });
      this._items = this._items.concat(page.children);
      this._nextStart = page.next_start;
      this._cache.set(this._current.id ?? "", { page: this._page, items: this._items, nextStart: this._nextStart });
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._loading = false;
    }
    this._render();
  }

  get _canBack() {
    return this._stack.length > (this._stack[0].skip ? 2 : 1);
  }

  _back() {
    if (!this._canBack) return;
    this._stack.pop();
    this._open(this._current.id, false);
  }

  _home() {
    if (!this._canBack) return;
    this._stack = this._stack.slice(0, this._stack[0].skip ? 2 : 1);
    this._open(this._current.id, false);
  }

  async _search(query) {
    query = query.trim();
    this._searchQuery = query;
    if (!query) return;
    this._loading = true;
    this._results = null;
    this._render();
    try {
      const scope = this._page?.can_play || this._page?.kind === "directory" ? this._page?.id : null;
      const result = await this._ws("search", { query, media_content_id: scope || undefined });
      this._results = result.children;
      this._error = null;
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._loading = false;
    }
    this._render(0);
  }

  async _load(id, mode) {
    this._menu = null;
    this._render();
    try {
      const result = await this._ws("load", { media_content_id: id, mode });
      this._showToast(this._t.added[mode](result.count));
    } catch (err) {
      this._showToast(`${this._t.error}: ${err?.message || err}`, true);
    }
  }

  _showToast(text, error = false) {
    this._toast = { text, error };
    clearTimeout(this._toastTimer);
    this._render();
    this._toastTimer = setTimeout(() => {
      this._toast = null;
      this._render();
    }, 2800);
  }

  // ----------------------------------------------------------- render
  _isGrid(items) {
    const view = this._config.view;
    if (view === "grid") return true;
    if (view === "list") return false;
    const containers = items.filter((i) => i.container);
    if (containers.length < items.length * 0.6 || !containers.length) return false;
    const withArt = containers.filter((i) => i.image || i.kind === "album").length;
    return withArt >= containers.length * 0.5;
  }

  _render(scrollTo = null) {
    if (!this._config) return;
    const t = this._t;
    const keepScroll = this.shadowRoot.querySelector(".scroll")?.scrollTop;
    const focused = this.shadowRoot.activeElement?.classList?.contains("search-input");

    let body;
    if (!this._config.entity) body = `<div class="message">${esc(t.noEntity)}</div>`;
    else body = this._renderNav() + `<div class="scroll">${this._renderContent()}</div>`;

    this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card>${body}${this._renderMenu()}${
      this._toast ? `<div class="toast ${this._toast.error ? "error" : ""}">${esc(this._toast.text)}</div>` : ""
    }</ha-card>`;

    const scroll = this.shadowRoot.querySelector(".scroll");
    if (scroll) {
      scroll.style.maxHeight = this._config.max_height;
      scroll.scrollTop = scrollTo ?? keepScroll ?? 0;
    }
    const input = this.shadowRoot.querySelector(".search-input");
    if (input && (focused || this._focusSearch)) {
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
      this._focusSearch = false;
    }
    this._bind();
  }

  _renderNav() {
    const t = this._t;
    const canBack = this._canBack;
    if (this._searchOpen) {
      return `
        <div class="nav">
          <button class="icon" data-action="search-close" title="${esc(t.back)}"><ha-icon icon="mdi:arrow-left"></ha-icon></button>
          <input class="search-input" type="search" enterkeyhint="search" placeholder="${esc(t.searchPlaceholder)}" value="${esc(this._searchQuery)}">
          <button class="icon" data-action="search-go" title="${esc(t.search)}"><ha-icon icon="mdi:magnify"></ha-icon></button>
        </div>`;
    }
    const title = this._current.title || this._config.title || t.library;
    const path = this._stack
      .slice(0, -1)
      .filter((s) => !s.skip)
      .map((s, i, arr) => s.title || (i === 0 ? this._config.title || t.library : ""))
      .filter(Boolean)
      .slice(-3)
      .join(" › ");
    return `
      <div class="nav">
        ${canBack ? `<button class="icon" data-action="back" title="${esc(t.back)}"><ha-icon icon="mdi:arrow-left"></ha-icon></button>` : `<span class="nav-pad"></span>`}
        <div class="nav-title">
          ${path ? `<div class="path">${esc(path)}</div>` : ""}
          <div class="name">${esc(title)}</div>
        </div>
        ${this._loading ? `<ha-icon class="spin" icon="mdi:loading"></ha-icon>` : ""}
        <button class="icon" data-action="search" title="${esc(t.search)}"><ha-icon icon="mdi:magnify"></ha-icon></button>
        ${canBack ? `<button class="icon" data-action="home" title="${esc(t.home)}"><ha-icon icon="mdi:home-outline"></ha-icon></button>` : ""}
      </div>`;
  }

  _renderContent() {
    const t = this._t;
    if (this._error) return `<div class="message warning">${esc(this._error)}</div>`;
    if (this._searchOpen) {
      if (this._results === null) {
        return this._loading ? `<div class="message"><ha-icon class="spin" icon="mdi:loading"></ha-icon></div>` : "";
      }
      if (!this._results.length) return `<div class="message">${esc(t.noResults)}</div>`;
      const containers = this._results.filter((i) => i.container);
      const tracks = this._results.filter((i) => !i.container);
      return (
        (containers.length ? this._renderItems(containers, false) : "") +
        (tracks.length ? this._renderList(tracks, false) : "")
      );
    }
    if (!this._page) return `<div class="message"><ha-icon class="spin" icon="mdi:loading"></ha-icon></div>`;
    if (!this._items.length) return this._renderHero() + `<div class="message">${esc(t.empty)}</div>`;

    const containers = this._items.filter((i) => i.container);
    const tracks = this._items.filter((i) => !i.container);
    const albumView = !!this._page.can_play && tracks.length > 0 && containers.length === 0;
    let html = this._renderHero(albumView ? tracks : null);
    if (containers.length) html += this._renderItems(containers, false);
    if (tracks.length) html += this._renderList(tracks, albumView);
    if (this._nextStart != null) {
      html += `<div class="more"><button class="text primary" data-action="more">${esc(
        t.loadMore(this._items.length, this._page.total)
      )}</button></div><div class="sentinel"></div>`;
    }
    return html;
  }

  _renderHero(tracks = null) {
    const p = this._page;
    // folders of folders (e.g. "Alben") get no hero: playing them would load everything
    if (!p || !p.can_play || (!tracks && !["album", "artist", "playlist", "genre"].includes(p.kind))) return "";
    const t = this._t;
    const info = [];
    if (tracks) {
      info.push(t.tracks(this._page.total ?? tracks.length));
      const total = tracks.reduce((sum, tr) => sum + (tr.duration || 0), 0);
      if (total) info.push(fmtDuration(total));
    } else if (p.total) {
      info.push(t.items(p.total));
    }
    const cover = p.image
      ? `<div class="hero-art" style="background-image:url('${esc(p.image)}')"></div>`
      : `<div class="hero-art placeholder"><ha-icon icon="${KIND_ICON[p.kind] || "mdi:folder-music"}"></ha-icon></div>`;
    return `
      <div class="hero">
        ${cover}
        <div class="hero-text">
          <div class="hero-title">${esc(p.title)}</div>
          ${p.subtitle ? `<div class="hero-sub">${esc(p.subtitle)}</div>` : ""}
          <div class="hero-info">${esc(info.join(" · "))}</div>
          <div class="hero-actions">
            <button class="pill primary" data-action="load" data-mode="replace" data-id="${esc(p.id)}"><ha-icon icon="mdi:play"></ha-icon>${esc(t.play)}</button>
            <button class="icon" data-action="load" data-mode="next" data-id="${esc(p.id)}" title="${esc(t.next)}"><ha-icon icon="mdi:playlist-play"></ha-icon></button>
            <button class="icon" data-action="load" data-mode="add" data-id="${esc(p.id)}" title="${esc(t.add)}"><ha-icon icon="mdi:playlist-plus"></ha-icon></button>
          </div>
        </div>
      </div>`;
  }

  _renderItems(items, forceList) {
    if (!forceList && this._isGrid(items)) {
      return `<div class="grid">${items
        .map((item) => {
          const art = item.image
            ? `<div class="tile-art" style="background-image:url('${esc(item.image)}')"></div>`
            : `<div class="tile-art placeholder"><ha-icon icon="${KIND_ICON[item.kind] || "mdi:folder-music"}"></ha-icon></div>`;
          const play =
            item.kind !== "server"
              ? `<button class="tile-play" data-action="load" data-mode="replace" data-id="${esc(item.id)}" title="${esc(this._t.play)}"><ha-icon icon="mdi:play"></ha-icon></button>`
              : "";
          return `
            <div class="tile" data-open="${esc(item.id)}" data-title="${esc(item.title)}">
              <div class="tile-cover">${art}${play}</div>
              <div class="tile-foot">
                <div class="tile-text"><div class="t">${esc(item.title)}</div><div class="s">${esc(item.subtitle || "")}</div></div>
                ${item.kind !== "server" ? `<button class="icon small" data-action="menu" data-id="${esc(item.id)}"><ha-icon icon="mdi:dots-vertical"></ha-icon></button>` : ""}
              </div>
            </div>`;
        })
        .join("")}</div>`;
    }
    return `<div class="list">${items
      .map((item) => {
        const thumb = item.image
          ? `<div class="thumb" style="background-image:url('${esc(item.image)}')"></div>`
          : `<div class="thumb placeholder"><ha-icon icon="${KIND_ICON[item.kind] || "mdi:folder-music"}"></ha-icon></div>`;
        const sub = [item.subtitle, item.count ? this._t.items(item.count) : null].filter(Boolean).join(" · ");
        return `
          <div class="row" data-open="${esc(item.id)}" data-title="${esc(item.title)}">
            ${thumb}
            <div class="text"><div class="t">${esc(item.title)}</div><div class="s">${esc(sub)}</div></div>
            ${item.kind !== "server" ? `<button class="icon small" data-action="menu" data-id="${esc(item.id)}"><ha-icon icon="mdi:dots-vertical"></ha-icon></button>` : ""}
            <ha-icon class="chevron" icon="mdi:chevron-right"></ha-icon>
          </div>`;
      })
      .join("")}</div>`;
  }

  _renderList(tracks, albumView) {
    return `<div class="list">${tracks
      .map((item, i) => {
        const lead = albumView
          ? `<div class="lead">${esc(item.track ?? i + 1)}</div>`
          : item.image
            ? `<div class="thumb" style="background-image:url('${esc(item.image)}')"></div>`
            : `<div class="thumb placeholder"><ha-icon icon="mdi:music-note"></ha-icon></div>`;
        const sub = albumView
          ? item.subtitle && item.subtitle !== this._page?.subtitle
            ? item.subtitle
            : ""
          : [item.subtitle, item.album].filter(Boolean).join(" · ");
        return `
          <div class="row track" data-track="${esc(item.id)}">
            ${lead}
            <div class="text"><div class="t">${esc(item.title)}</div>${sub ? `<div class="s">${esc(sub)}</div>` : ""}</div>
            <span class="dur">${esc(fmtDuration(item.duration))}</span>
            <button class="icon small" data-action="menu" data-id="${esc(item.id)}"><ha-icon icon="mdi:dots-vertical"></ha-icon></button>
          </div>`;
      })
      .join("")}</div>`;
  }

  _renderMenu() {
    if (!this._menu) return "";
    const t = this._t;
    const { id, x, y, container } = this._menu;
    const entries = [
      ["play", "mdi:play", t.playNow],
      ["next", "mdi:playlist-play", t.next],
      ["add", "mdi:playlist-plus", t.add],
      ["replace", "mdi:playlist-music", t.replace],
    ];
    return `
      <div class="menu" style="left:${x}px;top:${y}px">
        ${container ? `<button class="menu-item" data-action="open-from-menu" data-id="${esc(id)}"><ha-icon icon="mdi:folder-open-outline"></ha-icon>${esc(t.open)}</button>` : ""}
        ${entries
          .map(
            ([mode, icon, label]) =>
              `<button class="menu-item" data-action="load" data-mode="${mode}" data-id="${esc(id)}"><ha-icon icon="${icon}"></ha-icon>${esc(label)}</button>`
          )
          .join("")}
      </div>`;
  }

  // ------------------------------------------------------------ events
  _findItem(id) {
    return [...(this._items || []), ...(this._results || [])].find((i) => i.id === id);
  }

  _bind() {
    const root = this.shadowRoot;
    root.querySelectorAll("button[data-action]").forEach((btn) =>
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._onAction(btn.dataset.action, btn.dataset, btn);
      })
    );
    root.querySelectorAll("[data-open]").forEach((el) =>
      el.addEventListener("click", () => this._open(el.dataset.open, true, el.dataset.title))
    );
    root.querySelectorAll("[data-track]").forEach((el) =>
      el.addEventListener("click", () => this._load(el.dataset.track, this._config.track_action))
    );
    root.querySelectorAll("[data-open], [data-track]").forEach((el) =>
      el.addEventListener("contextmenu", (ev) => {
        ev.preventDefault();
        const id = el.dataset.open || el.dataset.track;
        this._openMenu(id, ev.clientX, ev.clientY);
      })
    );
    const input = root.querySelector(".search-input");
    if (input) {
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") this._search(input.value);
        if (ev.key === "Escape") this._onAction("search-close", {});
      });
      input.addEventListener("input", () => (this._searchQuery = input.value));
    }
    const sentinel = root.querySelector(".sentinel");
    const scroll = root.querySelector(".scroll");
    if (sentinel && scroll && "IntersectionObserver" in window) {
      this._observer?.disconnect();
      this._observer = new IntersectionObserver(
        (entries) => entries.some((e) => e.isIntersecting) && this._loadMore(),
        { root: scroll, rootMargin: "200px" }
      );
      this._observer.observe(sentinel);
    }
  }

  _openMenu(id, clientX, clientY) {
    const card = this.shadowRoot.querySelector("ha-card").getBoundingClientRect();
    const width = 220;
    const height = 230;
    const x = Math.max(8, Math.min(clientX - card.left - width + 20, card.width - width - 8));
    const y = Math.max(8, Math.min(clientY - card.top, card.height - height - 8));
    this._menu = { id, x, y, container: !!this._findItem(id)?.container };
    this._render();
  }

  _onAction(action, data, btn) {
    switch (action) {
      case "back":
        return this._back();
      case "home":
        return this._home();
      case "search":
        this._searchOpen = true;
        this._results = null;
        this._focusSearch = true;
        return this._render();
      case "search-go":
        return this._search(this.shadowRoot.querySelector(".search-input")?.value || "");
      case "search-close":
        this._searchOpen = false;
        this._results = null;
        return this._render();
      case "more":
        return this._loadMore();
      case "menu": {
        const rect = btn.getBoundingClientRect();
        return this._openMenu(data.id, rect.right, rect.bottom);
      }
      case "open-from-menu":
        this._menu = null;
        return this._open(data.id, true, this._findItem(data.id)?.title);
      case "load":
        return this._load(data.id, data.mode);
    }
  }
}

const STYLE = `
  :host { display: block; }
  ha-card { overflow: hidden; display: flex; flex-direction: column; height: 100%; position: relative; }
  button { font: inherit; color: inherit; background: none; border: none; cursor: pointer; padding: 0; }
  button:disabled { opacity: 0.35; cursor: default; }
  .icon { width: 40px; height: 40px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
          color: var(--secondary-text-color); flex: none; }
  .icon:not(:disabled):hover { background: rgba(127,127,127,0.15); }
  .icon.small { width: 32px; height: 32px; --mdc-icon-size: 20px; }
  .text { padding: 6px 12px; border-radius: 16px; font-weight: 500; }
  .text:hover { background: rgba(127,127,127,0.15); }
  .primary { color: var(--primary-color); }
  .pill { display: inline-flex; align-items: center; gap: 6px; padding: 0 16px 0 12px; height: 36px; border-radius: 18px;
          background: var(--primary-color); color: var(--text-primary-color, #fff) !important; font-weight: 500; --mdc-icon-size: 20px; }
  .pill:hover { filter: brightness(1.08); }
  .nav { display: flex; align-items: center; gap: 4px; padding: 6px 8px; border-bottom: 1px solid var(--divider-color); min-height: 52px; }
  .nav-pad { width: 8px; }
  .nav-title { flex: 1; min-width: 0; }
  .nav-title .path { font-size: 0.75em; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .nav-title .name { font-size: 1.05em; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .search-input { flex: 1; min-width: 0; height: 36px; border-radius: 18px; border: 1px solid var(--divider-color); padding: 0 14px;
                  font: inherit; color: var(--primary-text-color); background: var(--secondary-background-color, transparent); outline: none; }
  .search-input:focus { border-color: var(--primary-color); }
  .scroll { overflow-y: auto; flex: 1; overscroll-behavior: contain; }
  .hero { display: flex; gap: 16px; padding: 16px; align-items: center; }
  .hero-art { width: 112px; height: 112px; border-radius: 8px; background: center / cover no-repeat; flex: none;
              box-shadow: 0 2px 6px rgba(0,0,0,0.3); --mdc-icon-size: 48px; }
  .hero-text { min-width: 0; flex: 1; }
  .hero-title { font-size: 1.25em; font-weight: 500; line-height: 1.25; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .hero-sub { color: var(--secondary-text-color); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .hero-info { color: var(--secondary-text-color); font-size: 0.85em; margin-top: 2px; }
  .hero-actions { display: flex; align-items: center; gap: 4px; margin-top: 10px; }
  .placeholder { display: flex; align-items: center; justify-content: center;
                 background-color: var(--secondary-background-color, rgba(127,127,127,0.15)); color: var(--secondary-text-color); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)); gap: 14px 12px; padding: 12px 12px 16px; }
  .tile { cursor: pointer; min-width: 0; }
  .tile-cover { position: relative; }
  .tile-art { width: 100%; aspect-ratio: 1; border-radius: 6px; background: center / cover no-repeat;
              box-shadow: 0 1px 3px rgba(0,0,0,0.25); --mdc-icon-size: 44px; transition: filter 0.15s; }
  .tile:hover .tile-art { filter: brightness(0.85); }
  .tile-play { position: absolute; right: 8px; bottom: 8px; width: 40px; height: 40px; border-radius: 50%;
               background: var(--primary-color); color: var(--text-primary-color, #fff); display: flex; align-items: center;
               justify-content: center; box-shadow: 0 2px 6px rgba(0,0,0,0.35); opacity: 0; transform: translateY(4px);
               transition: opacity 0.15s, transform 0.15s; --mdc-icon-size: 24px; }
  .tile:hover .tile-play { opacity: 1; transform: none; }
  @media (hover: none) { .tile-play { opacity: 0.95; transform: none; width: 34px; height: 34px; } }
  .tile-foot { display: flex; align-items: flex-start; gap: 2px; margin-top: 6px; }
  .tile-text { flex: 1; min-width: 0; }
  .tile .t { font-weight: 500; font-size: 0.95em; }
  .list { padding: 4px 0; }
  .row { display: flex; align-items: center; gap: 12px; padding: 4px 4px 4px 12px; cursor: pointer; min-height: 48px;
         transition: background 0.15s; }
  .row:hover { background: rgba(127,127,127,0.08); }
  .lead { width: 28px; text-align: center; color: var(--secondary-text-color); font-size: 0.85em; flex: none; font-variant-numeric: tabular-nums; }
  .thumb { width: 40px; height: 40px; border-radius: 4px; background: center / cover no-repeat; flex: none; --mdc-icon-size: 22px; }
  .row .text { flex: 1; min-width: 0; padding: 0; font-weight: normal; border-radius: 0; }
  .row .text:hover { background: none; }
  .t, .s { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .s { color: var(--secondary-text-color); font-size: 0.85em; }
  .dur { color: var(--secondary-text-color); font-size: 0.85em; font-variant-numeric: tabular-nums; min-width: 42px; text-align: right; }
  .chevron { color: var(--secondary-text-color); --mdc-icon-size: 20px; margin-right: 4px; }
  .more { display: flex; justify-content: center; padding: 8px 0 16px; }
  .sentinel { height: 1px; }
  .menu { position: absolute; z-index: 5; width: 220px; padding: 6px 0; border-radius: 10px;
          background: var(--card-background-color, #fff); box-shadow: 0 6px 24px rgba(0,0,0,0.3); }
  .menu-item { display: flex; align-items: center; gap: 12px; width: 100%; padding: 10px 16px; text-align: left; --mdc-icon-size: 20px; }
  .menu-item ha-icon { color: var(--secondary-text-color); }
  .menu-item:hover { background: rgba(127,127,127,0.12); }
  .toast { position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%); max-width: calc(100% - 32px);
           background: var(--primary-text-color); color: var(--card-background-color, #fff); padding: 9px 16px;
           border-radius: 18px; font-size: 0.9em; box-shadow: 0 4px 14px rgba(0,0,0,0.3); white-space: nowrap;
           overflow: hidden; text-overflow: ellipsis; animation: toast-in 0.2s ease-out; z-index: 6; }
  .toast.error { background: var(--error-color, #db4437); color: #fff; }
  .message { padding: 24px 16px; text-align: center; color: var(--secondary-text-color); --mdc-icon-size: 32px; }
  .message.warning { color: var(--error-color, #db4437); }
  .spin { animation: spin 1s linear infinite; color: var(--secondary-text-color); }
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes toast-in { from { opacity: 0; transform: translate(-50%, 8px); } }
`;

if (!customElements.get("linn-library-card")) {
  customElements.define("linn-library-card", LinnLibraryCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "linn-library-card",
    name: "Linn Bibliothek",
    description: "Musik auf den DLNA-Servern auswählen und in die Linn-Playlist laden (DLNA Musikauswahl).",
    preview: false,
    documentationURL: "https://github.com/flaxg73/dlna-MusikAuswahl",
  });
  console.info(`%c LINN-LIBRARY-CARD %c ${CARD_VERSION} `, "background:#000;color:#fff", "background:#ddd;color:#000");
}
})();

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
const CARD_VERSION = "0.6.2";

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
    close: "Schließen",
    tabs: { server: "Musik-Server", qobuz: "Qobuz", radio: "Radio" },
    configuredServers: "In der Integration eingerichtete Server",
    presetsFailed:
      "TuneIn hat die Favoriten nicht herausgegeben. Meist ist der Benutzername falsch: Benötigt wird der öffentliche TuneIn-Benutzername aus deinem Profil, nicht die E-Mail-Adresse. Ändern unter Einstellungen → Geräte & Dienste → DLNA Musikauswahl → Konfigurieren.",
    noPresets:
      "Keine TuneIn-Favoriten gefunden. Prüfe den Benutzernamen in den Optionen der Integration und ob in deinem TuneIn-Konto Favoriten gespeichert sind.",
    retry: "Erneut prüfen",
    searchRadio: "Sender suchen …",
    radioPlaying: (name) => `📻 ${name} wird gespielt`,
    qobuzMissing:
      "Kein Qobuz-Server eingerichtet. Qobuz erscheint hier über einen DLNA-Brückenserver wie upmpdcli (Qobuz-Plugin) oder BubbleUPnP Server. Richte ihn ein, füge ihn in der Integration als Musikserver hinzu und wähle ihn in den Optionen unter „Qobuz-Server“.",
    buttonName: "Musik auswählen",
    off: "Standby",
    idle: "Bereit",
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
    close: "Close",
    tabs: { server: "Music server", qobuz: "Qobuz", radio: "Radio" },
    configuredServers: "Servers set up in the integration",
    presetsFailed:
      "TuneIn did not return the favourites. Usually the username is wrong: the public TuneIn username from your profile is needed, not the e-mail address. Change it in Settings → Devices & services → DLNA Musikauswahl → Configure.",
    noPresets:
      "No TuneIn favourites found. Check the username in the integration options and that your TuneIn account has favourites.",
    retry: "Check again",
    searchRadio: "Search stations …",
    radioPlaying: (name) => `📻 Playing ${name}`,
    qobuzMissing:
      "No Qobuz server set up. Qobuz is shown here through a DLNA bridge such as upmpdcli (Qobuz plugin) or BubbleUPnP Server. Set it up, add it to the integration as a music server and choose it in the options as “Qobuz server”.",
    buttonName: "Choose music",
    off: "Standby",
    idle: "Ready",
  },
};

const TAB_ICON = { server: "mdi:server-network", qobuz: "mdi:music-circle", radio: "mdi:radio" };
const RADIO_SCHEME = "dlnamusik-radio://";

const KIND_ICON = {
  radio: "mdi:radio",
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
    this._tab = "server";
    this._tabState = {};
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
        {
          name: "display",
          selector: {
            select: {
              mode: "dropdown",
              options: [
                { value: "inline", label: "Karte im Dashboard / Inline card" },
                { value: "button", label: "Button → Vollbild / Button → fullscreen" },
              ],
            },
          },
        },
        {
          name: "sources",
          selector: {
            select: {
              multiple: true,
              mode: "list",
              options: [
                { value: "server", label: "Musik-Server" },
                { value: "qobuz", label: "Qobuz" },
                { value: "radio", label: "Radio (TuneIn)" },
              ],
            },
          },
        },
        { name: "button_name", selector: { text: {} } },
        { name: "button_icon", selector: { icon: {} } },
        { name: "playlist_panel", selector: { boolean: {} } },
      ],
    };
  }

  static getStubConfig(hass) {
    const entities = hass?.entities || {};
    const entity = Object.keys(entities).find((id) => entities[id].platform === DOMAIN && id.startsWith("media_player."));
    return { entity: entity || "" };
  }

  get _isButton() {
    return this._config?.display === "button";
  }

  setConfig(config) {
    const entityChanged = this._config?.entity !== config.entity;
    this._config = {
      track_action: "play",
      view: "auto",
      max_height: "520px",
      display: "inline",
      playlist_panel: true,
      ...config,
    };
    if (this._isButton) {
      this._overlayChild?.setConfig(this._overlayConfig());
      this._renderButton();
      return;
    }
    if (entityChanged) {
      this._cache.clear();
      this._tabState = {};
      this._tab = this._restoreTab();
      this._stack = this._restoreStack();
      this._page = null;
      if (this._hass) this._open(this._current.id, false);
    }
    this._render();
  }

  getCardSize() {
    return this._isButton ? 1 : 9;
  }

  getGridOptions() {
    return this._isButton
      ? { columns: 6, min_columns: 3, rows: 1, min_rows: 1 }
      : { columns: 12, min_columns: 6, rows: 9, min_rows: 5 };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._isButton) {
      if (this._overlayChild) this._overlayChild.hass = hass;
      if (this._overlayPlaylist) this._overlayPlaylist.hass = hass;
      this._renderButton();
      return;
    }
    if (first && this._config) this._open(this._current.id, false);
  }

  connectedCallback() {
    document.addEventListener("click", this._onOutside, true);
  }

  disconnectedCallback() {
    document.removeEventListener("click", this._onOutside, true);
    this._closeOverlay(false);
  }

  // ------------------------------------------------- button + fullscreen
  _renderButton() {
    const t = this._t;
    const stateObj = this._hass?.states?.[this._config.entity];
    let sub = "";
    if (stateObj) {
      const a = stateObj.attributes;
      if (stateObj.state === "off") sub = t.off;
      else if (a.media_title) sub = [a.media_title, a.media_artist].filter(Boolean).join(" · ");
      else sub = t.idle;
    }
    const name = this._config.button_name || t.buttonName;
    const icon = this._config.button_icon || "mdi:music-box-multiple";
    const key = [name, icon, sub].join("|");
    if (key === this._buttonKey && this.shadowRoot.querySelector(".launcher")) return;
    this._buttonKey = key;
    this.shadowRoot.innerHTML = `<style>${BUTTON_STYLE}</style>
      <ha-card><div class="launcher" role="button" tabindex="0">
        <div class="l-icon"><ha-icon icon="${esc(icon)}"></ha-icon></div>
        <div class="l-text"><div class="l-name">${esc(name)}</div>${sub ? `<div class="l-sub">${esc(sub)}</div>` : ""}</div>
        <ha-icon class="l-go" icon="mdi:arrow-expand"></ha-icon>
      </div></ha-card>`;
    const card = this.shadowRoot.querySelector(".launcher");
    card.addEventListener("click", () => this._openOverlay());
    card.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        this._openOverlay();
      }
    });
  }

  _overlayConfig() {
    const { display, button_name, button_icon, playlist_panel, ...rest } = this._config;
    return { ...rest, display: "inline", max_height: "none", _overlay: true };
  }

  _openOverlay() {
    if (this._overlayHost || !this._config?.entity) return;
    const host = document.createElement("div");
    host.className = "linn-library-overlay";
    const root = host.attachShadow({ mode: "open" });
    const panel = this._config.playlist_panel !== false && customElements.get("linn-playlist-card");
    root.innerHTML = `<style>${OVERLAY_STYLE}</style>
      <div class="ov ${panel ? "with-panel" : ""}"><div class="main"></div>${panel ? `<div class="side"></div>` : ""}</div>`;

    const lib = document.createElement("linn-library-card");
    lib.setConfig(this._overlayConfig());
    lib.hass = this._hass;
    lib.addEventListener("linn-library-close", () => this._closeOverlay());
    root.querySelector(".main").append(lib);
    this._overlayChild = lib;

    if (panel) {
      const playlist = document.createElement("linn-playlist-card");
      playlist.setConfig({ entity: this._config.entity, show_header: true, max_height: "none", _embedded: true });
      playlist.hass = this._hass;
      root.querySelector(".side").append(playlist);
      this._overlayPlaylist = playlist;
    }

    this._bodyOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.body.append(host);
    this._overlayHost = host;
  }

  _closeOverlay(animate = true) {
    const host = this._overlayHost;
    if (!host) return;
    this._overlayHost = null;
    this._overlayChild = null;
    this._overlayPlaylist = null;
    document.body.style.overflow = this._bodyOverflow || "";
    const remove = () => host.remove();
    if (!animate) return remove();
    host.shadowRoot.querySelector(".ov")?.classList.add("closing");
    setTimeout(remove, 180);
  }

  get _t() {
    const lang = (this._hass?.locale?.language || this._hass?.language || "de").slice(0, 2);
    return STRINGS[lang] || STRINGS.en;
  }

  get _current() {
    return this._stack[this._stack.length - 1];
  }

  // -------------------------------------------------------- persistence
  get _sources() {
    const sources = (this._config?.sources || ["server", "qobuz", "radio"]).filter((s) => TAB_ICON[s]);
    return sources.length ? sources : ["server"];
  }

  get _storageKey() {
    const tab = this._tab === "server" ? "" : `:${this._tab}`;
    return `linn-library-card:${this._config?.entity}${tab}`;
  }

  _restoreTab() {
    try {
      const tab = localStorage.getItem(`linn-library-card:${this._config?.entity}:tab`);
      if (tab && this._sources.includes(tab)) return tab;
    } catch (err) {
      /* storage not available */
    }
    return this._sources[0];
  }

  _switchTab(tab) {
    if (tab === this._tab || !this._sources.includes(tab)) return;
    this._tabState[this._tab] = {
      stack: this._stack,
      cache: this._cache,
      page: this._page,
      items: this._items,
      nextStart: this._nextStart,
      scroll: this.shadowRoot.querySelector(".scroll")?.scrollTop || 0,
    };
    this._tab = tab;
    try {
      localStorage.setItem(`linn-library-card:${this._config?.entity}:tab`, tab);
    } catch (err) {
      /* storage not available */
    }
    this._searchOpen = false;
    this._results = null;
    this._menu = null;
    this._error = null;
    const state = this._tabState[tab];
    if (state && state.page && !state.page.message) {
      Object.assign(this, {
        _stack: state.stack,
        _cache: state.cache,
        _page: state.page,
        _items: state.items,
        _nextStart: state.nextStart,
      });
      this._render(state.scroll);
      return;
    }
    this._stack = this._restoreStack();
    this._cache = new Map();
    this._page = null;
    this._open(this._current.id, false);
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
      const page = await this._ws("browse", { media_content_id: id, source: this._tab });
      // a single server: jump straight into it
      if (this._tab === "server" && id === null && page.children.length === 1 && this._stack.length === 1) {
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
      // hints like "Qobuz not configured" are not cached: the setup may change
      if (!page.message) this._cache.set(id ?? "", { page, items: this._items, nextStart: this._nextStart });
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
      const page = await this._ws("browse", { media_content_id: this._page.id, start: this._nextStart, source: this._tab });
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
      const scope =
        this._tab !== "radio" && (this._page?.can_play || this._page?.kind === "directory") ? this._page?.id : null;
      const result = await this._ws("search", { query, media_content_id: scope || undefined, source: this._tab });
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
      if (id.startsWith(RADIO_SCHEME)) this._showToast(this._t.radioPlaying(this._findItem(id)?.title || ""));
      else this._showToast(this._t.added[mode](result.count));
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
    else body = this._renderNav() + this._renderTabs() + `<div class="scroll">${this._renderContent()}</div>`;

    this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card class="${this._config._overlay ? "full" : ""}"><div class="body">${body}${this._renderMenu()}${
      this._toast ? `<div class="toast ${this._toast.error ? "error" : ""}">${esc(this._toast.text)}</div>` : ""
    }</div></ha-card>`;

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
    const close = this._config._overlay
      ? `<button class="icon" data-action="close" title="${esc(t.close)}"><ha-icon icon="mdi:close"></ha-icon></button>`
      : "";
    if (this._searchOpen) {
      return `
        <div class="nav">
          <button class="icon" data-action="search-close" title="${esc(t.back)}"><ha-icon icon="mdi:arrow-left"></ha-icon></button>
          <input class="search-input" type="search" enterkeyhint="search" placeholder="${esc(this._tab === "radio" ? t.searchRadio : t.searchPlaceholder)}" value="${esc(this._searchQuery)}">
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
        ${close}
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

  _renderTabs() {
    const sources = this._sources;
    if (sources.length < 2 || this._searchOpen) return "";
    const t = this._t;
    return `<div class="tabs">${sources
      .map(
        (src) =>
          `<button class="tab ${src === this._tab ? "active" : ""}" data-action="tab" data-tab="${src}"><ha-icon icon="${TAB_ICON[src]}"></ha-icon><span>${esc(t.tabs[src])}</span></button>`
      )
      .join("")}</div>`;
  }

  _renderRadio(items) {
    let section = null;
    const rows = items.map((item) => {
      let head = "";
      if (item.section && item.section !== section) {
        section = item.section;
        head = `<div class="section">${esc(section)}</div>`;
      }
      const logo = item.image
        ? `<div class="logo" style="background-image:url('${esc(item.image)}')"></div>`
        : `<div class="logo placeholder"><ha-icon icon="${KIND_ICON[item.kind] || "mdi:folder"}"></ha-icon></div>`;
      if (item.container) {
        return `${head}<div class="row" data-open="${esc(item.id)}" data-title="${esc(item.title)}">${logo}
          <div class="text"><div class="t">${esc(item.title)}</div>${item.subtitle ? `<div class="s">${esc(item.subtitle)}</div>` : ""}</div>
          <ha-icon class="chevron" icon="mdi:chevron-right"></ha-icon></div>`;
      }
      const sub = [item.subtitle, item.details].filter(Boolean).join(" · ");
      return `${head}<div class="row station" data-station="${esc(item.id)}">${logo}
        <div class="text"><div class="t">${esc(item.title)}</div>${sub ? `<div class="s">${esc(sub)}</div>` : ""}</div>
        <button class="icon small play" data-action="load" data-mode="play" data-id="${esc(item.id)}" title="${esc(this._t.play)}"><ha-icon icon="mdi:play-circle"></ha-icon></button></div>`;
    });
    return `<div class="list">${rows.join("")}</div>`;
  }

  _renderContent() {
    const t = this._t;
    if (this._error) return `<div class="message warning">${esc(this._error)}</div>`;
    if (this._tab === "radio" && this._searchOpen && this._results) {
      return this._results.length ? this._renderRadio(this._results) : `<div class="message">${esc(t.noResults)}</div>`;
    }
    if (!this._searchOpen && this._page?.message === "qobuz_not_configured") {
      const servers = this._page.servers || [];
      return `<div class="message info"><ha-icon icon="mdi:music-circle"></ha-icon><div>${esc(t.qobuzMissing)}</div>
        ${servers.length ? `<div class="hint">${esc(t.configuredServers)}: ${esc(servers.join(", "))}</div>` : ""}
        <button class="text primary" data-action="retry"><ha-icon icon="mdi:refresh"></ha-icon> ${esc(t.retry)}</button></div>`;
    }
    if (!this._searchOpen && this._page?.message === "tunein_presets_failed") {
      return `<div class="message info"><ha-icon icon="mdi:star-off-outline"></ha-icon><div>${esc(t.presetsFailed)}</div></div>`;
    }
    if (!this._searchOpen && this._page?.message === "tunein_no_presets") {
      return `<div class="message info"><ha-icon icon="mdi:star-outline"></ha-icon><div>${esc(t.noPresets)}</div></div>`;
    }
    if (!this._searchOpen && this._tab === "radio" && this._page) {
      return this._items.length ? this._renderRadio(this._items) : `<div class="message">${esc(t.empty)}</div>`;
    }
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
    root.querySelectorAll("[data-station]").forEach((el) =>
      el.addEventListener("click", () => this._load(el.dataset.station, "play"))
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
      case "retry":
        this._cache.clear();
        return this._open(this._current.id, false);
      case "tab":
        return this._switchTab(data.tab);
      case "close":
        this.dispatchEvent(new CustomEvent("linn-library-close", { bubbles: true, composed: true }));
        return;
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
  ha-card { overflow: hidden; height: 100%; position: relative; }
  .body { display: flex; flex-direction: column; height: 100%; min-height: 0; }
  ha-card.full { border-radius: 0; box-shadow: none; border: none; }
  .full .nav { min-height: 60px; padding: 8px 12px; padding-top: calc(8px + env(safe-area-inset-top, 0px)); }
  .full .nav-title .name { font-size: 1.2em; }
  .full .grid { grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 20px 16px; padding: 16px 20px 24px; }
  .full .hero { padding: 20px; }
  .full .hero-art { width: 160px; height: 160px; }
  .full .hero-title { font-size: 1.5em; }
  @media (max-width: 600px) {
    .full .grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); padding: 12px; gap: 14px 12px; }
    .full .hero-art { width: 112px; height: 112px; }
  }
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
  .tabs { display: flex; gap: 6px; padding: 8px 12px; border-bottom: 1px solid var(--divider-color); overflow-x: auto; }
  .tab { display: inline-flex; align-items: center; gap: 6px; height: 34px; padding: 0 14px 0 10px; border-radius: 17px;
         border: 1px solid var(--divider-color); color: var(--primary-text-color); font-weight: 500; white-space: nowrap;
         --mdc-icon-size: 18px; transition: background 0.15s, color 0.15s; }
  .tab ha-icon { color: var(--secondary-text-color); }
  .tab:hover { background: rgba(127,127,127,0.1); }
  .tab.active { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .tab.active ha-icon { color: inherit; }
  .section { padding: 14px 16px 4px; font-size: 0.8em; font-weight: 500; letter-spacing: 0.04em; text-transform: uppercase;
             color: var(--secondary-text-color); }
  .logo { width: 44px; height: 44px; border-radius: 8px; background: #fff center / contain no-repeat; flex: none;
          box-shadow: 0 0 0 1px var(--divider-color) inset; --mdc-icon-size: 22px; }
  .logo.placeholder { box-shadow: none; }
  .station .play { color: var(--primary-color); --mdc-icon-size: 28px; width: 40px; height: 40px; }
  .message.info .hint { font-size: 0.85em; }
  .message.info { display: flex; flex-direction: column; align-items: center; gap: 10px; line-height: 1.45; max-width: 520px; margin: 0 auto; }
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

const BUTTON_STYLE = `
  :host { display: block; height: 100%; }
  ha-card { height: 100%; overflow: hidden; }
  .launcher { display: flex; align-items: center; gap: 12px; padding: 0 12px; height: 100%; min-height: 56px;
              box-sizing: border-box; cursor: pointer; transition: background 0.15s; outline: none; }
  .launcher:hover, .launcher:focus-visible { background: rgba(127,127,127,0.08); }
  .l-icon { width: 36px; height: 36px; border-radius: 50%; flex: none; display: flex; align-items: center; justify-content: center;
            background: rgba(var(--rgb-primary-color, 3,169,244), 0.2); color: var(--primary-color); --mdc-icon-size: 22px; }
  .l-text { flex: 1; min-width: 0; }
  .l-name { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .l-sub { font-size: 0.85em; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .l-go { color: var(--secondary-text-color); --mdc-icon-size: 20px; flex: none; }
`;

const OVERLAY_STYLE = `
  .ov { position: fixed; inset: 0; z-index: 1000; display: flex; background: var(--primary-background-color, #fafafa);
        animation: ov-in 0.2s ease-out; padding: 0 env(safe-area-inset-right, 0px) env(safe-area-inset-bottom, 0px) env(safe-area-inset-left, 0px); }
  .ov.closing { animation: ov-out 0.18s ease-in forwards; }
  .main { flex: 1; min-width: 0; display: flex; }
  .main > *, .side > * { flex: 1; min-width: 0; height: 100%; }
  .side { width: 420px; flex: none; display: flex; border-left: 1px solid var(--divider-color); }
  @media (max-width: 999px) { .side { display: none; } }
  @keyframes ov-in { from { opacity: 0; transform: translateY(24px); } }
  @keyframes ov-out { to { opacity: 0; transform: translateY(24px); } }
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

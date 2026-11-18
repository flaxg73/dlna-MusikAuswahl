/*
 * Linn Player Card – everything for the Linn in one card:
 * player controls, TuneIn favourites, a button to the fullscreen music
 * selection and the playlist (with saving/loading playlists).
 *
 *   type: custom:linn-player-card
 *   entity: media_player.wohnzimmer
 *   radio_count: 8              # number of TuneIn favourites (0 = hide)
 *   show_library_button: true
 *   show_playlist: true
 *   playlist_height: 360px
 *   sources: [server, qobuz, radio]   # passed to the music selection
 *   track_action: play                # passed to the music selection
 */

(() => {
const DOMAIN = "dlna_musikauswahl";
const CARD_VERSION = "0.7.1";
const FAVORITES_REFRESH = 10 * 60 * 1000;
const MEDIUM = 480; // px width of the player area: controls side by side
const ROOMY = 640; // bigger cover and buttons
const TWO_COLUMNS = 820; // px card width: playlist in a second column

const STRINGS = {
  de: {
    off: "Standby",
    unavailable: "Linn nicht erreichbar",
    nothing: "Nichts wird abgespielt",
    power: "Ein/Aus",
    source: "Quelle",
    mute: "Stumm",
    volDown: "Leiser",
    volUp: "Lauter",
    prev: "Zurück",
    playPause: "Wiedergabe/Pause",
    next: "Weiter",
    radio: "Radio-Favoriten",
    radioHint: "TuneIn-Benutzername in den Optionen der Integration eintragen, dann erscheinen hier deine Favoriten.",
    radioFailed: "TuneIn-Favoriten konnten nicht geladen werden.",
    library: "Musik auswählen",
    libraryHint: "Musik-Server · Qobuz · Radio",
    playing: (n) => `📻 ${n} wird gespielt`,
    error: "Fehler",
    noEntity: "Bitte eine Linn-Player-Entität auswählen",
  },
  en: {
    off: "Standby",
    unavailable: "Linn not reachable",
    nothing: "Nothing playing",
    power: "Power",
    source: "Source",
    mute: "Mute",
    volDown: "Volume down",
    volUp: "Volume up",
    prev: "Previous",
    playPause: "Play/Pause",
    next: "Next",
    radio: "Radio favourites",
    radioHint: "Enter your TuneIn username in the integration options to see your favourites here.",
    radioFailed: "Could not load the TuneIn favourites.",
    library: "Choose music",
    libraryHint: "Music server · Qobuz · Radio",
    playing: (n) => `📻 Playing ${n}`,
    error: "Error",
    noEntity: "Please select a Linn player entity",
  },
};

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class LinnPlayerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._favorites = null;
    this._favoritesState = null;
    this._favoritesAt = 0;
    this._dragging = false;
  }

  // ------------------------------------------------------------ HA API
  static getConfigForm() {
    return {
      schema: [
        { name: "entity", required: true, selector: { entity: { domain: "media_player", integration: DOMAIN } } },
        { name: "radio_count", selector: { number: { min: 0, max: 24, mode: "box" } } },
        { name: "show_library_button", selector: { boolean: {} } },
        { name: "show_playlist", selector: { boolean: {} } },
        { name: "playlist_height", selector: { text: {} } },
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
    this._config = {
      radio_count: 8,
      show_library_button: true,
      show_playlist: true,
      playlist_height: "360px",
      ...config,
    };
    if (entityChanged) {
      this._favorites = null;
      this._favoritesAt = 0;
    }
    this._built = false;
    this._build();
    if (this._hass) this.hass = this._hass;
  }

  getCardSize() {
    return 6 + (this._config?.show_playlist ? 6 : 0);
  }

  getGridOptions() {
    return { columns: "full", min_columns: 6, rows: "auto" };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    if (!this._built) this._build();
    if (this._playlist) this._playlist.hass = hass;
    if (this._library) this._library.hass = hass;
    this._update();
    const count = Number(this._config.radio_count) || 0;
    if (count > 0 && Date.now() - this._favoritesAt > FAVORITES_REFRESH) {
      this._favoritesAt = Date.now();
      this._fetchFavorites();
    }
  }

  connectedCallback() {
    if (this._built) this._observeSize();
  }

  disconnectedCallback() {
    this._resizeObserver?.disconnect();
    // leaving the dashboard closes the fullscreen selection
    this._library?._closeOverlay?.(false);
  }

  get _stateObj() {
    return this._hass && this._config?.entity ? this._hass.states[this._config.entity] : undefined;
  }

  get _t() {
    const lang = (this._hass?.locale?.language || this._hass?.language || "de").slice(0, 2);
    return STRINGS[lang] || STRINGS.en;
  }

  // ------------------------------------------------------------- build
  _build() {
    if (!this._config) return;
    const t = this._t;
    if (!this._config.entity) {
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div class="message">${esc(t.noEntity)}</div></ha-card>`;
      return;
    }
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <ha-card>
        <div class="layout">
          <div class="left">
            <div class="player">
              <div class="art"></div>
              <div class="now-text"><div class="title"></div><div class="sub"></div></div>
              <button class="icon power" data-action="power" title="${esc(t.power)}"><ha-icon icon="mdi:power"></ha-icon></button>
              <div class="source-row">
                <ha-icon class="source-icon" icon="mdi:import"></ha-icon>
                <select class="source" title="${esc(t.source)}"></select>
              </div>
              <div class="transport">
                <button class="icon" data-action="prev" title="${esc(t.prev)}"><ha-icon icon="mdi:skip-previous"></ha-icon></button>
                <button class="icon play" data-action="playpause" title="${esc(t.playPause)}"><ha-icon icon="mdi:play"></ha-icon></button>
                <button class="icon" data-action="next" title="${esc(t.next)}"><ha-icon icon="mdi:skip-next"></ha-icon></button>
              </div>
              <div class="volume">
                <button class="icon small mute" data-action="mute" title="${esc(t.mute)}"><ha-icon icon="mdi:volume-high"></ha-icon></button>
                <button class="icon small" data-action="voldown" title="${esc(t.volDown)}"><ha-icon icon="mdi:minus"></ha-icon></button>
                <input class="slider" type="range" min="0" max="100" step="1" value="0">
                <button class="icon small" data-action="volup" title="${esc(t.volUp)}"><ha-icon icon="mdi:plus"></ha-icon></button>
                <span class="vol-value">–</span>
              </div>
            </div>
            <div class="section radio">
              <div class="section-head"><ha-icon icon="mdi:radio"></ha-icon><span>${esc(t.radio)}</span></div>
              <div class="stations"></div>
            </div>
            <button class="library" data-action="library">
              <ha-icon icon="mdi:music-box-multiple"></ha-icon>
              <span class="lib-text"><span class="lib-name">${esc(t.library)}</span><span class="lib-hint">${esc(t.libraryHint)}</span></span>
              <ha-icon class="lib-go" icon="mdi:arrow-expand"></ha-icon>
            </button>
          </div>
          <div class="right playlist-slot"></div>
        </div>
        <div class="toast" hidden></div>
      </ha-card>`;

    const root = this.shadowRoot;
    root.querySelectorAll("[data-action]").forEach((el) =>
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._onAction(el.dataset.action);
      })
    );
    const select = root.querySelector(".source");
    select.addEventListener("change", () =>
      this._service("media_player", "select_source", { source: select.value })
    );
    const slider = root.querySelector(".slider");
    slider.addEventListener("input", () => {
      this._dragging = true;
      root.querySelector(".vol-value").textContent = slider.value;
      this._paintSlider(slider);
    });
    slider.addEventListener("change", () => {
      this._dragging = false;
      this._service("media_player", "volume_set", { volume_level: Number(slider.value) / 100 });
    });

    // embedded playlist card
    const slot = root.querySelector(".playlist-slot");
    this._playlist = null;
    if (this._config.show_playlist) {
      const playlist = document.createElement("linn-playlist-card");
      playlist.setConfig({
        entity: this._config.entity,
        show_header: false,
        max_height: this._config.playlist_height,
        _embedded: true,
      });
      slot.append(playlist);
      this._playlist = playlist;
    }
    root.querySelector(".radio").hidden = !(Number(this._config.radio_count) > 0);
    root.querySelector(".library").hidden = !this._config.show_library_button;

    // the fullscreen music selection (not attached, opened on demand)
    this._library?._closeOverlay?.(false);
    this._library = null;
    if (this._config.show_library_button && customElements.get("linn-library-card")) {
      const library = document.createElement("linn-library-card");
      const libConfig = { entity: this._config.entity, display: "button" };
      for (const key of ["sources", "track_action", "view"]) {
        if (this._config[key] !== undefined) libConfig[key] = this._config[key];
      }
      library.setConfig(libConfig);
      this._library = library;
    }
    this._built = true;
    this._lastKey = null;
    this._layout = false; // the playlist was created for the single column
    this._renderStations();
    this._observeSize();
  }

  // ---------------------------------------------------------- layout
  /* The layout follows the width of the card itself (a card can be narrow on a
     PC, too): "narrow" stacked (phone), "medium" with controls side by side,
     "two" with the playlist in a second column (tablet landscape, PC). */
  _observeSize() {
    this._resizeObserver?.disconnect();
    const card = this.shadowRoot.querySelector("ha-card");
    if (!card || !("ResizeObserver" in window)) return;
    this._resizeObserver = new ResizeObserver(() => this._applyLayout());
    this._resizeObserver.observe(card);
    const left = this.shadowRoot.querySelector(".left");
    if (left) this._resizeObserver.observe(left);
  }

  _applyLayout() {
    const card = this.shadowRoot.querySelector("ha-card");
    const left = this.shadowRoot.querySelector(".left");
    if (!card || !left) return;
    const width = card.clientWidth;
    if (!width) return;
    const two = !!this._playlist && width >= TWO_COLUMNS;
    card.classList.toggle("two", two);
    // the player area itself decides between stacked and side by side
    const playerWidth = two ? left.clientWidth : width;
    card.classList.toggle("medium", playerWidth >= MEDIUM);
    card.classList.toggle("roomy", playerWidth >= ROOMY);
    if (two !== this._layout) {
      this._layout = two;
      if (this._playlist) {
        this._playlist.setConfig({
          entity: this._config.entity,
          show_header: false,
          max_height: two ? "none" : this._config.playlist_height,
          _embedded: true,
        });
        if (this._hass) this._playlist.hass = this._hass;
      }
    }
    // second column: as high as the left one, the list scrolls inside
    const right = this.shadowRoot.querySelector(".right");
    if (right) right.style.height = two ? `${Math.max(left.offsetHeight, 420)}px` : "";
  }

  // ------------------------------------------------------------ update
  _update() {
    const root = this.shadowRoot;
    const stateObj = this._stateObj;
    if (!stateObj || !root.querySelector(".player")) return;
    const t = this._t;
    const a = stateObj.attributes;
    const state = stateObj.state;
    const off = state === "off";
    const unavailable = state === "unavailable";

    let title = a.media_title || t.nothing;
    let sub = [a.media_artist, a.media_album_name].filter(Boolean).join(" · ");
    if (off) [title, sub] = [t.off, ""];
    if (unavailable) [title, sub] = [t.unavailable, ""];
    root.querySelector(".title").textContent = title;
    root.querySelector(".sub").textContent = sub;
    const art = root.querySelector(".art");
    if (a.entity_picture && !off) {
      art.style.backgroundImage = `url('${a.entity_picture}')`;
      art.classList.remove("placeholder");
      art.innerHTML = "";
    } else if (!art.classList.contains("placeholder")) {
      art.style.backgroundImage = "";
      art.classList.add("placeholder");
      art.innerHTML = `<ha-icon icon="mdi:music"></ha-icon>`;
    }

    const power = root.querySelector(".power");
    power.classList.toggle("on", !off && !unavailable);
    power.disabled = unavailable;

    // sources
    const select = root.querySelector(".source");
    const sources = a.source_list || [];
    const key = sources.join("|");
    if (key !== this._sourcesKey) {
      this._sourcesKey = key;
      select.innerHTML = sources.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    }
    if (this.shadowRoot.activeElement !== select) select.value = a.source || "";
    select.disabled = off || unavailable || !sources.length;
    root.querySelector(".source-row").hidden = !sources.length;

    // transport
    const playing = state === "playing" || state === "buffering";
    root.querySelector(".play ha-icon").setAttribute("icon", playing ? "mdi:pause" : "mdi:play");
    root.querySelectorAll(".transport button").forEach((b) => (b.disabled = off || unavailable));

    // volume
    const hasVolume = a.volume_level !== undefined;
    root.querySelector(".volume").hidden = !hasVolume;
    if (hasVolume) {
      const slider = root.querySelector(".slider");
      const value = Math.round((a.volume_level || 0) * 100);
      if (!this._dragging) {
        slider.value = value;
        root.querySelector(".vol-value").textContent = String(value);
        this._paintSlider(slider);
      }
      root.querySelector(".mute ha-icon").setAttribute("icon", a.is_volume_muted ? "mdi:volume-off" : "mdi:volume-high");
      root.querySelector(".mute").classList.toggle("active", !!a.is_volume_muted);
      root.querySelectorAll(".volume button, .volume input").forEach((el) => (el.disabled = off || unavailable));
    }

    // highlight the station that is playing
    const station = a.source && /radio/i.test(a.source) ? a.media_title : null;
    if (station !== this._playingStation) {
      this._playingStation = station;
      this._renderStations();
    }
  }

  _paintSlider(slider) {
    slider.style.setProperty("--value", `${slider.value}%`);
  }

  // ---------------------------------------------------------- stations
  async _fetchFavorites() {
    try {
      const result = await this._hass.callWS({
        type: `${DOMAIN}/radio_favorites`,
        entity_id: this._config.entity,
        limit: Number(this._config.radio_count) || 8,
      });
      this._favorites = result.items || [];
      this._favoritesState = !result.configured ? "hint" : result.message ? "failed" : null;
    } catch (err) {
      this._favorites = [];
      this._favoritesState = "failed";
    }
    this._renderStations();
  }

  _renderStations() {
    const box = this.shadowRoot.querySelector(".stations");
    if (!box) return;
    const t = this._t;
    if (this._favorites === null) {
      box.innerHTML = `<div class="hint"><ha-icon class="spin" icon="mdi:loading"></ha-icon></div>`;
      return;
    }
    if (this._favoritesState === "hint") {
      box.innerHTML = `<div class="hint">${esc(t.radioHint)}</div>`;
      return;
    }
    if (this._favoritesState === "failed" || !this._favorites.length) {
      box.innerHTML = `<div class="hint">${esc(t.radioFailed)}</div>`;
      return;
    }
    box.innerHTML = this._favorites
      .map((st) => {
        const logo = st.image
          ? `<span class="logo" style="background-image:url('${esc(st.image)}')"></span>`
          : `<span class="logo placeholder"><ha-icon icon="mdi:radio"></ha-icon></span>`;
        const active = this._playingStation && st.title === this._playingStation ? "active" : "";
        return `<button class="station ${active}" data-id="${esc(st.id)}" title="${esc(st.title)}">${logo}<span class="st-name">${esc(st.title)}</span></button>`;
      })
      .join("");
    box.querySelectorAll(".station").forEach((el) =>
      el.addEventListener("click", () => this._playStation(el.dataset.id, el.title))
    );
  }

  async _playStation(id, name) {
    try {
      await this._hass.callWS({ type: `${DOMAIN}/load`, entity_id: this._config.entity, media_content_id: id, mode: "play" });
      this._toast(this._t.playing(name));
    } catch (err) {
      this._toast(`${this._t.error}: ${err?.message || err}`, true);
    }
  }

  // ----------------------------------------------------------- actions
  _service(domain, service, data = {}) {
    return this._hass
      .callService(domain, service, { entity_id: this._config.entity, ...data })
      .catch((err) => this._toast(`${this._t.error}: ${err?.message || err}`, true));
  }

  _onAction(action) {
    const state = this._stateObj?.state;
    switch (action) {
      case "power":
        return this._service("media_player", state === "off" ? "turn_on" : "turn_off");
      case "prev":
        return this._service("media_player", "media_previous_track");
      case "next":
        return this._service("media_player", "media_next_track");
      case "playpause":
        return this._service("media_player", "media_play_pause");
      case "mute":
        return this._service("media_player", "volume_mute", {
          is_volume_muted: !this._stateObj?.attributes?.is_volume_muted,
        });
      case "voldown":
        return this._service("media_player", "volume_down");
      case "volup":
        return this._service("media_player", "volume_up");
      case "library":
        if (this._library) {
          this._library.hass = this._hass;
          this._library._openOverlay();
        }
        return;
    }
  }

  _toast(text, error = false) {
    const el = this.shadowRoot.querySelector(".toast");
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("error", error);
    el.hidden = false;
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => (el.hidden = true), 2800);
  }
}

const STYLE = `
  :host { display: block; }
  ha-card { overflow: hidden; position: relative; }
  [hidden] { display: none !important; }
  button { font: inherit; color: inherit; background: none; border: none; cursor: pointer; padding: 0; }
  button:disabled, select:disabled, input:disabled { opacity: 0.35; cursor: default; }
  .icon { width: 40px; height: 40px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
          color: var(--secondary-text-color); flex: none; transition: background 0.15s, color 0.15s; }
  .icon:not(:disabled):hover { background: rgba(127,127,127,0.15); }
  .icon.small { width: 34px; height: 34px; --mdc-icon-size: 20px; }
  .icon.active { color: var(--primary-color); }
  /* ---- player: CSS grid, arranged by the width classes ---- */
  .player { padding: 14px 14px 8px; display: grid; gap: 8px 12px; align-items: center;
            grid-template-columns: 64px minmax(0, 1fr) auto;
            grid-template-areas: "art text power" "source source source" "transport transport transport" "volume volume volume"; }
  .art { grid-area: art; }
  .now-text { grid-area: text; min-width: 0; }
  .power { grid-area: power; }
  .source-row { grid-area: source; }
  .transport { grid-area: transport; }
  .volume { grid-area: volume; }
  .medium .player { grid-template-columns: 72px minmax(0, 1fr) minmax(0, 1fr) auto;
                    grid-template-areas: "art text text power" "transport transport volume volume" "source source source source";
                    row-gap: 12px; }
  .medium .transport { justify-content: flex-start; gap: 10px; }
  .roomy .player { grid-template-columns: 96px minmax(0, 1fr) minmax(170px, 240px) auto;
                   grid-template-areas: "art text source power" "art transport volume volume"; }
  .roomy .source-icon { display: none; }
  .roomy .art { width: 96px; height: 96px; }
  .roomy .title { font-size: 1.3em; }
  .art { width: 64px; height: 64px; border-radius: 8px; background: center / cover no-repeat; flex: none;
         box-shadow: 0 1px 4px rgba(0,0,0,0.3); --mdc-icon-size: 30px; }
  .medium .art { width: 72px; height: 72px; }
  .art.placeholder { display: flex; align-items: center; justify-content: center; box-shadow: none;
                     background-color: var(--secondary-background-color, rgba(127,127,127,0.15)); color: var(--secondary-text-color); }
  .title { font-size: 1.1em; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sub { color: var(--secondary-text-color); font-size: 0.9em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .power { --mdc-icon-size: 24px; }
  .power.on { color: var(--primary-color); background: rgba(var(--rgb-primary-color, 3,169,244), 0.14); }
  .source-row { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .source-icon { color: var(--secondary-text-color); --mdc-icon-size: 20px; margin-left: 10px; }
  .source { flex: 1; min-width: 0; height: 36px; border-radius: 18px; border: 1px solid var(--divider-color); padding: 0 12px;
            font: inherit; color: var(--primary-text-color); background: var(--card-background-color, #fff); outline: none; }
  .source:focus { border-color: var(--primary-color); }
  .transport { display: flex; align-items: center; justify-content: center; gap: 18px; }
  .transport .icon { width: 48px; height: 48px; --mdc-icon-size: 30px; color: var(--primary-text-color); }
  .transport .play { width: 60px; height: 60px; --mdc-icon-size: 36px; background: var(--primary-color);
                     color: var(--text-primary-color, #fff); box-shadow: 0 2px 6px rgba(0,0,0,0.25); }
  .transport .play:not(:disabled):hover { background: var(--primary-color); filter: brightness(1.08); }
  /* ---- two columns: player | playlist ---- */
  .layout { display: block; }
  .two .layout { display: grid; grid-template-columns: minmax(360px, 5fr) minmax(380px, 6fr); align-items: start; }
  .two .right { border-top: none; border-left: 1px solid var(--divider-color); display: flex; min-height: 0; overflow: hidden; }
  .two .right > * { flex: 1; min-width: 0; height: 100%; }
  .two .player { padding-top: 18px; }
  .two .library { margin-bottom: 16px; }
  /* station tiles wrap instead of scrolling sideways when there is room */
  .medium .stations, .two .stations { flex-wrap: wrap; overflow: visible; }
  .medium .station { width: 76px; }
  .medium .logo { width: 54px; height: 54px; }
  .volume { display: flex; align-items: center; gap: 4px; }
  .slider { flex: 1; min-width: 60px; -webkit-appearance: none; appearance: none; height: 6px; border-radius: 3px; outline: none;
            background: linear-gradient(to right, var(--primary-color) var(--value, 0%), var(--divider-color) var(--value, 0%)); }
  .slider::-webkit-slider-thumb { -webkit-appearance: none; width: 18px; height: 18px; border-radius: 50%;
            background: var(--primary-color); border: 2px solid var(--card-background-color, #fff); box-shadow: 0 1px 3px rgba(0,0,0,0.4); cursor: pointer; }
  .slider::-moz-range-thumb { width: 16px; height: 16px; border-radius: 50%; background: var(--primary-color);
            border: 2px solid var(--card-background-color, #fff); cursor: pointer; }
  .vol-value { width: 30px; text-align: right; font-variant-numeric: tabular-nums; color: var(--secondary-text-color); font-size: 0.9em; }
  .section { border-top: 1px solid var(--divider-color); padding: 10px 0 8px; }
  .section-head { display: flex; align-items: center; gap: 8px; padding: 0 16px 8px; font-weight: 500; --mdc-icon-size: 18px; }
  .section-head ha-icon { color: var(--secondary-text-color); }
  .stations { display: flex; gap: 10px; overflow-x: auto; padding: 0 14px 4px; scrollbar-width: thin; }
  .station { display: flex; flex-direction: column; align-items: center; gap: 4px; width: 68px; flex: none; padding: 4px 0; border-radius: 10px; }
  .station:hover { background: rgba(127,127,127,0.08); }
  .logo { width: 48px; height: 48px; border-radius: 10px; background: #fff center / contain no-repeat;
          box-shadow: 0 0 0 1px var(--divider-color) inset; display: flex; align-items: center; justify-content: center;
          color: var(--secondary-text-color); --mdc-icon-size: 24px; }
  .logo.placeholder { background-color: var(--secondary-background-color, rgba(127,127,127,0.12)); box-shadow: none; }
  .station.active .logo { box-shadow: 0 0 0 2px var(--primary-color); }
  .station.active .st-name { color: var(--primary-color); font-weight: 500; }
  .st-name { font-size: 0.75em; line-height: 1.2; width: 100%; text-align: center; overflow: hidden; display: -webkit-box;
             -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow-wrap: break-word; hyphens: auto; }
  .hint { padding: 0 16px 6px; color: var(--secondary-text-color); font-size: 0.85em; }
  .library { display: flex; align-items: center; gap: 12px; width: calc(100% - 28px); margin: 6px 14px 12px; padding: 10px 14px;
             border-radius: 14px; background: var(--primary-color); color: var(--text-primary-color, #fff); text-align: left;
             box-shadow: 0 2px 6px rgba(0,0,0,0.2); --mdc-icon-size: 26px; transition: filter 0.15s; }
  .library:hover { filter: brightness(1.08); }
  .lib-text { flex: 1; display: flex; flex-direction: column; }
  .lib-name { font-weight: 500; font-size: 1.05em; }
  .lib-hint { font-size: 0.8em; opacity: 0.85; }
  .lib-go { --mdc-icon-size: 20px; opacity: 0.85; }
  .playlist-slot { border-top: 1px solid var(--divider-color); }
  @media (max-width: 360px) { .transport { gap: 8px; } .volume .vol-value { display: none; } }
  .message { padding: 24px 16px; text-align: center; color: var(--secondary-text-color); }
  .toast { position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%); max-width: calc(100% - 32px);
           background: var(--primary-text-color); color: var(--card-background-color, #fff); padding: 9px 16px;
           border-radius: 18px; font-size: 0.9em; box-shadow: 0 4px 14px rgba(0,0,0,0.3); white-space: nowrap;
           overflow: hidden; text-overflow: ellipsis; z-index: 6; }
  .toast.error { background: var(--error-color, #db4437); color: #fff; }
  .spin { animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
`;

if (!customElements.get("linn-player-card")) {
  customElements.define("linn-player-card", LinnPlayerCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "linn-player-card",
    name: "Linn Player",
    description: "Linn-Steuerung, Radio-Favoriten, Musikauswahl und Playlist in einer Karte (DLNA Musikauswahl).",
    preview: false,
    documentationURL: "https://github.com/flaxg73/dlna-MusikAuswahl",
  });
  console.info(`%c LINN-PLAYER-CARD %c ${CARD_VERSION} `, "background:#000;color:#fff", "background:#ddd;color:#000");
}
})();

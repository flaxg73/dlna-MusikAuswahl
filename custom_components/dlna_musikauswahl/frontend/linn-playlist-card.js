/*
 * Linn Playlist Card – shows and edits the internal playlist of a Linn DS/DSM
 * that is controlled by the "DLNA Musikauswahl" integration.
 *
 *   type: custom:linn-playlist-card
 *   entity: media_player.wohnzimmer
 *   title: Playlist          # optional
 *   show_header: true        # optional, "now playing" header with controls
 *   max_height: 480px        # optional, height of the scrolling list
 */

const DOMAIN = "dlna_musikauswahl";
const CARD_VERSION = "0.7.0";

const STRINGS = {
  de: {
    playlist: "Playlist",
    empty: "Die Playlist ist leer.",
    emptyHint: "Musik über „Medien“ auswählen und hinzufügen.",
    tracks: (n) => (n === 1 ? "1 Titel" : `${n} Titel`),
    edit: "Bearbeiten",
    done: "Fertig",
    clear: "Playlist leeren",
    clearConfirm: "Die gesamte Playlist auf dem Linn löschen?",
    removeSelected: (n) => `${n} entfernen`,
    selectAll: "Alle",
    selectNone: "Keine",
    remove: "Entfernen",
    up: "Nach oben",
    down: "Nach unten",
    top: "An den Anfang",
    playNext: "Als Nächstes",
    nothing: "Nichts wird abgespielt",
    otherSource: (s) => `Quelle: ${s}`,
    off: "Standby",
    unavailable: "Linn nicht erreichbar",
    notFound: "Entität gehört nicht zur Integration DLNA Musikauswahl",
    noEntity: "Bitte eine Linn-Player-Entität auswählen",
    error: "Fehler",
    save: "Playlist speichern",
    saved: "Gespeicherte Playlists",
    saveName: "Name der Playlist",
    saveDo: "Speichern",
    savedOk: (n) => `„${n}“ gespeichert`,
    overwrite: (n) => `Es gibt bereits eine Playlist „${n}“. Überschreiben?`,
    noSaved: "Noch keine gespeicherten Playlists.",
    loadReplace: "Laden (Playlist ersetzen)",
    loadAdd: "Hinten anhängen",
    deleteSaved: "Löschen",
    deleteConfirm: (n) => `Gespeicherte Playlist „${n}“ löschen?`,
    loadedOk: (n, c) => `„${n}“ geladen (${c} Titel)`,
    close: "Schließen",
  },
  en: {
    playlist: "Playlist",
    empty: "The playlist is empty.",
    emptyHint: "Pick music in “Media” and add it.",
    tracks: (n) => (n === 1 ? "1 track" : `${n} tracks`),
    edit: "Edit",
    done: "Done",
    clear: "Clear playlist",
    clearConfirm: "Delete the whole playlist on the Linn?",
    removeSelected: (n) => `Remove ${n}`,
    selectAll: "All",
    selectNone: "None",
    remove: "Remove",
    up: "Move up",
    down: "Move down",
    top: "Move to top",
    playNext: "Play next",
    nothing: "Nothing playing",
    otherSource: (s) => `Source: ${s}`,
    off: "Standby",
    unavailable: "Linn not reachable",
    notFound: "Entity does not belong to the DLNA Musikauswahl integration",
    noEntity: "Please select a Linn player entity",
    error: "Error",
    save: "Save playlist",
    saved: "Saved playlists",
    saveName: "Playlist name",
    saveDo: "Save",
    savedOk: (n) => `“${n}” saved`,
    overwrite: (n) => `A playlist “${n}” already exists. Overwrite?`,
    noSaved: "No saved playlists yet.",
    loadReplace: "Load (replace playlist)",
    loadAdd: "Append",
    deleteSaved: "Delete",
    deleteConfirm: (n) => `Delete saved playlist “${n}”?`,
    loadedOk: (n, c) => `“${n}” loaded (${c} tracks)`,
    close: "Close",
  },
};

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const fmtDate = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleDateString();
};

const fmtDuration = (seconds) => {
  if (seconds == null || isNaN(seconds)) return "";
  seconds = Math.round(seconds);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
};

class LinnPlaylistCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tracks = [];
    this._version = null;
    this._currentId = null;
    this._editing = false;
    this._selected = new Set();
    this._busy = false;
    this._error = null;
    this._loaded = false;
    this._drag = null;
  }

  // ------------------------------------------------------------ HA API
  static getConfigForm() {
    return {
      schema: [
        { name: "entity", required: true, selector: { entity: { domain: "media_player", integration: DOMAIN } } },
        { name: "title", selector: { text: {} } },
        { name: "show_header", selector: { boolean: {} } },
        { name: "max_height", selector: { text: {} } },
      ],
    };
  }

  static getStubConfig(hass) {
    const entities = hass?.entities || {};
    const entity = Object.keys(entities).find((id) => entities[id].platform === DOMAIN && id.startsWith("media_player."));
    return { entity: entity || "", show_header: true };
  }

  setConfig(config) {
    this._config = { show_header: true, max_height: "480px", ...config };
    this._version = null;
    this._loaded = false;
    if (this._hass) this._fetch(true);
    this._render();
  }

  getCardSize() {
    return 3 + Math.min(this._tracks.length, 8);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: 8, min_rows: 4 };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    const stateObj = this._stateObj;
    if (!stateObj) {
      this._render();
      return;
    }
    const version = stateObj.attributes.playlist_version;
    const current = stateObj.attributes.current_track_id ?? null;
    const stateKey = [stateObj.state, stateObj.attributes.media_title, stateObj.attributes.entity_picture, stateObj.attributes.source].join("|");

    if (first || !this._loaded) {
      this._fetch(true);
    } else if (version !== this._version) {
      this._fetch(false);
    }
    if (current !== this._currentId || stateKey !== this._stateKey) {
      const trackChanged = current !== this._currentId;
      this._currentId = current;
      this._stateKey = stateKey;
      this._render();
      if (trackChanged) this._scrollToCurrent();
    }
  }

  get _stateObj() {
    return this._hass && this._config?.entity ? this._hass.states[this._config.entity] : undefined;
  }

  get _t() {
    const lang = (this._hass?.locale?.language || this._hass?.language || "de").slice(0, 2);
    return STRINGS[lang] || STRINGS.en;
  }

  // ------------------------------------------------------------- data
  async _fetch(refresh) {
    if (!this._hass || !this._config?.entity || this._fetching) {
      if (this._fetching) this._refetch = true;
      return;
    }
    this._fetching = true;
    try {
      const result = await this._hass.callWS({ type: `${DOMAIN}/playlist`, entity_id: this._config.entity, refresh });
      this._tracks = result.tracks || [];
      this._version = result.version;
      this._currentId = result.current_id ?? null;
      this._error = null;
      const ids = new Set(this._tracks.map((t) => t.id));
      this._selected = new Set([...this._selected].filter((id) => ids.has(id)));
    } catch (err) {
      this._error = err?.code === "not_found" ? this._t.notFound : err?.message || String(err);
    } finally {
      this._loaded = true;
      this._fetching = false;
    }
    this._render();
    if (this._refetch) {
      this._refetch = false;
      this._fetch(false);
    }
  }

  async _service(service, data) {
    if (this._busy) return;
    this._busy = true;
    this._render();
    try {
      await this._hass.callService(DOMAIN, service, data, { entity_id: this._config.entity });
      await this._fetch(false);
    } catch (err) {
      this._error = `${this._t.error}: ${err?.message || err}`;
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _mediaService(service, data = {}) {
    this._hass.callService("media_player", service, { entity_id: this._config.entity, ...data });
  }

  _move(id, position) {
    const index = this._tracks.findIndex((t) => t.id === id);
    if (index < 0 || position < 1 || position > this._tracks.length || position === index + 1) return;
    // optimistic reorder
    const [track] = this._tracks.splice(index, 1);
    this._tracks.splice(position - 1, 0, track);
    this._render();
    this._service("move_track", { track_id: id, position });
  }

  _remove(ids) {
    if (!ids.length) return;
    const set = new Set(ids);
    this._tracks = this._tracks.filter((t) => !set.has(t.id));
    ids.forEach((id) => this._selected.delete(id));
    this._render();
    this._service("remove_tracks", { track_ids: ids });
  }

  // ----------------------------------------------------------- render
  _render() {
    if (!this._config) return;
    if (this._drag) return; // re-rendered when the drag ends
    const t = this._t;
    const stateObj = this._stateObj;
    const title = this._config.title ?? t.playlist;

    let body;
    if (!this._config.entity) {
      body = `<div class="message">${esc(t.noEntity)}</div>`;
    } else if (!stateObj) {
      body = `<div class="message warning">${esc(this._config.entity)}?</div>`;
    } else {
      body =
        this._renderHeader(stateObj) + this._renderToolbar(title) + this._renderPanel() + this._renderList();
    }
    if (this._error) body += `<div class="message warning">${esc(this._error)}</div>`;

    const scroll = this.shadowRoot.querySelector(".list")?.scrollTop;
    const typing = this.shadowRoot.activeElement?.classList?.contains("save-name");
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card class="${this._config._embedded ? "full" : ""}"><div class="body">${body}</div></ha-card>`;
    const list = this.shadowRoot.querySelector(".list");
    if (list) {
      list.style.maxHeight = this._config.max_height;
      if (scroll) list.scrollTop = scroll;
    }
    const input = this.shadowRoot.querySelector(".save-name");
    if (input && (typing || this._focusSave)) {
      input.focus();
      if (this._focusSave) input.select();
      this._focusSave = false;
    }
    this._bind();
  }

  _renderHeader(stateObj) {
    if (!this._config.show_header) return "";
    const t = this._t;
    const a = stateObj.attributes;
    const state = stateObj.state;
    let line1 = a.media_title || t.nothing;
    let line2 = [a.media_artist, a.media_album_name].filter(Boolean).join(" · ");
    if (state === "off") {
      line1 = t.off;
      line2 = "";
    } else if (state === "unavailable") {
      line1 = t.unavailable;
      line2 = "";
    } else if (a.source && a.source.toLowerCase() !== "playlist") {
      line2 = t.otherSource(a.source) + (line2 ? ` · ${line2}` : "");
    }
    const playing = state === "playing" || state === "buffering";
    const art = a.entity_picture
      ? `<div class="art" style="background-image:url('${esc(a.entity_picture)}')"></div>`
      : `<div class="art placeholder"><ha-icon icon="mdi:music"></ha-icon></div>`;
    const disabled = state === "unavailable" ? "disabled" : "";
    return `
      <div class="header">
        ${art}
        <div class="now">
          <div class="title">${esc(line1)}</div>
          <div class="sub">${esc(line2)}</div>
        </div>
        <div class="controls">
          <button class="icon" data-action="prev" ${disabled}><ha-icon icon="mdi:skip-previous"></ha-icon></button>
          <button class="icon big" data-action="playpause" ${disabled}><ha-icon icon="${playing ? "mdi:pause" : "mdi:play"}"></ha-icon></button>
          <button class="icon" data-action="next" ${disabled}><ha-icon icon="mdi:skip-next"></ha-icon></button>
        </div>
      </div>`;
  }

  _renderToolbar(title) {
    const t = this._t;
    const total = this._tracks.reduce((sum, tr) => sum + (tr.duration || 0), 0);
    const info = [t.tracks(this._tracks.length), total ? fmtDuration(total) : ""].filter(Boolean).join(" · ");
    if (this._editing) {
      const n = this._selected.size;
      const all = n === this._tracks.length && n > 0;
      return `
        <div class="toolbar editing">
          <button class="text" data-action="select-all">${esc(all ? t.selectNone : t.selectAll)}</button>
          <button class="text danger" data-action="remove-selected" ${n ? "" : "disabled"}>
            <ha-icon icon="mdi:delete"></ha-icon>${esc(t.removeSelected(n))}</button>
          <span class="spacer"></span>
          <button class="icon" data-action="clear" title="${esc(t.clear)}" ${this._tracks.length ? "" : "disabled"}>
            <ha-icon icon="mdi:playlist-remove"></ha-icon></button>
          <button class="text primary" data-action="edit">${esc(t.done)}</button>
        </div>`;
    }
    return `
      <div class="toolbar">
        <div class="heading"><span class="name">${esc(title)}</span><span class="info">${esc(info)}</span></div>
        <span class="spacer"></span>
        ${this._busy ? `<ha-icon class="spin" icon="mdi:loading"></ha-icon>` : ""}
        <button class="icon" data-action="locate" title="▶" ${this._currentId ? "" : "disabled"}>
          <ha-icon icon="mdi:crosshairs-gps"></ha-icon></button>
        <button class="icon ${this._panel === "save" ? "active" : ""}" data-action="panel-save" title="${esc(t.save)}" ${this._tracks.length ? "" : "disabled"}>
          <ha-icon icon="mdi:content-save-outline"></ha-icon></button>
        <button class="icon ${this._panel === "load" ? "active" : ""}" data-action="panel-load" title="${esc(t.saved)}">
          <ha-icon icon="mdi:playlist-star"></ha-icon></button>
        <button class="icon" data-action="edit" title="${esc(t.edit)}" ${this._tracks.length ? "" : "disabled"}>
          <ha-icon icon="mdi:pencil"></ha-icon></button>
      </div>`;
  }

  _renderPanel() {
    const t = this._t;
    const notice = this._notice
      ? `<div class="notice ${this._notice.error ? "error" : ""}">${esc(this._notice.text)}</div>`
      : "";
    if (this._panel === "save") {
      return `<div class="panel">
        <div class="save-row">
          <input class="save-name" type="text" enterkeyhint="done" maxlength="80" placeholder="${esc(t.saveName)}" value="${esc(this._saveName ?? "")}">
          <button class="pill" data-action="save-do" ${this._busy ? "disabled" : ""}><ha-icon icon="mdi:content-save"></ha-icon>${esc(t.saveDo)}</button>
          <button class="icon small" data-action="panel-close" title="${esc(t.close)}"><ha-icon icon="mdi:close"></ha-icon></button>
        </div>${notice}</div>`;
    }
    if (this._panel === "load") {
      const items = this._savedList;
      let list;
      if (items == null) list = `<div class="message"><ha-icon class="spin" icon="mdi:loading"></ha-icon></div>`;
      else if (!items.length) list = `<div class="message small">${esc(t.noSaved)}</div>`;
      else {
        list = items
          .map((pl) => {
            const info = [t.tracks(pl.count), pl.duration ? fmtDuration(pl.duration) : "", fmtDate(pl.saved_at)]
              .filter(Boolean)
              .join(" · ");
            const thumb = pl.image
              ? `<div class="thumb" style="background-image:url('${esc(pl.image)}')"></div>`
              : `<div class="thumb placeholder"><ha-icon icon="mdi:playlist-music"></ha-icon></div>`;
            return `<div class="saved-row">${thumb}
              <div class="sv-text"><div class="t">${esc(pl.name)}</div><div class="s">${esc(info)}</div></div>
              <button class="icon small primary" data-action="saved-load" data-name="${esc(pl.name)}" title="${esc(t.loadReplace)}"><ha-icon icon="mdi:play-circle"></ha-icon></button>
              <button class="icon small" data-action="saved-add" data-name="${esc(pl.name)}" title="${esc(t.loadAdd)}"><ha-icon icon="mdi:playlist-plus"></ha-icon></button>
              <button class="icon small danger" data-action="saved-delete" data-name="${esc(pl.name)}" title="${esc(t.deleteSaved)}"><ha-icon icon="mdi:delete-outline"></ha-icon></button>
            </div>`;
          })
          .join("");
      }
      return `<div class="panel"><div class="panel-head"><span>${esc(t.saved)}</span>
        <button class="icon small" data-action="panel-close" title="${esc(t.close)}"><ha-icon icon="mdi:close"></ha-icon></button></div>
        ${notice}<div class="saved-list">${list}</div></div>`;
    }
    return notice ? `<div class="panel">${notice}</div>` : "";
  }

  async _fetchSaved() {
    try {
      const result = await this._hass.callWS({ type: `${DOMAIN}/saved_playlists`, entity_id: this._config.entity });
      this._savedList = result.playlists || [];
    } catch (err) {
      this._savedList = [];
      this._showNotice(`${this._t.error}: ${err?.message || err}`, true);
    }
    this._render();
  }

  _showNotice(text, error = false) {
    this._notice = { text, error };
    clearTimeout(this._noticeTimer);
    this._render();
    this._noticeTimer = setTimeout(() => {
      this._notice = null;
      this._render();
    }, 3500);
  }

  async _savePlaylist() {
    const t = this._t;
    const name = (this._saveName || "").trim();
    if (!name) return this.shadowRoot.querySelector(".save-name")?.focus();
    if (this._savedList == null) {
      try {
        const result = await this._hass.callWS({ type: `${DOMAIN}/saved_playlists`, entity_id: this._config.entity });
        this._savedList = result.playlists || [];
      } catch (err) {
        this._savedList = [];
      }
    }
    if (this._savedList.some((pl) => pl.name === name) && !confirm(t.overwrite(name))) return;
    this._busy = true;
    this._render();
    try {
      await this._hass.callService(DOMAIN, "save_playlist", { name }, { entity_id: this._config.entity });
      this._panel = null;
      this._savedList = null;
      this._showNotice(t.savedOk(name));
    } catch (err) {
      this._showNotice(`${t.error}: ${err?.message || err}`, true);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _loadSaved(name, mode) {
    const t = this._t;
    try {
      const result = await this._hass.callWS({
        type: `${DOMAIN}/load`,
        entity_id: this._config.entity,
        media_content_id: `saved:${name}`,
        mode,
      });
      this._panel = null;
      this._showNotice(t.loadedOk(name, result.count));
      this._fetch(false);
    } catch (err) {
      this._showNotice(`${t.error}: ${err?.message || err}`, true);
    }
  }

  async _deleteSaved(name) {
    const t = this._t;
    if (!confirm(t.deleteConfirm(name))) return;
    try {
      await this._hass.callService(DOMAIN, "delete_playlist", { name }, { entity_id: this._config.entity });
      this._savedList = (this._savedList || []).filter((pl) => pl.name !== name);
      this._render();
    } catch (err) {
      this._showNotice(`${t.error}: ${err?.message || err}`, true);
    }
  }

  _renderList() {
    const t = this._t;
    if (!this._loaded) return `<div class="message"><ha-icon class="spin" icon="mdi:loading"></ha-icon></div>`;
    if (!this._tracks.length) {
      return `<div class="message"><ha-icon icon="mdi:playlist-music"></ha-icon><div>${esc(t.empty)}</div>
        <div class="hint">${esc(t.emptyHint)}</div></div>`;
    }
    const playing = ["playing", "buffering"].includes(this._stateObj?.state);
    const rows = this._tracks
      .map((tr, i) => {
        const current = tr.id === this._currentId;
        const sub = [tr.artist, tr.album].filter(Boolean).join(" · ");
        const thumb = tr.image
          ? `<div class="thumb" style="background-image:url('${esc(tr.image)}')"></div>`
          : `<div class="thumb placeholder"><ha-icon icon="mdi:music-note"></ha-icon></div>`;
        const lead = this._editing
          ? `<input type="checkbox" class="check" data-id="${tr.id}" ${this._selected.has(tr.id) ? "checked" : ""}>`
          : current
            ? `<ha-icon class="eq ${playing ? "on" : ""}" icon="${playing ? "mdi:volume-high" : "mdi:pause"}"></ha-icon>`
            : `<span class="num">${i + 1}</span>`;
        const trail = this._editing
          ? `<button class="icon small" data-action="up" data-id="${tr.id}" title="${esc(t.up)}" ${i ? "" : "disabled"}><ha-icon icon="mdi:chevron-up"></ha-icon></button>
             <button class="icon small" data-action="down" data-id="${tr.id}" title="${esc(t.down)}" ${i < this._tracks.length - 1 ? "" : "disabled"}><ha-icon icon="mdi:chevron-down"></ha-icon></button>
             <button class="icon small danger" data-action="remove" data-id="${tr.id}" title="${esc(t.remove)}"><ha-icon icon="mdi:close"></ha-icon></button>
             <span class="handle" data-id="${tr.id}" title="⇅"><ha-icon icon="mdi:drag-horizontal-variant"></ha-icon></span>`
          : `<span class="dur">${esc(fmtDuration(tr.duration))}</span>
             <button class="icon small more" data-action="next-up" data-id="${tr.id}" title="${esc(t.playNext)}" ${current || !this._currentId ? "disabled" : ""}><ha-icon icon="mdi:playlist-play"></ha-icon></button>`;
        return `
          <div class="row ${current ? "current" : ""} ${this._editing ? "edit" : ""}" data-id="${tr.id}" data-index="${i}">
            <div class="lead">${lead}</div>
            ${thumb}
            <div class="text"><div class="t">${esc(tr.title)}</div><div class="s">${esc(sub)}</div></div>
            <div class="trail">${trail}</div>
          </div>`;
      })
      .join("");
    return `<div class="list">${rows}</div>`;
  }

  _scrollToCurrent(smooth = true) {
    requestAnimationFrame(() => {
      const row = this.shadowRoot.querySelector(".row.current");
      const list = this.shadowRoot.querySelector(".list");
      if (!row || !list || this._editing) return;
      const top = row.offsetTop - list.offsetTop - list.clientHeight / 2 + row.clientHeight / 2;
      list.scrollTo({ top, behavior: smooth ? "smooth" : "auto" });
    });
  }

  // ------------------------------------------------------------ events
  _bind() {
    const root = this.shadowRoot;
    root.querySelectorAll("button[data-action]").forEach((btn) =>
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._onAction(btn.dataset.action, Number(btn.dataset.id), btn.dataset);
      })
    );
    root.querySelectorAll(".row").forEach((row) =>
      row.addEventListener("click", () => {
        const id = Number(row.dataset.id);
        if (this._editing) {
          this._selected.has(id) ? this._selected.delete(id) : this._selected.add(id);
          this._render();
        } else if (!this._busy) {
          this._service("play_track", { track_id: id });
        }
      })
    );
    root.querySelectorAll("input.check").forEach((box) => box.addEventListener("click", (ev) => ev.stopPropagation()));
    root.querySelectorAll("input.check").forEach((box) =>
      box.addEventListener("change", () => {
        const id = Number(box.dataset.id);
        box.checked ? this._selected.add(id) : this._selected.delete(id);
        this._render();
      })
    );
    const saveInput = root.querySelector(".save-name");
    if (saveInput) {
      saveInput.addEventListener("input", () => (this._saveName = saveInput.value));
      saveInput.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") this._savePlaylist();
        if (ev.key === "Escape") this._onAction("panel-close");
      });
    }
    root.querySelectorAll(".handle").forEach((handle) => handle.addEventListener("pointerdown", (ev) => this._dragStart(ev, handle)));
  }

  _onAction(action, id, data = {}) {
    const t = this._t;
    const index = this._tracks.findIndex((tr) => tr.id === id);
    switch (action) {
      case "panel-save":
        this._panel = this._panel === "save" ? null : "save";
        if (this._panel) {
          const now = new Date();
          this._saveName = `Playlist ${now.toLocaleDateString()} ${now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
          this._focusSave = true;
        }
        return this._render();
      case "panel-load":
        this._panel = this._panel === "load" ? null : "load";
        if (this._panel) {
          this._savedList = null;
          this._fetchSaved();
        }
        return this._render();
      case "panel-close":
        this._panel = null;
        return this._render();
      case "save-do":
        return this._savePlaylist();
      case "saved-load":
        return this._loadSaved(data.name, "replace");
      case "saved-add":
        return this._loadSaved(data.name, "add");
      case "saved-delete":
        return this._deleteSaved(data.name);
      case "prev":
        return this._mediaService("media_previous_track");
      case "next":
        return this._mediaService("media_next_track");
      case "playpause":
        return this._mediaService("media_play_pause");
      case "locate":
        return this._scrollToCurrent();
      case "edit":
        this._editing = !this._editing;
        this._selected.clear();
        this._render();
        if (!this._editing) this._scrollToCurrent(false);
        return;
      case "select-all":
        this._selected = this._selected.size === this._tracks.length ? new Set() : new Set(this._tracks.map((tr) => tr.id));
        return this._render();
      case "remove-selected":
        return this._remove([...this._selected]);
      case "remove":
        return this._remove([id]);
      case "clear":
        if (confirm(t.clearConfirm)) {
          this._tracks = [];
          this._selected.clear();
          this._editing = false;
          this._render();
          this._hass
            .callService("media_player", "clear_playlist", { entity_id: this._config.entity })
            .then(() => this._fetch(false));
        }
        return;
      case "up":
        return this._move(id, index);
      case "down":
        return this._move(id, index + 2);
      case "next-up": {
        const current = this._tracks.findIndex((tr) => tr.id === this._currentId);
        if (current < 0) return;
        // position directly after the current track, once the moved track is taken out
        const position = index < current ? current + 1 : current + 2;
        return this._move(id, position);
      }
    }
  }

  // ----------------------------------------------------- drag & drop
  _dragStart(ev, handle) {
    ev.preventDefault();
    ev.stopPropagation();
    const row = handle.closest(".row");
    const list = this.shadowRoot.querySelector(".list");
    const rows = [...list.querySelectorAll(".row")];
    const from = rows.indexOf(row);
    const rect = row.getBoundingClientRect();
    this._drag = { id: Number(row.dataset.id), from, to: from, row, rows, list, startY: ev.clientY, height: rect.height };
    row.classList.add("dragging");
    handle.setPointerCapture(ev.pointerId);
    const move = (e) => this._dragMove(e);
    const up = (e) => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      this._dragEnd(e);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  }

  _dragMove(ev) {
    const d = this._drag;
    if (!d) return;
    const dy = ev.clientY - d.startY;
    d.row.style.transform = `translateY(${dy}px)`;
    const to = Math.max(0, Math.min(d.rows.length - 1, d.from + Math.round(dy / d.height)));
    d.to = to;
    d.rows.forEach((r, i) => {
      if (r === d.row) return;
      let shift = 0;
      if (d.from < to && i > d.from && i <= to) shift = -d.height;
      if (d.from > to && i < d.from && i >= to) shift = d.height;
      r.style.transform = shift ? `translateY(${shift}px)` : "";
    });
    // auto scroll near the edges
    const box = d.list.getBoundingClientRect();
    if (ev.clientY < box.top + 30) d.list.scrollTop -= 10;
    if (ev.clientY > box.bottom - 30) d.list.scrollTop += 10;
  }

  _dragEnd() {
    const d = this._drag;
    if (!d) return;
    this._drag = null;
    d.rows.forEach((r) => (r.style.transform = ""));
    d.row.classList.remove("dragging");
    if (d.to !== d.from) this._move(d.id, d.to + 1);
    else this._render();
  }
}

const STYLE = `
  :host { display: block; }
  ha-card { overflow: hidden; height: 100%; }
  .body { display: flex; flex-direction: column; height: 100%; min-height: 0; }
  ha-card.full { border-radius: 0; box-shadow: none; border: none; }
  .icon.active { color: var(--primary-color); background: rgba(var(--rgb-primary-color, 3,169,244), 0.12); }
  .icon.primary { color: var(--primary-color); --mdc-icon-size: 26px; }
  .panel { border-bottom: 1px solid var(--divider-color); padding: 8px 12px; background: var(--secondary-background-color, rgba(127,127,127,0.06)); }
  .panel-head { display: flex; align-items: center; justify-content: space-between; font-weight: 500; padding-left: 4px; }
  .save-row { display: flex; align-items: center; gap: 8px; }
  .save-name { flex: 1; min-width: 0; height: 36px; border-radius: 18px; border: 1px solid var(--divider-color); padding: 0 14px;
               font: inherit; color: var(--primary-text-color); background: var(--card-background-color, #fff); outline: none; }
  .save-name:focus { border-color: var(--primary-color); }
  .pill { display: inline-flex; align-items: center; gap: 6px; height: 36px; padding: 0 14px 0 10px; border-radius: 18px;
          background: var(--primary-color); color: var(--text-primary-color, #fff); font-weight: 500; --mdc-icon-size: 18px; flex: none; }
  .saved-list { max-height: 260px; overflow-y: auto; }
  .saved-row { display: flex; align-items: center; gap: 10px; padding: 4px 0 4px 4px; }
  .sv-text { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .notice { margin: 6px 4px 2px; font-size: 0.9em; color: var(--primary-color); }
  .notice.error { color: var(--error-color, #db4437); }
  .message.small { padding: 12px; font-size: 0.9em; }
  .full .header { padding-top: calc(12px + env(safe-area-inset-top, 0px)); }
  button { font: inherit; color: inherit; background: none; border: none; cursor: pointer; padding: 0; }
  button:disabled { opacity: 0.35; cursor: default; }
  .icon { width: 40px; height: 40px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
          color: var(--secondary-text-color); flex: none; }
  .icon:not(:disabled):hover { background: rgba(127,127,127,0.15); }
  .icon.small { width: 32px; height: 32px; --mdc-icon-size: 20px; }
  .icon.big { width: 48px; height: 48px; color: var(--primary-color); --mdc-icon-size: 32px; }
  .danger { color: var(--error-color, #db4437) !important; }
  .text { padding: 6px 10px; border-radius: 16px; display: inline-flex; align-items: center; gap: 4px;
          --mdc-icon-size: 18px; font-weight: 500; }
  .text:not(:disabled):hover { background: rgba(127,127,127,0.15); }
  .text.primary { color: var(--primary-color); }
  .header { display: flex; align-items: center; gap: 12px; padding: 12px 12px 8px 16px; }
  .art { width: 64px; height: 64px; border-radius: 6px; background: center / cover no-repeat; flex: none;
         box-shadow: 0 1px 3px rgba(0,0,0,0.3); }
  .placeholder { display: flex; align-items: center; justify-content: center; background-color: var(--secondary-background-color, rgba(127,127,127,0.15));
                 color: var(--secondary-text-color); }
  .now { flex: 1; min-width: 0; }
  .now .title { font-size: 1.1em; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .now .sub { color: var(--secondary-text-color); font-size: 0.9em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .controls { display: flex; align-items: center; }
  .toolbar { display: flex; align-items: center; gap: 4px; padding: 4px 8px 4px 16px; border-bottom: 1px solid var(--divider-color); min-height: 48px; }
  .toolbar.editing { padding-left: 8px; }
  .heading { display: flex; flex-direction: column; }
  .heading .name { font-size: 1.05em; font-weight: 500; }
  .heading .info { font-size: 0.8em; color: var(--secondary-text-color); }
  .spacer { flex: 1; }
  .list { overflow-y: auto; flex: 1; overscroll-behavior: contain; }
  .row { display: flex; align-items: center; gap: 10px; padding: 4px 8px 4px 4px; cursor: pointer; position: relative;
         background: var(--card-background-color, var(--ha-card-background)); transition: background 0.15s; user-select: none; }
  .row:hover { background: rgba(127,127,127,0.08); }
  .row.current { background: rgba(var(--rgb-primary-color, 3,169,244), 0.12); }
  .row.current .t { color: var(--primary-color); font-weight: 500; }
  .row.dragging { z-index: 2; box-shadow: 0 4px 12px rgba(0,0,0,0.3); transition: none; }
  .row:not(.dragging) { transition: transform 0.15s, background 0.15s; }
  .lead { width: 32px; display: flex; justify-content: center; flex: none; color: var(--secondary-text-color); font-size: 0.85em; }
  .eq { color: var(--primary-color); --mdc-icon-size: 20px; }
  .eq.on { animation: pulse 1.2s ease-in-out infinite; }
  .check { width: 18px; height: 18px; accent-color: var(--primary-color); cursor: pointer; }
  .thumb { width: 40px; height: 40px; border-radius: 4px; background: center / cover no-repeat; flex: none; --mdc-icon-size: 20px; }
  .row .text { flex: 1; min-width: 0; padding: 0; display: block; font-weight: normal; }
  .row .text:hover { background: none; }
  .t, .s { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .s { color: var(--secondary-text-color); font-size: 0.85em; }
  .trail { display: flex; align-items: center; flex: none; }
  .dur { color: var(--secondary-text-color); font-size: 0.85em; font-variant-numeric: tabular-nums; min-width: 42px; text-align: right; }
  .more { opacity: 0; }
  .row:hover .more:not(:disabled) { opacity: 1; }
  @media (hover: none) { .more { opacity: 1; } }
  .handle { width: 36px; height: 36px; display: inline-flex; align-items: center; justify-content: center; cursor: grab;
            touch-action: none; color: var(--secondary-text-color); }
  .message { padding: 24px 16px; text-align: center; color: var(--secondary-text-color); display: flex; flex-direction: column;
             align-items: center; gap: 6px; --mdc-icon-size: 36px; }
  .message .hint { font-size: 0.85em; }
  .message.warning { color: var(--error-color, #db4437); padding: 8px 16px; }
  .spin { animation: spin 1s linear infinite; color: var(--secondary-text-color); }
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes pulse { 50% { opacity: 0.4; } }
`;

if (!customElements.get("linn-playlist-card")) {
  customElements.define("linn-playlist-card", LinnPlaylistCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "linn-playlist-card",
    name: "Linn Playlist",
    description: "Zeigt und bearbeitet die Playlist des Linn (DLNA Musikauswahl).",
    preview: false,
    documentationURL: "https://github.com/flaxg73/dlna-MusikAuswahl",
  });
  console.info(`%c LINN-PLAYLIST-CARD %c ${CARD_VERSION} `, "background:#000;color:#fff", "background:#ddd;color:#000");
}

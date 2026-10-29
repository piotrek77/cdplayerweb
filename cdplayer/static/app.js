"use strict";

const $ = (id) => document.getElementById(id);
const audio = $("audio");

const DRIVE_STATUS = {
  no_disc: "Brak płyty w napędzie",
  tray_open: "Szuflada otwarta",
  not_ready: "Napęd się przygotowuje…",
  no_audio: "To nie jest płyta audio",
  error: "Nie można otworzyć napędu",
};
const LOOKUP_STATUS = {
  searching: "Szukam opisu płyty w MusicBrainz…",
  not_found: "Płyty nie ma w MusicBrainz – możesz uzupełnić opis",
  offline: "MusicBrainz niedostępny – możesz uzupełnić opis",
};

let state = null;
let tracksSignature = "";
// Currently loaded track. For streams straight from the CD the browser only
// knows the time since `offset`, so the real position is offset + currentTime.
let current = null; // { discId, number, duration, fromCd, offset }
let seeking = false;

function fmt(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  const m = Math.floor(seconds / 60);
  return `${m}:${String(seconds % 60).padStart(2, "0")}`;
}

function disc() { return state && state.disc; }
function track(number) { return disc() && disc().tracks.find((t) => t.number === number); }

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Błąd ${res.status}`);
  return data;
}

function showMessage(text) {
  $("message").textContent = text || "";
  $("message").hidden = !text;
}

// -- playback ---------------------------------------------------------------

function play(number, offset = 0) {
  const d = disc();
  const t = track(number);
  if (!d || !t) return;
  const fromCd = !t.ripped;
  current = { discId: d.disc_id, number, duration: t.duration, fromCd, offset: fromCd ? offset : 0 };
  let src = `/api/track/${encodeURIComponent(d.disc_id)}/${number}`;
  if (fromCd && offset > 0) src += `?t=${offset.toFixed(1)}`;
  audio.src = src;
  if (!fromCd && offset > 0) {
    audio.addEventListener("loadedmetadata", () => { audio.currentTime = offset; }, { once: true });
  }
  audio.play().catch((e) => { if (e.name !== "AbortError") showMessage(`Nie można odtworzyć: ${e.message}`); });
  updateMediaSession();
  renderNowPlaying();
}

function stop() {
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  current = null;
  renderNowPlaying();
}

function position() {
  if (!current) return 0;
  return current.offset + (audio.currentTime || 0);
}

function step(delta) {
  const d = disc();
  if (!d) return;
  const numbers = d.tracks.map((t) => t.number);
  if (!current) return play(numbers[0]);
  if (delta < 0 && position() > 3) return play(current.number);
  const i = numbers.indexOf(current.number) + delta;
  if (i >= 0 && i < numbers.length) play(numbers[i]);
  else if (delta > 0) stop();
}

function togglePlay() {
  if (!disc()) return;
  if (!current) return play(disc().tracks[0].number);
  if (audio.paused) {
    // A paused CD stream may have been dropped by the server; resume from the position.
    if (current.fromCd && audio.readyState === 0) play(current.number, position());
    else audio.play();
  } else {
    audio.pause();
  }
}

function seekTo(seconds) {
  if (!current) return;
  if (current.fromCd) play(current.number, seconds);
  else audio.currentTime = seconds;
}

audio.addEventListener("ended", () => step(1));
audio.addEventListener("play", renderNowPlaying);
audio.addEventListener("pause", renderNowPlaying);
audio.addEventListener("waiting", renderNowPlaying);
audio.addEventListener("playing", renderNowPlaying);
audio.addEventListener("timeupdate", renderTime);
audio.addEventListener("error", () => {
  if (current && audio.getAttribute("src")) showMessage("Błąd odtwarzania utworu.");
});

$("play").onclick = togglePlay;
$("stop").onclick = stop;
$("prev").onclick = () => step(-1);
$("next").onclick = () => step(1);
$("volume").oninput = (e) => { audio.volume = e.target.value; };
$("seek").addEventListener("input", (e) => { seeking = true; $("pos").textContent = fmt(e.target.value); });
$("seek").addEventListener("change", (e) => { seeking = false; seekTo(Number(e.target.value)); });
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !e.target.closest("input, button, dialog")) {
    e.preventDefault();
    togglePlay();
  }
});

function updateMediaSession() {
  if (!("mediaSession" in navigator) || !current) return;
  const d = disc();
  const t = track(current.number);
  navigator.mediaSession.metadata = new MediaMetadata({
    title: t ? t.title : "",
    artist: (t && t.artist) || (d ? d.artist : ""),
    album: d ? d.album : "",
    artwork: d && d.has_cover ? [{ src: `/api/cover/${d.disc_id}`, type: "image/jpeg" }] : [],
  });
  navigator.mediaSession.setActionHandler("previoustrack", () => step(-1));
  navigator.mediaSession.setActionHandler("nexttrack", () => step(1));
}

// -- rendering --------------------------------------------------------------

function renderTime() {
  const duration = current ? current.duration : 0;
  const seek = $("seek");
  seek.max = duration || 1;
  seek.disabled = !current;
  if (!seeking) {
    seek.value = position();
    $("pos").textContent = fmt(position());
  }
  $("dur").textContent = fmt(duration);
}

function renderNowPlaying() {
  const t = current && track(current.number);
  const playing = current && !audio.paused;
  $("play").textContent = playing ? "⏸" : "▶";
  $("play").title = playing ? "Pauza" : "Odtwarzaj";
  $("art").classList.toggle("playing", Boolean(playing));
  $("now").textContent = t
    ? `${t.number}. ${t.title} · ${current.fromCd ? "z płyty" : "z dysku"}`
    : " ";
  document.querySelectorAll("#tracks li[data-number]").forEach((li) => {
    li.classList.toggle("current", Boolean(current && Number(li.dataset.number) === current.number));
  });
  document.title = t ? `${t.title} – ${disc().artist}` : "Odtwarzacz CD";
  renderTime();
}

function renderTracks(d) {
  const signature = JSON.stringify(d ? [d.disc_id, d.tracks] : null);
  if (signature === tracksSignature) return;
  tracksSignature = signature;
  const list = $("tracks");
  list.replaceChildren();
  if (!d) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "Włóż płytę CD do napędu.";
    list.append(li);
    return;
  }
  for (const t of d.tracks) {
    const li = document.createElement("li");
    li.dataset.number = t.number;
    li.title = "Odtwórz";
    const num = Object.assign(document.createElement("span"), { className: "num", textContent: t.number });
    const title = Object.assign(document.createElement("span"), { className: "title", textContent: t.title });
    if (t.artist) title.append(Object.assign(document.createElement("small"), { textContent: t.artist }));
    const time = Object.assign(document.createElement("span"), { className: "time", textContent: fmt(t.duration) });
    const src = Object.assign(document.createElement("span"), {
      className: "src",
      textContent: t.ripped ? "💾" : "💿",
      title: t.ripped ? "Zgrany na dysk" : "Odtwarzany z płyty",
    });
    li.append(num, title, time, src);
    li.onclick = () => play(t.number);
    list.append(li);
  }
}

function render() {
  const d = disc();
  const rip = state.rip && d && state.rip.disc_id === d.disc_id ? state.rip : null;
  const ripping = Boolean(rip && rip.state === "running");

  if (d) {
    const ripped = d.ripped_count === d.tracks.length;
    const parts = [`${d.tracks.length} utworów · ${fmt(d.duration)}`];
    if (ripped) parts.push("zgrana na dysk");
    else if (d.ripped_count) parts.push(`zgrano ${d.ripped_count} z ${d.tracks.length}`);
    $("status").textContent = LOOKUP_STATUS[d.lookup] || parts.join(" · ");
    $("album").textContent = d.year ? `${d.album} (${d.year})` : d.album;
    $("artist").textContent = d.artist;
  } else {
    $("status").textContent = DRIVE_STATUS[state.drive] || "Brak płyty";
    $("album").textContent = "—";
    $("artist").textContent = "";
  }

  const cover = $("cover");
  const coverSrc = d && d.has_cover ? `/api/cover/${encodeURIComponent(d.disc_id)}` : "";
  if (cover.getAttribute("src") !== coverSrc) {
    if (coverSrc) cover.src = coverSrc; else cover.removeAttribute("src");
  }
  cover.hidden = !coverSrc;
  $("disc").hidden = Boolean(coverSrc);

  const allRipped = d && d.ripped_count === d.tracks.length;
  $("rip").hidden = ripping;
  $("rip").disabled = !d || allRipped || d.lookup === "searching";
  $("rip").textContent = allRipped
    ? "Płyta zgrana na dysk ✓"
    : d && d.ripped_count ? "Dokończ zgrywanie płyty" : "Zgraj całą płytę na dysk (MP3)";
  $("cancel-rip").hidden = !ripping;
  $("edit").disabled = !d || d.ripped_count > 0 || ripping;
  $("edit").title = d && d.ripped_count ? "Płyta jest zgrana – opis jest zapisany w plikach" : "";
  $("refresh").disabled = !d || d.ripped_count > 0 || ripping || d.lookup === "searching";
  $("eject").disabled = ripping || state.drive === "tray_open";

  $("rip-progress").hidden = !rip;
  if (rip) {
    $("rip-bar").style.width = `${rip.percent.toFixed(1)}%`;
    const text = {
      running: `Zgrywanie${rip.track ? ` utworu ${rip.track}` : ""} · ${Math.floor(rip.percent)}%`,
      done: "Zgrywanie zakończone",
      cancelled: "Zgrywanie przerwane",
      error: `Błąd: ${rip.error || "nieznany"}`,
    }[rip.state];
    $("rip-text").textContent = text;
  }

  // Disc changed or removed: a CD stream cannot continue, files can.
  if (current && (!d || d.disc_id !== current.discId)) {
    if (current.fromCd || !d) stop();
  }
  renderTracks(d);
  renderNowPlaying();
}

async function poll() {
  try {
    state = await api("GET", "/api/state");
    render();
  } catch (e) {
    $("status").textContent = "Brak połączenia z serwerem";
  }
  setTimeout(poll, 2000);
}

// -- actions ----------------------------------------------------------------

async function action(fn) {
  showMessage("");
  try {
    await fn();
  } catch (e) {
    showMessage(e.message);
  }
  state = await api("GET", "/api/state").catch(() => state);
  if (state) render();
}

$("rip").onclick = () => action(() => api("POST", "/api/rip"));
$("cancel-rip").onclick = () => action(() => api("POST", "/api/rip/cancel"));
$("refresh").onclick = () => action(() => api("POST", "/api/metadata/refresh"));
$("eject").onclick = () => action(async () => {
  if (current && current.fromCd) stop();
  await api("POST", "/api/eject");
});

$("edit").onclick = () => {
  const d = disc();
  if (!d) return;
  const form = $("editor-form");
  form.artist.value = d.artist;
  form.album.value = d.album;
  form.year.value = d.year;
  const box = $("editor-tracks");
  box.replaceChildren();
  for (const t of d.tracks) {
    const label = document.createElement("label");
    label.textContent = `Utwór ${t.number}`;
    const input = Object.assign(document.createElement("input"), { value: t.title, required: true });
    input.dataset.number = t.number;
    label.append(input);
    box.append(label);
  }
  $("editor").returnValue = "";
  $("editor").showModal();
};

$("editor-cancel").onclick = () => $("editor").close("cancel");

$("editor").addEventListener("close", () => {
  if ($("editor").returnValue !== "save") return;
  const form = $("editor-form");
  const tracks = [...$("editor-tracks").querySelectorAll("input")].map((i) => ({
    number: Number(i.dataset.number),
    title: i.value,
  }));
  action(() => api("PUT", "/api/metadata", {
    artist: form.artist.value, album: form.album.value, year: form.year.value, tracks,
  }));
});

poll();

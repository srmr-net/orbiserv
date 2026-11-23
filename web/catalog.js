const byId = (id) => document.getElementById(id);
const group = byId("group");
const search = byId("search");
const groupFeed = byId("group-feed");
const form = byId("feed-form");
const start = byId("start");
const duration = byId("duration");
const feedPage = byId("feed-page");
const feedUrl = byId("url");
const copy = byId("copy");
const download = byId("download");
const feedback = byId("feedback");

const rowsPerPage = 15;
let groups = [];
let satellites = [];
let selection;
let page = 1;
let loadVersion = 0;

async function getJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`Request failed (HTTP ${response.status}).`);
  return response.json();
}

function renderCatalog() {
  const query = search.value.trim().toLowerCase();
  const matches = satellites.filter(({ name, noradId }) =>
    name.toLowerCase().includes(query) || String(noradId).includes(query)
  );
  const pages = Math.max(1, Math.ceil(matches.length / rowsPerPage));
  page = Math.min(page, pages);
  byId("count").textContent = matches.length
    ? `${matches.length.toLocaleString()} ${matches.length === 1 ? "satellite" : "satellites"}`
    : "No satellites match your search.";
  byId("results").hidden = !matches.length;
  byId("pagination").hidden = pages === 1;
  byId("previous").disabled = page === 1;
  byId("next").disabled = page === pages;
  byId("page-label").textContent = `Page ${page} of ${pages}`;

  const rows = matches.slice((page - 1) * rowsPerPage, page * rowsPerPage).map((satellite) => {
    const row = document.createElement("tr");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = satellite.name;
    const selected = selection?.orbitUrl === satellite.orbitUrl;
    button.setAttribute("aria-pressed", selected);
    row.classList.toggle("selected", selected);
    button.addEventListener("click", () => {
      chooseFeed(satellite);
      for (const old of byId("satellites").querySelectorAll('[aria-pressed="true"]')) {
        old.setAttribute("aria-pressed", "false");
        old.closest("tr").classList.remove("selected");
      }
      button.setAttribute("aria-pressed", "true");
      row.classList.add("selected");
      start.focus();
    });
    row.insertCell().append(button);
    row.insertCell().textContent = satellite.noradId;
    row.insertCell().textContent = satellite.elementEpoch.slice(0, 16).replace("T", " ");
    return row;
  });
  byId("satellites").replaceChildren(...rows);
}

async function loadCatalog() {
  const version = ++loadVersion;
  const current = groups.find(({ id }) => id === group.value);
  groupFeed.hidden = true;
  byId("results").hidden = true;
  byId("pagination").hidden = true;
  byId("count").textContent = "Loading catalog…";
  page = 1;
  try {
    const data = await getJson(current?.membersUrl ?? "/satellites");
    if (version !== loadVersion) return;
    satellites = data;
    groupFeed.hidden = !current;
    search.disabled = false;
    renderCatalog();
  } catch (error) {
    if (version !== loadVersion) return;
    satellites = [];
    search.disabled = true;
    byId("count").textContent = `Could not load the catalog. ${error.message}`;
  }
}

function chooseFeed(item) {
  selection = item;
  const isGroup = Boolean(item.pageSize);
  byId("selection").textContent = isGroup ? item.id : `${item.name} · NORAD ${item.noradId}`;
  byId("feed-page-label").hidden = !isGroup || item.count <= item.pageSize;
  const pages = isGroup ? Math.ceil(item.count / item.pageSize) : 1;
  feedPage.replaceChildren(...Array.from({ length: pages }, (_, index) =>
    new Option(String(index + 1), String(index + 1))
  ));
  form.hidden = false;
  updateFeed();
}

function updateFeed() {
  if (!selection) return;
  feedback.textContent = "";
  const valid = duration.validity.valid && start.validity.valid;
  copy.disabled = !valid;
  download.setAttribute("aria-disabled", String(!valid));
  download.tabIndex = valid ? 0 : -1;
  if (!valid) {
    feedUrl.value = "";
    download.removeAttribute("href");
    return;
  }
  const url = new URL(selection.orbitUrl, window.location.origin);
  if (start.value) url.searchParams.set("start", new Date(`${start.value}Z`).toISOString());
  const minutes = duration.valueAsNumber;
  if (minutes !== 120) url.searchParams.set("duration", `${minutes}m`);
  if (feedPage.value !== "1") url.searchParams.set("page", feedPage.value);
  feedUrl.value = url.href;
  download.href = url.href;
  download.download = selection.pageSize ? `${selection.id}.czml` : `norad-${selection.noradId}.czml`;
}

group.addEventListener("change", loadCatalog);
search.addEventListener("input", () => { page = 1; renderCatalog(); });
byId("previous").addEventListener("click", () => { page -= 1; renderCatalog(); });
byId("next").addEventListener("click", () => { page += 1; renderCatalog(); });
groupFeed.addEventListener("click", () => {
  const current = groups.find(({ id }) => id === group.value);
  if (current) {
    chooseFeed(current);
    renderCatalog();
    start.focus();
  }
});
form.addEventListener("submit", (event) => event.preventDefault());
form.addEventListener("input", updateFeed);
copy.addEventListener("click", async () => {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(feedUrl.value);
    } else {
      feedUrl.focus();
      feedUrl.select();
      if (!document.execCommand("copy")) throw new Error("Clipboard unavailable");
      copy.focus();
    }
    feedback.textContent = "URL copied.";
  } catch {
    feedUrl.focus();
    feedUrl.select();
    feedback.textContent = "Press Ctrl+C or Cmd+C to copy the selected URL.";
  }
});

async function initialize() {
  group.disabled = true;
  search.disabled = true;
  try {
    const data = await getJson("/ui/catalog-config");
    groups = data.groups;
    const fixture = data.fixture;
    group.append(...groups.map(({ id }) => new Option(id, id)));
    group.disabled = !groups.length;
    byId("source").hidden = !fixture;
    byId("source").textContent = "Historical fixture data";
    byId("start-hint").textContent = fixture
      ? "Leave blank to start at the fixture’s element epoch."
      : "Leave blank to start when the feed is requested.";
    await loadCatalog();
  } catch (error) {
    byId("count").textContent = `Could not load the catalog. ${error.message}`;
  }
}

initialize();

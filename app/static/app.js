// Theme switching without a reload. The server renders data-theme from the same cookie, so there's no flash on load.
(() => {
  const NEXT = { auto: "light", light: "dark", dark: "auto" };
  const label = (t) => t[0].toUpperCase() + t.slice(1);
  const apply = (theme) => {
    document.documentElement.dataset.theme = theme;
    document.cookie = `rememberry_theme=${theme}; path=/; max-age=${60 * 60 * 24 * 365 * 5}; samesite=lax`;
    document.querySelectorAll("form[data-theme-toggle]").forEach((form) => {
      const next = NEXT[theme];
      form.elements.theme.value = next;
      const btn = form.querySelector("button");
      btn.title = `Theme: ${label(theme)} (click for ${label(next)})`;
      btn.setAttribute("aria-label", `Theme: ${label(theme)}. Switch to ${label(next)}`);
    });
    document.querySelectorAll("form[data-theme-picker] input[name=theme]").forEach((r) => { r.checked = r.value === theme; });
  };
  document.querySelectorAll("form[data-theme-toggle]").forEach((form) => form.addEventListener("submit", (e) => {
    e.preventDefault();
    apply(form.elements.theme.value);
  }));
  document.querySelectorAll("form[data-theme-picker] input[name=theme]").forEach((r) => r.addEventListener("change", () => apply(r.value)));
})();

// @mention autocomplete for textareas marked with data-mentions.
(() => {
  let people = null;
  const loadPeople = async () => {
    if (!people) people = await fetch("/api/people").then((r) => (r.ok ? r.json() : [])).catch(() => []);
    return people;
  };

  function attach(textarea) {
    const wrap = document.createElement("div");
    wrap.className = "mention-wrap";
    textarea.replaceWith(wrap);
    wrap.append(textarea);
    const menu = document.createElement("ul");
    menu.className = "mention-menu";
    menu.hidden = true;
    wrap.append(menu);
    let matches = [], active = 0, start = -1;

    const close = () => { menu.hidden = true; matches = []; };
    const pick = (person) => {
      const v = textarea.value, caret = textarea.selectionStart;
      const insert = "@" + person.name + " ";
      textarea.value = v.slice(0, start) + insert + v.slice(caret);
      textarea.selectionStart = textarea.selectionEnd = start + insert.length;
      textarea.focus();
      close();
    };
    const draw = () => {
      menu.replaceChildren(...matches.map((p, i) => {
        const li = document.createElement("li");
        li.textContent = p.name;
        li.className = i === active ? "active" : "";
        li.addEventListener("mousedown", (e) => { e.preventDefault(); pick(p); });
        return li;
      }));
      menu.hidden = !matches.length;
    };

    textarea.addEventListener("input", async () => {
      const before = textarea.value.slice(0, textarea.selectionStart);
      const m = before.match(/(^|[^\w@])@([\w'-]*(?: [\w'-]*)?)$/);
      if (!m) return close();
      start = before.length - m[2].length - 1;
      const q = m[2].toLowerCase();
      const all = await loadPeople();
      matches = all.filter((p) => p.name.toLowerCase().startsWith(q) ||
        p.name.toLowerCase().split(/\s+/).some((w) => w.startsWith(q))).slice(0, 6);
      active = 0;
      draw();
    });
    textarea.addEventListener("keydown", (e) => {
      if (menu.hidden) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        active = (active + (e.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length;
        draw();
      } else if (e.key === "Enter" || e.key === "Tab") {
        pick(matches[active]);
      } else if (e.key === "Escape") {
        close();
      } else return;
      e.preventDefault();
    });
    textarea.addEventListener("blur", () => setTimeout(close, 150));
  }

  document.querySelectorAll("textarea[data-mentions]").forEach(attach);
})();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

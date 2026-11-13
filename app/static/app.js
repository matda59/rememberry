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

// Microphone dictation for journal textareas. Recognition stays in the browser.
(() => {
  const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
  const supported = typeof Ctor === "function" && window.isSecureContext;
  const LANG_NOTE = "Follows your browser's language.";
  const MIC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M6 11a6 6 0 0 0 12 0M12 17v4M8 21h8"/></svg>';

  const squash = (s) => (s || "").replace(/\s+/g, " ").trim();
  const joinParts = (parts) => parts.map(squash).filter(Boolean).join(" ");
  const joinEdge = (pre, mid, suf) => {
    let out = pre;
    if (mid) {
      if (out && !/\s$/.test(out)) out += " ";
      out += mid.replace(/^\s+/, "");
    }
    if (suf) {
      if (out && !/\s$/.test(out) && !/^\s/.test(suf)) out += " ";
      out += suf;
    }
    return out;
  };

  let activeStop = null;

  document.querySelectorAll("textarea[data-dictate]").forEach((textarea, index) => {
    const wrap = textarea.closest(".mention-wrap") || textarea.parentElement;
    const bar = document.createElement("div");
    bar.className = "dictate-bar";
    const note = document.createElement("p");
    note.className = "dictate-note muted small";
    note.id = `dictate-note-${index}`;

    if (!supported) {
      note.textContent = "Dictation needs Chrome or Edge over HTTPS.";
      bar.append(note);
      textarea.before(bar);
      return;
    }

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dictate";
    btn.setAttribute("aria-pressed", "false");
    btn.setAttribute("aria-label", "Start dictation");
    btn.setAttribute("aria-describedby", note.id);
    const dot = document.createElement("span");
    dot.className = "dictate-dot";
    dot.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.textContent = "Dictate";
    btn.innerHTML = MIC;
    btn.prepend(dot);
    btn.append(label);
    note.textContent = LANG_NOTE;
    note.setAttribute("aria-live", "polite");
    bar.append(btn, note);
    textarea.before(bar);

    const recognition = new Ctor();
    recognition.continuous = true;
    recognition.interimResults = true;
    if (navigator.language) recognition.lang = navigator.language;

    let want = false;
    let running = false;
    let restarts = 0;
    let prefix = "";
    let suffix = "";
    let priorFinals = "";
    let sessionFinals = "";
    let interim = "";
    let problem = "";
    let insertAt = null;

    const applySpoken = (spoken) => {
      const text = spoken ? joinEdge(prefix, spoken, suffix) : prefix + suffix;
      textarea.value = text;
      const caret = spoken ? joinEdge(prefix, spoken, "").length : prefix.length;
      try { textarea.setSelectionRange(caret, caret); } catch (err) { /* detached */ }
    };
    const render = () => applySpoken(joinParts([priorFinals, sessionFinals, interim]));
    const setListening = (on) => {
      btn.classList.toggle("is-recording", on);
      wrap.classList.toggle("is-listening", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.setAttribute("aria-label", on ? "Stop dictation" : "Start dictation");
      label.textContent = on ? "Stop" : "Dictate";
      if (on) note.textContent = "Listening…";
      else if (!problem) note.textContent = LANG_NOTE;
    };
    const finish = () => {
      want = false;
      running = false;
      interim = "";
      priorFinals = joinParts([priorFinals, sessionFinals]);
      sessionFinals = "";
      render();
      if (activeStop === stop) activeStop = null;
      setListening(false);
    };
    function stop() {
      want = false;
      if (running) recognition.stop();
      else finish();
    }

    recognition.onstart = () => { running = true; };
    recognition.onresult = (event) => {
      let finals = "";
      interim = "";
      for (let i = 0; i < event.results.length; i++) {
        const piece = event.results[i][0].transcript;
        if (event.results[i].isFinal) finals += piece;
        else interim += piece;
      }
      sessionFinals = finals;
      render();
    };
    recognition.onerror = (event) => {
      if (event.error === "no-speech" || event.error === "aborted") return;
      want = false;
      problem = event.error === "not-allowed" ? "Allow the microphone to dictate." : "Dictation stopped.";
      note.textContent = problem;
    };
    recognition.onend = () => {
      running = false;
      priorFinals = joinParts([priorFinals, sessionFinals]);
      sessionFinals = "";
      interim = "";
      render();
      if (want && restarts < 50) {
        restarts += 1;
        window.setTimeout(() => {
          if (!want) return;
          try { recognition.start(); }
          catch (err) { finish(); }
        }, 120);
        return;
      }
      finish();
    };

    const start = () => {
      if (activeStop && activeStop !== stop) activeStop();
      activeStop = stop;
      const at = insertAt || { start: textarea.value.length, end: textarea.value.length };
      insertAt = null;
      prefix = textarea.value.slice(0, at.start);
      suffix = textarea.value.slice(at.end);
      priorFinals = "";
      sessionFinals = "";
      interim = "";
      restarts = 0;
      problem = "";
      want = true;
      setListening(true);
      try { recognition.start(); }
      catch (err) {
        want = false;
        activeStop = null;
        problem = "Couldn't start dictation.";
        setListening(false);
        note.textContent = problem;
      }
    };

    btn.addEventListener("pointerdown", () => {
      const here = document.activeElement === textarea;
      insertAt = {
        start: here ? textarea.selectionStart : textarea.value.length,
        end: here ? textarea.selectionEnd : textarea.value.length,
      };
    });
    btn.addEventListener("click", () => { if (want) stop(); else start(); });
  });
})();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

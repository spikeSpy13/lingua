(() => {
  "use strict";
  const form = document.querySelector("[data-narrative-form]");
  if (!form) return;
  const tabs = Array.from(form.querySelectorAll("[data-narrative-tab]"));
  const panels = Array.from(form.querySelectorAll("[data-narrative-panel]"));
  const activeInput = form.querySelector("[data-active-narrative-tab]");
  const progress = form.querySelector("[data-narrative-progress]");

  function activateTab(id, { updateUrl = true, focus = false } = {}) {
    const selected = tabs.find((tab) => tab.dataset.narrativeTab === String(id));
    if (!selected) return;
    tabs.forEach((tab) => {
      const active = tab === selected;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    panels.forEach((panel) => { panel.hidden = panel.dataset.narrativePanel !== String(id); });
    activeInput.value = String(id);
    progress.textContent = `Movimento ${id} de 5 · ${selected.querySelector("span").textContent.split(" · ").slice(1).join(" · ")}`;
    if (promptPreview && promptPreview.dataset.promptMovement !== String(id)) invalidatePromptPreview();
    if (updateUrl) {
      const url = new URL(window.location.href);
      url.searchParams.set("aba", id);
      window.history.replaceState(window.history.state, "", url);
    }
    if (focus) selected.focus();
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", (event) => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      activateTab(tab.dataset.narrativeTab);
    });
    tab.addEventListener("keydown", (event) => {
      const targets = {
        ArrowRight: (index + 1) % tabs.length,
        ArrowLeft: (index - 1 + tabs.length) % tabs.length,
        Home: 0,
        End: tabs.length - 1,
      };
      if (event.key === " " || event.key === "Enter") {
        event.preventDefault();
        activateTab(tab.dataset.narrativeTab);
      } else if (Object.hasOwn(targets, event.key)) {
        event.preventDefault();
        activateTab(tabs[targets[event.key]].dataset.narrativeTab, { focus: true });
      }
    });
  });
  window.addEventListener("popstate", () => {
    activateTab(new URL(window.location.href).searchParams.get("aba") || "1", { updateUrl: false });
  });

  const provider = form.querySelector("#provedor");
  const model = form.querySelector("#modelo");
  const catalog = JSON.parse(form.querySelector("#narrative-model-catalog").textContent);
  provider.addEventListener("change", () => {
    const previous = model.value;
    const choices = catalog[provider.value] || [];
    model.replaceChildren(...choices.map((id) => {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = id === "openrouter/free" ? `${id} · gratuito` : id;
      return option;
    }));
    if (choices.includes(previous)) model.value = previous;
  });

  form.querySelectorAll("[data-composition-enabled]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      checkbox.closest("[data-composition-control]").classList.toggle("is-active", checkbox.checked);
    });
  });

  const promptPreview = form.querySelector("[data-prompt-preview]");
  function invalidatePromptPreview() {
    if (!promptPreview) return;
    promptPreview.querySelector("[data-prompt-stale]").hidden = false;
    promptPreview.querySelector("[data-prompt-current-note]").hidden = true;
    promptPreview.querySelector("#prompt-completo").hidden = true;
    promptPreview.querySelector("[data-copy-prompt]").hidden = true;
    promptPreview.querySelector("[data-copy-prompt-status]").textContent = "";
  }
  form.addEventListener("input", invalidatePromptPreview);
  form.addEventListener("change", invalidatePromptPreview);
  if (promptPreview) {
    promptPreview.focus({ preventScroll: true });
    promptPreview.scrollIntoView({ block: "start" });
  }
  form.querySelector("[data-copy-prompt]")?.addEventListener("click", async () => {
    const prompt = document.getElementById("prompt-completo");
    const status = form.querySelector("[data-copy-prompt-status]");
    try {
      await navigator.clipboard.writeText(prompt.textContent);
      status.textContent = "Prompt copiado.";
    } catch {
      const range = document.createRange();
      range.selectNodeContents(prompt);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      status.textContent = "Prompt selecionado. Use Ctrl+C ou ⌘C para copiar.";
    }
  });

  let submitting = false;
  let dirty = false;
  form.addEventListener("input", () => { dirty = true; });
  window.addEventListener("beforeunload", (event) => {
    if (dirty && !submitting) { event.preventDefault(); event.returnValue = ""; }
  });
  form.addEventListener("submit", (event) => {
    if (submitting) { event.preventDefault(); return; }
    submitting = true;
    form.setAttribute("aria-busy", "true");
    const status = form.querySelector("[data-submitting-status]");
    status.textContent = event.submitter?.hasAttribute("data-generates")
      ? "Solicitando somente este parágrafo à API. Aguarde…"
      : event.submitter?.value.startsWith("visualizar") ? "Preparando o prompt…" : "Salvando…";
    // Preserva o botão e todos os campos na submissão; não desabilita inputs.
  });
  form.querySelector("[data-copy-narrative]")?.addEventListener("click", async () => {
    const textarea = document.getElementById("texto-final");
    const status = form.querySelector("[data-copy-status]");
    try {
      await navigator.clipboard.writeText(textarea.value);
      status.textContent = "Narrativa copiada.";
    } catch {
      textarea.focus(); textarea.select();
      status.textContent = "Texto selecionado. Use Ctrl+C ou ⌘C para copiar.";
    }
  });
})();

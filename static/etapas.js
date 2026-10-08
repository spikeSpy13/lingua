(() => {
  "use strict";

  const workflow = document.querySelector("[data-workflow]");
  if (!workflow) return;

  const tabs = Array.from(workflow.querySelectorAll("[data-workflow-tab-id]"));
  const panels = Array.from(workflow.querySelectorAll("[data-workflow-panel]"));
  const progress = workflow.querySelector("[data-workflow-progress]");
  const availableTabs = tabs.filter((tab) => tab.getAttribute("aria-disabled") !== "true");

  function activate(id, { focusPanel = false, updateUrl = true } = {}) {
    const selected = availableTabs.find((tab) => tab.dataset.workflowTabId === id);
    if (!selected) return false;

    tabs.forEach((tab) => {
      const active = tab === selected;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    panels.forEach((panel) => {
      panel.hidden = panel.dataset.workflowPanel !== id;
    });
    if (progress) {
      progress.textContent = `Passo ${selected.dataset.workflowNumber} de ${tabs.length} · ${selected.dataset.workflowLabel}`;
    }
    if (updateUrl) {
      const url = new URL(window.location.href);
      url.searchParams.set("aba", id);
      window.history.replaceState(window.history.state, "", url);
    }

    selected.scrollIntoView({ block: "nearest", inline: "nearest" });
    const panel = panels.find((item) => item.dataset.workflowPanel === id);
    if (focusPanel && panel) panel.focus({ preventScroll: true });
    return true;
  }

  workflow.addEventListener("click", (event) => {
    const link = event.target.closest("[data-workflow-tab-id], [data-workflow-step]");
    if (!link || !workflow.contains(link)) return;
    if (link.getAttribute("aria-disabled") === "true") {
      event.preventDefault();
      return;
    }
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const id = link.dataset.workflowTabId || link.dataset.workflowStep;
    if (activate(id, { focusPanel: Boolean(link.dataset.workflowStep) })) {
      event.preventDefault();
    }
  });

  workflow.addEventListener("keydown", (event) => {
    const tab = event.target.closest("[data-workflow-tab-id]");
    const currentIndex = availableTabs.indexOf(tab);
    if (currentIndex < 0) return;

    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate(tab.dataset.workflowTabId);
      return;
    }

    let nextIndex;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % availableTabs.length;
    else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + availableTabs.length) % availableTabs.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = availableTabs.length - 1;
    else return;

    event.preventDefault();
    const next = availableTabs[nextIndex];
    activate(next.dataset.workflowTabId);
    next.focus({ preventScroll: true });
  });

  window.addEventListener("popstate", () => {
    const id = new URL(window.location.href).searchParams.get("aba");
    if (id) activate(id, { updateUrl: false });
  });
})();

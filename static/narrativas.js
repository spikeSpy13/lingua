(() => {
  "use strict";
  const form = document.querySelector("[data-narrative-form]");
  if (!form) return;
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
      ? "Solicitando somente este campo à API. Aguarde…" : "Salvando…";
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

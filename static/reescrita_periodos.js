(() => {
  "use strict";

  document.querySelectorAll("[data-period-rewrite-form]").forEach((form) => {
    const button = form.querySelector('button[type="submit"]');
    const status = form.querySelector("[data-period-rewrite-status]");
    const result = form.querySelector("[data-period-rewrite-result]");
    let generating = false;

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (generating) return;
      generating = true;
      button.disabled = true;
      form.setAttribute("aria-busy", "true");
      status.classList.remove("is-error");
      status.textContent = "Reescrevendo o período…";

      try {
        const fields = new FormData(form);
        const response = await fetch(form.action, {
          method: "POST",
          headers: { "Content-Type": "application/json", "Accept": "application/json" },
          body: JSON.stringify({
            segmentacao_id: fields.get("segmentacao_id"),
            periodo_id: fields.get("periodo_id"),
            prompt: fields.get("prompt"),
          }),
        });
        const payload = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(typeof payload?.erro === "string"
            ? payload.erro : "Não foi possível reescrever o período. Tente novamente.");
        }
        if (typeof payload?.texto !== "string" || !payload.texto.trim()) {
          throw new Error("A geração retornou um resultado inválido. Tente novamente.");
        }
        result.value = payload.texto;
        status.textContent = "Reescrita concluída.";
      } catch (error) {
        status.classList.add("is-error");
        status.textContent = error instanceof TypeError
          ? "Não foi possível conectar ao servidor. Tente novamente."
          : error.message;
      } finally {
        generating = false;
        button.disabled = false;
        form.removeAttribute("aria-busy");
      }
    });
  });
})();

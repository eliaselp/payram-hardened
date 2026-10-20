(function () {
  "use strict";

  // Copiar al portapapeles.
  document.addEventListener("click", async function (event) {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    event.preventDefault();
    const selector = button.getAttribute("data-copy");
    let value = button.getAttribute("data-copy-value") || "";
    if (!value && selector) {
      const target = document.querySelector(selector);
      if (target) value = target.value || target.textContent || "";
    }
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value.trim());
    } catch (_error) {
      const input = document.querySelector(selector);
      if (input && input.select) {
        input.select();
        document.execCommand("copy");
      }
    }
    const original = button.textContent;
    button.textContent = "Copiado";
    button.classList.add("copied");
    window.setTimeout(function () {
      button.textContent = original;
      button.classList.remove("copied");
    }, 1800);
  });

  // Editor de líneas de factura.
  const addItem = document.getElementById("add-item");
  const itemsBody = document.getElementById("items-body");
  const itemTemplate = document.getElementById("item-template");

  function refreshRemoveButtons() {
    if (!itemsBody) return;
    const rows = itemsBody.querySelectorAll("tr");
    rows.forEach(function (row) {
      const button = row.querySelector(".item-remove");
      if (button) button.disabled = rows.length <= 1;
    });
  }

  if (addItem && itemsBody && itemTemplate) {
    addItem.addEventListener("click", function () {
      const index = Number(itemsBody.getAttribute("data-next-index") || itemsBody.children.length);
      if (itemsBody.children.length >= 50) return;
      const html = itemTemplate.innerHTML.replace(/__INDEX__/g, String(index));
      const wrapper = document.createElement("tbody");
      wrapper.innerHTML = html.trim();
      const row = wrapper.firstElementChild;
      if (row) {
        itemsBody.appendChild(row);
        itemsBody.setAttribute("data-next-index", String(index + 1));
        const firstInput = row.querySelector("input");
        if (firstInput) firstInput.focus();
      }
      refreshRemoveButtons();
    });

    itemsBody.addEventListener("click", function (event) {
      const button = event.target.closest(".item-remove");
      if (!button) return;
      event.preventDefault();
      if (itemsBody.children.length <= 1) return;
      button.closest("tr").remove();
      refreshRemoveButtons();
    });

    refreshRemoveButtons();
  }

  // Confirmación para acciones destructivas.
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.getAttribute("data-confirm"))) {
        event.preventDefault();
      }
    });
  });

  // Polling del estado de la factura pública.
  const statusRoot = document.querySelector("[data-status-url]");
  if (statusRoot) {
    const statusUrl = statusRoot.getAttribute("data-status-url");
    let attempts = 0;
    const timer = window.setInterval(async function () {
      attempts += 1;
      if (attempts > 80) {
        window.clearInterval(timer);
        return;
      }
      try {
        const response = await fetch(statusUrl, {
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!response.ok) return;
        const data = await response.json();
        const badge = document.querySelector("[data-status-badge]");
        if (badge && data.status !== badge.getAttribute("data-status")) {
          window.location.reload();
          return;
        }
        const remaining = document.querySelector("[data-remaining]");
        if (remaining && typeof data.remaining_cents === "number") {
          remaining.textContent = (data.remaining_cents / 100).toLocaleString("en-US", {
            style: "currency",
            currency: "USD",
          });
        }
      } catch (_error) {
        // Silencioso: el polling es una mejora, no debe romper la página.
      }
    }, 15000);
  }
})();

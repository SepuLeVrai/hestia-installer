"use strict";

const form = document.getElementById("bootstrap-form");
const codeInput = document.getElementById("bootstrap-code");
const message = document.getElementById("bootstrap-message");
const submitButton = form.querySelector("button[type=submit]");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.textContent = "";
  const code = codeInput.value.trim();
  if (!code) {
    message.textContent = "Saisissez le code affiché dans le terminal.";
    codeInput.focus();
    return;
  }

  submitButton.disabled = true;
  try {
    const response = await fetch("/api/bootstrap/unlock", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({code})
    });
    codeInput.value = "";
    if (!response.ok) {
      message.textContent = response.status === 429
        ? "Trop de tentatives. Redémarrez le bootstrap pour générer un nouveau code."
        : "Code incorrect ou expiré.";
      codeInput.focus();
      return;
    }
    window.location.replace("/");
  } catch (_error) {
    codeInput.value = "";
    message.textContent = "Connexion au bootstrap indisponible.";
  } finally {
    submitButton.disabled = false;
  }
});

"use strict";

const steps = [
  {
    name: "Bienvenue",
    eyebrow: "Installation clé en main",
    title: "Bienvenue dans l'installation<br>de HESTIA",
    lead: "Cet assistant va vous guider pas à pas pour installer et configurer votre environnement HESTIA."
  },
  {
    name: "Préflight",
    eyebrow: "Préflight machine",
    title: "Préparons un environnement<br>propre et fiable",
    lead: "HESTIA vérifiera les prérequis essentiels avant toute modification : système, services, réseau et espace disponible."
  },
  {
    name: "Modules",
    eyebrow: "Composition HESTIA",
    title: "Choisissez les composants<br>à installer",
    lead: "Web, Gateway et Mobile pourront être combinés selon votre besoin, avec les options avancées repliées par défaut."
  },
  {
    name: "Plan",
    eyebrow: "Validation avant action",
    title: "Relisez le plan avant<br>de l'appliquer",
    lead: "L'installateur présentera les changements prévus et les contrôles de sécurité avant la première mutation système."
  },
  {
    name: "Installation",
    eyebrow: "Prêt à commencer",
    title: "Tout est prêt pour<br>installer HESTIA",
    lead: "Une fois lancé, le chantier restera traçable, reprenable et validé étape par étape jusqu'au rapport final."
  }
];

const content = document.getElementById("wizard-content");
const title = document.getElementById("wizard-title");
const lead = document.getElementById("wizard-lead");
const eyebrow = document.getElementById("wizard-eyebrow");
const stepLabel = document.getElementById("step-label");
const stepName = document.getElementById("step-name");
const progressTrack = document.getElementById("progress-track");
const progressDots = Array.from(document.querySelectorAll(".progress-dot"));
const previousButton = document.getElementById("previous-button");
const nextButton = document.getElementById("next-button");
const cancelButton = document.getElementById("cancel-button");
const cancelDialog = document.getElementById("cancel-dialog");

let currentStep = 0;
let transitionTimer = null;

function renderStep(index, { animate = true } = {}) {
  const safeIndex = Math.min(Math.max(Number(index) || 0, 0), steps.length - 1);
  const step = steps[safeIndex];

  if (transitionTimer !== null) {
    window.clearTimeout(transitionTimer);
  }

  const applyContent = () => {
    currentStep = safeIndex;
    eyebrow.textContent = step.eyebrow;
    title.innerHTML = step.title;
    lead.textContent = step.lead;
    stepLabel.textContent = `Étape ${currentStep + 1} sur ${steps.length}`;
    stepName.textContent = step.name;
    progressTrack.setAttribute("aria-valuenow", String(currentStep + 1));

    progressDots.forEach((dot, dotIndex) => {
      dot.classList.toggle("is-active", dotIndex === currentStep);
      dot.classList.toggle("is-complete", dotIndex < currentStep);
      dot.setAttribute("aria-current", dotIndex === currentStep ? "step" : "false");
    });

    previousButton.disabled = currentStep === 0;
    nextButton.innerHTML = currentStep === steps.length - 1
      ? "Commencer <span aria-hidden=\"true\">›</span>"
      : "Suivant <span aria-hidden=\"true\">›</span>";

    content.classList.remove("is-changing");
  };

  if (!animate || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    applyContent();
    return;
  }

  content.classList.add("is-changing");
  transitionTimer = window.setTimeout(applyContent, 120);
}

previousButton.addEventListener("click", () => {
  renderStep(currentStep - 1);
});

nextButton.addEventListener("click", () => {
  if (currentStep < steps.length - 1) {
    renderStep(currentStep + 1);
  }
});

progressDots.forEach((dot) => {
  dot.addEventListener("click", () => {
    renderStep(dot.dataset.step);
  });
});

cancelButton.addEventListener("click", () => {
  if (typeof cancelDialog.showModal === "function") {
    cancelDialog.showModal();
  }
});

cancelDialog.addEventListener("close", () => {
  if (cancelDialog.returnValue === "reset") {
    renderStep(0, { animate: false });
  }
});

renderStep(0, { animate: false });

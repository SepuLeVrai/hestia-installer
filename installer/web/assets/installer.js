"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const names = {web: "HESTIA Web", gateway: "Mobile Gateway", apk: "Application Android"};
  const repositories = {web: "SepuLeVrai/hestia-nexus-avv", gateway: "SepuLeVrai/hestia-mobile-gateway", apk: "SepuLeVrai/hestia-apk"};
  const states = {PLANNED: "Planifié", RUNNING: "En cours", DONE: "Validé", FAILED: "Échec", ROLLED_BACK: "Annulé", MANUAL_ACTION_REQUIRED: "Action manuelle requise"};
  const checks = {UNCHECKED: "À vérifier", ACCESSIBLE: "Accessible", DENIED: "Refusé", UNAVAILABLE: "Indisponible"};
  const errors = {
    BUSY: "Une autre action ou un autre onglet utilise le chantier. Actualisez son état avant de réessayer.",
    SECRET_REQUIRED: "Le jeton GitHub doit être saisi à nouveau avant un nouvel accès aux sources.",
    SECRET_REJECTED: "Une valeur sensible ou invalide a été refusée. Vérifiez les champs.",
    GITHUB_ACCESS_DENIED: "Accès refusé. Vérifiez les trois dépôts et les permissions Metadata et Contents en lecture.",
    GITHUB_RATE_LIMITED: "La limite de requêtes GitHub est atteinte. Réessayez après sa réinitialisation.",
    GITHUB_UNAVAILABLE: "GitHub est indisponible. Vérifiez la connexion sortante du serveur.",
    GITHUB_INVALID_RESPONSE: "La réponse GitHub n'a pas pu être validée.",
    GITHUB_REDIRECT_REJECTED: "La redirection de téléchargement a été refusée pour des raisons de sécurité.",
    SOURCE_DRIFT: "Des sources ont changé ou disparu. Aucune recréation automatique : vérification manuelle nécessaire.",
    SOURCE_LIMIT: "Les sources dépassent les limites autorisées.",
    SOURCE_LAYOUT_UNSUPPORTED: "La structure des sources n'est pas prise en charge.",
    ARCHIVE_REJECTED: "L'archive a été refusée par les contrôles de sécurité.",
    PLAN_EXISTS: "Un plan est déjà figé. Consultez-le avant de modifier les choix.",
    NOT_PLANNED: "Aucun plan n'est enregistré.",
    VALIDATION_FAILED: "Les contrôles ne sont pas tous validés. Consultez les résultats avant de poursuivre.",
    INVALID_DATA: "Les données saisies sont invalides. Vérifiez la sélection et les références GitHub.",
    INVALID_STATE: "Le journal n'est pas valide. Ne le supprimez pas : une vérification est nécessaire.",
    UNSAFE_STATE_PATH: "Le chemin ou les permissions du journal ont été refusés.",
    INCOMPATIBLE_STATE: "Ce journal n'est pas compatible avec les opérations disponibles.",
    CONFIRMATION_REQUIRED: "Une confirmation explicite du plan affiché est nécessaire.",
    DEPENDENCY_BLOCKED: "Une dépendance empêche cette action. Consultez le plan.",
    TARGETED_RETRY_REQUIRED: "Utilisez la reprise ciblée de l'étape concernée.",
    MANUAL_ACTION_REQUIRED: "Une action manuelle est nécessaire. Consultez le rapport avant toute modification.",
    OPERATION_FAILED: "L'opération a échoué. Son état est conservé pour une reprise ciblée.",
    ROLLBACK_FAILED: "Le retour arrière n'a pas pu être confirmé. Consultez le rapport.",
    ROLLBACK_UNSUPPORTED: "Cette frontière ne permet pas de retour arrière automatique.",
    SHUTTING_DOWN: "Le serveur est en cours d'arrêt. Reprenez depuis le terminal.",
    NETWORK: "Connexion interrompue. Une action serveur peut continuer : actualisez l'état, sans relancer automatiquement.",
    SESSION: "La session a expiré. Déverrouillez de nouveau le bootstrap."
  };
  const steps = [
    ["Bienvenue", "Installation clé en main", "Bienvenue dans l'installation\nde HESTIA", "Cet assistant va vous guider pas à pas pour installer et configurer votre environnement HESTIA."],
    ["Accès GitHub", "Accès aux sources", "Autorisez l'accès aux\nsources HESTIA", "Validez la lecture des trois dépôts. Aucune archive n'est téléchargée à cette étape."],
    ["Préflight", "Préflight machine", "Vérifions les prérequis\nde votre serveur", "Ces contrôles sont non destructifs. Un prérequis manquant bloque la suite."],
    ["Modules", "Composition HESTIA", "Choisissez les sources\nà préparer", "Seuls les composants sélectionnés seront acquis. Leur installation applicative sera disponible dans les phases suivantes."],
    ["Plan", "Validation avant action", "Relisez le plan\navant de l'appliquer", "Les commits sont figés. Vérifiez les chemins et les possibilités de retour arrière avant de confirmer."],
    ["Acquisition", "Suivi du chantier", "Préparation des sources\nHESTIA", "Le serveur conserve l'avancement. Une fermeture ou un rafraîchissement du navigateur ne relance aucune étape validée."]
  ];
  let csrf = "", current = 0, initialized = false, busy = false, serverBusy = false;
  let installation = null, preflight = null, github = {ready: false, repositories: [], checks: []};
  let draft = {revision: 0, step: 0, modules: ["web"], refs: {}, mode: "fresh"};
  let saveChain = Promise.resolve(), draftConflict = false, polling = false, confirmed = false;
  let pendingAction = null, lastRevision = "", pollCount = 0;
  const content = $("wizard-form");

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function message(text = "") { $("wizard-message").textContent = text; }
  function errorMessage(error) { return errors[error.code] || "Action indisponible. Consultez l'état du chantier avant de réessayer."; }
  function button(label, handler, id, primary = false) {
    const b = element("button", label, "action-button " + (primary ? "action-primary" : "action-secondary"));
    b.type = "button";
    if (id) b.id = id;
    b.disabled = busy || serverBusy;
    b.addEventListener("click", handler);
    return b;
  }
  function hint(text) { return element("p", text, "wizard-hint"); }
  function technical(parent, title, value) {
    const details = element("details");
    details.append(element("summary", title), element("pre", JSON.stringify(value, null, 2)));
    parent.append(details);
  }
  function badge(text, status) { const b = element("span", text, "wizard-badge"); b.dataset.status = status; return b; }
  function field(label, input) { const wrapper = element("label", label); wrapper.append(input); return wrapper; }
  function selection() { return {modules: [...draft.modules], mode: draft.mode, refs: {...draft.refs}}; }
  function confirmation() { return {confirm: true, confirmation: installation.plan_sha256}; }
  function failed(code) { const err = new Error("HESTIA request failed"); err.code = code; return err; }

  async function api(path, payload) {
    const options = {method: payload === undefined ? "GET" : "POST", cache: "no-store", credentials: "same-origin"};
    if (payload !== undefined) {
      options.headers = {"Content-Type": "application/json", "X-Hestia-CSRF": csrf};
      options.body = JSON.stringify(payload);
    }
    let response;
    try { response = await fetch(path, options); } catch (_) { throw failed("NETWORK"); }
    if (response.status === 401) {
      csrf = "";
      $("github-credential")?.setAttribute("disabled", "");
      if ($("github-credential")) $("github-credential").value = "";
      window.location.replace("/bootstrap");
      throw failed("SESSION");
    }
    if (response.status === 204) return {};
    let value;
    try { value = await response.json(); } catch (_) { throw failed("NETWORK"); }
    if (!response.ok) throw failed(Object.hasOwn(errors, value.error) ? value.error : "UNAVAILABLE");
    return value;
  }

  function allowed(index) {
    if (installation) return [1, 4, 5].includes(index);
    if (index <= 1) return true;
    if (index === 2) return github.ready === true;
    return index === 3 && github.ready === true && preflight?.ok === true;
  }
  function controls() {
    const disabled = !initialized || busy || serverBusy || draftConflict;
    $("previous-button").disabled = disabled || current === 0 || (installation && current === 1);
    $("cancel-button").disabled = !initialized || busy || serverBusy;
    let nextAllowed = current === 0 || current === 5;
    if (current === 1) nextAllowed = github.ready === true || installation?.state === "DONE";
    if (current === 2) nextAllowed = preflight?.ok === true && github.ready === true;
    if (current === 3) nextAllowed = draft.modules.length > 0 && github.ready === true && preflight?.ok === true;
    if (current === 4) nextAllowed = Boolean(installation) && (installation.approved_plan_sha256 !== null || confirmed);
    $("next-button").disabled = disabled || !nextAllowed;
    $("next-button-label").textContent = current === 1 && installation ? "Retour au chantier" : current === 3 ? "Préparer le plan" : current === 4 ? (installation?.approved_plan_sha256 ? "Voir le suivi" : "Acquérir les sources") : current === 5 ? "Actualiser" : "Suivant";
    $("previous-button").textContent = current === 4 && installation?.approved_plan_sha256 === null ? "Modifier le plan" : "Précédent";
    for (const dot of document.querySelectorAll(".progress-dot")) {
      const i = Number(dot.dataset.step);
      dot.disabled = disabled || !allowed(i) || (!installation && i > current);
      dot.classList.toggle("is-active", i === current);
      dot.classList.toggle("is-complete", i < current);
      dot.setAttribute("aria-current", i === current ? "step" : "false");
    }
    for (const b of content.querySelectorAll("button")) b.disabled = busy || serverBusy || draftConflict;
    const retry = $("reload-state");
    if (retry) retry.disabled = busy;
    content.setAttribute("aria-busy", busy || serverBusy ? "true" : "false");
  }

  function saveDraft() {
    const snapshot = {step: Math.min(current, 3), ...selection()};
    saveChain = saveChain.catch(() => {}).then(async () => {
      if (draftConflict || installation) return;
      try {
        const result = await api("/api/wizard/draft", {revision: draft.revision, ...snapshot});
        draft.revision = result.draft.revision;
        draft.step = snapshot.step;
      } catch (error) {
        draftConflict = true;
        message(errorMessage(error));
        controls();
        throw error;
      }
    });
    return saveChain;
  }
  async function run(work, status) {
    if (busy || serverBusy) return;
    busy = true;
    if (status) message(status);
    controls();
    try { await work(); }
    catch (error) {
      message(errorMessage(error));
      if (error.code === "SECRET_REQUIRED") github.ready = false;
    } finally {
      busy = false;
      if (current === 5) show(5, false);
      controls();
    }
  }

  function githubForm() {
    const form = element("form");
    form.autocomplete = "off";
    const input = element("input");
    input.id = "github-credential"; input.type = "password"; input.autocomplete = "off";
    input.required = true; input.maxLength = 255; input.spellcheck = false;
    input.setAttribute("autocapitalize", "none"); input.setAttribute("aria-describedby", "credential-hint");
    const note = hint("Fine-grained PAT : Metadata Read et Contents Read, limité aux trois dépôts HESTIA. Aucun mot de passe GitHub. Le jeton n'est jamais enregistré dans le navigateur.");
    note.id = "credential-hint";
    form.append(field("Jeton GitHub en lecture seule", input), note);
    const submit = button("Valider l'accès", () => {}, "validate-github", true); submit.type = "submit";
    form.append(submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!input.reportValidity() || busy || serverBusy) return;
      const body = {credential: input.value};
      input.value = "";
      void run(async () => {
        try {
          github = (await api("/api/github/validate", body)).github;
          message("Lecture des trois dépôts validée. Aucune archive téléchargée.");
        } finally {
          body.credential = "";
          try { github = (await api("/api/github/status")).github; } catch (_) { github.ready = false; }
          renderGitHubRows();
        }
      }, "Vérification des accès GitHub...");
    });
    content.append(form, element("div", null, "wizard-repositories"));
    content.lastChild.id = "github-repositories";
    renderGitHubRows();
    const controlsRow = element("div", null, "wizard-controls");
    controlsRow.append(button("Effacer le jeton", () => void run(async () => {
      github = (await api("/api/github/clear", {})).github;
      input.value = ""; renderGitHubRows(); message("Le jeton a été effacé du serveur.");
    }), "clear-github"));
    content.append(controlsRow);
    if (installation) content.append(hint("Le plan déjà enregistré reste inchangé. Revalider un jeton ne modifie pas ses commits."));
  }
  function renderGitHubRows() {
    const target = $("github-repositories");
    if (!target) return;
    target.replaceChildren();
    for (const module of Object.keys(names)) {
      const status = github.checks?.find((c) => c.module === module)?.status || (github.ready ? "ACCESSIBLE" : "UNCHECKED");
      const row = element("div", null, "wizard-row");
      row.append(element("span", repositories[module]), badge(checks[status] || "À vérifier", status));
      target.append(row);
    }
  }
  function preflightForm() {
    content.append(button("Vérifier les prérequis", () => void run(async () => {
      preflight = (await api("/api/preflight/run", {})).preflight;
      renderPreflight(); message(preflight.ok ? "Tous les prérequis sont validés." : errors.VALIDATION_FAILED);
    }, "Vérification du serveur..."), "run-preflight", true));
    const rows = element("div"); rows.id = "preflight-results"; content.append(rows); renderPreflight();
  }
  function renderPreflight() {
    const rows = $("preflight-results");
    if (!rows) return;
    rows.replaceChildren();
    if (!preflight) { rows.append(hint("Aucun contrôle effectué dans cette session.")); return; }
    for (const c of preflight.checks) {
      const row = element("div", null, "wizard-row");
      row.append(element("span", c.label), badge(c.ok ? "Validé" : "Manquant", c.ok ? "PASS" : "FAIL"));
      rows.append(row);
    }
  }
  function modulesForm() {
    const group = element("fieldset"); group.append(element("legend", "Composants à acquérir"));
    for (const module of Object.keys(names)) {
      const input = element("input"); input.type = "checkbox"; input.id = "module-" + module;
      input.checked = draft.modules.includes(module);
      const label = element("label", null, "wizard-choice"); label.append(input, element("span", names[module]));
      input.addEventListener("change", () => {
        draft.modules = Object.keys(names).filter((m) => $("module-" + m).checked);
        for (const m of Object.keys(names)) {
          if (!draft.modules.includes(m)) delete draft.refs[m];
          $("ref-field-" + m).hidden = !draft.modules.includes(m);
        }
        controls(); void saveDraft().catch(() => {});
      }); group.append(label);
    }
    content.append(group);
    const mode = element("select"); mode.id = "installation-mode";
    for (const [value, text] of [["fresh", "Nouvelle installation"], ["upgrade", "Préparation d'une mise à niveau"]]) {
      const option = element("option", text); option.value = value; mode.append(option);
    }
    mode.value = draft.mode; mode.addEventListener("change", () => { draft.mode = mode.value; void saveDraft().catch(() => {}); });
    content.append(field("Mode", mode), hint("À ce stade, le mode concerne le plan et l'acquisition. Aucune base, aucun service ni APK n'est installé ou modifié."));
    const advanced = element("details"); advanced.append(element("summary", "Références GitHub avancées"));
    for (const module of Object.keys(names)) {
      const input = element("input"); input.type = "text"; input.id = "ref-" + module; input.maxLength = 200;
      input.autocomplete = "off"; input.spellcheck = false; input.value = draft.refs[module] || "";
      input.placeholder = "Branche validée par défaut";
      const label = field(names[module], input); label.id = "ref-field-" + module; label.hidden = !draft.modules.includes(module);
      input.addEventListener("change", () => {
        if (input.value.trim()) draft.refs[module] = input.value.trim(); else delete draft.refs[module];
        void saveDraft().catch(() => {});
      }); advanced.append(label);
    }
    advanced.append(hint("Une branche, un tag ou un SHA. Les références choisies seront figées en commits dans le plan.")); content.append(advanced);
  }
  function planForm() {
    if (!installation) { content.append(hint("Aucun plan disponible.")); return; }
    content.append(hint("Identifiant : " + installation.installation_id));
    for (const spec of installation.plan.steps) {
      const card = element("article", null, "wizard-card"); card.append(element("h2", names[spec.module] || "Contrôle core"));
      card.append(element("p", spec.action));
      if (spec.source) {
        card.append(element("p", spec.source.repository + " / " + spec.source.ref));
        card.append(element("code", spec.source.commit_sha));
      }
      for (const resource of spec.resources) card.append(element("p", (resource.preexisting ? "Existant : " : "À créer : ") + resource.target));
      card.append(hint(spec.rollback_supported ? "Retour arrière limité aux ressources de cette frontière." : "Pas de retour arrière automatique."));
      for (const warning of spec.warnings) card.append(hint(warning));
      content.append(card);
    }
    technical(content, "Plan technique complet (non secret)", installation.plan);
    content.append(hint("SHA-256 du plan"), element("code", installation.plan_sha256));
    if (installation.approved_plan_sha256 === null) {
      const input = element("input"); input.type = "checkbox"; input.id = "confirm-plan"; input.checked = confirmed;
      const label = element("label", null, "wizard-choice");
      label.append(input, element("span", "J'ai vérifié ce plan et j'autorise uniquement l'acquisition des sources indiquées."));
      input.addEventListener("change", () => { confirmed = input.checked; controls(); }); content.append(label);
    } else content.append(hint("Ce plan a déjà été approuvé. Sa consultation ne rejoue aucune étape."));
  }
  function executionForm() {
    if (!installation) { content.append(hint("Aucun chantier enregistré.")); return; }
    const completed = installation.steps.filter((s) => s.state === "DONE").length;
    const bar = element("progress"); bar.max = installation.steps.length || 1; bar.value = completed;
    bar.setAttribute("aria-label", "Étapes validées"); content.append(bar);
    const title = installation.state === "DONE" ? (installation.mode === "check" ? "Contrôles core terminés" : "Sources prêtes") : states[installation.state] || "État inconnu";
    const summary = element("p", title + " - " + completed + " / " + installation.steps.length); summary.id = "execution-state";
    summary.dataset.state = installation.state; content.append(summary);
    if (installation.state === "DONE") content.append(hint("HESTIA n'est pas encore déployé. L'installation Web, Gateway et APK appartient aux phases suivantes."));
    if (serverBusy || busy) content.append(hint("Une opération serveur est active. Vous pouvez fermer cette page : elle continue et son état reste consultable."));
    if (installation.last_error_redacted) content.append(hint(errorMessage({code: installation.last_error_redacted})));
    for (const record of installation.steps) {
      const spec = installation.plan.steps.find((s) => s.name === record.name);
      const card = element("article", null, "wizard-card");
      card.append(element("h3", names[spec.module] || spec.name), badge(states[record.state] || record.state, record.state));
      card.append(hint("Phase : " + record.phase + " - Tentatives : " + record.attempts));
      if (record.last_error_redacted) card.append(hint(errorMessage({code: record.last_error_redacted})));
      const row = element("div", null, "wizard-controls");
      if (["FAILED", "MANUAL_ACTION_REQUIRED", "ROLLED_BACK"].includes(record.state)) {
        row.append(button("Réessayer cette étape", () => confirmAction("retry", {name: record.name}, "Réessayer uniquement " + (names[spec.module] || spec.name) + " ? Les étapes validées ne sont pas rejouées."), "retry-" + record.name));
      }
      if (spec.rollback_supported && !["PLANNED", "ROLLED_BACK"].includes(record.state)) {
        row.append(button("Annuler cette frontière", () => confirmAction("rollback", {boundary: spec.boundary}, "Supprimer uniquement les ressources créées par " + (names[spec.module] || spec.name) + " ? Une ressource préexistante ou modifiée ne sera pas supprimée aveuglément."), "rollback-" + record.name));
      }
      card.append(row); content.append(card);
    }
    const row = element("div", null, "wizard-controls");
    if (installation.steps.some((s) => s.state === "RUNNING") || (installation.approved_plan_sha256 !== null && installation.steps.some((s) => s.state === "PLANNED"))) {
      row.append(button("Reprendre le chantier", () => confirmAction("resume", {}, "Reprendre les étapes restantes du plan enregistré, sans rejouer les étapes validées ?"), "resume-installation", true));
    }
    if (installation.state !== "DONE") row.append(button("Ressaisir le jeton", () => show(1), "renew-credential"));
    row.append(button("Télécharger le rapport", () => void run(downloadReport), "download-report"));
    content.append(row); technical(content, "Journal technique (non secret)", installation);
  }
  async function downloadReport() {
    const data = await api("/api/installation/report");
    const blob = new Blob([JSON.stringify(data, null, 2) + "\n"], {type: "application/json"});
    const url = URL.createObjectURL(blob); const a = element("a");
    a.href = url; a.download = "HESTIA-ACQUISITION-REPORT.json"; a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function show(index, focus = true) {
    if (!allowed(index)) return;
    if ($("github-credential")) $("github-credential").value = "";
    current = index; document.body.dataset.wizardStep = String(index);
    const step = steps[index];
    $("wizard-eyebrow").textContent = step[1]; $("wizard-title").textContent = step[2]; $("wizard-lead").textContent = step[3];
    $("step-label").textContent = index === 0 ? "Préambule" : `Étape ${index} sur 5`;
    $("step-name").textContent = step[0]; $("welcome-features").hidden = index !== 0;
    content.replaceChildren();
    [() => {}, githubForm, preflightForm, modulesForm, planForm, executionForm][index]();
    const utilities = element("div", null, "wizard-controls");
    utilities.append(button("Actualiser l'état", () => void run(() => reload(true)), "reload-state"));
    if (index !== 0) content.append(utilities);
    controls();
    if (focus) { $("wizard-title").focus({preventScroll: true}); $("wizard-content").scrollTop = 0; }
  }
  function confirmAction(action, extra, text) {
    if (busy || serverBusy || !installation) return;
    pendingAction = {action, payload: {...confirmation(), ...extra}};
    $("operation-title").textContent = action === "reset-plan" ? "Modifier le plan ?" : "Confirmer l'action";
    $("operation-description").textContent = text;
    $("operation-dialog").returnValue = ""; $("operation-dialog").showModal();
  }
  $("operation-dialog").addEventListener("close", () => {
    const action = pendingAction; pendingAction = null;
    if ($("operation-dialog").returnValue !== "confirm" || !action) return;
    void run(async () => {
      if (action.action === "reset-plan") {
        const result = await api("/api/wizard/reset-plan", action.payload);
        installation = null; confirmed = false; draft = result.draft;
        show(github.ready && preflight?.ok ? 3 : 1); message("Plan non appliqué retiré. Les choix sont de nouveau modifiables.");
      } else {
        installation = (await api("/api/installation/" + action.action, action.payload)).installation;
        show(5); message(installation.last_error_redacted ? errorMessage({code: installation.last_error_redacted}) : "État du chantier mis à jour.");
      }
    }, "Action en cours...");
  });
  async function reload(navigate = false) {
    if (navigate) await saveChain.catch(() => {});
    const result = await api("/api/wizard/state");
    installation = result.installation; serverBusy = result.busy; preflight = result.preflight;
    if (navigate) { draft = result.draft; draftConflict = false; }
    if (!serverBusy) {
      try { github = (await api("/api/github/status")).github; } catch (error) { if (error.code !== "BUSY") throw error; }
    }
    if (navigate) {
      confirmed = false;
      show(installation ? (installation.approved_plan_sha256 === null ? 4 : 5) : Math.min(draft.step, github.ready ? (preflight?.ok ? 3 : 2) : 1), false);
      message(serverBusy ? "Une action serveur est en cours." : "État synchronisé avec le serveur.");
    }
    return result;
  }
  async function next() {
    if (current === 5) { await reload(true); return; }
    if (current === 1 && installation) { show(5); message(); return; }
    if (current === 3) {
      await saveDraft();
      if (draftConflict) return;
      installation = (await api("/api/wizard/plan", selection())).installation;
      confirmed = false; show(4); message("Plan enregistré. Aucune source n'a encore été téléchargée."); return;
    }
    if (current === 4) {
      if (installation.approved_plan_sha256 !== null) { show(5); return; }
      if (!confirmed) return;
      show(5); message("Acquisition en cours. Le serveur conserve son avancement.");
      installation = (await api("/api/installation/apply", confirmation())).installation;
      github.ready = false;
      show(5); message(installation.last_error_redacted ? errorMessage({code: installation.last_error_redacted}) : "Acquisition terminée et validée."); return;
    }
    const target = current + 1;
    if (!allowed(target)) return;
    show(target); message(); await saveDraft();
  }
  $("next-button").addEventListener("click", () => void run(next));
  $("previous-button").addEventListener("click", () => {
    if (current === 4 && installation?.approved_plan_sha256 === null) {
      confirmAction("reset-plan", {}, "Retirer ce plan jamais appliqué pour modifier la sélection ? Une nouvelle confirmation sera obligatoire."); return;
    }
    void run(async () => { show(installation ? 4 : current - 1); message(); if (!installation) await saveDraft(); });
  });
  for (const dot of document.querySelectorAll(".progress-dot")) dot.addEventListener("click", () => void run(async () => {
    const index = Number(dot.dataset.step);
    if (allowed(index)) { show(index); message(); if (!installation) await saveDraft(); }
  }));
  $("cancel-button").addEventListener("click", () => { $("cancel-dialog").returnValue = ""; $("cancel-dialog").showModal(); });
  $("cancel-dialog").addEventListener("close", () => {
    if ($("cancel-dialog").returnValue !== "quit") return;
    void run(async () => { await saveChain.catch(() => {}); await api("/api/logout", {}); csrf = ""; window.location.replace("/bootstrap"); });
  });
  window.addEventListener("pagehide", () => { if ($("github-credential")) $("github-credential").value = ""; });
  window.addEventListener("pageshow", (event) => { if (event.persisted) window.location.reload(); });

  async function poll() {
    if (!initialized || polling || document.hidden) return;
    polling = true;
    try {
      if (installation || serverBusy) {
        const result = await api("/api/wizard/state");
        serverBusy = result.busy;
        const stamp = result.installation ? result.installation.installation_id + ":" + result.installation.revision + ":" + serverBusy : "";
        if (stamp !== lastRevision) {
          lastRevision = stamp;
          installation = result.installation;
          if (current === 5) show(5, false);
        }
      } else if (!busy && current === 1 && ++pollCount % 4 === 0) {
        github = (await api("/api/github/status")).github; renderGitHubRows();
      }
    } catch (error) { if (error.code !== "BUSY") message(errorMessage(error)); }
    finally { polling = false; controls(); }
  }
  async function start() {
    try {
      csrf = (await api("/api/session")).csrf_token;
      await reload(false);
      const result = await api("/api/wizard/state"); draft = result.draft;
      initialized = true;
      show(installation ? (installation.approved_plan_sha256 === null ? 4 : 5) : Math.min(draft.step, github.ready ? (preflight?.ok ? 3 : 2) : 1), false);
    } catch (error) {
      message(errorMessage(error));
      content.replaceChildren(button("Réessayer la connexion", () => void start(), "retry-connection"));
    }
    controls();
  }
  void start();
  window.setInterval(poll, 1500);
})();

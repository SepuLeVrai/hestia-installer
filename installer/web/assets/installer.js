"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const names = {web: "HESTIA Web", gateway: "Mobile Gateway", apk: "Application Android"};
  const repositories = {web: "SepuLeVrai/hestia-nexus-avv", gateway: "SepuLeVrai/hestia-mobile-gateway", apk: "SepuLeVrai/hestia-apk"};
  const states = {PLANNED: "Planifié", RUNNING: "En cours", DONE: "Validé", FAILED: "Échec", ROLLED_BACK: "Annulé", MANUAL_ACTION_REQUIRED: "Action manuelle requise"};
  const checks = {UNCHECKED: "À vérifier", ACCESSIBLE: "Accessible", DENIED: "Refusé", UNAVAILABLE: "Indisponible"};
  const errors = {
    BUSY: "Une autre action ou un autre onglet utilise le chantier. Actualisez son état avant de réessayer.",
    SECRET_REQUIRED: "Un identifiant temporaire doit être saisi à nouveau. Consultez les champs requis dans le suivi du chantier.",
    SECRET_REJECTED: "Une valeur sensible ou invalide a été refusée. Vérifiez les champs.",
    GITHUB_ACCESS_DENIED: "Accès refusé. Vérifiez les trois dépôts et les permissions Metadata et Contents en lecture, ainsi que Actions en lecture pour le paquet Gateway.",
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
    ["Modules", "Composition HESTIA", "Choisissez votre parcours\nHESTIA", "Sélectionnez les sources à acquérir ou la préparation d'une nouvelle instance Web sur un serveur déjà prêt."],
    ["Plan", "Validation avant action", "Relisez le plan\navant de l'appliquer", "Les commits sont figés. Vérifiez les chemins et les possibilités de retour arrière avant de confirmer."],
    ["Acquisition", "Suivi du chantier", "Préparation des sources\nHESTIA", "Le serveur conserve l'avancement. Une fermeture ou un rafraîchissement du navigateur ne relance aucune étape validée."]
  ];
  let csrf = "", current = 0, initialized = false, busy = false, serverBusy = false;
  let installation = null, preflight = null, github = {ready: false, repositories: [], checks: []};
  let draft = {revision: 0, step: 0, modules: ["web"], refs: {}, mode: "fresh"};
  let saveChain = Promise.resolve(), draftConflict = false, polling = false, confirmed = false;
  let pendingAction = null, lastRevision = "", pollCount = 0;
  let application = {draft: null, missing_credentials: []}, useApplication = false, applicationDirty = false;
  let upgrade = {profile: null, missing_credentials: []}, useUpgrade = false;
  let boot = {installation: null, availability: null};
  let acmePackages = {profile: null, acquisition: null, installation: null, selection: null};
  let publicTLS = {installation: null, availability: null};
  let gateway = {preparation: null, profile: null};
  let foundation = {installation: null, profile: null, availability: null};
  let gatewayService = {installation: null, profile: null, availability: null};
  let mobileActivation = {state: "NOT_PLANNED", profile: null, steps: [], availability: null};
  let activation = {installation: null, availability: null};
  let packages = {profile: null, acquisition: null, installation: null, selection: null};
  let mariadb = {profile: null, installation: null, missing_credentials: []};
  const credentialLabels = {
    database_password: "Mot de passe du compte SQL applicatif", admin_password: "Mot de passe du premier administrateur",
    migration_user: "Compte SQL de préparation", migration_password: "Mot de passe SQL de préparation",
    authority_user: "Compte SQL d'autorité", authority_password: "Mot de passe SQL d'autorité",
    openai_api_key: "Clé API OpenAI (facultative)"
  };
  const content = $("wizard-form");
  function isUpgrade() { return installation?.plan.steps.some((s) => s.operation === "web.upgrade-workspace.create") === true; }
  function isApplication() { return isUpgrade() || installation?.plan.steps.some((s) => s.operation === "web.host-profile.check") === true; }
  function clearPasswords() { for (const input of content.querySelectorAll('input[type="password"]')) input.value = ""; }

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

  async function api(path, payload, binaryPlan) {
    const options = {method: payload === undefined ? "GET" : "POST", cache: "no-store", credentials: "same-origin"};
    if (payload !== undefined) {
      options.headers = {"Content-Type": binaryPlan ? "application/zip" : "application/json", "X-Hestia-CSRF": csrf};
      if (binaryPlan) options.headers["X-Hestia-Plan"] = binaryPlan;
      options.body = binaryPlan ? payload : JSON.stringify(payload);
    }
    let response;
    try { response = await fetch(path, options); } catch (_) { throw failed("NETWORK"); }
    if (response.status === 401) {
      csrf = "";
      clearPasswords();
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
    if (current === 3) nextAllowed = draft.modules.length > 0 && github.ready === true && preflight?.ok === true &&
      (!useApplication || (application.draft !== null && !applicationDirty)) && (!useUpgrade || upgrade.missing_credentials.length === 0) &&
      (!packages.profile || (packages.installation?.state === "DONE" && (!useApplication || mariadb.installation?.state === "DONE")));
    if (current === 4) nextAllowed = Boolean(installation) && (installation.approved_plan_sha256 !== null || confirmed);
    $("next-button").disabled = disabled || !nextAllowed;
    $("next-button-label").textContent = current === 1 && installation ? "Retour au chantier" : current === 3 ? "Préparer le plan" : current === 4 ? (installation?.approved_plan_sha256 ? "Voir le suivi" : "Acquérir les sources") : current === 5 ? "Actualiser" : "Suivant";
    if (current === 4 && isApplication() && !installation.approved_plan_sha256) $("next-button-label").textContent = isUpgrade() ? "Migrer HESTIA Web" : "Préparer HESTIA Web";
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
      if (error.code === "SECRET_REQUIRED" && !isApplication()) github.ready = false;
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
    const note = hint("Fine-grained PAT : Metadata Read et Contents Read, limité aux trois dépôts HESTIA. Pour le paquet Gateway : Actions Read également. Aucun mot de passe GitHub. Le jeton n'est jamais enregistré dans le navigateur.");
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
    packagesForm();
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
    packagesForm();
  }
  function packageAction(phase, action, extra = {}) {
    if (busy || serverBusy) return;
    const document = packages[phase === "acquire" ? "acquisition" : "installation"];
    pendingAction = {action: "packages." + phase + "/" + action, payload: {confirmation: document.plan_sha256, confirm: true, ...extra}};
    $("operation-title").textContent = phase === "acquire" ? "Confirmer le téléchargement" : "Confirmer l'installation des paquets";
    $("operation-description").textContent = phase === "acquire" ? "Télécharger les dépendances Debian officielles dans un cache privé ? Aucun paquet ne sera installé à cette étape." : "Installer les versions exactes du plan affiché ? Les services applicatifs par défaut resteront masqués. Aucun retour arrière ni réparation automatique des paquets n'est prévu.";
    $("operation-dialog").returnValue = ""; $("operation-dialog").showModal();
  }
  function packagesForm() {
    if (installation && !packages.profile) return;
    const card = element("details", null, "wizard-card"); card.id = "package-preparation"; card.open = packages.profile !== null;
    card.append(element("summary", "Préparer les dépendances d'un serveur vierge"),
      hint("Debian 13 sans Apache, FPM, nginx ni serveur SQL installé. Les paquets sont téléchargés puis installés après deux confirmations distinctes. Aucun jeton GitHub n'est nécessaire pour ces paquets Debian."));
    card.append(hint("Ce parcours prépare les dépendances. La configuration MariaDB, l'installation HESTIA, le boot et le frontal TLS public restent des étapes séparées."));
    if (!packages.profile) {
      const nginx = element("input"); nginx.type = "checkbox"; nginx.id = "packages-nginx";
      card.append(field("Inclure le paquet nginx pour le futur frontal", nginx), button("Préparer le plan de téléchargement", () => void run(async () => {
        packages = (await api("/api/system/packages/acquire/plan", {nginx: nginx.checked})).packages;
        show(current, false); message("Plan de téléchargement prêt à relire. Aucun paquet téléchargé ni installé.");
      }), "plan-packages-acquire", true));
    } else {
      card.append(hint("Paquet nginx : " + (packages.profile.nginx ? "inclus" : "non demandé") + ". Ce choix est figé pour ce chantier."));
      if (!installation && !packages.acquisition) card.append(button("Retrouver le plan de téléchargement", () => void run(async () => {
        packages = (await api("/api/system/packages/acquire/plan", {nginx: packages.profile.nginx})).packages;
        show(current, false);
      }), "plan-packages-acquire"));
      if (packages.selection) {
        const rows = packages.selection.packages;
        card.append(element("p", rows.length + " paquets, " + Math.ceil(rows.reduce((n, p) => n + p.bytes, 0) / 1048576) + " Mio d'archives. Installation depuis le cache, sans téléchargement."));
        const versions = element("details"); versions.id = "package-versions"; versions.append(element("summary", "Relire les versions exactes"));
        const list = element("ul");
        for (const row of rows) list.append(element("li", row.name + " — " + row.version + " (" + row.architecture + ")"));
        versions.append(list); card.append(versions);
      }
      for (const [phase, key, label] of [["acquire", "acquisition", "Téléchargement"], ["install", "installation", "Installation des paquets"]]) {
        const document = packages[key];
        if (!document) continue;
        const status = element("p", label + " : " + states[document.state]); status.id = "packages-" + phase + "-state"; status.dataset.state = document.state; card.append(status);
        technical(card, "Plan « " + label + " »", document.plan);
        if (document.state === "DONE") continue;
        if (installation) { card.append(hint("Un plan principal existe. Les actions paquets sont bloquées pour ce chantier.")); continue; }
        if (document.approved_plan_sha256 === null) card.append(button(phase === "acquire" ? "Valider et télécharger" : "Valider et installer les paquets", () => packageAction(phase, "apply"), "apply-packages-" + phase, true));
        else if (document.steps.some((s) => ["RUNNING", "PLANNED"].includes(s.state))) card.append(button("Reprendre « " + label + " »", () => packageAction(phase, "resume"), "resume-packages-" + phase));
        for (const record of document.steps.filter((s) => ["FAILED", "MANUAL_ACTION_REQUIRED"].includes(s.state))) {
          card.append(hint("Une interruption peut exiger une inspection manuelle. La reprise vérifie les reçus existants ; elle ne répare pas une installation partielle."),
            button("Vérifier la reprise de « " + label + " »", () => packageAction(phase, "retry", {name: record.name}), "retry-packages-" + phase));
        }
      }
      if (!installation && packages.acquisition?.state === "DONE" && !packages.installation) card.append(button("Préparer le plan d'installation des paquets", () => void run(async () => {
        packages = (await api("/api/system/packages/install/plan", {acquisition_sha256: packages.acquisition.plan_sha256})).packages;
        show(current, false); message("Versions exactes prêtes à relire. L'installation attend votre confirmation.");
      }), "plan-packages-install", true));
      if (packages.installation?.state === "DONE") card.append(hint("Installation des paquets validée. Les services applicatifs par défaut restent masqués. Poursuivez avec la préparation MariaDB ci-dessous."));
      card.append(button("Télécharger le rapport", () => void run(downloadReport), "download-packages-report"));
    }
    content.append(card);
    mariadbForm();
  }
  function mariadbAction(action, extra = {}) {
    if (busy || serverBusy) return;
    pendingAction = {action: "mariadb." + action, payload: {confirmation: mariadb.installation.plan_sha256, confirm: true, ...extra}};
    $("operation-title").textContent = "Confirmer la préparation MariaDB";
    $("operation-description").textContent = "Créer une instance MariaDB dédiée et son autorité SQL, puis l'écouter uniquement sur 127.0.0.1:3306 ? Une interruption partielle exige une inspection manuelle. Le démarrage au boot reste une étape ultérieure.";
    $("operation-dialog").returnValue = ""; $("operation-dialog").showModal();
  }
  function mariadbForm() {
    if (packages.installation?.state !== "DONE") return;
    const card = element("details", null, "wizard-card"); card.id = "mariadb-preparation"; card.open = true;
    card.append(element("summary", "Préparer MariaDB pour HESTIA"), hint("Instance dédiée, répertoire neuf et compte système verrouillé. Le service MariaDB Debian reste masqué. Aucun répertoire SQL existant n'est repris."));
    const document = mariadb.installation;
    if (!document && !installation) {
      card.append(button("Préparer le plan MariaDB", () => void run(async () => {
        mariadb = (await api("/api/system/mariadb/plan", {packages_sha256: packages.installation.plan_sha256})).mariadb;
        show(current, false); message("Plan MariaDB prêt à relire. Aucun compte ni service créé.");
      }), "plan-mariadb", true));
    } else if (document) {
      const status = element("p", "MariaDB : " + states[document.state]); status.id = "mariadb-state"; status.dataset.state = document.state; card.append(status);
      technical(card, "Plan MariaDB", document.plan);
      card.append(hint("Autorité SQL : " + mariadb.authority_user + ". Ce compte administre l'instance locale ; conservez son mot de passe pour la configuration HESTIA et les opérations futures."));
      if (document.state === "DONE") card.append(hint(publicTLS.phase5_complete ? "Préparation MariaDB validée. La configuration Web et HTTPS est également terminée." : "Préparation MariaDB validée. Vous pouvez poursuivre la configuration Web avec ce même mot de passe d'autorité. Le boot et le TLS public restent à préparer."));
      else if (!installation) {
        const form = element("form"); form.autocomplete = "off";
        const password = element("input"); password.type = "password"; password.id = "mariadb-authority-password"; password.required = true; password.minLength = 20; password.maxLength = 1024; password.autocomplete = "new-password";
        form.append(field("Nouveau mot de passe d'autorité SQL (20 caractères minimum)", password));
        const save = button("Garder le mot de passe pour cette session", () => {}, "save-mariadb-credentials"); save.type = "submit"; form.append(save);
        form.addEventListener("submit", (event) => {
          event.preventDefault(); if (busy || serverBusy || !form.reportValidity()) return;
          let secret = password.value; password.value = "";
          void run(async () => {
            try { mariadb = (await api("/api/system/mariadb/credentials", {confirmation: document.plan_sha256, authority_password: secret})).mariadb;
              show(current, false); message("Mot de passe gardé uniquement pour cette session. Confirmez le plan pour préparer MariaDB."); }
            finally { secret = ""; }
          });
        });
        card.append(form, hint(mariadb.missing_credentials.length ? "Mot de passe à renseigner avant la création du compte. Une reprise peut reconnaître une création déjà terminée sans le ressaisir." : "Mot de passe présent en mémoire pour cette session."));
        if (document.approved_plan_sha256 === null) card.append(button("Valider et préparer MariaDB", () => mariadbAction("apply"), "apply-mariadb", true));
        else if (document.steps.some((s) => ["RUNNING", "PLANNED"].includes(s.state))) card.append(button("Reprendre MariaDB", () => mariadbAction("resume"), "resume-mariadb"));
        for (const record of document.steps.filter((s) => ["FAILED", "MANUAL_ACTION_REQUIRED"].includes(s.state))) card.append(button("Vérifier la reprise MariaDB", () => mariadbAction("retry", {name: record.name}), "retry-mariadb"));
      }
    }
    content.append(card);
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
    if (packages.profile && packages.installation?.state !== "DONE") content.append(hint("Terminez le parcours des paquets dans l'étape Préflight avant de figer un plan HESTIA."));
    const group = element("fieldset"); group.append(element("legend", "Composants à acquérir"));
    for (const module of Object.keys(names)) {
      const input = element("input"); input.type = "checkbox"; input.id = "module-" + module;
      input.checked = draft.modules.includes(module);
      const label = element("label", null, "wizard-choice"); label.append(input, element("span", names[module]));
      input.addEventListener("change", () => {
        useApplication = false;
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
    mode.value = draft.mode; mode.addEventListener("change", () => { useApplication = false; draft.mode = mode.value; show(3, false); void saveDraft().catch(() => {}); });
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
    applicationForm(); upgradeForm();
    if (useApplication || useUpgrade) {
      mode.disabled = true;
      for (const module of Object.keys(names)) { $("module-" + module).disabled = true; $("ref-" + module).disabled = true; }
    }
  }
  function upgradeSummary() {
    const config = upgrade.profile.configuration;
    const card = element("article", null, "wizard-card"); card.id = "upgrade-profile-choices";
    card.append(element("h2", "Migration de l’instance gérée"), element("p", "DNS : " + config.web.hostname),
      element("p", "Base conservée : " + config.database.name + " / " + config.database.user),
      element("p", "Version source : " + upgrade.source_commit), element("p", "Version cible : " + upgrade.target_commit),
      hint("Administrateurs, assistant et données conservés. Arrêt sous maintenance et verrou SQL global de lecture pendant la sauvegarde vérifiée. Aucune migration SQL. Réouverture et démarrage sur confirmation séparée."));
    return card;
  }
  function upgradeForm() {
    if (!upgrade.profile) return;
    const group = element("fieldset"); group.append(element("legend", "Instance existante gérée"));
    const enable = element("input"); enable.type = "checkbox"; enable.id = "upgrade-web-application"; enable.checked = useUpgrade;
    const choice = element("label", null, "wizard-choice"); choice.append(enable, element("span", "Migrer l’instance HESTIA Web enregistrée")); group.append(choice); content.append(group);
    enable.addEventListener("change", () => {
      useUpgrade = enable.checked;
      if (useUpgrade) { useApplication = false; draft.modules = ["web"]; draft.mode = "upgrade"; draft.refs = {}; }
      show(3, false); void saveDraft().catch(() => {});
    });
    if (!useUpgrade) return;
    group.append(upgradeSummary());
    const form = element("form"); form.id = "upgrade-credentials"; form.autocomplete = "off";
    const inputs = {};
    for (const name of ["database_password", "authority_user", "authority_password"]) {
      const node = element("input"); node.id = "upgrade-" + name; node.type = "password"; node.autocomplete = "off";
      node.maxLength = 1024; node.required = true; node.spellcheck = false; inputs[name] = node; form.append(field(credentialLabels[name], node));
    }
    const submit = button("Enregistrer les identifiants de migration", () => {}, "save-upgrade-credentials", true); submit.type = "submit"; form.append(submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault(); if (busy || serverBusy) return;
      const credentials = Object.fromEntries(Object.entries(inputs).map(([name, node]) => [name, node.value])); clearPasswords();
      void run(async () => {
        try {
          await saveChain;
          upgrade = (await api("/api/web/upgrade/credentials", {confirmation: upgrade.profile_sha256, credentials})).upgrade;
          show(3, false); message("Identifiants conservés uniquement pour cette session. Préparez le plan à confirmer.");
        } finally { for (const name of Object.keys(credentials)) credentials[name] = ""; }
      });
    });
    group.append(form, hint(upgrade.missing_credentials.length ? "Identifiants à renseigner avant de préparer le plan." : "Identifiants disponibles pour cette session."));
  }
  function applicationForm() {
    const group = element("fieldset"); group.append(element("legend", "Préparation applicative Web"));
    const enable = element("input"); enable.type = "checkbox"; enable.id = "prepare-web-application"; enable.checked = useApplication;
    const choice = element("label", null, "wizard-choice"); choice.append(enable, element("span", "Préparer une nouvelle instance HESTIA Web"));
    group.append(choice, hint("Debian 13 avec Apache, PHP 8.4 et MariaDB déjà disponibles. Base et administrateur initial, stockages externes et services préparés sous maintenance. Le démarrage des services reste une étape ultérieure."));
    enable.addEventListener("change", () => {
      useApplication = enable.checked; if (useApplication) useUpgrade = false;
      if (useApplication) { draft.modules = ["web"]; draft.mode = "fresh"; draft.refs = {}; }
      show(3, false); void saveDraft().catch(() => {});
    });
    content.append(group);
    if (!useApplication) return;
    const saved = application.draft?.configuration;
    const form = element("form"); form.id = "application-form"; form.autocomplete = "off";
    const inputs = {};
    function input(name, label, value, type = "text", required = true, maximum = 100) {
      const node = element("input"); node.id = "application-" + name; node.type = type;
      node.value = value || ""; node.required = required; node.maxLength = maximum; node.autocomplete = "off";
      if (type === "password") { node.spellcheck = false; node.setAttribute("autocapitalize", "none"); }
      node.addEventListener("input", () => { applicationDirty = true; controls(); });
      inputs[name] = node; form.append(field(label, node)); return node;
    }
    input("hostname", "Nom DNS du Web", saved?.web.hostname || "", "text", true, 253);
    const mode = element("select"); mode.id = "application-database-mode";
    for (const [value, text] of [["managed", "Créer une base et ses comptes SQL"], ["existing_local", "Utiliser une base locale vide et ses comptes existants"]]) {
      const option = element("option", text); option.value = value; mode.append(option);
    }
    mode.value = saved?.database.mode || "managed";
    if (mariadb.profile) { mode.value = "managed"; mode.disabled = true; }
    mode.addEventListener("change", () => { applicationDirty = true; controls(); }); form.append(field("Base MariaDB locale (127.0.0.1:3306)", mode));
    input("database-name", "Nom de la base", saved?.database.name || "hestia", "text", true, 64);
    input("database-user", "Compte SQL applicatif", saved?.database.user || "hestia_app", "text", true, 32);
    input("first-name", "Prénom de l'administrateur", saved?.administrator.first_name);
    input("last-name", "Nom de l'administrateur", saved?.administrator.last_name);
    input("email", "E-mail de l'administrateur", saved?.administrator.email, "email", true, 254);
    const assistant = element("select"); assistant.id = "application-assistant";
    for (const [value, text] of [["disabled", "Assistant désactivé"], ["configure", "Configurer l'Assistant avec une clé API"]]) {
      const option = element("option", text); option.value = value; assistant.append(option);
    }
    assistant.value = saved?.assistant.action || "disabled";
    assistant.addEventListener("change", () => { applicationDirty = true; controls(); }); form.append(field("Assistant", assistant));
    for (const [name, label] of Object.entries(credentialLabels)) {
      input(name, label, "", "password", !["openai_api_key", "authority_user", "authority_password"].includes(name), name.endsWith("_user") ? 32 : name === "admin_password" ? 72 : name === "openai_api_key" ? 500 : 1024);
    }
    if (mariadb.profile) {
      for (const name of ["authority_user", "migration_user"]) { inputs[name].type = "text"; inputs[name].value = mariadb[name]; inputs[name].readOnly = true; }
      inputs.authority_password.required = true;
      form.append(hint("Ressaisissez le mot de passe d'autorité choisi pendant la préparation MariaDB. Le compte temporaire de préparation sera créé puis supprimé par le parcours Web."));
    }
    form.append(hint("Le compte de préparation est distinct du compte applicatif. Pour une base gérée, renseignez aussi l'autorité SQL. Les secrets restent en mémoire jusqu'à la fin du chantier ou la fermeture de session."));
    const submit = button("Enregistrer la configuration Web", () => {}, "save-web-application", true); submit.type = "submit"; form.append(submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault(); if (busy || serverBusy || !form.reportValidity()) return;
      const credentials = {};
      for (const name of Object.keys(credentialLabels)) if (inputs[name].value) credentials[name] = inputs[name].value;
      const payload = {revision: application.draft?.revision || 0, configuration: {
        hostname: inputs.hostname.value, database: {mode: mode.value, name: inputs["database-name"].value, user: inputs["database-user"].value},
        administrator: {first_name: inputs["first-name"].value, last_name: inputs["last-name"].value, email: inputs.email.value},
        assistant: {action: assistant.value}}, credentials};
      clearPasswords();
      void run(async () => {
        try {
          await saveChain;
          if (draftConflict) throw failed("BUSY");
          application = (await api("/api/web/setup", payload)).application; applicationDirty = false;
          show(3, false); message("Configuration enregistrée. Les chemins et services seront visibles dans le plan avant confirmation.");
        } finally { for (const name of Object.keys(credentials)) credentials[name] = ""; }
      }, "Validation de la configuration Web...");
    });
    group.append(form);
    if (saved) group.append(hint(applicationDirty ? "Modifications à enregistrer." : "Configuration enregistrée. Vous pouvez préparer le plan sans ressaisir les champs."));
  }
  function renewApplicationCredentials() {
    const details = element("details"); details.append(element("summary", "Ressaisir les identifiants applicatifs"));
    details.append(hint("Champs manquants : " + ((isUpgrade() ? upgrade : application).missing_credentials?.map((name) => credentialLabels[name]).join(", ") || "aucun signalé") + ". Les étapes validées ne seront pas rejouées."));
    const form = element("form"); form.autocomplete = "off"; const inputs = {};
    const allowed = new Set(installation.plan.steps.flatMap((s) => s.requires_secrets));
    for (const [name, label] of Object.entries(credentialLabels)) {
      if (!allowed.has("web." + name)) continue;
      const node = element("input"); node.id = "renew-" + name; node.type = "password"; node.autocomplete = "off";
      node.maxLength = 1024; node.spellcheck = false; inputs[name] = node; form.append(field(label, node));
    }
    const submit = button("Mettre à jour les identifiants", () => {}, "renew-web-credentials", true); submit.type = "submit"; form.append(submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault(); if (busy || serverBusy) return;
      const credentials = {}; for (const [name, node] of Object.entries(inputs)) if (node.value) credentials[name] = node.value;
      clearPasswords();
      void run(async () => {
        try {
          const result = await api(isUpgrade() ? "/api/web/upgrade/credentials" : "/api/web/credentials", {confirmation: installation.plan_sha256, credentials});
          if (isUpgrade()) upgrade = result.upgrade; else application = result.application;
          message("Identifiants mis à jour. Choisissez explicitement l'étape à reprendre.");
        } finally { for (const name of Object.keys(credentials)) credentials[name] = ""; }
      });
    });
    details.append(form); content.append(details);
  }
  function planForm() {
    if (!installation) { content.append(hint("Aucun plan disponible.")); return; }
    content.append(hint("Identifiant : " + installation.installation_id));
    if (isUpgrade() && upgrade.profile) content.append(upgradeSummary());
    if (!isUpgrade() && isApplication() && application.draft) {
      const config = application.draft.configuration;
      const card = element("article", null, "wizard-card"); card.id = "application-plan-choices";
      card.append(element("h2", "Configuration Web à confirmer"),
        element("p", "DNS : " + config.web.hostname),
        element("p", "Base : " + config.database.name + " / " + config.database.user + " / " + config.database.host + ":" + config.database.port),
        element("p", config.database.mode === "managed" ? "Création de la base et des comptes SQL" : "Base vide et comptes SQL existants"),
        element("p", "Administrateur : " + config.administrator.first_name + " " + config.administrator.last_name + " / " + config.administrator.email),
        element("p", config.assistant.desired_enabled ? "Assistant configuré (accès API non testé)" : "Assistant désactivé"),
        hint("Backend local prévu : 127.0.0.1:9080. Services arrêtés, accès sous maintenance en fin de préparation."));
      content.append(card);
    }
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
      label.append(input, element("span", isUpgrade() ? "J’autorise la migration de cette instance gérée : arrêt sous maintenance, verrou SQL global de lecture et sauvegarde vérifiée. Les données et les comptes sont conservés. Les services resteront arrêtés jusqu’à une confirmation distincte." : isApplication() ? "J'ai vérifié ce plan et j'autorise la création des comptes, du Web, de la base et de l'administrateur indiqués, puis la préparation des services sous maintenance." : "J'ai vérifié ce plan et j'autorise uniquement l'acquisition des sources indiquées."));
      input.addEventListener("change", () => { confirmed = input.checked; controls(); }); content.append(label);
    } else content.append(hint("Ce plan a déjà été approuvé. Sa consultation ne rejoue aucune étape."));
  }
  function executionForm() {
    if (!installation) { content.append(hint("Aucun chantier enregistré.")); return; }
    const upgradeRolledBack = isUpgrade() && installation.steps.some((s) => s.name === "web.storage-upgrade" && s.state === "ROLLED_BACK");
    const completed = installation.steps.filter((s) => s.state === "DONE").length;
    const bar = element("progress"); bar.max = installation.steps.length || 1; bar.value = completed;
    bar.setAttribute("aria-label", "Étapes validées"); content.append(bar);
    const title = upgradeRolledBack ? "Web revenu à la version source sous maintenance" : installation.state === "DONE" ? (isUpgrade() ? "Web migré sous maintenance" : isApplication() ? (publicTLS.phase5_complete ? "Configuration Web terminée" : "Web préparé sous maintenance") : installation.mode === "check" ? "Contrôles core terminés" : "Sources prêtes") : states[installation.state] || "État inconnu";
    const summary = element("p", title + " - " + completed + " / " + installation.steps.length); summary.id = "execution-state";
    summary.dataset.state = upgradeRolledBack ? "ROLLED_BACK" : installation.state; content.append(summary);
    if (upgradeRolledBack) content.append(hint("Les données sont conservées et les services restent arrêtés. La réouverture de la version source exige une évaluation distincte."));
    if (installation.state === "DONE") content.append(hint(isApplication() ? "La préparation sous maintenance est acquise. Le plan d'activation et la vérification actuelle du Web figurent ci-dessous." : "HESTIA n'est pas encore déployé. L'installation Web, Gateway et APK appartient aux phases suivantes."));
    if (isApplication() && installation.state === "DONE") activationForm();
    if (isApplication() && !isUpgrade() && activation.installation?.state === "DONE" && mariadb.installation?.state === "DONE") bootForm();
    if (isApplication() && !isUpgrade() && boot.installation?.state === "DONE") acmePackagesForm();
    if (isApplication() && !isUpgrade() && acmePackages.installation?.state === "DONE") publicTLSForm();
    if (isApplication() && !isUpgrade() && installation.state === "DONE") gatewayForm();
    if (isApplication() && !isUpgrade() && gateway.preparation?.state === "DONE" && activation.installation?.state === "DONE") foundationForm();
    if (isApplication() && !isUpgrade() && foundation.installation?.state === "DONE") gatewayServiceForm();
    if (isApplication() && !isUpgrade() && gatewayService.installation?.state === "DONE") mobileActivationForm();
    if (isApplication() && (installation.state !== "DONE" || isUpgrade() && !activation.installation?.approved_plan_sha256)) renewApplicationCredentials();
    if (serverBusy || busy) content.append(hint("Une opération serveur est active. Vous pouvez fermer cette page : elle continue et son état reste consultable."));
    if (installation.last_error_redacted) content.append(hint(errorMessage({code: installation.last_error_redacted})));
    for (const record of installation.steps) {
      const spec = installation.plan.steps.find((s) => s.name === record.name);
      const card = element("article", null, "wizard-card");
      card.append(element("h3", isApplication() ? spec.action : names[spec.module] || spec.name), badge(states[record.state] || record.state, record.state));
      card.append(hint("Phase : " + record.phase + " - Tentatives : " + record.attempts));
      if (record.last_error_redacted) card.append(hint(errorMessage({code: record.last_error_redacted})));
      const row = element("div", null, "wizard-controls");
      if (["FAILED", "MANUAL_ACTION_REQUIRED", "ROLLED_BACK"].includes(record.state) && !(upgradeRolledBack && record.name === "web.storage-upgrade")) {
        row.append(button("Réessayer cette étape", () => confirmAction("retry", {name: record.name}, "Réessayer uniquement « " + (isApplication() ? spec.action : names[spec.module] || spec.name) + " » ? Les étapes validées ne sont pas rejouées."), "retry-" + record.name));
      }
      if (spec.rollback_supported && !["PLANNED", "ROLLED_BACK"].includes(record.state) && !(isUpgrade() && activation.installation?.approved_plan_sha256)) {
        row.append(button("Annuler cette frontière", () => confirmAction("rollback", {boundary: spec.boundary}, isUpgrade() && spec.name === "web.storage-upgrade" ? "Revenir à la version source sous maintenance avec les identifiants ressaisis ? Aucun SQL ne sera restauré. La réouverture après rollback exige une évaluation distincte." : "Supprimer uniquement les ressources créées par " + (names[spec.module] || spec.name) + " ? Une ressource préexistante ou modifiée ne sera pas supprimée aveuglément."), "rollback-" + record.name));
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
  function gatewayAction(action, extra = {}, file = null) {
    pendingAction = {action: "gateway." + action, payload: {confirmation: gateway.preparation.plan_sha256, confirm: true, ...extra}};
    if (file) pendingAction.file = file;
    $("operation-title").textContent = "Préparer Mobile Gateway ?";
    $("operation-description").textContent = action === "import" ? "Transmettre le ZIP sélectionné, vérifier le paquet qualifié et préparer les identités privées du plan affiché ? Le raccordement aux services aura son propre plan." : "Acquérir le paquet qualifié et préparer les identités privées du plan affiché ? Le raccordement aux services aura son propre plan.";
    $("operation-dialog").returnValue = ""; $("operation-dialog").showModal();
  }
  function gatewayForm() {
    const card = element("article", null, "wizard-card"); card.id = "gateway-preparation";
    card.append(element("h2", "Préparation Mobile Gateway"));
    const document = gateway.preparation;
    if (!document) {
      const form = element("form"); form.id = "gateway-form";
      const origin = element("input"); origin.id = "gateway-origin"; origin.type = "url"; origin.required = true;
      origin.maxLength = 261;
      const dev = element("input"); dev.id = "gateway-dev"; dev.type = "checkbox";
      const acquisition = element("select"); acquisition.id = "gateway-acquisition";
      for (const [value, label] of [["github", "Télécharger depuis GitHub Actions"], ["package", "Importer le ZIP binaire qualifié"]]) {
        const option = element("option", label); option.value = value; acquisition.append(option);
      }
      form.append(field("Origine HTTPS publique Mobile", origin), field("Préparer aussi une identité DEV distincte", dev), field("Obtenir le paquet Gateway", acquisition));
      const submit = element("button", "Préparer le plan Gateway"); submit.id = "plan-gateway"; submit.type = "submit"; form.append(submit);
      form.addEventListener("submit", (event) => {
        event.preventDefault(); if (!form.reportValidity() || busy || serverBusy) return;
        void run(async () => {
          gateway = (await api("/api/gateway/preparation/plan", {web_plan_sha256: installation.plan_sha256,
            public_origin: origin.value, dev_enabled: dev.checked,
            ...(acquisition.value === "package" ? {acquisition: "package"} : {})})).gateway; show(5);
        });
      }); card.append(form);
    } else {
      const status = element("p", "Préparation : " + states[document.state]); status.id = "gateway-state"; status.dataset.state = document.state;
      card.append(status, hint(gateway.profile.identity.public_origin + (gateway.profile.identity.dev_enabled ? " - MAIN et DEV" : " - MAIN uniquement")));
      for (const spec of document.plan.steps) card.append(hint(spec.action));
      if (document.last_error_redacted) card.append(hint(errorMessage({code: document.last_error_redacted})));
      if (document.state !== "DONE") {
        const packagePending = gateway.profile.acquisition === "package" && document.steps[0].state !== "DONE";
        if (packagePending) {
          const form = element("form"); form.id = "gateway-import-form";
          const file = element("input"); file.id = "gateway-package"; file.type = "file"; file.accept = ".zip,application/zip"; file.required = true;
          form.append(field("ZIP binaire Gateway qualifié", file), hint("Taille attendue : " + gateway.release.package_bytes + " octets. Après interruption, sélectionnez de nouveau le même ZIP."));
          const submit = element("button", "Importer et préparer Gateway"); submit.id = "import-gateway"; submit.type = "submit"; form.append(submit);
          form.addEventListener("submit", (event) => {
            event.preventDefault(); if (!form.reportValidity() || busy || serverBusy) return;
            const selected = file.files[0];
            if (!selected || selected.size !== gateway.release.package_bytes) { message("La taille du ZIP ne correspond pas au paquet qualifié.", true); return; }
            file.value = ""; gatewayAction("import", {}, selected);
          }); card.append(form);
          card.append(hint("Aucun jeton GitHub requis pour cet import. Le serveur vérifie la taille, le SHA-256 et le contenu du ZIP avant de préparer les identités."));
          if (document.approved_plan_sha256 !== null) {
            const retry = ["FAILED", "MANUAL_ACTION_REQUIRED"].includes(document.steps[0].state);
            card.append(button("Reprendre sans renvoyer le ZIP", () => gatewayAction(retry ? "retry" : "resume", retry ? {name: "gateway.binary"} : {}), "recover-gateway-package"));
            card.append(hint("Cette reprise utilise un reçu déjà écrit. Si l'envoi était incomplet, réimportez le ZIP."));
          }
        } else {
          if (gateway.profile.acquisition !== "package") {
            card.append(hint("Le téléchargement requiert un jeton GitHub avec Actions Read. Ressaisissez-le après une interruption si nécessaire."));
            card.append(button("Ressaisir le jeton pour Gateway", () => show(1), "gateway-credential"));
          }
          if (document.approved_plan_sha256 === null) card.append(button("Valider la préparation Gateway", () => gatewayAction("apply"), "apply-gateway", true));
          else if (document.steps.some((s) => ["RUNNING", "PLANNED"].includes(s.state))) card.append(button("Reprendre Gateway", () => gatewayAction("resume"), "resume-gateway"));
          for (const record of document.steps.filter((s) => ["FAILED", "MANUAL_ACTION_REQUIRED"].includes(s.state))) card.append(button("Réessayer : " + record.name, () => gatewayAction("retry", {name: record.name}), "retry-" + record.name));
        }
      } else {
        card.append(hint("Paquet et identités préparés. Le raccordement MAIN dispose de son propre plan ci-dessous. Le service Gateway et l'accès HTTPS Mobile restent à installer."));
        card.append(button("Vérifier le paquet et les identités", () => void run(async () => {
          gateway = (await api("/api/gateway/preparation/check", {confirmation: document.plan_sha256, confirm: true})).gateway;
          show(5); message("Paquet et identités vérifiés.");
        }), "check-gateway"));
      }
      technical(card, "Plan Gateway (non secret)", document.plan);
    }
    if (gateway.release) {
      card.append(hint("Gateway " + gateway.release.version + " - " + gateway.release.architecture + " - SQLite " + gateway.release.sqlite_schema));
      card.append(hint(gateway.profile?.acquisition === "package" ? "L'import du ZIP conservé reste possible après expiration de l'artefact CI. Aucune autre version n'est choisie automatiquement." : "Artefact CI disponible jusqu'au " + gateway.release.expires_at + ". L'import du ZIP conservé se choisit lors de la création du plan."));
      technical(card, "Référence du paquet qualifié", {sha256: gateway.release.package_sha256, bytes: gateway.release.package_bytes});
    }
    content.append(card);
  }
  function activationAction(action, extra = {}) {
    pendingAction = {action: "activation." + action, payload: {confirmation: activation.installation.plan_sha256, confirm: true, ...extra}};
    $("operation-title").textContent = "Confirmer l'activation";
    $("operation-description").textContent = "Appliquer les étapes autorisées de ce plan d'activation ? La sortie de maintenance autorise les écritures Web. Aucun retour arrière SQL automatique n'est prévu.";
    $("operation-dialog").returnValue = ""; $("operation-dialog").showModal();
  }
  function foundationForm() {
    const card = element("article", null, "wizard-card"); card.id = "foundation-main";
    card.append(element("h2", "Raccordement Foundation MAIN"), hint("Canal interne sur 127.0.0.1:9082, protégé par les assertions signées et la maintenance du Web. DEV, Gateway, accès Mobile public et démarrage automatique restent à raccorder."));
    const document = foundation.installation;
    const action = (name, extra = {}) => {
      pendingAction = {action: "foundation." + name, payload: {confirmation: document.plan_sha256, confirm: true, ...extra}};
      $("operation-title").textContent = "Raccorder Foundation MAIN ?";
      $("operation-description").textContent = "Créer et démarrer le canal MAIN de ce plan, puis vérifier les assertions signées ? Le contrôle ajoute uniquement des nonces et événements techniques dans le Web.";
      $("operation-dialog").returnValue = ""; $("operation-dialog").showModal();
    };
    if (!document) {
      card.append(button("Préparer le plan MAIN", () => void run(async () => {
        foundation = (await api("/api/gateway/foundation/plan", {parents: {web: installation.plan_sha256,
          activation: activation.installation.plan_sha256, gateway: gateway.preparation.plan_sha256}})).foundation;
        show(5);
      }), "plan-foundation"));
    } else {
      const status = element("p", "Raccordement MAIN : " + states[document.state]); status.id = "foundation-state"; status.dataset.state = document.state;
      card.append(status);
      for (const spec of document.plan.steps) card.append(hint(spec.action));
      if (document.last_error_redacted) card.append(hint(errorMessage({code: document.last_error_redacted})));
      if (document.state === "DONE") {
        card.append(hint("Le contrôle de disponibilité est explicite et vaut pour cet instant."));
        card.append(button("Vérifier Foundation MAIN", () => void run(async () => {
          foundation = (await api("/api/gateway/foundation/check", {confirmation: document.plan_sha256, confirm: true})).foundation;
          show(5);
        }), "check-foundation"));
      } else {
        if (document.approved_plan_sha256 === null) card.append(button("Raccorder MAIN", () => action("apply"), "apply-foundation", true));
        else if (document.steps.some((s) => ["RUNNING", "PLANNED"].includes(s.state))) card.append(button("Reprendre MAIN", () => action("resume"), "resume-foundation"));
        for (const record of document.steps.filter((s) => ["FAILED", "MANUAL_ACTION_REQUIRED"].includes(s.state))) card.append(button("Réessayer : " + record.name, () => action("retry", {name: record.name}), "retry-" + record.name));
      }
      if (foundation.availability) {
        const available = foundation.availability.state === "FOUNDATION_MAIN_VERIFIED";
        const status = element("p", available ? "Foundation MAIN vérifiée : signature acceptée, rejeu et appel non signé refusés." : "Foundation MAIN indisponible. Aucun service n'a été redémarré.");
        status.id = "foundation-availability"; card.append(status);
      }
      technical(card, "Plan Foundation MAIN (non secret)", document.plan);
    }
    content.append(card);
  }
  function gatewayServiceForm() {
    const card = element("article", null, "wizard-card"); card.id = "gatewayService-main";
    card.append(element("h2", "Raccordement Gateway MAIN"), hint("Service local MAIN sur 127.0.0.1:9083. Son arrêt est coordonné avec la maintenance Web. DEV, accès Mobile public et démarrage automatique restent à raccorder."));
    const document = gatewayService.installation;
    const action = (name, extra = {}) => {
      pendingAction = {action: "gateway-service." + name, payload: {confirmation: document.plan_sha256, confirm: true, ...extra}};
      $("operation-title").textContent = "Raccorder Gateway MAIN ?";
      $("operation-description").textContent = "Créer le compte privé, installer et démarrer Gateway, puis vérifier sa connexion signée à MAIN ? Le contrôle ajoute uniquement des quotas, nonces et événements techniques.";
      $("operation-dialog").returnValue = ""; $("operation-dialog").showModal();
    };
    if (!document) {
      card.append(button("Préparer le plan MAIN", () => void run(async () => {
        gatewayService = (await api("/api/gateway/service/plan", {parents: {web: installation.plan_sha256,
          activation: activation.installation.plan_sha256, gateway: gateway.preparation.plan_sha256, foundation: foundation.installation.plan_sha256}})).gateway_service;
        show(5);
      }), "plan-gatewayService"));
    } else {
      const status = element("p", "Raccordement MAIN : " + states[document.state]); status.id = "gatewayService-state"; status.dataset.state = document.state;
      card.append(status);
      for (const spec of document.plan.steps) card.append(hint(spec.action));
      if (document.last_error_redacted) card.append(hint(errorMessage({code: document.last_error_redacted})));
      if (document.state === "DONE") {
        card.append(hint("Le contrôle de disponibilité est explicite et vaut pour cet instant."));
        card.append(button("Vérifier Gateway MAIN", () => void run(async () => {
          gatewayService = (await api("/api/gateway/service/check", {confirmation: document.plan_sha256, confirm: true})).gateway_service;
          show(5);
        }), "check-gatewayService"));
      } else {
        if (document.approved_plan_sha256 === null) card.append(button("Raccorder MAIN", () => action("apply"), "apply-gatewayService", true));
        else if (document.steps.some((s) => ["RUNNING", "PLANNED"].includes(s.state))) card.append(button("Reprendre MAIN", () => action("resume"), "resume-gatewayService"));
        for (const record of document.steps.filter((s) => ["FAILED", "MANUAL_ACTION_REQUIRED"].includes(s.state))) card.append(button("Réessayer : " + record.name, () => action("retry", {name: record.name}), "retry-" + record.name));
      }
      if (gatewayService.availability) {
        const available = gatewayService.availability.state === "GATEWAY_MAIN_VERIFIED";
        const status = element("p", available ? "Gateway MAIN vérifiée : connexion signée active, origine incorrecte et en-têtes de transfert refusés." : "Gateway MAIN indisponible. Aucun service n'a été redémarré.");
        status.id = "gatewayService-availability"; card.append(status);
      }
      technical(card, "Plan Gateway MAIN (non secret)", document.plan);
    }
    content.append(card);
  }
  function mobileActivationForm() {
    const card = element("article", null, "wizard-card"); card.id = "mobile-activation";
    card.append(element("h2", "Reprise après sauvegarde mobile"), hint("Ce parcours utilise la reprise préparée sur ce serveur. Il vérifie une dernière fois les sauvegardes et SQL, lève la maintenance puis démarre les cinq services dans l'ordre."));
    if (mobileActivation.state === "NOT_PLANNED") {
      card.append(hint("Une sauvegarde mobile avec préparation de reprise doit avoir été terminée au préalable."));
      card.append(button("Préparer l'activation mobile", () => void run(async () => {
        mobileActivation = (await api("/api/mobile/activation/plan", {parents: {
          web: installation.plan_sha256, activation: activation.installation.plan_sha256,
          gateway: gateway.preparation.plan_sha256, foundation: foundation.installation.plan_sha256,
          gateway_service: gatewayService.installation.plan_sha256}})).mobile_activation;
        show(5);
      }), "plan-mobile-activation"));
    } else {
      const labels = {AWAITING_CONFIRMATION: "En attente de confirmation", RESUME_REQUIRED: "Action engagée, reprise explicite disponible", DONE: "Activation enregistrée comme terminée", UNAVAILABLE: "Journal indisponible, vérification manuelle requise"};
      const status = element("p", labels[mobileActivation.state]); status.id = "mobile-activation-state";
      status.dataset.state = mobileActivation.state; status.setAttribute("aria-live", "polite"); card.append(status);
      const roles = {php: "PHP", apache: "Apache", foundation: "Foundation MAIN", gateway: "Gateway MAIN", timer: "Nettoyage des sessions"};
      const progress = {PENDING: "en attente", INTENT_RECORDED: "intention enregistrée, résultat à vérifier", START_RECORDED: "démarrage enregistré"};
      for (const row of mobileActivation.steps) card.append(element("p", roles[row.role] + " : " + progress[row.state]));
      card.append(hint("Cet historique ne garantit pas la disponibilité actuelle. Le rafraîchissement lit l'avancement sans relancer d'action."));
      if (mobileActivation.state === "AWAITING_CONFIRMATION" || mobileActivation.state === "RESUME_REQUIRED") {
        const inputs = {};
        const credentials = element("details"); credentials.id = "mobile-sql-credentials";
        credentials.open = !mobileActivation.admission_recorded;
        credentials.append(element("summary", "Identifiants pour l'admission SQL finale"), hint("Requis tant que la maintenance reste fermée. Après admission, la reprise utilise le journal sans nouvel export SQL. Les valeurs ne sont pas enregistrées."));
        for (const [name, label] of [["database_password", "Mot de passe SQL de l'application"], ["authority_user", "Compte SQL d'autorité"], ["authority_password", "Mot de passe du compte d'autorité"]]) {
          const input = element("input"); input.type = name === "authority_user" ? "text" : "password";
          input.id = "mobile-" + name; input.autocomplete = "off"; input.maxLength = 1024;
          inputs[name] = input; credentials.append(field(label, input));
        }
        const consent = element("input"); consent.type = "checkbox"; consent.id = "mobile-sql-consent";
        credentials.append(field("J'autorise le verrou global de lecture SQL, limité à 180 secondes.", consent));
        card.append(credentials);
        const action = mobileActivation.state === "AWAITING_CONFIRMATION" ? "apply" : "resume";
        card.append(button(action === "apply" ? "Confirmer l'activation mobile" : "Reprendre l'activation mobile", () => {
          const supplied = Object.values(inputs).some((input) => input.value !== "");
          if ((action === "apply" || supplied) && (!consent.checked || Object.values(inputs).some((input) => !input.value))) {
            message("Renseignez les trois identifiants et autorisez le verrou SQL, ou reprenez sans identifiants si l'admission est déjà passée."); return;
          }
          const values = supplied ? Object.fromEntries(Object.entries(inputs).map(([name, input]) => [name, input.value])) : {};
          for (const input of Object.values(inputs)) input.value = "";
          pendingAction = {action: "mobile-activation." + action, payload: {confirmation: mobileActivation.confirmation,
            confirm: true, credentials: values, allow_global_read_lock: consent.checked}};
          consent.checked = false;
          $("operation-title").textContent = action === "apply" ? "Activer les services locaux ?" : "Reprendre l'activation ?";
          $("operation-description").textContent = "La reprise respecte les intentions déjà enregistrées. Elle ne relance pas un service dont le démarrage est incertain. L'accès Mobile public et le démarrage automatique restent à qualifier.";
          $("operation-dialog").returnValue = ""; $("operation-dialog").showModal();
        }, action + "-mobile-activation", true));
      }
      if (mobileActivation.state === "DONE") card.append(button("Vérifier les services locaux", () => void run(async () => {
        mobileActivation = (await api("/api/mobile/activation/check", {confirmation: mobileActivation.confirmation, confirm: true})).mobile_activation;
        show(5);
      }), "check-mobile-activation"));
      if (mobileActivation.last_error_redacted) card.append(hint(errorMessage({code: mobileActivation.last_error_redacted})));
      technical(card, "Référence du plan confirmé", mobileActivation.profile);
    }
    const available = mobileActivation.availability;
    const live = element("p", available ? (available.state === "LOCAL_SERVICES_AVAILABLE" ? "Services locaux et page de connexion vérifiés" : "Services locaux indisponibles") + " - " + available.checked_at : "Disponibilité actuelle non vérifiée.");
    live.id = "mobile-activation-availability"; card.append(live);
    card.append(hint("Accès Mobile public, TLS public et démarrage automatique non qualifiés par cette activation."));
    content.append(card);
  }
  function activationForm() {
    const card = element("article", null, "wizard-card"); card.id = "application-activation";
    card.append(element("h2", "Activation du Web local"), hint("Backend : 127.0.0.1:" + (isUpgrade() ? upgrade.profile.descriptor.http.port : 9080) + (publicTLS.phase5_complete ? ". Le frontal HTTPS est configuré. Sa disponibilité actuelle se vérifie dans la carte HTTPS." : ". Le frontal TLS public reste à configurer. Le démarrage automatique dispose de son propre plan ci-dessous.")));
    const document = activation.installation;
    if (!document) {
      card.append(button("Préparer le plan d'activation", () => void run(async () => {
        activation = (await api("/api/web/activation/plan", {preparation_sha256: installation.plan_sha256})).activation;
        show(5); message("Plan d'activation prêt à relire. Aucun service n'a été démarré.");
      }), "plan-activation"));
    } else {
      const status = element("p", "Activation : " + (states[document.state] || document.state));
      status.id = "activation-state"; status.dataset.state = document.state; card.append(status);
      for (const spec of document.plan.steps) {
        const record = document.steps.find((row) => row.name === spec.name);
        card.append(element("p", spec.action + " : " + states[record.state]));
        if (["FAILED", "MANUAL_ACTION_REQUIRED"].includes(record.state)) card.append(button("Réessayer « " + spec.action + " »", () => activationAction("retry", {name: record.name}), "retry-" + record.name));
      }
      technical(card, "Plan d'activation complet", document.plan);
      if (document.approved_plan_sha256 === null) card.append(button("Valider et activer le Web local", () => activationAction("apply"), "apply-activation", true));
      else if (document.steps.some((s) => ["RUNNING", "PLANNED"].includes(s.state))) card.append(button("Reprendre l'activation", () => activationAction("resume"), "resume-activation", true));
      if (document.state === "DONE") card.append(button("Vérifier maintenant le Web local", () => void run(async () => {
        activation = (await api("/api/web/activation/check", {confirmation: document.plan_sha256, confirm: true})).activation;
        show(5); message("Vérification locale terminée.");
      }), "check-availability"));
      const availability = activation.availability;
      const live = element("p", availability ? (availability.state === "LOCAL_WEB_AVAILABLE" ? "Page de connexion locale disponible" : "Web local indisponible") + " - Vérifié à " + availability.checked_at : "Disponibilité actuelle non vérifiée. Le journal décrit les actions déjà terminées.");
      live.id = "activation-availability"; card.append(live);
      if (document.last_error_redacted) card.append(hint(errorMessage({code: document.last_error_redacted})));
    }
    content.append(card);
  }
  function bootAction(action, extra = {}) {
    pendingAction = {action: "boot." + action, payload: {confirmation: boot.installation.plan_sha256, confirm: true, ...extra}};
    $("operation-title").textContent = "Confirmer le démarrage automatique";
    $("operation-description").textContent = "Configurer MariaDB et les services Web dédiés pour les prochains démarrages ? Une maintenance en cours continue de bloquer le Web. Aucun redémarrage immédiat n'est effectué.";
    $("operation-dialog").showModal();
  }
  function bootForm() {
    const card = element("article", null, "wizard-card"); card.id = "server-boot";
    card.append(element("h2", "Démarrage automatique"), hint("MariaDB, PHP, Apache et nettoyage des sessions. Le journal conserve la configuration acquise ; une vérification actuelle reste explicite."));
    const document = boot.installation;
    if (!document) {
      card.append(button("Préparer le plan de démarrage", () => void run(async () => {
        boot = (await api("/api/system/boot/plan", {activation_sha256: activation.installation.plan_sha256})).boot;
        show(5); message("Plan de démarrage prêt à relire.");
      }), "plan-boot"));
    } else {
      const status = element("p", "Démarrage automatique : " + (states[document.state] || document.state));
      status.id = "boot-state"; status.dataset.state = document.state; card.append(status);
      for (const spec of document.plan.steps) {
        const record = document.steps.find((row) => row.name === spec.name);
        card.append(element("p", spec.action + " : " + states[record.state]));
        if (["FAILED", "MANUAL_ACTION_REQUIRED"].includes(record.state)) card.append(button("Vérifier la reprise du démarrage", () => bootAction("retry", {name: record.name}), "retry-boot"));
      }
      technical(card, "Plan de démarrage complet", document.plan);
      if (document.approved_plan_sha256 === null) card.append(button("Valider le démarrage automatique", () => bootAction("apply"), "apply-boot", true));
      else if (document.steps.some((s) => ["RUNNING", "PLANNED"].includes(s.state))) card.append(button("Reprendre la configuration du démarrage", () => bootAction("resume"), "resume-boot"));
      if (document.state === "DONE") card.append(button("Vérifier la configuration du démarrage", () => void run(async () => {
        boot = (await api("/api/system/boot/check", {confirmation: document.plan_sha256, confirm: true})).boot;
        show(5); message("Vérification du démarrage terminée.");
      }), "check-boot"));
      if (boot.availability) card.append(hint((boot.availability.boot_persistence_configured ? "Configuration du démarrage vérifiée" : "Configuration ou disponibilité à contrôler") + " - " + boot.availability.checked_at));
      if (document.last_error_redacted) card.append(hint(errorMessage({code: document.last_error_redacted})));
    }
    content.append(card);
  }
  function acmePackagesAction(phase, action, extra = {}) {
    const document = acmePackages[phase === "acquire" ? "acquisition" : "installation"];
    pendingAction = {action: "acme-packages." + phase + "/" + action, payload: {confirmation: document.plan_sha256, confirm: true, ...extra}};
    $("operation-title").textContent = phase === "acquire" ? "Confirmer le téléchargement" : "Confirmer les dépendances HTTPS";
    $("operation-description").textContent = phase === "acquire" ? "Télécharger Certbot et, si nécessaire, NGINX depuis les dépôts Debian authentifiés ? Les versions exactes seront présentées avant l'installation." : "Installer les paquets indiqués ? Leurs services par défaut resteront bloqués. Le certificat et le renouvellement devront ensuite être configurés.";
    $("operation-dialog").showModal();
  }
  function acmePackagesForm() {
    const card = element("article", null, "wizard-card"); card.id = "acme-packages";
    card.append(element("h2", "Dépendances HTTPS"), hint("Préparer Certbot pour Let's Encrypt et NGINX pour l'accès Web. Ce plan n'ouvre aucun port public et ne demande pas encore de certificat."));
    for (const phase of ["acquire", "install"]) {
      const acquired = acmePackages.acquisition;
      if (phase === "install" && acquired?.state !== "DONE") continue;
      const document = acmePackages[phase === "acquire" ? "acquisition" : "installation"];
      if (!document) {
        card.append(button(phase === "acquire" ? "Préparer le téléchargement HTTPS" : "Relire les paquets HTTPS à installer", () => void run(async () => {
          const payload = phase === "acquire" ? {packages_sha256: packages.installation.plan_sha256} : {acquisition_sha256: acquired.plan_sha256};
          acmePackages = (await api("/api/system/acme-packages/" + phase + "/plan", payload)).acme_packages;
          show(5); message("Plan des dépendances HTTPS prêt à relire.");
        }), "plan-acme-" + phase));
        continue;
      }
      const status = element("p", (phase === "acquire" ? "Téléchargement" : "Installation des dépendances") + " : " + states[document.state]);
      status.id = "acme-" + phase + "-state"; status.dataset.state = document.state; card.append(status);
      if (phase === "install" && acmePackages.selection) {
        for (const row of acmePackages.selection.packages) card.append(element("p", row.name + " — " + row.version + " (" + row.architecture + ")"));
      }
      technical(card, "Plan des dépendances HTTPS", document.plan);
      if (document.approved_plan_sha256 === null) card.append(button(phase === "acquire" ? "Valider le téléchargement HTTPS" : "Installer les dépendances HTTPS", () => acmePackagesAction(phase, "apply"), "apply-acme-" + phase, true));
      else if (document.steps.some((s) => ["RUNNING", "PLANNED"].includes(s.state))) card.append(button("Reprendre les dépendances HTTPS", () => acmePackagesAction(phase, "resume"), "resume-acme-" + phase));
      for (const record of document.steps.filter((s) => ["FAILED", "MANUAL_ACTION_REQUIRED"].includes(s.state))) card.append(button("Vérifier la reprise HTTPS", () => acmePackagesAction(phase, "retry", {name: record.name}), "retry-acme-" + phase));
      if (document.last_error_redacted) card.append(hint(errorMessage({code: document.last_error_redacted})));
    }
    if (acmePackages.installation?.state === "DONE" && !publicTLS.phase5_complete) card.append(hint("Dépendances installées. Le frontal public, le certificat et son renouvellement restent à configurer."));
    content.append(card);
  }
  function publicTLSAction(action, extra = {}) {
    pendingAction = {action: "public-tls." + action, payload: {confirmation: publicTLS.installation.plan_sha256, confirm: true, ...extra}};
    $("operation-title").textContent = "Confirmer l'accès HTTPS public";
    $("operation-description").textContent = "Ouvrir les ports 80 et 443, demander le certificat Let's Encrypt en acceptant ses conditions de service, tester puis programmer son renouvellement ? Apache sera brièvement placé en maintenance et remis en service. Relisez le domaine et les réseaux autorisés dans le plan.";
    $("operation-dialog").showModal();
  }
  function publicTLSForm() {
    const card = element("article", null, "wizard-card"); card.id = "public-tls";
    card.append(element("h2", "Accès HTTPS et renouvellement"), hint("Le domaine Web doit pointer vers ce serveur en IPv4, sans enregistrement AAAA. Les ports 80 et 443 doivent être disponibles et accessibles depuis Internet. Le port 80 reste ouvert pour HTTP-01 et redirige les autres requêtes vers HTTPS."));
    const document = publicTLS.installation;
    if (!document) {
      const form = element("form"); form.id = "public-tls-form";
      const email = element("input"); email.type = "email"; email.id = "public-tls-email"; email.required = true; email.maxLength = 254;
      const access = element("select"); access.id = "public-tls-access";
      for (const [value, label] of [["allowlist", "Limiter aux réseaux indiqués"], ["public", "Autoriser tous les clients IPv4"]]) {
        const option = element("option", label); option.value = value; access.append(option);
      }
      const networks = element("input"); networks.id = "public-tls-networks"; networks.placeholder = "203.0.113.0/24, 198.51.100.7/32"; networks.maxLength = 600;
      form.append(field("Adresse e-mail Let's Encrypt", email), field("Accès Web", access), field("Réseaux IPv4 autorisés (CIDR, séparés par des virgules)", networks));
      const submit = element("button", "Préparer le plan HTTPS"); submit.type = "submit"; submit.id = "plan-public-tls"; form.append(submit);
      form.addEventListener("submit", (event) => { event.preventDefault(); void run(async () => {
        const choices = {email: email.value.trim(), access: access.value, networks: access.value === "public" ? [] : [...new Set(networks.value.split(",").map((x) => x.trim()).filter(Boolean))].sort()};
        publicTLS = (await api("/api/web/public-tls/plan", {acme_sha256: acmePackages.installation.plan_sha256, choices})).public_tls;
        show(5); message("Plan HTTPS prêt à relire et confirmer.");
      }); });
      card.append(form);
    } else {
      const status = element("p", "Accès HTTPS : " + states[document.state]); status.id = "public-tls-state"; status.dataset.state = document.state; card.append(status);
      if (publicTLS.configuration) {
        const config = publicTLS.configuration;
        card.append(element("p", "Domaine : " + config.hostname), element("p", "Contact : " + config.email));
        card.append(element("p", config.access === "public" ? "Accès : tous les clients IPv4" : "Accès : " + config.networks.join(", ") + " et contrôle local"));
      }
      for (const spec of document.plan.steps) {
        const record = document.steps.find((row) => row.name === spec.name);
        card.append(element("p", spec.action + " : " + states[record.state]));
      }
      technical(card, "Plan HTTPS complet", document.plan);
      if (document.approved_plan_sha256 === null) card.append(button("Valider l'accès HTTPS", () => publicTLSAction("apply"), "apply-public-tls", true));
      else if (document.steps.some((s) => ["RUNNING", "PLANNED"].includes(s.state))) card.append(button("Reprendre le plan HTTPS", () => publicTLSAction("resume"), "resume-public-tls"));
      for (const record of document.steps.filter((s) => ["FAILED", "MANUAL_ACTION_REQUIRED"].includes(s.state))) card.append(button("Vérifier la reprise du frontal", () => publicTLSAction("retry", {name: record.name}), "retry-public-tls"));
      if (document.state === "DONE") {
        card.append(hint("Configuration du serveur terminée : HTTPS, démarrage automatique et renouvellement sont configurés."));
        card.append(button("Vérifier HTTPS maintenant", () => void run(async () => {
          publicTLS = (await api("/api/web/public-tls/check", {confirmation: document.plan_sha256, confirm: true})).public_tls;
          show(5); message("Vérification HTTPS terminée.");
        }), "check-public-tls"));
      }
      if (publicTLS.availability) card.append(hint((publicTLS.availability.state === "PUBLIC_TLS_AVAILABLE" ? "HTTPS disponible" : "Disponibilité HTTPS à contrôler") + " — " + publicTLS.availability.checked_at));
      if (document.last_error_redacted) card.append(hint(errorMessage({code: document.last_error_redacted})));
    }
    content.append(card);
  }
  async function downloadReport() {
    const data = await api("/api/installation/report");
    const blob = new Blob([JSON.stringify(data, null, 2) + "\n"], {type: "application/json"});
    const url = URL.createObjectURL(blob); const a = element("a");
    a.href = url; a.download = isApplication() ? "HESTIA-WEB-PREPARATION-REPORT.json" : "HESTIA-ACQUISITION-REPORT.json"; a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function show(index, focus = true) {
    if (!allowed(index)) return;
    clearPasswords();
    current = index; document.body.dataset.wizardStep = String(index);
    const step = steps[index];
    $("wizard-eyebrow").textContent = step[1]; $("wizard-title").textContent = step[2]; $("wizard-lead").textContent = step[3];
    if (index === 5 && isApplication()) {
      $("wizard-title").textContent = publicTLS.phase5_complete ? "Configuration Web\nterminée" : "Préparation applicative\nHESTIA Web";
      $("wizard-lead").textContent = publicTLS.phase5_complete ? "Les journaux conservent les étapes terminées. Vous pouvez vérifier la disponibilité HTTPS actuelle et télécharger le rapport." : "Le plan confirmé prépare le Web et ses services sous maintenance. Les étapes validées restent acquises après une interruption.";
    }
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
    if ($("operation-dialog").returnValue !== "confirm" || !action) {
      if (action?.payload.credentials) action.payload.credentials = {};
      return;
    }
    void run(async () => {
      if (action.action === "reset-plan") {
        const result = await api("/api/wizard/reset-plan", action.payload);
        installation = null; confirmed = false; draft = result.draft;
        show(github.ready && preflight?.ok ? 3 : 1); message("Plan non appliqué retiré. Les choix sont de nouveau modifiables.");
      } else if (action.action.startsWith("mobile-activation.")) {
        try {
          mobileActivation = (await api("/api/mobile/activation/" + action.action.slice(18), action.payload)).mobile_activation;
        } finally { action.payload.credentials = {}; }
        show(5);
      } else if (action.action.startsWith("gateway-service.")) {
        gatewayService = (await api("/api/gateway/service/" + action.action.slice(16), action.payload)).gateway_service;
        show(5);
      } else if (action.action.startsWith("foundation.")) {
        foundation = (await api("/api/gateway/foundation/" + action.action.slice(11), action.payload)).foundation;
        show(5);
      } else if (action.action === "gateway.import") {
        gateway = (await api("/api/gateway/preparation/import", action.file, action.payload.confirmation)).gateway;
        show(5);
      } else if (action.action.startsWith("gateway.")) {
        gateway = (await api("/api/gateway/preparation/" + action.action.slice(8), action.payload)).gateway;
        show(5);
      } else if (action.action.startsWith("public-tls.")) {
        publicTLS = (await api("/api/web/public-tls/" + action.action.slice(11), action.payload)).public_tls;
        show(5);
      } else if (action.action.startsWith("acme-packages.")) {
        acmePackages = (await api("/api/system/acme-packages/" + action.action.slice(14), action.payload)).acme_packages;
        show(5); message("État des dépendances HTTPS mis à jour.");
      } else if (action.action.startsWith("boot.")) {
        boot = (await api("/api/system/boot/" + action.action.slice(5), action.payload)).boot;
        show(5); message("État du démarrage automatique mis à jour.");
      } else if (action.action.startsWith("activation.")) {
        activation = (await api("/api/web/activation/" + action.action.slice(11), action.payload)).activation;
        show(5); message("État de l'activation mis à jour.");
      } else if (action.action.startsWith("packages.")) {
        packages = (await api("/api/system/packages/" + action.action.slice(9), action.payload)).packages;
        show(current, false); message("État des dépendances mis à jour.");
      } else if (action.action.startsWith("mariadb.")) {
        mariadb = (await api("/api/system/mariadb/" + action.action.slice(8), action.payload)).mariadb;
        show(current, false); message(mariadb.installation.last_error_redacted ? errorMessage({code: mariadb.installation.last_error_redacted}) : "État de MariaDB mis à jour.");
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
    application = result.application || {draft: null, missing_credentials: []};
    activation = result.activation || {installation: null, availability: null};
    boot = result.boot || {installation: null, availability: null};
    acmePackages = result.acme_packages || {profile: null, acquisition: null, installation: null, selection: null};
    publicTLS = result.public_tls || {installation: null, availability: null};
    gateway = result.gateway || {preparation: null, profile: null};
    foundation = result.foundation || {installation: null, profile: null, availability: null};
    gatewayService = result.gateway_service || {installation: null, profile: null, availability: null};
    mobileActivation = result.mobile_activation || {state: "NOT_PLANNED", profile: null, steps: [], availability: null};
    packages = result.packages || {profile: null, acquisition: null, installation: null, selection: null};
    mariadb = result.mariadb || {profile: null, installation: null, missing_credentials: []};
    upgrade = result.upgrade || {profile: null, missing_credentials: []};
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
      installation = (await api("/api/wizard/plan", {...selection(), ...(useApplication ? {application_revision: application.draft.revision} : useUpgrade ? {upgrade_profile_sha256: upgrade.profile_sha256} : {})})).installation;
      confirmed = false; show(4); message("Plan enregistré. Aucune source n'a encore été téléchargée."); return;
    }
    if (current === 4) {
      if (installation.approved_plan_sha256 !== null) { show(5); return; }
      if (!confirmed) return;
      show(5); message(isApplication() ? "Préparation Web en cours. Le serveur conserve son avancement." : "Acquisition en cours. Le serveur conserve son avancement.");
      installation = (await api("/api/installation/apply", confirmation())).installation;
      github.ready = false;
      show(5); message(installation.last_error_redacted ? errorMessage({code: installation.last_error_redacted}) : isApplication() ? "Préparation terminée sous maintenance. Les services restent arrêtés." : "Acquisition terminée et validée."); return;
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
  window.addEventListener("pagehide", () => { clearPasswords(); if (pendingAction?.payload.credentials) pendingAction.payload.credentials = {}; pendingAction = null; });
  window.addEventListener("pageshow", (event) => { if (event.persisted) window.location.reload(); });

  async function poll() {
    if (!initialized || polling || document.hidden) return;
    polling = true;
    try {
      if (installation || packages.profile || serverBusy) {
        const result = await api("/api/wizard/state");
        serverBusy = result.busy;
        application = result.application || application;
        upgrade = result.upgrade || upgrade;
        activation = result.activation || activation;
        boot = result.boot || boot;
        acmePackages = result.acme_packages || acmePackages;
        publicTLS = result.public_tls || publicTLS;
        gateway = result.gateway || gateway;
        foundation = result.foundation || foundation;
        gatewayService = result.gateway_service || gatewayService;
        mobileActivation = result.mobile_activation || mobileActivation;
        packages = result.packages || packages;
        mariadb = result.mariadb || mariadb;
        const stamp = JSON.stringify([result.installation?.installation_id, result.installation?.revision, serverBusy, activation.installation?.revision, boot.installation?.revision, publicTLS.installation?.revision, gateway.preparation?.revision, foundation.installation?.revision, gatewayService.installation?.revision, mobileActivation, acmePackages.acquisition?.revision, acmePackages.installation?.revision, packages.acquisition?.revision, packages.installation?.revision, mariadb.installation?.revision]);
        const editingMobile = !serverBusy && !busy && ($("mobile-sql-credentials")?.contains(document.activeElement) || $("operation-dialog").open);
        if (stamp !== lastRevision && !editingMobile) {
          lastRevision = stamp;
          installation = result.installation;
          if (current === 5) show(5, false);
          else if (packages.profile && !busy && [1, 2].includes(current)) { $("package-preparation")?.remove(); $("mariadb-preparation")?.remove(); packagesForm(); }
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

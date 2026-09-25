# Handoff WORK - après 5B2.3, reprise 5C

## Lire les références effectives avant toute modification

Base de ce lot : Installer main `036cbfd4b581b2245fbe7b6974625e6b11820818`.
Compagnon Web 5B2.3 : `46c03060625d4d53c675474b11aaa33007d9aad7`, arbre
`aaac278270e0fd1169396945916dfe997ae078bf`.
Relire les HEAD distants main Installer et dev-Bastien/main Web, puis les derniers
commentaires de livraison Installer #13 / Web #135. Ils fournissent les commits
finaux, runs, ZIPs et empreintes. Ce document gelé AVANT Quality n'est pas à lui
seul la preuve d'une campagne réussie ou d'une promotion.

Lire [PHASE5B23_FINALIZATION.md](PHASE5B23_FINALIZATION.md),
[PHASE5B22_DATABASE_PREPARATION.md](PHASE5B22_DATABASE_PREPARATION.md),
[QUALITY.md](QUALITY.md), [PROJECT_STATE.md](PROJECT_STATE.md), puis côté Web
`docs/INSTALLER_FINALIZATION.md` et `docs/INSTALLER_SHARED_ENGINE.md`.
Ne reprendre ni ancienne tentative monolithique, ni branche verification/assembly.

## Frontière acquise à préserver

5A : validation INPUT_ONLY. 5B1 : moteur SQL fresh partagé unique.
5B2.1 : transport privé et interlock. 5B2.2 : provisioning éventuel des objets SQL
sur serveur déjà prêt, audit DML/migration, ports et TLS, fresh, configuration privée.
5B2.3 : Assistant optionnel géré en JSON, activation cohérente et install.lock.
Tous les anciens pins et tests sont conservés. La finalisation a son propre pin
Web et vérifie tous les fichiers runtime de la source ET de la cible. Ne pas
ajouter une allowlist générique ou accepter une empreinte inconnue pour avancer.

API privée : `FinalizationStep.finalize`, `observe`, `configure_assistant` dans
`installer/finalization.py`. Aucun raccordement public/wizard n'est effectué.
La configuration doit provenir de DATABASE_CONFIGURATION_READY et du vrai reçu
SQL durable, pas de l'ancien audit seul. L'Admin est vérifié, jamais réinitialisé.
Le compte d'application reste DML sur seul schéma ; aucun credential de migration
ou d'autorité n'est requis ni conservé dans cette étape.

Les sondes PHP sont non privilégiées. Le contrôle SQL tourne sur copie privée
épinglée ; la sonde active tourne sous l'identité Web. La clé Assistant est une
donnée0660 dans un dossier root-owned0750 hors webroot, jamais du PHP exécutable.
Les anciennes priorités de clés et le RBAC demeurent pour les installations legacy.
Géré : aucun fallback de clé, champ vide conserve, désactivation conserve la clé,
effacement distinct et explicite via les helpers Web existants.

État positif privé : WEB_FRESH_FINALIZED, configuration_activated=true,
installation_sealed=true, application_installed=false,
system_qualification_required=true, api_access=NOT_TESTED.
Le wizard reste « Sources prêtes ». Aucun serveur HESTIA n'est déployé.

## Attention à l'enveloppe avant de concevoir 5C

includes/db.php est un pointeur root-owned sans credential. Il épingle le code
activation.php, install.lock, seal.json, database.json, le CA et le chargeur privé.
finalized.json lie les empreintes à la version de contrat et au commit Web.
Une évolution de ces fichiers ne doit pas simplement écraser le pointeur ou les
reçus pour faire passer observe : 5C doit prévoir sauvegarde, bascule contrôlée,
vérification et remise en état de l'enveloppe avec les secrets durables préservés.

Il n'y a pas de transaction atomique SQL + fichiers. Une interruption après le
sceau avant le reçu peut laisser un Web actif mais exige une action manuelle côté
Installer. Une réponse perdue après reçu complet est observable sans rejeu. Les
.attempt de finalisation ou réglage restent des interlocks, pas des autorisations
implicites de retry. Pas d'effacement des preuves, credentials ou bases partielles.

configure_assistant utilise un payload upgrade sans Admin pour des RÉGLAGES seuls
sur une instance finalisée par 5B2.3. Ce n'est ni un moteur upgrade, ni l'adoption
automatique d'une installation legacy. Le prochain chantier doit distinguer ces
opérations explicitement et garder le refus de fresh sur une base existante.

## Prochaine frontière : 5C uniquement

Concevoir et livrer le parcours upgrade réel intégré au contrat privé : versions
sources explicitement supportées, plan inspectable, comptes de migration temporaires
séparés du runtime DML, sauvegardes privées, reprise conservatrice après interruption
et retour arrière vérifié. Relire le moteur Web réel avant d'annoncer un catalogue
de versions supportées. Ne pas rejouer schema.sql, les seeds fresh ou la création
d'Admin sur une base existante. Ne pas appeler les anciennes Quality une preuve du
nouvel upgrade : ajouter les scénarios spécifiques et garder tous les historiques.

Vérifier préservation des utilisateurs, mots de passe, RBAC, sessions, données,
clé Assistant, paramètres et configuration SQL/TLS. Exercer erreurs SQL/permissions,
réponses perdues, interruptions avant/après chaque frontière, restaurations et
idempotence. Une transaction DDL fictive ou un rollback non testé est refusé.
Raccorder ensuite en5D seulement les services/identités système, droits des données,
écrans et recette système. Gateway/NGINX/APK/import restent d'autres phases.

## Quality, livraison et autorisations

Attendus de5B2.3 :390 core (353 préservés +37 nouveaux),16 gate inclus aussi core,
16 DOM,21 HTTPS natifs,21 nouveaux scénarios SQL/TLS/HTTP et18 historiques SQL/TLS,
Quality Web complète et27 nouveaux contrôles PHP. Les scénarios croisés ont leur
campagne distincte ; ne pas les compter dans les390, ni confondre PHP8.4/MariaDB11.8
local avec la matrice distante PHP8.3/8.4/MariaDB11.4. Consulter les preuves finales.

Les ZIPs doivent reconstruire les arbres exacts testés après application ET
réapplication sur les bases. Documentation incluse dans le gel ; aucune retouche
source après Quality sans tout requalifier. Métadonnées finales dans le rapport
compagnon et les issues, pas un commit documentaire après qualification.

Bastien autorise les écritures dans les dépôts concernés. Relecture des HEAD et
comparaison avant promotion, fast-forward force=false seulement après qualification
complète. Stop sur divergence inattendue. Aucun déploiement LAB-PAWEB30, aucune
compilation APK, aucune fusion des branches techniques. #13/#135 restent ouvertes
pour leurs frontières5C/5D encore non livrées. La Phase5 entière n'est pas terminée.

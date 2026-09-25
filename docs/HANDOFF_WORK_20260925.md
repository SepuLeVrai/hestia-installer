# Handoff WORK - 5C1, prochaine frontière 5C2

## Références et état réel

Base de ce lot : Installer main `fe912b7a7ce2622734d0b508683de3b4e55dc2dd`
(arbre `e8c20a88ad1ace36f69f2a80d5c7243626504207`). Le Web main/dev-Bastien reste
`46c03060625d4d53c675474b11aaa33007d9aad7` (arbre `aaac278270e0fd1169396945916dfe997ae078bf`).
Relire les HEAD et les derniers commentaires Installer #13 / Web #135 pour les
références publiées et les campagnes finales. Ce document gelé avant Quality ne
constitue pas à lui seul une preuve de promotion ou d'une campagne réussie.

La Phase 5B est terminée dans son périmètre privé. Ce lot ajoute 5C1 seulement :
précontrôle réel non mutant d'une instance scellée 5B2.3, inventaire SQL borné et
rapport immuable non exécutable. La Phase 5C complète reste ouverte.

Lire [PHASE5C_UPGRADE.md](PHASE5C_UPGRADE.md), [QUALITY.md](QUALITY.md),
[PROJECT_STATE.md](PROJECT_STATE.md), puis les contrats 5B
[PHASE5B23_FINALIZATION.md](PHASE5B23_FINALIZATION.md) et
[PHASE5B22_DATABASE_PREPARATION.md](PHASE5B22_DATABASE_PREPARATION.md).
Côté Web : docs/INSTALLER_FINALIZATION.md, docs/INSTALLER_SHARED_ENGINE.md et
includes/installation. Ne pas repartir d'une branche verification/assembly.

## Découpage à conserver

- 5C1 : inspection et rapport, implémentés dans ce lot.
- 5C2 : sauvegardes privées et restauration réellement vérifiée sur cible isolée.
- 5C3 : catalogue de transitions explicites, migration et bascule contrôlée.
- 5C4 : reprise, retour arrière, fautes injectées et qualification globale 5C.

5D conserve les services/identités système, les écrans et la recette système.
Gateway, NGINX, APK et import restent leurs phases distinctes. Aucun déploiement
sur LAB-PAWEB30 ni compilation APK autorisé par ce point de reprise.

## API 5C1 et garde-fous

`UpgradePreflight.inspect` dans installer/upgrade_preflight.py accepte uniquement
le mode upgrade, Assistant preserve, aucun Admin ni clé fournis, et le secret du
compte applicatif. Les identités privilégiées et les requêtes de modification sont
refusées. Les pins 5B sont conservés ; aucun fallback de version/empreinte.

Profil source reconnu : SEALED_5B23, commit Web ci-dessus,
APP_VERSION=3.0.0.0-stable-20260914. La source cible est actuellement identique.
Ce n'est ni un upgrade 2.x/3.x générique, ni une adoption legacy, ni une migration.

État positif : UPGRADE_PREFLIGHT_READY. apply_allowed, backup_verified,
rollback_verified, preservation_verified et application_installed restent faux.
Le catalogue de transitions est NOT_DELIVERED. Le wizard reste « Sources prêtes ».
`plan_sha256` identifie un JSON immuable, pas une permission ou un plan applicable.
Ne pas consommer ce rapport comme preuve actuelle lors d'une future mutation.

Le compte DML reste distinct du futur compte de migration. Les sondes PHP sont
non privilégiées, le transport borné et les secrets hors arguments/logs. La sonde
SQL utilise READ ONLY avec snapshot et requêtes fixes. Aucun secret PHP modifiable
par le Web n'est évalué sous root. Clé Assistant : donnée JSON privée, conservée,
aucun appel API ; un champ vide ne vaut pas une demande d'effacement.

L'inventaire est borné et visible par le seul compte applicatif. Ses compteurs et
son empreinte partielle ne prouvent ni les objets DEFINER/triggers/routines, ni
une sauvegarde cohérente globale. Les données peuvent évoluer après observation.
Les métadonnées et l'enveloppe sont recontrôlées, mais ce n'est pas un verrou de
maintenance. Une interruption brutale peut laisser du staging temporaire de code.

## Prochaine exécution : 5C2 seulement

Concevoir et qualifier les sauvegardes privées, avec vérification effective par
restauration dans une base/cible jetable distincte. Inclure l'enveloppe d'activation
et les secrets durables dans des fichiers protégés, jamais dans le rapport public.
Rendre explicites les limites et préconditions pour les données non transactionnelles,
les vues/routines/triggers/événements et les écritures Web concurrentes. Un simple
dump présent ou son hash ne prouve pas une restauration utilisable.

Déterminer et vérifier les privilèges des identités temporaires de sauvegarde ou
migration, sans élargir les GRANT applicatifs. Refuser une cible de restauration
préexistante, même vide, sauf contrat de retour arrière explicitement vérifié.
Garder les credentials éphémères hors journal, ps, logs et URL. Aucun effacement
ou écrasement implicite de données pour faire passer un test.

Préserver utilisateurs, mots de passe, RBAC, sessions, paramètres, clé Assistant,
configuration SQL/TLS et fichiers. Ne pas rejouer schema.sql, seeds fresh ou création
Admin sur l'existant. Ne pas déclarer 5C3/5C4 livrées avec la seule sauvegarde.

## Enveloppe 5B à ne pas contourner

includes/db.php épingle activation.php, install.lock, seal.json, database.json,
le CA et le chargeur privé. finalized.json lie le tout au runtime et au commit.
Ne pas écraser pointeur, reçus ou empreintes pour faire réussir observe. La future
bascule doit avoir une sauvegarde, une validation et un retour arrière cohérents.

Un crash entre sceau et reçu peut laisser le Web actif avec action manuelle exigée.
Les anciens .attempt sont des interlocks, pas des autorisations implicites de retry.
Aucune transaction atomique SQL+fichiers ni rollback DDL fictif. Le précontrôle 5C1
ne nettoie, n'adopte, ne répare et ne relance aucun de ces états partiels.

## Quality et livraison

Attendus 5C1 : 415 core (390 conservés +25), 16 gate également inclus dans core,
16 DOM et 21 HTTPS natifs inchangés. Nouvelle recette réelle 15 SQL/TLS/HTTP ;
18 SQL/TLS et 21 finalisation historiques conservés et réexécutables séparément.
Ne pas assimiler recette locale et matrice distante, ni nouveau précontrôle et
exécution de migrations. Le Web est inchangé ; son ancien run n'est pas une
nouvelle campagne de cette livraison. Aucune modification schema.sql/install.php.

Documentation incluse au gel testé, ZIPs légers de fichiers complets, application
puis réapplication vérifiées sur la base exacte. Pas de retouche après la campagne
finale. Métadonnées de publication dans le rapport compagnon et les issues.
Bastien autorise les écritures ; promotion fast-forward force=false après Quality
complète. Relire les HEAD et stopper sur divergence inattendue. #13/#135 ouvertes.

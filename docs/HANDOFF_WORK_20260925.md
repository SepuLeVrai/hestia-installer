# Handoff WORK - après l'étape 1 résiduelle 5B2.2

## Lire avant toute modification

Le dernier lot prépare le résiduel 5B2.2 depuis Installer
`07d0da54c317420463a3699ee96dd6000afa6c31` et Web
`dcb856bc5ef5f35006d2398289b49f5386dcc5f5`.
Relire les HEAD réels main Installer, dev-Bastien/main Web, puis le dernier
commentaire de livraison Installer #13 et Web #135 : ils portent les commits
publiés, runs, ZIPs, empreintes et limites finales. La présence de ce document
ne suffit pas à elle seule à prouver une CI réussie ou une promotion.
Lire [PHASE5B22_DATABASE_PREPARATION.md](PHASE5B22_DATABASE_PREPARATION.md),
[QUALITY.md](QUALITY.md), le nouveau contrat Web docs/INSTALLER_SQL_CONNECTION.md,
installer/database_step.py et ses pins. Ne pas reprendre la tentative monolithique.

## Acquis à préserver

5A : validation fermée INPUT_ONLY. 5B1 : moteur SQL partagé unique fresh.
5B2.1 : transport privé durci et interlock durable. 5B2.2a : audit/staging local
historique compatible. Le présent lot assemble provisioning SQL managed,
connexions/audit local ou remote TLS, fresh partagé et staging privé v2.
L'API antérieure garde son pin et ses tests ; la nouvelle API a son pin propre.
Aucun ancien ZIP ni branche technique ne doit être réappliqué sur main.

Managed crée base et comptes sur un MariaDB local déjà opérationnel, avec une
autorité explicite distincte de migration/application. Le compte applicatif est
DML sur seul schéma. Le compte temporaire de migration créé est supprimé après
succès ; les comptes existants fournis ne sont pas supprimés. Aucun secret
privilégié durable. Ancêtres root-owned, ACL/liens/refus d'écrasement, fsync et
configuration hors webroot. Remote exige CA protégée, TLS validé et REQUIRE SSL.
Les cibles et clauses non reconnues sont refusées, pas assouplies.

Résultat maximal DATABASE_CONFIGURATION_READY, configuration_activated=false,
application_installed=false. Le chargeur db.php préparé n'est PAS encore le
includes/db.php actif. Aucun install.lock/Assistant/écran ajouté. Le wizard
termine toujours par « Sources prêtes ». La préparation d'une configuration
ne prouve pas le déploiement ou une recette HTTP complète.

## Prochaine exécution : uniquement étape 2 / 5B2.3

Réutiliser et relire les mécanismes Assistant Web existants, les priorités des
fichiers externes et les fonctions de gestion des clés. Prévoir désactivation
explicite sans clé, configuration protégée avec clé, choix conserver/remplacer/
désactiver sur existant. Un champ vide ne doit pas effacer une clé. Ne pas
confondre format acceptable, stockage et accès API effectivement testé.
Aucune exécution root d'un fichier de secret modifiable par le Web.

Assembler ensuite l'activation des fichiers préparés, les vérifications de
cohérence et le scellement install.lock. Ne pas installer ou neutraliser le
formulaire historique après un simple audit SQL. Les interruptions doivent
rester observables sans rejouer fresh ni écraser l'existant. Les secrets
applicatifs durables survivent au nettoyage des credentials temporaires.
Qualifier sur les sources exactes des deux dépôts, sans accepter un autre pin.

Ne pas absorber upgrade 5C, Apache/PHP-FPM/identités système/écrans 5D. Les modes
managed de cette étape ne créent pas encore le service MariaDB. Les autres
phases Gateway/NGINX/APK/import restent indépendantes. Pas de compilation APK.

## Quality et limites de reprise

353 core attendus : 331 conservés +22 nouveaux. 16 tests du gate inclus aussi
core, 16 DOM et21 HTTPS natifs. Cross-dépôts : 18 scénarios SQL/TLS sur le Web
épinglé, puis Quality Web complète legacy fresh/upgrade. Lire les rapports réels
pour versions PHP/MariaDB et campagnes ; ne pas recycler le vert d'une ancienne
livraison. Les tests de connexion sous identité Web ne valent pas login HTTP.
Le scénario de DDL partiel injecte une erreur dans une copie fixture repinnée,
jamais dans les sources ou le pin livré. Aucune DB de production dans les tests.

L'audit est ponctuel, ne certifie pas les objets DEFINER ou toutes les fonctions
maintenance/restore avec DML. L'autorité managed exige un profil administratif
ALL global avec GRANT OPTION, pas un calcul automatique du minimum de privilèges.
La vérification des Host multiples avant création dépend de cette autorité ;
l'audit des comptes existants ne prétend pas cet inventaire global.
Une erreur/crash après réservation bloque le rejeu ; une base partielle et un
compte temporaire peuvent subsister, sans rollback DDL atomique promis.
Les essais TLS sont sur hostname/certificats/datadirs jetables locaux, pas le
réseau réel de l'utilisateur. Consulter les limites de la recette finale.

La récidive responsive sur le premier candidat de cette étape a été diagnostiquée :
la sonde observe innerHeight déjà actualisé mais body.minHeight encore ancien
après deux frames. Le banc DOM attend maintenant la concordance du viewport CSS,
comme le natif existant, sans attendre que les assertions de géométrie passent.
Dix tailles sont parcourues trois fois, toutes les bornes et timeout conservés,
aucun CSS/JS produit modifié. Lire docs/QUALITY.md et les preuves du diagnostic.
Ne pas réintroduire une mesure après seulement deux frames, ni relancer en boucle.

## Autorisations et livraison

Bastien a autorisé l'écriture dans les dépôts concernés, fast-forward seulement
après les Quality100%réussies. Aucun force push ou déploiement sur LAB-PAWEB30.
Relire et comparer les HEAD avant promotion ; arrêter sur divergence inattendue.
Ne fusionner aucune branche verification/assembly/anciens essais 5B complète.
Livrer ZIPs légers avec fichiers complets, documentation /docs et handoff.
Les octets et modes emballés sont ceux qualifiés. Aucune retouche après la
campagne finale : sinon nouvelle qualification. Les métadonnées finales peuvent
être dans le rapport compagnon et les issues sans changer les sources testées.
Issues #13 et Web #135 restent ouvertes pour leurs frontières encore non livrées.

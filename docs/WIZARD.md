# Wizard fonctionnel

## Parcours Web de phase 5

Les cartes du panneau existant pilotent les plans séparés de paquets, MariaDB,
Web/Admin/Assistant, activation locale, boot et dépendances ACME. Le dernier
[plan HTTPS](PHASE5D_PUBLIC_TLS.md) demande le contact Let's Encrypt et les
réseaux autorisés, présente le domaine et les effets, puis exige une confirmation.
Une fermeture de fenêtre, un refresh ou un GET ne rejoue aucune opération.

Après DONE, « Configuration du serveur terminée » désigne le parcours Web de ce
profil. Le bouton « Vérifier HTTPS maintenant » produit un résultat daté ; un
échec actuel ne réécrit pas le journal historique. Le rapport inclut le journal
`public_tls`, ses flags de configuration et cette disponibilité distincte.
Les assets, la navigation fixe et les styles acquis restent inchangés.
Les autres modules et le réseau complet relèvent des phases suivantes.

## Historique de la phase 4

Base : `710760aec85ae96795224adce8e91e37e5cb86e5`. Livraison suivie par #11,
dans le chantier du wizard complet #3 et le parent #1.

## Périmètre réellement exécutable

Le wizard prépare et acquiert les sources. Il ne prétend pas avoir déployé HESTIA.
Les six écrans conservés sont : bienvenue, accès GitHub, préflight, modules, plan
et acquisition. La fenêtre, l'illustration, le fond, les couleurs et les styles
historiques restent inchangés. `wizard.css` ajoute uniquement les contrôles.

L'étape GitHub exige les trois accès Metadata/Contents en lecture. Chaque dépôt
présente un état fixe : à vérifier, accessible, refusé ou indisponible. La première
erreur arrête la vérification ; les dépôts non essayés restent à vérifier.
Aucune archive n'est téléchargée avant le plan et sa confirmation.

Le préflight expose six résultats non secrets. Les sorties de commandes, chemins
exécutables et textes bruts d'exception ne sont pas renvoyés à l'interface.
Le serveur refait le contrôle avant la création du plan par le wizard.

Les modules Web/Gateway/APK peuvent être combinés, en mode fresh ou upgrade.
Ces modes concernent l'acquisition, pas un upgrade SQL. Les refs optionnelles
sont dans un volet avancé ; les SHA du plan sont immuables.

## Interface et confirmations

Le bouton Suivant et les points de navigation respectent les prérequis. Le plan
présente les modules, refs, SHA, ressources, limites de rollback et détails
techniques non secrets. Une case explicite autorise l'acquisition du digest affiché.

Le suivi lit les checkpoints serveur toutes les 1,5 secondes tant que le chantier
existe. Aucun refresh n'émet automatiquement apply, retry ou rollback. Les actions
ciblées utilisent une boîte de confirmation et le SHA du plan. Une source DONE
n'est pas téléchargée à nouveau. Les secrets expirés doivent être ressaisis.

Le rapport JSON téléchargeable ne contient que le rapport non secret du moteur.
L'état final est libellé « Sources prêtes », avec la mention explicite que HESTIA
n'est pas encore déployé. Une interruption réseau ne devient pas une autorisation
de rejouer l'action. Le bouton d'actualisation relit d'abord le serveur.

Annuler puis confirmer ferme la session et efface le credential, sans supprimer
les ressources ni le journal. Un nouveau bootstrap est nécessaire pour une nouvelle
session après logout, car le code initial est à usage unique. Fermer simplement
l'onglet ne commande pas un rollback.

## Brouillon et API

Le brouillon est `wizard.json`, à côté de `state.json` sous le chemin privé du
moteur. Son schéma fermé contient uniquement `revision`, `step` (0 à 3), `modules`,
`refs` et `mode`. Taille maximale : 8192 octets. Répertoire 0700, fichier 0600,
propriétaire vérifié, aucun lien suivi ; verrou partagé avec le moteur, fichier
temporaire puis fsync/rename/fsync. Un numéro de révision interdit l'écrasement
silencieux d'un choix plus récent par un autre onglet. Un conflit exige une relecture.
La lecture d'un état vide ne crée aucun fichier.

| Méthode | Route | Contrat |
|---|---|---|
| GET | `/api/wizard/state` | installation, brouillon, busy et préflight de session |
| POST | `/api/wizard/draft` | schéma fermé ci-dessus, révision attendue |
| POST | `/api/preflight/run` | objet vide ; contrôles non destructifs |
| POST | `/api/wizard/plan` | modules, refs, mode ; préflight revérifié |
| POST | `/api/wizard/reset-plan` | confirm=true et confirmation=SHA du plan |

Toutes ces routes exigent une session. Chaque POST exige le CSRF et les contrôles
Host/Origin existants. Aucun chemin ou exécutable système n'est fourni par le navigateur.
Les routes Phase 2/3 restent compatibles. Le GET de suivi ne prend pas le verrou
long de mutation, afin de rendre les checkpoints consultables pendant l'acquisition.

Le retrait d'un plan n'est autorisé que si le plan est PLANNED, jamais approuvé,
sans tentative d'exécution et sans rollback engagé. Un plan déjà approuvé, même
en échec ou revenu en arrière, reste un journal d'audit et n'est pas supprimé.
Le retrait ne touche ni les sources, ni le brouillon, ni l'inode du verrou. Un nouveau
plan exige une nouvelle confirmation. Les choix sont figés tant qu'un plan existe.

## Confidentialité et reprise

Le PAT est un champ password, autocomplete off ; il est vidé dès la soumission et
n'est jamais réinjecté. Le code client ne lit ni n'écrit localStorage, sessionStorage
ou IndexedDB. Seul le cookie de session HttpOnly existant est utilisé, jamais le PAT.
Le CSRF reste dans la fermeture JavaScript. Les messages serveur sont des codes
connus traduits localement, pas du HTML ou du texte d'exception injecté.

Avant planification, les choix validés côté serveur survivent au refresh et au
redémarrage ; un redémarrage impose une nouvelle session, un nouveau credential
et un nouveau préflight. Après planification, le journal fait autorité. Le cookie
ne constitue pas une copie de l'état d'installation. Une perte réseau initiale
propose explicitement de réessayer la connexion.

## Recette et prochaines phases

Exécuter `./scripts/quality-wizard.sh` dans l'environnement de développement doté
de Node, Playwright et Chromium. Ces outils ne sont pas des dépendances du bootstrap.
Le détail des tests et de leurs limites figure dans [QUALITY_PHASE4.md](QUALITY_PHASE4.md).

Restent les contrats applicatifs Web puis Gateway, réseau/TLS, APK, import et la
recette globale. Les écrans correspondants de #3 ne sont ni simulés comme terminés
ni fermés par cette livraison. L'essai avec un PAT réel et la navigation HTTPS native
du navigateur sur le serveur cible restent une recette d'intégration à effectuer.

> Suite 6B1 : [préparation privée des identités P-256](PHASE6B_PRIVATE_IDENTITIES.md).
> Gateway devient configurable (origine HTTPS, MAIN seul). Composition native et
> recette intégrée 6B/6C encore à terminer ; acquis phase 5/6A conservés.

# Phase 6 - Gateway et Mobile Foundation

## Reprise et références

Le code Installer conserve le gel phase 5
`3e2b0f83915d4729248e88611d3307c406a54bb4`, arbre
`ae116b94501e43a5976bd1c17ee21e678ec0e5a2`. Les ajouts Installer de 6A
sont uniquement documentaires. Aucun ancien StepSpec, journal, reçu, bundle
boot, CSS, hero, parcours wizard ou profil HTTPS n'est modifié.
Web reste épinglé à `2a27c7a1f9fe0a00289eb53278f75d5f230900b7`.

La base Gateway retenue est `544a576577b573bf43ddb588cc18f9fea766e574`,
arbre `7899ae5fa6de2ce376bd5b213fecceca6ee7671c`, 255 fichiers source,
version `0.12.0-bootstrap.rc1`, SQLite 6. La [Quality 102](https://github.com/SepuLeVrai/hestia-mobile-gateway/actions/runs/35707948408)
et le paquet publié dans Gateway #3 correspondent à cette base. SHA-256 ZIP :
`a5d4a1f7f8c48e3b19637a8d9d5ce7829baf99679a5016921b32b00a5856f060`.
Les octets du paquet et son arbre source ont été vérifiés sans rejouer les
campagnes historiques. Le `main` Gateway 0.10.0/SQLite 5 n'est pas une base
acceptable pour perdre Distribution ou bootstrap. Aucun downgrade SQLite.

## 6A - Matrice et corrections du composant

| Surface | Listener | Propriété |
|---|---|---|
| Web acquis | 127.0.0.1:9080 | Apache natif Installer |
| Foundation DEV | 127.0.0.1:9081 | Apache interne Web |
| Foundation MAIN | 127.0.0.1:9082 | Apache interne Web |
| Gateway Installer | 127.0.0.1:9083 | hestia-mobile-gateway.service |

Le profil historique Gateway autonome conserve 9080 ; il est distinct du
profil de cohabitation Installer. Il n'y a ni allocation aléatoire ni adoption
d'un service occupant un port. Gateway refuse tous les autres listeners,
dont wildcard, IPv6 et les deux ports Foundation. Les endpoints Foundation
ne peuvent pas cibler les listeners Gateway.

Le lot Gateway `0.12.1-installer.rc1` fournit une configuration exemple fermée
sur 9083 et adapte les sondes install/upgrade/rollback. Elles suivent la
configuration effective et vérifient le socket du MainPID Gateway, avec son
exécutable installé. Un simple HTTP 200 d'un autre processus ne suffit pas.
Avant mutation, le port occupé par un tiers bloque. Le bind réel reste requis.
Le rollback sonde le listener N-1 et conserve UUID/SQLite à schéma identique.

L'upgrade d'un ancien binaire commence avec sa configuration compatible 9080.
La transition vers 9083 est ensuite explicite ; la configuration N-1 reste
conservée. Si le port du rollback est occupé, le service courant reste actif.
Aucun déplacement automatique d'Apache ou d'une installation existante.

Le contrat détaillé et la recette sont dans
[Gateway INSTALLER_PHASE6A](https://github.com/SepuLeVrai/hestia-mobile-gateway/blob/work/phase6a-installer-listener-20260928/docs/INSTALLER_PHASE6A.md).
La référence candidate exacte, son run Quality et le hash du nouveau paquet
sont consignés dans Gateway #4 et le checkpoint de livraison. Une recette de
listeners sentinelles prouve les collisions, pas l'intégration Foundation/Web.

## 6B - Composition à implémenter

Un nouveau plan Gateway sera lié à ses sources, son paquet validé, son schema,
la matrice des ports, ses identités et sa configuration non secrète.
Il ne modifie jamais les plans Web scellés. Noms du contrat à implémenter :
`gateway.prepare`, `gateway.install`, `gateway.configure`,
`gateway.activate`, `gateway.check`, `gateway.rollback`.
États : PLANNED, RUNNING, DONE, FAILED, MANUAL_ACTION_REQUIRED, ROLLED_BACK.
Ces opérations ne sont pas encore exposées dans le wizard.

À réaliser dans cet ordre :

1. Acquisition et vérification du paquet binaire qualifié. Ne pas assimiler
   l'acquisition des seules sources au téléchargement d'un binaire validé.
2. Contrat privé de configuration, plan et reçus ; paramètres public_origin,
   MAIN et DEV facultatif. Le composant actuel impose encore le domaine
   historique et exige les deux contextes lorsqu'ils sont activés.
3. Réutilisation des migrations Gateway, des scripts et de l'unité durcie.
   Préservation des comptes, permissions et identités au rerun.
4. Clés P-256 distinctes et kid par environnement, privée Gateway/JWK public
   Web, assertions positives/négatives ; aucun secret dans plan ou rapport.
5. Foundation interne sur le serveur natif Installer. Le gate Gateway actuel
   attend `/etc/apache2/sites-enabled` et `apache2ctl` : il faut définir la
   preuve pour les configurations natives acquises, sans simplement désactiver
   ce gate ni remplacer le bundle boot ou les contrôles de drainage.
6. Credential FCM serveur privé, cohérence project_id, cycle de vie et erreurs.
   Les credentials Android restent séparés ; pas de build APK pour ce lot.
7. Raccordement wizard et frontal NGINX Mobile à routes/méthodes fermées,
   anti-spoof et politique indépendante de l'accès Web.

Les outils historiques Gateway `ops/operator.py`, `enroll_cli.py` et les
templates Apache 9080 ne pilotent pas le profil Installer 9083.
Le backend Web acquis ne doit pas être déplacé pour les réutiliser.

Les contrôles doivent refuser paquet/schema/identité/configuration N-1
incompatibles et port étranger avant mutation. Une interruption nécessite un
rapprochement de l'intention avec le reçu ; aucun rejeu aveugle des mutations.
DONE reste historique ; GET/rapport ne lancent aucune sonde de disponibilité.

## 6C - Sortie attendue

Quality Gateway complète et recette intégrée d'une candidate figée : fresh,
upgrade, reprise et rollback ; config/health ; clés et assertions ; FCM ;
listeners ; routes inconnues fermées ; NGINX Mobile et non-régression Web.
Les effets SQL/comptes/APT/services réels restent en CI jetable.
Une fixture FCM ne vaut pas push réel ; le téléphone doit disposer de sa preuve
distincte. Aucun PASS global phase 6 ni clôture Gateway #4 en 6A.

Phases 7 à 10 inchangées : réseau complet, APK, import/restauration, puis
packaging one-shot global. Aucun main/dev-Bastien promu par cette reprise.

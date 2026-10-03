# Phase 6 - chantier de clôture du 3 octobre 2026

Base qualifiée : 6B11 `dc0dca3ae0d33baafa136f002b2fdeff7f6095a8`.
Le chantier regroupe la composition Web/Gateway/ACME, le cockpit public,
le boot Mobile, la restauration originale, DEV/FCM et la recette finale 6C.
La phase reste ouverte jusqu'aux preuves de qualification de chaque périmètre.

## Cockpit du frontal commun

La préparation `SharedPublicPlan` et l'exécution `SharedPublicLifecycle` sont
exposées par des routes fixes distinctes, avec session HTTPS, Origin, CSRF,
verrou de mutation et verrou du journal parent. Les réseaux IPv4 Mobile sont
choisis indépendamment du Web. La sélection publique exige le choix explicite
de `0.0.0.0/0`. Le pré-plan seul n'autorise aucun effet système.

Le plan d'exécution conserve sa confirmation au SHA exact. Le dialogue décrit
les deux brèves bascules et la suspension du timer jusqu'à la fin du cycle.
Une fermeture, une annulation ou un rafraîchissement ne lance aucune opération.
La reprise ciblée utilise le moteur existant ; un effet incomplet reste manuel.
Les anciennes commandes du frontal Web ne sont plus proposées après approbation
du transfert. Les parents et leurs journaux restent conservés.

Les lectures sont historiques. Le contrôle explicite revalide les étapes et
retourne un résultat horodaté en mémoire, perdu au redémarrage du cockpit.
Il ne constitue pas un test de connexion administrateur, de push téléphone
ou une preuve de disponibilité publique depuis Internet.

## Qualification à obtenir

- [x] Cockpit `2f7a33bca0325b25880e7f90960774c14b92b667` : trois CI PASS,
  3971 exécutions, neuf jobs, huit artefacts et 475 fichiers exacts vérifiés.
- [x] Recette composée avec Web/Gateway réels et ACME privé, run `37119330868`.
- [x] SIGKILL du processus d'effet certificat après reçu durable et nouveau PID 1.
- [x] Boot Mobile du profil historique, login Web et bootstrap signé après boot.
- [ ] Restauration originale et upgrade/rollback Gateway.
- [ ] DEV distinct, origine/sonde Web configurables et FCM.
- [ ] Recette globale 6C et preuves terrain distinctes.

Les tests SQL/comptes/services/APT restent exclusivement en CI jetable.
Les campagnes historiques restent conservées, sans relance automatique.

## Candidat boot Mobile

Un plan et une confirmation distincts ajoutent un unique service privé de boot,
après le boot Web et avant le frontal HTTPS. L'enrôlement ne démarre aucun
service. Foundation et Gateway conservent leurs fragments statiques d'origine.
Le bundle privé inclut explicitement le modèle Apache Foundation ; le contrat
des bundles Web/public historiques reste inchangé.

Le worker contrôle les reçus complets, les parents, la configuration, le mode
SERVING et les processus possédés. Un journal privé sous `/run` lie chaque
tentative au boot du noyau, au démarrage de PID 1, au profil et au service fixe.
Une réponse perdue peut être réconciliée par observation. Un service arrêté
après une intention antérieure, un processus remplacé ou une dérive ne provoque
aucun nouveau démarrage automatique. La maintenance bloque les deux démarrages
et est contrôlée de nouveau entre Foundation et Gateway.

Les routes `/api/mobile/boot/{plan,apply,resume,retry,check}` suivent les gardes
HTTPS et les verrous existants. GET/rapport restent historiques. Le contrôle
explicite atteste la configuration actuelle, sans prouver un reboot effectif.
La recette composée est étendue au reboot réel et au bootstrap signé après
redémarrage de PID 1 ; elle est désormais PASS sur
`f8c004c38bff2fc97ad203a7ea12a9515f32aea9`, arbre
`d21b87c351d73a06969d161db9e5bb45dad5d1f8`, 481 fichiers.
Le run `37119330868` constate également deux renouvellements ACME réels,
le worker de renouvellement installé et un PID NGINX conservé lors du reload.
L'artefact `11272553912` a pour SHA-256
`2694da65b6a6a4866e8861a85cf25dbe4d12d28cdb390e96b8fb8a90bc509e18`.
Le test tue le processus d'effet après sa fin durable ; le contrôleur reste
vivant et une reprise explicite observe le reçu sans nouvelle émission.
La réinvocation du worker boot conserve les processus de la même époque.

Les trois CI Installer de ce même commit sont PASS : Quality `37120410610`,
système `37120410606`, paquets `37120410605`. Le run natif précédent
`37111502971` avait passé navigateur/SIGKILL mais échoué sur une mauvaise
invocation du renouvellement dans la recette. Cette invocation a été corrigée
sans étendre les rôles autorisés par le contrôleur produit.

## Correction HTTP-01 issue de la recette composée

Le run natif `37109584055` a installé les vrais services, puis échoué lors du
challenge Mobile. Le vhost Mobile refusait l'autorité HTTP contenant le port
standard explicite (`mobile.example.test:80`), contrairement au vhost Web.
Le compilateur accepte désormais le domaine exact avec ou sans `:80` et
continue à refuser les autres ports et autorités. Un contrôle NGINX natif
sert un jeton sentinelle sous les deux formes ; la recette ACME `37119330868`
confirme le parcours entier. Aucun échec partiel de certificat n'est rejoué.

## Successeur Web Mobile explicite

Le Web `a21fc758fc4c1de9580a953ec07f459f54609874` est qualifié par les quatre
jobs de Quality `37119716962` : PHP 8.3/8.4, MariaDB fresh/replay/upgrade et
Apache. Il inclut les correctifs publiés de main et la configuration privée
optionnelle `public_origin`/`gateway_port`. Le QR ignore les paramètres client
et les en-têtes Host/forwarded ; la sonde reste fermée à 127.0.0.1.

Le profil Installer `fresh-mobile-staged-v2` sélectionne ce commit exact.
Le profil v1 et ses sceaux gardent leur source d'origine. Le nouveau schéma
implique des empreintes SQL distinctes : elles sont mesurées sur l'archive
complète, avec l'arbre Git, les modes et les 1848 fichiers vérifiés. Un commit
connu du registre fresh n'autorise pas une transition SQL dans le catalogue
upgrade. Aucun sélecteur inconnu ne retombe sur les anciens moteurs.

Le plan Foundation v2 reprend l'origine de l'identité Gateway approuvée et
fixe la sonde au port 9083. Le FPM v2 utilise son `foundation/main.json` privé,
également contrôlé par les lecteurs de sauvegarde et boot. Les fragments v1
gardent leurs octets. Le choix v2 reste explicite dans l'API de préparation ;
il ne devient pas le défaut du cockpit avant qualification native.

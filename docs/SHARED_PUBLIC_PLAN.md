# 6B10 - lecture du frontal figé et préparation durable Web/Mobile

Ce lot suit 6B9e (`fef341b732f7fbe3f2d64ab434d424facf3a1823`). Il prépare
le transfert des responsabilités publiques, sans installer de configuration,
émettre de certificat, modifier les services ou exposer une nouvelle API.

## Parent public

`frozen_public_tls.reference` relit les parents boot/ACME, le profil et le
journal terminés. Il reconstruit exactement le registre d'origine et refuse
un registre partiel ou différent. Il ne demande pas que le bundle historique
contienne les nouveaux modules de l'installateur et ne le réécrit jamais.

La lecture seule fournit un état historique. L'observation explicite vérifie
chaque reçu avec les contrôles natifs existants : bundle privé, fichiers,
identité, liens d'activation, certificat, overlay Apache et services. Les quatre
unités restent soumises à leurs gardes d'origine, notamment le refus des
drop-ins étrangers. HTTP, HTTPS et le timer doivent être actifs. Une modification
du profil ou du journal pendant cette observation invalide le résultat.

Le lecteur ne lance aucun code du bundle figé. Il utilise le registre natif
connu, dont le contrat doit rester strictement identique. Une évolution future
de ce contrat ou du worker nécessite une migration explicite.

## Préparation durable

`SharedPublicPlan(public, gateway)` est un contrôleur interne séparé. Ses seules
actions sont `plan` et `check`. Il n'est pas encore exposé dans le cockpit.

Le plan lie :

- les journaux Web, frontal public et préparation Gateway ;
- le reçu des identités publiques et l'origine canonique Mobile ;
- les profils figés, réseaux Mobile et configurations composées 6B9 ;
- HTTP, HTTPS, renouvellement et timer, avec noms et empreintes des unités ;
- les trois liens de démarrage et les commandes fermées de renouvellement
  Web et Mobile, avec leurs répertoires et certificats distincts.

Il décrit sept étapes ordonnées : enrôlement, transfert HTTP, certificat Mobile,
test de renouvellement Mobile, transfert HTTPS, transfert du renouvellement,
vérification. Cette description n'est ni un registre d'exécution ni un reçu
de transfert. L'ancien worker de renouvellement ne peut pas être conservé comme
propriétaire des fichiers composés : ses gardes refuseraient ces nouveaux octets.

`plan` exige les deux empreintes de plans parents et une liste IPv4 canonique.
Il vérifie les parents courants avant de créer un unique fichier privé 0600
sous le verrou partagé du plan Web. Aucun chemin, unité, commande, hook,
autorité ACME ou identifiant secret n'est accepté en entrée. Les secrets connus
sont exclus avant l'écriture. Une autre sélection ne peut pas écraser le plan.

Après perte de réponse, la même demande vérifie puis retrouve le fichier exact.
Une écriture incomplète est conservée et refusée, jamais effacée ou adoptée.
`check` exige l'empreinte du plan préparé, refait les vérifications et n'écrit
aucun reçu d'autorisation. Le statut reste `PREPARED_HISTORICAL`, avec
`current_admission=false`, `execution_authorized=false` et
`ownership_transferred=false`. Une lecture ultérieure ne vaut pas une sonde.

## Qualification et limites

Trente tests supplémentaires couvrent les registres historiques, le bundle
réel copié dans un répertoire jetable, les dérives, confirmations, responsabilités,
reprises sans réécriture, liens symboliques/durs, permissions, écritures partielles,
verrou partagé, secrets et entrées atypiques. Les observations système de ces
nouveaux tests sont simulées ; ils ne qualifient pas un transfert systemd.
La baseline conserve tous les identifiants antérieurs. Les trois CI Installer
habituelles qualifient le gel, avec leurs parcours installation/mise à jour
et NGINX réels existants. Verdicts et manifestes exacts dans la livraison.

La recette native 6B7b12 terminée a été vérifiée sans relance : run Web
`37028464992`, recette `36d197074e028f4a0727dbf144767fdf435dcbe2`, Installer
`0289ba17ad97e134712e7f96072c115b07e6299d`, arbre
`fe285af8f8b5c4a21ba99f0abd044f15c75db8ea`, 453 fichiers, 37 tests PASS.
Artefact `11244771292`, 444091 octets, SHA-256
`bbd5778a93dde2028b4704b8e508490816f4e3ccb60aea43a1672a9e26ac32a6`.
Toutes les assertions `PY_PROOF`, le manifeste et les pins Web ont été vérifiés.
Cette preuve qualifie la préparation/activation locale historique, pas ce
nouveau transfert public ni l'ACME Mobile.

## Suite

Enrôler le nouveau code et ses reçus, vérifier de nouveau les parents et la
Gateway réellement déployée, puis demander un consentement distinct avant
transfert HTTP/HTTPS et renouvellement des deux certificats. Ne pas employer
ce pré-plan comme autorisation de déploiement. Ajouter ensuite l'action cockpit,
les interruptions/reprises natives et la recette ACME/Gateway/Web composée.
Boot Mobile, DEV/FCM et 6C restent ouverts. Aucun SQL, `schema.sql`, `install.php`
ou APK modifié par ce lot. Phase 6 toujours ouverte.

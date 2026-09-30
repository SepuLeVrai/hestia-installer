# Phase 6B7b1 - verrou durable de composition

## Base et périmètre

Base qualifiée 6B7a : `75ee3fc967d2018e4dd4484748e4a56af30c6fa9`, arbre
`c3bbe68a5bfe5569e24accbd07999990030d73f1`. Les 392 fichiers ont été comparés
au manifeste et à l'archive exacte de Quality avant reprise le 30 septembre 2026.
Le checkpoint 6B7a comporte 2 799 exécutions finales et 16 tests du gate.
Ces résultats historiques ne sont pas attribués au nouveau code.

Ce premier sous-lot prépare la composition 6B7b : primitive privée
`mobile_reopen_guard.begin/recover`, sans endpoint ni action dans le wizard.
Il crée `mobile-reopen.attempt` sous le bail de maintenance exclusif existant.
Le marqueur est un refus durable de réouverture. Il ne certifie ni l'admission,
ni une sauvegarde, ni l'état SQL courant, ni un service démarré.

La phase 6 et la réouverture composée restent ouvertes. Le plan/journal
TransactionEngine de composition sera ajouté au lot suivant ; un SHA de plan
fourni à cette primitive est une liaison, pas une preuve que le plan a été validé.

## Contrat et sécurité

- Consentement booléen exact, MaintenanceLease typé et vivant, root, empreinte
  hexadécimale minuscule SHA-256 du plan. Verrou de maintenance tenu tout au long.
- Un reçu Gateway 6B7a complet est obligatoire. Les marqueurs Gateway de gel ou
  de levée en cours doivent être absents. Le reçu doit conserver instance et bail.
- Liaison du plan, du bail et de cinq empreintes : reçu Gateway complet,
  snapshot Gateway, sauvegarde composée, reçu Web et profil des services.
- Fichiers privés root:root 0600, lecture bornée, inode et nom recontrôlés,
  refus des liens, liens physiques, xattrs et permissions inattendues.
- Création exclusive puis fsync fichier/répertoire. Aucun écrasement, aucune
  suppression de reçu Gateway, aucune levée de barrière, aucun start ou SQL.
- Reprise explicite du même bail et du même plan. Intention complète exigée,
  relecture des sources et absence de réécriture après une réponse perdue.
  Une intention absente, tronquée, étrangère ou altérée exige une intervention
  explicite ultérieure ; elle n'est jamais complétée par supposition.
- Les erreurs publiques sont fermées et sans contenu source. `report()` ne fait
  aucune sonde ; ses booléens d'admission et de reprise restent faux.

`MaintenanceLease.resume` refuse désormais aussi `mobile-reopen.attempt`,
quel que soit son contenu ou son type. Même si le reçu Gateway a disparu, un
marqueur vide, un lien pendant ou un répertoire maintient le refus. Le parcours
historique sans ce nouveau marqueur conserve son comportement.

Cette primitive ne possède volontairement aucune opération de consommation.
Le futur coordinateur devra la consommer uniquement après preuve durable
d'admission. Ne pas la supprimer manuellement pour reprendre l'activité.

## Vérifications et limites

21 tests supplémentaires sont obligatoires dans chaque core Debian 12/13,
sans retrait des 1 358 tests historiques. Ils utilisent de vrais fichiers,
permissions, verrous et un processus tué par SIGKILL après écriture durable.
Le reçu Gateway de ces tests est une fixture ; ils ne rejouent pas la recette
native Web/Gateway ni le worker SQLite déjà qualifiés en 6B7a.

Cas couverts : création, ancien parcours de maintenance, reprise exacte sans
réécriture, réponse perdue, écriture partielle, changement de plan/instance/bail,
source corrompue, schémas fermés, nombres atypiques, valeurs vides/longues/Unicode,
liens, modes, marqueur isolé et rapport sans effet. Aucun changement d'interface
ou de responsive. Aucun SQL, schema.sql ou install.php à modifier.

Quality complète exigée sur le gel final : 1 379 core par Debian, 20 bridge,
27 navigateur natif, 16 tests du gate, contrôles statiques et packaging exact.
Les résultats et références réels sont consignés dans le checkpoint de livraison,
sans préjuger ici de leur succès. Les parcours fresh/upgrade historiques restent
dans les suites obligatoires ; aucun nouveau moteur d'installation n'est livré.

Dans Work, les opérations de changement de GID vers 65534 sont indisponibles
dans le namespace courant. Les tests réels de maintenance sont donc exécutés
dans la CI Debian jetable, sans assouplir les propriétaires attendus.

La première campagne paquets a détecté une image Debian 13 de base moins récente
que son dépôt de sécurité : APT demandait `libpcre2-8-0` de `10.46-1~deb13u2`
vers `10.46-1~deb13u3`, puis 139 installations nouvelles. Le diagnostic isolé
`27063b3a2054225c5b9b41270c90451557c65a55` a enregistré la simulation exacte.
Le refus `SYSTEM_PACKAGES_UPGRADE_REFUSED` est correct et reste inchangé.

Le Dockerfile de recette actualise désormais les seuls paquets de son image
jetable avant d'installer les outils de test et avant l'inventaire initial.
Cette préparation CI ne modifie pas le provisionneur et n'installe aucune
dépendance HESTIA. La recette conserve acquisition sans mutation, installation
hors réseau et refus après interruption. Sur une vraie cible, mettre le système
à jour reste un préalable explicite ; l'installer ne s'autorise aucun upgrade
implicite. La branche diagnostique ne fait pas partie du candidat livré.

Le premier run Quality `36684965380` a aussi échoué dans l'ancien scénario
navigateur `test_draft_refresh_empty_selection_and_advanced_ref` : case APK
non cochée après refresh. Les 1 379 core par Debian, 20 bridge et 26 autres
scénarios natifs étaient verts. Le diagnostic isolé `36685710696`, commit
`de706c3e023134af440c335b2554bad8f599ebc7`, a rejoué le scénario exact 12 fois
avec fixtures indépendantes et trace des brouillons : 12 PASS, choix persistés
et rendus correctement. La cause de cette intermittence n'est pas établie.
Ni le scénario, ni ses assertions, ni le JavaScript produit n'ont été modifiés.
La campagne finale complète reste exigée ; ce diagnostic n'est pas un substitut
au gate et le premier échec reste conservé dans les preuves.

## Suite bornée

1. Construire le nouveau plan/journal et conserver les journaux complets des
   barrières avant la première libération. Limiter à MAIN local sans boot/public.
2. Adapter les reprises des trois barrières inode et de data_access, y compris
   les fenêtres RELEASE supprimé/MARKER présent et chmod effectué/marqueur présent.
3. Revérifier archives, sources et SQL courant sous le verrou borné à 180 s ;
   conserver le bloqueur pendant les étapes et les revérifications des DONE.
4. Produire l'admission durable, consommer les bloqueurs dans un ordre récupérable,
   puis démarrer les seules invocations prouvées avec intentions et reçus dédiés.
5. Qualifier les coupures natives et les sondes Web/MAIN. Boot composé, restauration
   originale, DEV, FCM, Mobile NGINX/TLS, origine QR et recette 6C restent distincts.

Aucun changement Gateway/Web/APK, aucune rotation de clé, aucune promotion de
branche active. Le ZIP de ce lot contient les fichiers complets modifiés et la
documentation, issus exactement de l'arbre testé.

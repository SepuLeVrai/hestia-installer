# Phase 5C3b - Migration réelle des stockages sous maintenance

## Résultat et profil

`installer.storage_upgrade.StorageUpgrade` exécute la transition exacte du Web
`46c03060625d4d53c675474b11aaa33007d9aad7` vers
`2a27c7a1f9fe0a00289eb53278f75d5f230900b7`. Les deux builds ont la même lignée
SQL : aucun schéma fresh, seed, script de migration ou compte administrateur
n'est recréé. Le catalogue 5C3a reste une analyse sans capacité d'exécution.

Le profil source est volontairement précis : Web historique scellé, code et
uploads appartenant à root, runtime dédié Installer, MariaDB managed local,
PHP 8.4 sur Debian 13, Ext4. La maintenance est liée au slot de configuration
dès la création du runtime historique. Les sessions, imports, temporaires et
logs utilisent déjà les cinq racines dédiées. Une installation legacy
quelconque, ses producteurs externes ou ses uploads modifiables dans le code
ne sont pas adoptés par cette API.

## Parcours exécuté

1. Vérifier le précontrôle historique et l'arbre complet de la cible épinglée.
2. Arrêter Apache, PHP-FPM et le collecteur dédié sous leur maintenance commune.
3. Fermer les données et protéger les inodes données/configuration/Web, réserver
   les anciens chemins IA, tenir les admissions réglages/schedulers et le verrou
   SQL de lecture continu.
4. Sauvegarder et restaurer réellement SQL/configuration/code et les six racines
   de données, y compris les uploads historiques. Conserver les preuves privées.
5. Préparer le nouveau Web dans un répertoire voisin ; restaurer les uploads,
   conserver leurs octets et dates, puis attribuer leur propriété au compte Web
   dédié (répertoires 0700, fichiers 0600).
6. Écrire l'intention durable avant de remplacer le Web canonique et déplacer
   les uploads dans `runtime/data/uploads`. Garder l'ancien Web dans un parent
   privé 0700. Les renommages exigent le même système de fichiers.
7. Mettre à jour les reçus et configurations Apache/FPM/collecteur sous la même
   maintenance ; vérifier les configurations, le Web scellé, les uploads et
   un second export SQL canonique identique à la sauvegarde.

Le résultat `STORAGE_UPGRADE_APPLIED_GATED` n'est écrit qu'après réussite des
contrôles de sortie, dont la libération du verrou SQL. Les services restent
arrêtés, les données restent fermées et `upgrade.attempt` interdit la levée
générique de maintenance. La méthode distincte `authorize_resume` revérifie la
cible, ses fichiers et son reçu avant de lever les réservations, rouvrir les
données et autoriser l'activité. Elle ne démarre aucun service.

## Incidents et limites

Une interruption conserve les sauvegardes, l'ancien code quand il a été déplacé,
les intentions durables et la maintenance. Une cible incomplète n'a pas de reçu
de succès et ne peut pas être autorisée. Relancer aveuglément `apply` n'est pas
une stratégie de reprise. Après la bascule, aucune restauration SQL automatique
ne peut écraser d'éventuelles nouvelles écritures.

La reprise idempotente après perte de réponse, la récupération de chaque étape
après arrêt brutal, le retour arrière et la décision après reprise d'activité
restent 5C4. Le raccordement au journal/wizard, le démarrage orchestré des services
et les parcours fresh/upgrade complets restent 5D. Les champs globaux
`phase5c3_complete`, `phase5_complete`, `rollback_verified` et
`application_installed` restent faux.

## Recette et preuves

`tests/integration/storage_upgrade_systemd.py` est réservé à la CI jetable :
Debian 13, MariaDB et systemd réels, proxy TLS de fixture, aucun réseau sortant,
volumes Ext4 jetables. Les deux arbres Web complets sont vérifiés avant/après.
Les six scénarios sont répartis dans deux environnements indépendants :

- Transition réelle, conservation des comptes/réglages/session/photo, connexion
  et remplacement de photo à travers les services TLS après autorisation.
- Interruption après préparation de la cible, avant bascule ; source conservée.
- Interruption après déplacement de la source ; maintenance conservée.
- Mauvais build cible refusé avant arrêt des services.
- Échec de sauvegarde SQL sans déplacement du Web.
- Altération des uploads restaurés refusée avant reprise.

Les 196 contrôles existants des composants concernés complètent cette recette.
La Quality globale reste le passage obligatoire avant promotion. Les commits,
manifestes exacts, résultats et traces initiales figurent dans le checkpoint et
l'issue Installer #13 ; aucune réussite antérieure n'est recyclée comme preuve
du nouveau contrôleur.

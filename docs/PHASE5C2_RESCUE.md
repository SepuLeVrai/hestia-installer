# Phase 5C2 - sauvegarde de secours des DEFINER orphelins

## Candidat privé en qualification

Cette extension ne répare pas la source et ne clôture pas 5C2. Elle supprime le
prérequis circulaire « backup opérationnel avant réparation » en permettant une
preuve de récupération distincte pour le défaut historique connu. La réparation,
la maintenance coordonnée, les fichiers métier/sessions et la réactivation Web
restent des frontières séparées à livrer avant la fin de Phase 5.

## Contrat fermé

`UpgradeBackup.create_rescue_and_verify` exige les deux consentements habituels,
dont celui au verrou global SQL, et `expected_orphaned_definer` explicite.
L'identité doit être un compte temporaire local à 127.0.0.1, non root, absent sous
toutes ses variantes Host, partagé par exactement les cinq triggers canoniques.
Corps, événements, ordre, tables et collations restent vérifiés. Aucun profil
inconnu, trigger modifié, compte existant ou compte durable hdf_ n'est adopté.

L'installation doit avoir ses reçus 5B2.3, son pin exact et un état de provisioning
avec `migration_retained=false`. Le payload existant utilise `existing_local`,
conformément au contrat 5A ; le passé managed est vérifié dans le reçu privé,
jamais déduit d'une simple option de l'appelant. Aucun compte n'est recréé sur
la source. Les autres contrôles de sauvegarde restent inchangés.

L'archive de secours utilise un en-tête version 2 portant la finalité
`ORPHANED_DEFINER_RESCUE`. Le vérificateur normal refuse ce format. Le vérificateur
de secours restaure les tables, données et définitions originales dans une
MariaDB jetable sans TCP. Les identités nécessaires n'existent que dans ce
vérificateur, verrouillées sans authentification ; elles sont ensuite supprimées.
Les contrôles de lignes, DDL, FK et cinq effets de triggers sont exécutés.

## Résultat exact

Le reçu privé `rescue-verified.json` est distinct de `verified.json` :

```text
state=RESCUE_RESTORE_VERIFIED
rescue_restoration_verified=true
source_definers_missing=true
backup_verified=false
operational_source_verified=false
apply_allowed=false
rollback_verified=false
web_activation_verified=false
```

Une restauration de secours réussie prouve que les données et définitions ont
été récupérées dans le profil annoncé. Elle ne rend pas les triggers source
fonctionnels. La sauvegarde normale continue à refuser cette même source avec
BACKUP_DEFINER_MISSING. Aucun appel implicite à une réparation n'est ajouté.
Les secrets/données archivés restent dans les répertoires privés 0700/0600 et
ne font jamais partie des artefacts de qualification.

## Recettes requises

Six scénarios SQL supplémentaires portent la suite backup à 30 : restauration
de secours réelle et non-mutation, rejet par le vérificateur normal, identité
inattendue/consentements manquants, origine non managed, variante Host existante,
trigger modifié et archive corrompue. Plusieurs assertions partagent un scénario.
Les 24 précédents restent requis, ainsi que les campagnes historiques et Quality.
Résultats à lire sur le prochain gel exact ; ce document ne les anticipe pas.

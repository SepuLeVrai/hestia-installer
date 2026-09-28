# Bilan de couverture 5C2 du profil provisionné

Ce bilan porte sur le Web épinglé `2a27c7a1f9fe0a00289eb53278f75d5f230900b7`,
ses 1843 fichiers et une instance issue du parcours fresh managed. Les sources,
paramètres et six racines sont imposés par le produit. Il ne s'agit pas de la
découverte/adoption d'un serveur existant arbitraire. Les résultats mesurés du
gel courant appartiennent au checkpoint ; les compteurs historiques ne le qualifient pas.

## Destinations et configurations

| Périmètre du profil | Traitement avant copie | Preuve ou limite |
| --- | --- | --- |
| Base MariaDB dédiée | Même connexion, verrou global continu, export, restauration isolée et deuxième empreinte logique | Une base utilisateur étrangère est refusée ; perte du worker invalide le reçu. |
| GED, héritage relatif, photos et exports | Sous `uploads`, une des six racines exactes | Racines GED externes ou traversées refusées ; écritures bloquées par immutable. |
| Imports, sessions, temporaires, upload temporaire et logs | Cinq autres racines, environnement FPM et chemins fixes | Parent canonique fermé, inodes protégés, sous-arbres supplémentaires refusés. |
| Web et activation | Arbre entier et deux pointeurs protégés | Enveloppe applicative restaurée comme données, métadonnées Git/CI exclues par le contrat archive ; pas encore d'activation d'une nouvelle version. |
| Configuration de l'instance et Assistant géré | Verrous partagés puis immutable sur le slot | Maintenance laissée disponible pour les journaux ; API de réglages exclue pendant copie. |
| Ancien PHP IA dans le Web | Absence puis protection du parent Web | Création ordinaire empêchée, aucun ancien PHP exécuté. |
| Ancienne configuration et stockage IA externes | Absence puis deux réservations vides immutable | Objets existants refusés ; parents Ext4 préparés requis ; reprise explicite journalisée. |
| Publication mobile/configuration fondation | Destinations externes refusées ; environnement FPM fermé | Ce profil n'adopte pas un stockage mobile extérieur ni un serveur Gateway. |
| Archives de sauvegarde | Destination privée, empreintes et restauration isolée, reçu final commun | Corruption ou copie partielle refuse ; les archives ne sont pas protégées indéfiniment après certification. |

## Neuf groupes de producteurs identifiés

Les noms ci-dessous correspondent à `storage_inventory.PRODUCERS`. La maîtrise
des destinations du profil ne transforme pas ce lecteur générique en inventaire
complet de tous les processus de l'hôte.

| Groupe | Maîtrise dans le profil provisionné | Frontière explicite |
| --- | --- | --- |
| `public_php` | Garde de maintenance, arrêt HTTP/FPM, cgroups vides et identité dédiée contrôlée | Un service Web étranger n'est pas adopté. |
| `internal_mobile_php` | Même garde et même environnement applicatif ; données protégées | Aucun listener étranger ni Gateway externe n'est certifié. |
| `php_upload_staging` | Admission frontale fermée et FPM drainé ; répertoire temporaire exact protégé | Un proxy supplémentaire impose un autre profil. |
| `native_session_cleaner` | Verrou partagé, timer/collecteur arrêtés, reprise manuelle | Pas de reprise automatique en fin de sauvegarde. |
| `cli_admin_and_mobile` | Recensement de l'identité ; écritures ordinaires sur inodes protégés et SQL bloquées | Identités/configurations externes ou administration privilégiée non inventoriées globalement. |
| `converter_children` | Groupes de processus drainés, orphelins de l'identité refusés, destinations fermées | Un convertisseur lancé par un service étranger n'est pas adopté. |
| `installer_settings` | Verrous de configuration et protection du slot | Les écritures administratives sur l'hôte sortent du contrat. |
| `host_schedulers` | Empreintes cron/at/anacron et unités classiques refusées ; destinations admises protégées contre de nouveaux writers ordinaires | L'absence observée n'est pas un inventaire global persistant des planificateurs. |
| `other_sql_writers` | Serveur dédié et verrou global de lecture sur la connexion tenue | La perte de connexion ferme le reçu ; aucun verrou persistant après décès n'est revendiqué. |

## Décision et ordre de suite

Le chemin ciblé réunit maintenant les protections de données, de configuration,
de Web et des deux anciens chemins externes. Une sauvegarde/restauration peut
être certifiée **pour ce périmètre enregistré**, avec maintien de la maintenance.
Les chemins ou services inconnus continuent à être refusés ou explicitement
hors garantie ; aucun résultat inconnu ne devient une autorisation.

La [qualification globale du 28 septembre](PHASE5C2_QUALIFICATION_20260928.md)
a passé les gates du commit `713335d` et clôt ce profil 5C2. Les sources
runtime restent identiques dans le présent delta documentaire.

Restent les étapes suivantes :

1. 5C3 : brancher une vraie transition entre deux versions supportées, avec
   migration du schéma et activation cohérente du code/configuration restaurés.
2. 5C4 : qualifier les incidents, interruptions, reprise et rollback de cette
   transition réelle, y compris ses nouveaux points de bascule.
3. 5D : raccorder ces opérations au wizard, aux prérequis système et à
   l'activation explicite des services ; livrer les parcours opérateur.

`storage_inventory_complete`, `foreign_cli_controlled`, `complete_web_backup`,
`system_wiring_verified`, `phase5c2_complete`, `phase5_complete`, `apply_allowed`
et `rollback_verified` restent faux. Les deux premiers sont des garanties
génériques plus larges que ce profil ; ils ne sont pas un objectif implicite
d'inventaire illimité de l'hôte. La clôture documentaire nomme son profil
et ses gates avec `phase5c2_provisioned_profile_complete`, sans convertir les
champs runtime globaux en vrais. Aucune branche active n'est promue par ce delta.

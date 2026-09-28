# Qualification globale et clôture du profil 5C2 provisionné

Décision du 28 septembre 2026 : **le profil 5C2 de sauvegarde/restauration
provisionnée est qualifié et clos** sur le commit Installer
`713335d95494512d20c633c8e0c634385b6ada9f`, arbre
`238eaf2baa09b50793c04196f716bff403054719` (293 fichiers).
Cette décision autorise le passage au développement de 5C3. Elle n'autorise
aucune migration, restauration en place ou activation de service par le produit.

## Profil accepté

Instance fresh managed, serveur MariaDB local dédié, code Web épinglé,
configuration scellée et six racines métier imposées. Recette applicative
provisionnée sur Debian 13, PHP-FPM 8.4 et Ext4. Les parents des deux anciens
chemins IA doivent préexister avec les propriétés exigées ; un objet existant
à ces noms est refusé. Aucune adoption d'un hôte legacy ou d'un stockage arbitraire.

Le [bilan de couverture](PHASE5C2_COVERAGE.md) conserve le détail des destinations
et des neuf groupes de producteurs. La copie/restauration isolée est liée à
une maintenance tenue, au drain des services et du collecteur, aux protections
persistantes des données, du slot, du Web et des noms externes, ainsi qu'au
verrou SQL continu. Une perte de preuve ou une dérive empêche le reçu commun.

## Gates effectivement passés

| Gate | Résultat | Campagne |
| --- | --- | --- |
| Core Debian 12 | 1 077 tests, préflight réel, zéro erreur/échec/skip | [36388476888](https://github.com/SepuLeVrai/hestia-installer/actions/runs/36388476888) |
| Core Debian 13 | 1 077 tests, préflight réel, zéro erreur/échec/skip | Même campagne |
| DOM / HTTPS natif | 16 / 21 tests, assets originaux, captures conservées | Même campagne |
| Système Debian 12 / 13 | 91 / 151 scénarios | [36388476934](https://github.com/SepuLeVrai/hestia-installer/actions/runs/36388476934) |
| Paquets officiels Debian 12 / 13 | 18 / 18 tests, acquisition puis installation hors réseau | [36388476901](https://github.com/SepuLeVrai/hestia-installer/actions/runs/36388476901) |
| SQL / HTTP historiques | 118 scénarios dans huit suites | Campagnes de recettes ci-dessous |
| Proxy / Web déployé / métier | 14 / 10 / 9 scénarios | Campagnes de recettes ci-dessous |
| Sauvegarde provisionnée complète | 36 scénarios, quatre groupes disjoints | [36386468227](https://github.com/SepuLeVrai/hestia-nexus-avv/actions/runs/36386468227) |

Les nouveaux gates totalisent 2 620 exécutions réussies. Les 16 tests du gate
lui-même, déjà inclus dans le core, ne sont pas ajoutés une seconde fois à ce
total. Les 36 scénarios provisionnés de la campagne précédente portent sur
exactement le même arbre et sont conservés, sans relance supplémentaire.

La baseline core impose 1 072 identifiants ; la découverte complète en exécute
1 077, sans identifiant manquant ni doublon. Les sept étapes système réservées
à Debian 13 sont volontairement non lancées sur Debian 12. Cela ne constitue
pas un test ignoré à l'intérieur d'une suite. Le profil applicatif complet
Debian 12/PHP 8.2 n'est pas revendiqué par la recette provisionnée Debian 13.

Les deux sources Web complètes sont contrôlées avant et après les recettes :
`46c03060625d4d53c675474b11aaa33007d9aad7` (1 840 fichiers, contrat historique)
et `2a27c7a1f9fe0a00289eb53278f75d5f230900b7` (1 843 fichiers, stockage métier).
Les sources Installer, modes, identifiants de cas, empreintes des artefacts
et ZIP de sources Quality sont comparés aux manifestes du gel.

## Incident de banc conservé et reprise minimale

La première campagne de recettes
[36388782999](https://github.com/SepuLeVrai/hestia-nexus-avv/actions/runs/36388782999)
reste en échec dans l'historique. Cinq suites y ont passé 66 scénarios. Quatre
suites suivantes se sont arrêtées dans leur préparation, avant tout test,
car le port SQL jetable 3306 n'était pas immédiatement réutilisable après la
suite précédente. Le précontrôle a correctement refusé de démarrer.

La seconde campagne
[36389232973](https://github.com/SepuLeVrai/hestia-nexus-avv/actions/runs/36389232973)
isole chaque suite restante dans un conteneur et un espace réseau neufs :
21 finalisation, 30 sauvegarde, 7 maintenance, 10 coordination, 8 inventaire,
9 stockage métier, soit 85 scénarios. Aucune source produit, assertion ou
condition de refus n'est modifiée. Les suites déjà vertes ne sont pas rejouées.
Le vérificateur exige l'union exacte des onze suites, 66 + 85 = 151 cas,
sans omission ni double comptage ; il conserve les quatre erreurs de préparation.

Cinq workflows ont été lancés pour cette qualification : trois gates globaux
et deux campagnes de recettes. Les preuves sont dans le checkpoint compagnon
`HESTIA-INSTALLER-PHASE5C2-GLOBAL-QUALIFIE-20260928.zip`, qui inclut le parent
chemins externes et le ZIP complet des sources qualifiées.

## Sens de la clôture et frontières inchangées

`phase5c2_provisioned_profile_complete` est la décision documentaire de ce profil.
Elle ne convertit aucun indicateur runtime global en garantie générale.
`storage_inventory_complete`, `foreign_cli_controlled`, `complete_web_backup`,
`system_wiring_verified`, `phase5c2_complete`, `phase5_complete`, `apply_allowed`,
`rollback_verified` et `application_installed` restent faux.

L'administration des parents, montages, flags immutable, périphériques bruts
et noyau reste hors contrat. Les sauvegardes ne sont pas protégées indéfiniment
après leur certification. Les réouvertures de recette sont explicites et
appartiennent au banc ; aucune fin de sauvegarde ne rouvre les services.

Cette note et les mises à jour de synthèse sont un delta documentaire après
qualification. Le code, les tests, les workflows produit et leurs modes restent
identiques au commit qualifié. Le contrôle statique et la comparaison de ce
delta sont conservés séparément ; les campagnes ci-dessus ne sont pas attribuées
au nouvel arbre documentaire. Aucune branche active n'est promue par cette note.

## Chantier suivant : 5C3

1. Définir un couple source/cible réellement supporté et un catalogue explicite
   de transitions. Deux pins disponibles ne constituent pas, à eux seuls,
   une migration de schéma qualifiée.
2. Lier l'autorisation d'application au précontrôle, à la sauvegarde vérifiée,
   aux versions exactes et à la maintenance toujours tenue.
3. Implémenter migration, publication cohérente du code/configuration et journal
   de bascule. Aucune simple modification d'APP_VERSION ne remplace cette étape.
4. Qualifier cette vraie transition. 5C4 couvrira ses nouveaux points
   d'interruption, réponses perdues, reprise et rollback. 5D raccordera le
   wizard, les prérequis opérateur et l'activation explicite des services.

La phase 5 complète reste ouverte. Les sections historiques des autres documents
décrivent leurs gels respectifs ; cette décision fixe le point de départ de 5C3.

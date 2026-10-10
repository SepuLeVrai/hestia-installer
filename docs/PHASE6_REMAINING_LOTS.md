# Phase 6 - lots de finition

## État courant du 10 octobre 2026

| Lot | État | Limite à conserver |
| --- | --- | --- |
| FCM initial, #15 | Terminé, fermé et livré, `b9a3997` | Compte de recette synthétique ; réception Google/téléphone non prouvée |
| DEV distinct initial, #16 | Terminé, fermé et livré, `eaa2faf` | Ne qualifie pas une transition de version avec DEV/FCM |
| Compatibilité et cockpit privé Gateway, #17 | Terminé dans son périmètre, `1028c05` | Première génération MAIN avant public/boot |
| Premier transfert public/boot MAIN | Qualifié à `24a210d` ; preuves dans le document lié ci-dessous | Deux paquets catalogue, sans DEV/FCM actif |
| Générations successives et aller-retour sur le même hôte | À faire | Nouveau contrat de chaîne de générations nécessaire |
| Transitions avec DEV/FCM compatibles | À faire | Retour FCM 0.12.3 vers 0.12.2 incompatible |
| Restauration sur l'instance originale | À faire | Autorité distincte, époque d'authentification et révocations |
| Recette 6C et intégration, #18 | À faire | Qualifier les périmètres restants avant clôture de phase 6 |

Voir [la qualification public/boot](GATEWAY_PUBLIC_BOOT_QUALIFICATION_20261010.md),
[le contrat de transfert](GATEWAY_PUBLIC_FRAGMENT_PROTOCOL.md) et
[le handoff consolidé](HANDOFF_WORK_20261010.md).
#17 et #18 restent ouverts. Un succès du premier transfert ne clôture pas ces
issues composites ni l'ensemble de la phase 6.

## Prochain lot : générations successives

Le parcours courant choisit un SharedPublic v1 et des parents historiques
immuables. Il ne peut pas être réutilisé pour une deuxième transition en
remplaçant simplement sa source par la version publiée. Le pointeur de sélection,
les journaux d'exécution et les preuves de boot ont une identité propre.

Le prochain contrat doit :

1. Lier explicitement la génération active et son admission consommée à une
   nouvelle sauvegarde/bail, une nouvelle cible et de nouveaux journaux.
2. Préserver chaque ancien bundle et reçu, tout en transférant la sélection
   courante de manière durable avec contrôle de l'ancien propriétaire.
3. Résoudre les lecteurs réellement utilisés au boot Web/Mobile et par le
   renouvellement après chaque génération, sans adoption implicite.
4. Qualifier upgrade/rollback/upgrade sur un même hôte avec interruptions,
   conservation des clés/UUID/données et absence de rejeu ambigu.
5. Raccorder le cockpit aux générations et maintenir les lectures sans effet.

Les refus actuels restent obligatoires jusqu'à ce nouveau contrat et ses preuves.
Une archive SQLite ne restaure pas les clés P-256 ni un credential Firebase.

## Restauration originale et 6C

La restauration sur l'origine traite séparément les données, identités,
révocations Web et sessions techniques. Les reçus actuels portant
`restore_to_original_allowed: false` ne sont pas une autorisation de restauration.

La recette 6C compose les périmètres qualifiés, contrôle les erreurs, les reprises,
les parcours fresh/upgrade et le package exact, puis prépare l'intégration.
L'intégration sur une branche active et le déploiement restent des actions
séparées, selon l'autorisation explicite applicable.

## Preuves historiques à conserver

- Frontal commun/ACME privé et boot Mobile : `f8c004c`, recette Web `37119330868`.
- Web Mobile v2 : `261e053`, recette `37141785730`.
- FCM : `b9a3997`, recette `37153956770` et trois CI Installer acquises.
- Cockpit privé : `1028c05`, Quality `37272264853`, runtime `37272264790`,
  packages `37272264786`, recette composée `37272334536`.

Les erreurs historiques et corrections détaillées restent dans les documents
spécifiques et #17. Ne pas attribuer un ancien PASS à un nouveau gel et ne pas
rejouer les campagnes historiques sans risque concret à résoudre.

# Phase 6 - lots de finition

## État courant du 10 octobre 2026

| Lot | État | Limite à conserver |
| --- | --- | --- |
| FCM initial, #15 | Terminé, fermé et livré, `b9a3997` | Compte de recette synthétique ; réception Google/téléphone non prouvée |
| DEV distinct initial, #16 | Terminé, fermé et livré, `eaa2faf` | Ne qualifie pas une transition de version avec DEV/FCM |
| Compatibilité et cockpit privé Gateway, #17 | Terminé dans son périmètre, `1028c05` | Première génération MAIN avant public/boot |
| Premier transfert public/boot MAIN | Qualifié à `24a210d` ; preuves dans le document lié ci-dessous | Deux paquets catalogue, sans DEV/FCM actif |
| Générations successives et aller-retour sur le même hôte | Implémenté, qualification native en cours sur `dd4d89a` | Pas de PASS global ; trois cycles et six étapes à prouver |
| Transitions avec DEV/FCM compatibles | À faire | Retour FCM 0.12.3 vers 0.12.2 incompatible |
| Restauration sur l'instance originale | À faire | Autorité distincte, époque d'authentification et révocations |
| Recette 6C et intégration, #18 | À faire | Qualifier les périmètres restants avant clôture de phase 6 |

Voir [la qualification public/boot](GATEWAY_PUBLIC_BOOT_QUALIFICATION_20261010.md),
[le contrat de transfert](GATEWAY_PUBLIC_FRAGMENT_PROTOCOL.md) et
[le handoff consolidé](HANDOFF_WORK_20261010.md).
#17 et #18 restent ouverts. Un succès du premier transfert ne clôture pas ces
issues composites ni l'ensemble de la phase 6.

## Lot courant : générations successives

Le contrat de chaîne, la sélection publique successive, les archives distinctes,
les liens de journaux et le cockpit sont implémentés. Le candidat `dd4d89a`
passe Quality, protocole et systemd. La recette native complète reste ouverte.
Voir [le contrat et son diagnostic](GATEWAY_SUCCESSIVE_GENERATIONS_20261010.md).

Le prochain jalon exige upgrade/rollback/upgrade sur le même hôte, chaque fois
avec interruption, reprise explicite, nouveau PID 1, Web/Mobile accessibles,
clés/UUID/anciens fichiers conservés et deux renouvellements réels. Les recettes
indépendantes d'upgrade et rollback doivent aussi passer sur le gel exact.
Un échec conservé ne devient pas un PASS parce qu'un test voisin réussit.

Les variantes DEV/FCM, la restauration originale et un reboot noyau restent
hors de ce lot. Une archive SQLite ne restaure pas les clés P-256 ni un
credential Firebase.

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

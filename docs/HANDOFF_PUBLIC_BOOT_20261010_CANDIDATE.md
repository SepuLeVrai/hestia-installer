# Reprise public/boot - candidat partiel du 10 octobre 2026

## État exact

Le bloc demandé n'est pas terminé et n'est pas qualifié. Ce document complète
`HANDOFF_WORK_20261010.md` fourni dans la livraison documentaire du 10 octobre.

Le HEAD distant de travail est toujours
`1028c05ce48d0f528f8b6f71837be51ee15710a0`, branche
`quality/phase6-gateway-lifecycle-20261004`. Le clone est complet. Aucun
AGENTS.md n'a été trouvé dans ce clone. La livraison précédente a été
contrôlée par son SHA-256 et son `verify_delivery.py` : intégrité PASS.

## Travail réalisé

- Lecture des workers figés, de l'overlay Apache et des admissions natives.
- Développement du protocole interne `gateway_public_fragments.py`.
- 25 nouveaux contrats ajoutés à la baseline, dont 23 tests de fichiers réels.
- Workflow Debian 13 ciblé, branche technique
  `validation/phase6-public-fragments-20261010` uniquement.
- Documentation de portée et limites dans
  [GATEWAY_PUBLIC_FRAGMENT_PROTOCOL.md](GATEWAY_PUBLIC_FRAGMENT_PROTOCOL.md).

Le protocole n'est appelé par aucun contrôleur. Les refus public/boot acquis
sont conservés. Aucune modification SQL, aucun changement à schema.sql ou
install.php n'est nécessaire. Les branches actives et la production restent
inchangées.

## Validation et autorisation

L'utilisateur a autorisé la poursuite et la publication sur la branche technique
`validation/phase6-public-fragments-20261010`. Le refus initial est levé.
Les branches actives restent inchangées.

La recette Debian 13 du protocole initial a réussi : run GitHub `38038107737`,
commit `86a65998432a7c3aa7b1bda2d266d08c0869e042`, 25 tests et contrôles statiques.
Deux essais antérieurs ont échoué dans la préparation du workflow (Git absent,
puis modes d'extraction trop permissifs) ; ces défauts sont corrigés.

Le candidat suivant ajoute un lecteur des huit inodes installés, utilisable
après fermeture du bail, un compilateur de génération, les lecteurs successeurs
Web/Mobile/public et un worker scellé. Les chemins des certificats, profils
historiques, routes, dépendances et commandes de renouvellement sont conservés.
Les contrôles natifs Gateway restent différés après Web au boot, comme dans le
contrat acquis. Les contrôles précoces sont des lectures de fichiers.

Les 14 tests de compilation/bundle passent localement. Les 28 tests de fragments
et les nouveaux contrats sont requis dans la baseline. Le workflow ciblé exécute
les 42 tests ; Quality complète est aussi déclenchée sur la branche technique.
Ces résultats à venir doivent être lus sur le SHA exact, sans extrapoler le
succès précédent au code nouveau.

Localement, seuls UID/GID 0 sont mappés. La maintenance utilisant GID 65534 ne
peut pas être testée ici. Aucun contrôle chown n'est simulé ou supprimé.

## Travaux restant obligatoires

1. Lire les résultats du gel courant, ciblés et Quality complète.
2. Qualifier la copie native du bundle, sa reprise et les lecteurs en conditions
   systemd réelles avant toute intégration.
3. Développer l'autorité et les bundles successeurs complets, avec arrêt et
   contrôle des workers publics. La primitive seule n'accorde aucune admission.
4. Raccorder les admissions fermées dans `mobile_reopen_files.py`,
   `mobile_external_admission.py`, `mobile_data_admission.py` et
   `mobile_resume_plan.py`, puis `gateway_transition_native.py` et
   `gateway_transition_resume.py`, sans supprimer simplement leurs refus.
5. Traiter les lecteurs réellement exécutés par les boot Web et Mobile, le
   transfert systemd et la reprise après perte de réponse, puis le cockpit.
6. Recette native Web/Mobile, certificats, renouvellement, interruptions et
   nouvelle époque PID 1, installation initiale et upgrade ; Quality globale.

Ne pas clôturer #17, #18 ni la phase 6 sur ce candidat. Le ZIP est un checkpoint
de développement, pas un correctif de production ni un lot qualifié.

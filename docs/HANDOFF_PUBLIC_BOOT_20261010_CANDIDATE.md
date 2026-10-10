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

## Blocage de validation

La revue automatique a rejeté le push de la branche technique car elle ne
considère pas la publication du code/workflow vers ce dépôt comme autorisée.
Il faut l'autorisation explicite de publier ce candidat sur cette branche
technique pour lancer la recette ciblée. Ne pas contourner le refus.

Localement, seuls UID/GID 0 sont mappés. Les tests de fichiers échouent dès
la préparation de maintenance (`fchown` vers GID 65534), avant d'exercer le
protocole. Ne pas supprimer les contrôles, simuler chown, ni transformer ces
échecs en skips pour afficher un PASS.

## Reprise après autorisation

1. Vérifier le HEAD distant et l'absence de collision de branche technique.
2. Publier le gel du candidat et lire la recette Debian ciblée ; corriger toute
   erreur réelle avant d'intégrer cette primitive.
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

# Gateway : changements de version, lot 3

## 3A, matrice fermée et plan sans effet

Base acquise : lot DEV `eaa2faf3c90f993923b99c8fe6f3bca5dc94ee04`.
Cette sous-étape ajoute une évaluation de compatibilité et un plan privé lié au
service terminé. Elle ne déploie aucun binaire, ne lance aucun service et ne
restaure aucune donnée. Les reçus des parents restent inchangés. Les étapes 3B,
3C et 3D doivent qualifier les effets et la restauration sur l'origine.

| Source | Cible | Profil conservé | Compatibilité des fichiers |
| --- | --- | --- | --- |
| 0.12.2-installer.rc1 | 0.12.3-installer.rc1 | MAIN, sans FCM | Candidate |
| 0.12.2-installer.rc1 | 0.12.3-installer.rc1 | MAIN et DEV distinct, sans FCM | Candidate |
| 0.12.3-installer.rc1 | 0.12.2-installer.rc1 | MAIN, sans FCM | Candidate rollback |
| 0.12.3-installer.rc1 | 0.12.2-installer.rc1 | MAIN et DEV distinct, sans FCM | Candidate rollback |
| 0.12.3-installer.rc1 | 0.12.2-installer.rc1 | FCM, avec ou sans DEV | Refusée avant effet |
| Une version | Même version | Tout profil | Aucun changement de version |

Une candidate compatible n'est pas une transition exécutée ou qualifiée. Les
références admises sont exclusivement `e2c09f53593bf316906ccc4387f185e73e7f85a8`
et `33927821bbda57a2c10791d0523eaf3b254c8c9e`. Branches, tags et versions textuelles
ne sélectionnent jamais des octets. Une extension du catalogue fresh ne peut pas
étendre implicitement cette matrice.

Les deux versions partagent les six migrations SQLite identiques et le schéma 6.
Leurs empreintes Git sont dans `installer/gateway_transition.py`. En revanche,
0.12.2 refuse `project_push_project_id` et le chemin systemd privé du credential,
et ne fournit pas `--check-push-credential`. La compatibilité SQLite ne permet
donc pas un rollback FCM. Aucun projet n'est supprimé et aucun secret n'est déplacé
vers une configuration globale pour contourner ce refus.

## Contrat du plan

`POST /api/gateway/transition/plan` exige session, origine, CSRF et les trois champs
`source_plan_sha256`, `target_commit`, `direction`. Le service source doit avoir
un journal terminé et un profil exactement reconstructible. Le plan lie le SHA
du journal source, le SHA du profil, la direction, les deux paquets exacts, les
configurations, les identités MAIN/DEV et le reçu FCM public éventuel.

Le cockpit affiche l'examen de la mise à jour ou du retour arrière. Une action
explicite prépare le plan ; le refresh lit seulement les métadonnées. Le plan
reste immuable. Une sélection ou un parent modifié est refusé. Aucune route
`apply`, `resume`, `restore`, `rollback` ou `retry` n'est exposée pour ce plan 3A.
Son SHA ne vaut pas autorisation d'écriture sur l'hôte.

Les contextes, comptes, clés, chemins, origine, unité et configuration sont
conservés. L'ajout DEV et l'activation/désactivation FCM sur service existant ne
sont pas implicitement absorbés par un changement de binaire.

## Contrats requis avant les effets 3B et 3C

La bascule doit authentifier les deux paquets et l'instance source réelle,
vérifier une sauvegarde composée courante, tenir le garde de maintenance,
arrêter le Gateway exact et contrôler schéma/UUID. Chaque effet doit avoir une
intention durable et une reprise par observation. Les vérifications du service,
des deux contextes et du boot devront suivre le profil actif après bascule.

Un rollback de fichiers conserve la base actuelle : sessions, révocations,
quotas et UUID ne sont jamais rembobinés. Une restauration SQLite sur l'origine
est une opération séparée, avec autorisation distincte et traitement explicite
de l'époque d'authentification et des révocations Web. Les sauvegardes existantes
restent `restore_to_original_allowed: false` tant que ce contrat n'est pas acquis.
Les clés P-256 et le credential Firebase ne font pas partie d'une sauvegarde
SQLite. Aucune restauration de ces secrets n'est supposée.

## Vérification

Les nouveaux contrats sont ajoutés à la baseline obligatoire sans retirer un
ancien identifiant. Les tests navigateur utilisent les routes HTTPS réelles.
`scripts/audit_gateway_transition.py --legacy PACKAGE_0122 --current PACKAGE_0123
--output REPORT` authentifie les deux ZIP complets puis exécute leurs vrais
binaires : version, schéma, configuration MAIN/DEV avec et sans FCM. Il ne lance
aucun serveur ni migration et n'ouvre pas SQLite. Ce contrôle des parseurs ne
qualifie pas une bascule réelle. Les résultats et SHA du gel se trouvent dans
les preuves du checkpoint ; aucun succès d'un gel antérieur n'est réattribué.

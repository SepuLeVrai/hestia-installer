# Phase 5C3a - Catalogue fermé et audit des sources

## Résultat et frontière

Le premier lot 5C3 classe les builds Web connus et livre un audit reproductible
de leurs arbres Git complets. Il ne livre pas encore une transition applicable.
5C2 reste clôturée pour le [profil provisionné](PHASE5C2_QUALIFICATION_20260928.md).
5C3, 5C4 et 5D restent ouvertes dans Installer #13, ainsi que #1, #3 et Web #135.

Le registre des releases fresh n'est pas le catalogue des upgrades : ajouter une
release fresh ne la rend pas implicitement migratable. Le catalogue possède sa
propre liste fermée de commits. Il refuse branches, tags, versions textuelles,
préfixes de SHA, dépôts différents et listes de migrations fournies par l'appelant.

## Audit de la première paire candidate

| Identité | Source historique | Build avec stockages externes |
| --- | --- | --- |
| Commit | `46c03060625d4d53c675474b11aaa33007d9aad7` | `2a27c7a1f9fe0a00289eb53278f75d5f230900b7` |
| Arbre | `aaac278270e0fd1169396945916dfe997ae078bf` | `783be5abdcd5e13addefe96d743eee3a97b7a6de` |
| Fichiers | 1840 | 1843 |
| APP_VERSION | `3.0.0.0-stable-20260914` | `3.0.0.0-stable-20260914` |
| Uploads | Dans le Web historique | Racine externe explicite possible |

Les deux arbres complets, obtenus sans troncature, ont été reconstruits selon le
format et l'ordre des objets Git. Les identités suivantes sont communes :

- `sql/schema.sql` : blob `17e55373cb3033ad7c2a1779e8e29c47c5e90579`.
- `sql/migrations` : arbre `ebce0d11a07693f00c7f1cb90e08865517410870`, 113 fichiers SQL.
- `includes/version.php` : blob `94e959167a2f890a18bcd9063be777abc8f78d4a`.
- `includes/installation/core.php` : blob `1695eb07ddc7c5826687e30fd10ff56937395c38`.
- `includes/installation/fresh.php` : blob `ac863b697455d04c6162781ed6743c193db903e3`.

24 fichiers diffèrent, dont trois ajouts. Aucun fichier SQL ne diffère. La paire
est donc classée `STORAGE_LAYOUT_CHANGE_SAME_SQL_LINEAGE`. Aucun script SQL n'est
sélectionné ; les 113 migrations historiques ne constituent pas une liste à
rejouer. Le schéma fresh, les seeds et l'administrateur ne sont jamais appelés par
ce lot. Ce constat sur les sources ne certifie pas le schéma d'une base en place.

Le passage du stockage historique dans le Web aux racines externes reste un vrai
travail de migration de fichiers et de bascule de configuration. Le catalogue
ne le masque pas derrière un changement de SHA ni un marqueur de version commun.
Le profil 5C2 provisionné qualifié part du build avec stockages externes : ses
preuves ne couvrent pas rétroactivement la sauvegarde complète du build historique.

## API privée sans effet de bord

```python
from installer.upgrade_catalog import assess_transition

assessment = assess_transition(
    repository="SepuLeVrai/hestia-nexus-avv",
    source_commit="46c03060625d4d53c675474b11aaa33007d9aad7",
    target_commit="2a27c7a1f9fe0a00289eb53278f75d5f230900b7",
)
report = assessment.report()
```

Le rapport canonique est immuable, son empreinte lie les deux identités et leur
direction. `report()` rend une copie indépendante. Ni cette empreinte ni un rapport
reconstitué ne sont une signature ou une capacité d'exécution. Aucune méthode
apply, route HTTP, commande du journal ou connexion SQL n'est ajoutée. L'API 5C1
et ses réponses sont inchangées ; ce catalogue est une frontière indépendante.

| Couple | Classement | Motif de refus d'application |
| --- | --- | --- |
| Historique vers historique | IDENTICAL_RELEASE | Aucune transition de version |
| Stockages externes vers même build | IDENTICAL_RELEASE | Aucune transition de version |
| Historique vers stockages externes | STORAGE_LAYOUT_CHANGE_SAME_SQL_LINEAGE | Sauvegarde du profil source, déplacement, bascule et reprise non qualifiés |
| Stockages externes vers historique | REVERSE_STORAGE_TRANSITION_UNSUPPORTED | Downgrade non catalogué |

Pour chaque couple : `scope=RELEASE_CATALOG_ONLY`, `apply_allowed=false`,
`transition_supported=false`, `source_host_verified=false`,
`target_files_verified=false`, `backup_verified=false`, `maintenance_held=false`,
`migration_verified=false`, `cutover_verified=false`, `rollback_verified=false`,
`phase5c3_complete=false`, `application_installed=false`.

## Vérification indépendante des preuves de source

```bash
python3 scripts/audit_upgrade_catalog.py \
  --source-tree /preuves/source_tree.json \
  --target-tree /preuves/target_tree.json
```

Les deux entrées sont les réponses complètes de Git Trees pour les arbres épinglés.
L'outil fonctionne hors réseau. Il ne télécharge et n'exécute aucun fichier Web.
Il recalcule chaque répertoire, valide le SHA racine, les modes, les effectifs,
les cinq identités SQL/moteur/version et les 113 migrations. Doublons, troncature,
liens, sous-modules, chemins ambigus, parents absents, dérive de contenu ou de
mode sont refusés. Taille d'entrée limitée à 2 Mio, erreurs JSON fermées sans
recopie du contenu. PASS signifie uniquement **métadonnées sources vérifiées**.

## Qualification du lot

26 tests ciblés couvrent la politique des quatre couples, les sélecteurs fermés,
l'indépendance du registre fresh, l'immuabilité du rapport et les preuves Git.
Les fixtures d'arbres sont produites par Git lui-même, indépendamment du vérificateur.
Les 26 identifiants sont ajoutés à la baseline sans retrait des tests antérieurs.
L'audit des deux vrais arbres est conservé séparément des fixtures.

La première exécution locale a révélé une exception JSON non normalisée et une
ambiguïté du helper de test pour une entrée `None` ; les deux sont corrigées,
avec les traces initiales conservées. Les résultats définitifs, commit testé,
campagnes CI et ZIP sont consignés dans le checkpoint et le commentaire #13.
Les preuves 5C2 portent sur leur runtime précédent ; elles ne sont pas présentées
comme une nouvelle exécution de l'ensemble des recettes pour ce catalogue.

## Suite concrète 5C3b

La paire historique vers stockages externes reste une candidate, pas une paire
supportée. La prochaine étape doit qualifier la sauvegarde de ses fichiers
historiques, inventoriés sous une maintenance tenue, puis leur déplacement vers
les racines dédiées avec comparaison des octets et métadonnées. Elle devra lier
précontrôle, sauvegarde restaurée et intention durable avant toute écriture.
La bascule devra publier une enveloppe cohérente code/configuration/reçus,
laisser les services arrêtés jusqu'aux contrôles et qualifier les interruptions.

Ne pas inventer de migration SQL ni de nouveau numéro de version pour rendre
cette paire artificiellement migratoire. Une future paire avec delta SQL exige
ses propres pins, préconditions, ordre explicite et recettes réelles. Les sources
2.x et les hôtes legacy arbitraires ne sont pas admis par ce catalogue.

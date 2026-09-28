# 5D4 — Wizard upgrade du profil géré

Ce lot raccorde au wizard la transition native legacy → stockages externes,
qualifiée en 5C4 puis au journal privé en 5D1. Il conserve le fresh 5D2,
l'activation 5D3, le hero/CSS, les contrôleurs natifs et le code Web.
Les résultats réels du gel et les limites restent consignés au checkpoint/#13.

## Profil fermé

Debian 13, PHP 8.4, Ext4, instance legacy provisionnée et scellée par l'Installer,
base managed locale 127.0.0.1 sans TLS SQL, Apache/FPM et collecteur dédiés avec
preuves natives. Le compte worker PHP reste distinct du compte Web. Le proxy
local de confiance utilise 127.0.0.2 et la déclaration 127.0.0.1/32.

Paire immuable :

- Source : `46c03060625d4d53c675474b11aaa33007d9aad7`.
- Cible : `2a27c7a1f9fe0a00289eb53278f75d5f230900b7`.

Un administrateur local prépare un descripteur JSON dans un répertoire root
0700, fichier root 0600, puis exécute :

```sh
python3 -m installer --state-dir /var/lib/hestia-operator-upgrade --register-managed-upgrade /var/lib/hestia-private/profile.json
```

Le descripteur contient exactement `version: 1`, `http` et `worker` :

| Objet | Champs |
| --- | --- |
| http | instance, root, webroot, service_user, hostname, port, maintenance_directory |
| worker | user, run_root, state_root |

Les champs reprennent les valeurs **réelles** du RuntimeSpec et du PhpRuntime
provisionnés. Aucun mot de passe, version source/cible, commande, UID libre ou
chemin de sauvegarde n'est accepté. La CLI vérifie les configurations natives,
le scellement, les pointeurs, le worker et le profil SQL enregistré en lecture
seule. Elle dérive les choix non secrets et les conserve en 0600 dans le dossier
opérateur ; elle ne migre pas, ne démarre pas et ne contacte pas SQL. Une
inscription différente ou un journal déjà planifié est refusé. Ce mécanisme
n'adopte pas une installation historique arbitraire.

## Parcours

1. Dans la sélection des modules, choisir l'instance gérée enregistrée. Les
   versions et les chemins sont fermés ; seul le digest du profil est transmis.
2. Fournir le mot de passe SQL applicatif et le compte/mot de passe d'autorité
   dédiés. Ces trois secrets restent dans le coffre mémoire et doivent être
   ressaisis après arrêt de l'Installer. Aucun changement d'administrateur ou
   d'assistant n'est proposé.
3. Relire les quatre étapes : acquisition exacte de la source et de la cible,
   réservation du dossier privé de sauvegarde, puis migration native. Confirmer
   explicitement l'arrêt sous maintenance et le verrou SQL global de lecture.
4. À DONE, le Web est migré **sous maintenance**, services arrêtés. SQL,
   administrateurs, assistant et données restent conservés. La sauvegarde SQL
   a été restaurée et vérifiée par le contrôleur ; aucune migration SQL.
5. Préparer puis confirmer séparément l'activation : autorisation native de
   réouverture, PHP-FPM, Apache, timer, page de connexion locale. La disponibilité
   actuelle n'est sondée que sur action explicite et porte son horodatage.

La sauvegarde a un chemin dérivé :
`/var/lib/hst-upgrade-<instance>/backup/upgrade-<lease>`. Le dossier parent est
créé exclusivement, avec une preuve privée liée au journal hors de la sauvegarde.
Un dossier partiel sans preuve n'est pas adopté. Les contrôles natifs imposent
le même système de fichiers et la séparation des ressources protégées.

## Reprise et retour arrière

GET, report, restauration du registre et consultation après SIGKILL restent en
lecture seule, même lorsque le webroot est temporairement déplacé. La totalité
du plan est comparée au registre, pas seulement les étapes connues. Une reprise
explicite conserve les DONE ; les contrôleurs reconnaissent leurs preuves
natives et n'exécutent une récupération qu'après le checkpoint opérateur.

Le journal d'activation est séparé dans `activation/state.json`. Son digest lie
le plan parent et l'autorisation retrouve **l'installation du parent**, jamais
celle du nouveau journal. Une réponse perdue après autorisation est reconnue
sans nouvelles écritures SQL ni deuxième réouverture.

Avant autorisation d'activation, le wizard permet le rollback de la frontière
storage après ressaisie des trois identifiants. Le Web source revient sous
maintenance, sans restauration SQL ni suppression de données. Sa réouverture
après rollback reste hors de ce wizard et exige une évaluation distincte.
Une activation déjà approuvée bloque le rollback dans la façade ; l'intention
native durable le bloque également, y compris après réponse perdue. Une simple
prévisualisation du plan d'activation ne retire pas le rollback.

Après retour arrière, la frontière storage est ROLLED_BACK et le wizard affiche
explicitement la version source sous maintenance, sans bouton pour rejouer cette
frontière. Le journal global reste PLANNED car les acquisitions et le dossier de
sauvegarde restent DONE : ce contrat historique du moteur n'est pas modifié.

## Qualification et reste à faire

Treize nouveaux contrats sans SQL/comptes/services et six scénarios CI jetables :
parcours navigateur complet avec démarrage produit et connexion TLS réelle de
fixture ; réponse migration perdue ; coupure après déplacement source et reprise
avec nouveaux secrets ; rollback navigateur ; réponse autorisation perdue ;
refus d'une preuve runtime altérée. Les matrices historiques ne sont pas
rejouées pour reprendre le contexte. Quality générales sur le nouveau gel.

Le frontal TLS utilisé pour la connexion métier en recette appartient à la
fixture. Cela ne qualifie ni un frontal public livré par le produit, ni le boot,
ni un serveur vierge, ni une adoption libre d'installation existante. La phase 5
reste ouverte. Aucun serveur utilisateur n'est utilisé pour les tests système.

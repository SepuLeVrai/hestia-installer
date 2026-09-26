# Arrêt coordonné HTTP et collecteur dédié

## Base, portée et coût de qualification

Base de travail `8de010e1d64416f1ffef682b9479bdda130700eb`, arbre
`df3d4333065043e22bad17c96238c469631ac640`, 185 fichiers. Sa validation
ciblée `36221268362` a passé 64 tests de contrôleurs et 41 scénarios système
dans un seul job Debian 13. Ce n'est pas une qualification globale. La dernière
base globalement qualifiée reste `3a5e2a44c411895653bd6a0b173c7b575988183d` ;
les références de ses campagnes figurent dans [le lot HTTP](PHASE5_HTTP_DRAIN.md).

Ce chantier raccorde uniquement le collecteur existant et son timer à HttpDrain.
Le Web reste `2a27c7a1f9fe0a00289eb53278f75d5f230900b7`. Aucun démarrage,
activation, écran, producteur CLI, transition SQL ou promotion n'est ajouté.
L'inventaire des neuf groupes de producteurs reste incomplet.

## Composition fermée

`HttpDrain(runtime, cleaner=collector)` exige un `SessionCleaner` exact associé
au même objet `HttpRuntime`. L'instance, le gate, l'UID/GID, les fichiers et les
plans proviennent du provisionnement vérifié. Le profil durable lie les trois
fragments, le plan du collecteur et le timer exact. Un ancien reçu à deux rôles
ne peut pas être adopté comme reçu de cette composition à trois rôles.

Le contrôle privé `SessionCleaner._inspect_configuration` vérifie les octets,
modes, dépendances, journal et lease du staging d'origine. Il permet de vérifier
la configuration après activation ; il ne constitue pas une observation d'état.
`observe()` conserve ses exigences initiales : gate original, HTTP et collecteur
inactifs, timer arrêté. Les reçus de staging ne deviennent pas permissifs.

Le collecteur oneshot réellement en cours peut avoir un job d'activation
systemd. Seul ce contrôle explicite accepte un Job numérique positif avec
Type=oneshot, ActiveState=activating et SubState=start. Les autres propriétés,
fragments, drop-in et cgroups restent vérifiés. L'adaptateur SystemDrain à
quatre rôles et la preuve finale d'arrêt exigent toujours Job vide.

## Acquisition, arrêt et reprise

1. Vérifier la configuration HTTP/collecteur, les trois unités, le timer et
   le recensement des threads de l'identité dans ces trois cgroups seulement.
2. Publier le gate commun durable et attendre le verrou exclusif de maintenance.
   Le worker détient lui aussi ce verrou pendant sa collecte bornée. Le gate
   bloque les nouvelles admissions et est revu avant toute suppression.
3. Écrire et synchroniser la tentative liée au profil exact et à la lease.
4. Arrêter le timer exact, avec commande fixe et délai de cinq secondes, puis
   exiger inactive/dead, Job vide et configuration chargée sans dérive.
5. Arrêter Apache, PHP puis le collecteur. Vérifier chaque arrêt immédiatement,
   y compris le cgroup récursif vide, avant de poursuivre.
6. Recontrôler l'ensemble et l'absence de threads de l'identité dédiée.

Après arrêt du timer, chaque contrôle intermédiaire l'exige encore arrêté.
Chaque `report()` et accès à `maintenance_lease` répète la preuve finale.
Réarmer le timer, changer un fichier ou créer un producteur étranger invalide
la preuve. Le timer n'est ni désactivé ni supprimé ; aucune remise en route
n'est livrée. Fermeture, échec ou mort du contrôleur laissent le gate durable.
La reprise exige la même lease et le même profil. Aucun PID observé n'est tué.

Le résultat `PROVISIONED_HTTP_AND_CLEANER_DRAINED` indique trois services et
un timer arrêté. Le profil HTTP seul reste disponible avec ses deux services.
Le recensement reste ponctuel, dans le namespace PID de systemd, et ne bloque
pas root ou de futurs planificateurs. Un CLI, convertisseur ou NGINX hors de
ces cgroups utilisant la même identité est encore refusé. Cette preuve ne
constitue pas une barrière contre les autres clients SQL.

## Sessions et preuves ciblées

Les octets du worker, de l'unité et du timer générés restent inchangés :
43200 secondes de rétention, 10000 entrées et deux secondes par scan,
OnBootSec=5min et OnUnitInactiveSec=30min. Le nettoyage Debian natif et les
choix fonctionnels de session 1 h/4 h/8 h sont conservés. Les fichiers de
sessions ne sont pas lus ou supprimés par HttpDrain.

Dix nouveaux tests core portent la détection complète à 650 tests sans retrait.
La sélection affectée comprend 74 tests locaux, puis les mêmes dans le conteneur.
Les six nouvelles recettes système exercent : timer original et trois arrêts
avec conservation des sessions ; worker réel tenant le verrou pendant la
publication du gate ; réarmement du timer ; dérive avant toute action ; SIGKILL
du contrôleur après arrêt du timer et reprise ; producteur étranger non adopté.
Le test d'attente suspend brièvement le vrai worker du banc après observation
de son verrou, sans remplacer son programme ni modifier ses limites.

Pour limiter les coûts, une seule campagne ciblée, un seul job Debian 13 de
dix minutes maximum : 18 HTTP, 11 cgroups et 18 collecteur, soit 47 scénarios.
Le conteneur officiel est jetable et sans réseau pendant les recettes. Les
trois manifestes doivent correspondre aux sources et modes gelés. Résultats,
empreintes et incidents éventuels figurent dans le checkpoint compagnon.

Debian 12, Quality globale, paquets, navigateur, SQL/proxy et Web complet sont
différés. Cette sélection ne requalifie pas le parcours Web métier ni la
politique fonctionnelle de login. Aucune promotion avant toutes les Quality
requises sur l'arbre futur exact. Tous les indicateurs d'activation, d'inventaire
complet, de sauvegarde exhaustive et de fin de phase restent faux.

Après preuves et checkpoint : arrêt pour redémarrage. Prochain chantier borné :
contrat et inventaire des producteurs CLI et planifiés, sans inventer une unité
CLI universelle. Les autres clients SQL, 5C2/5C3/5C4 et le wizard restent séparés.

# Maintenance coordonnée Web - contrat privé

## Raccordement du profil métier externe

Le [profil externe](PHASE5_BUSINESS_STORAGE.md) utilise le même scope
`<slot de configuration>/maintenance` pour FPM, les conditions des deux services,
le collecteur et la sauvegarde coordonnée. Il vérifie l'instance finalisée avant
création. Le guard reste postérieur à la réception multipart. La recette arrête
les services et vérifie leurs cgroups avant capture ; ce drainage de banc ne
vaut pas orchestration produit ni inventaire exhaustif des producteurs.

## Candidat, pas encore un déploiement système

`installer/maintenance.py` et le prepend PHP définissent une barrière commune
pour les requêtes Web et les autres producteurs déclarés. Le branchement dans
Apache/PHP-FPM, les tâches planifiées et le journal système appartient à 5D.
Créer ces fichiers ou obtenir un verrou ne prouve pas qu'un serveur arbitraire
les utilise. Toute opération qui exige la quiescence doit être réservée à un
déploiement dont tous les producteurs sont effectivement raccordés.

## Protocole

L'orchestrateur crée exclusivement une portée privée root:web 0750, un verrou
root:web 0640 et un prepend PHP root:web 0640. L'identité Web peut prendre un
verrou partagé en lecture, mais ne peut ni remplacer l'inode ni supprimer la
barrière. Le profil lie le code exact du prepend, le groupe et l'identité stable
de l'instance. Liens, hardlinks, ACL et profils de permissions inconnus échouent.

Chaque requête PHP prend le verrou partagé et le garde jusqu'à la fin réelle
de la requête, callbacks de shutdown et écriture de session inclus. Le prepend
contrôle la présence de la barrière avant et après la prise du verrou. Lorsqu'une
maintenance est annoncée, la réponse est 503, Retry-After et no-store, sans
chargement de l'application. Les producteurs non HTTP utilisent `writer()`.

Le contrôleur publie et fsync `maintenance.attempt` avant d'attendre le verrou
exclusif. Il laisse finir les requêtes déjà actives dans une attente bornée.
Un timeout, une exception, une perte de réponse ou un SIGKILL conserve cette
barrière sur disque. La fermeture d'un contexte ne rouvre jamais le service.
Une nouvelle tentative aveugle est refusée. `recover` exige l'identifiant exact
de la barrière et une confirmation, reprend le verrou, mais n'exécute aucun SQL.

`resume` est une frontière explicite et confirmée. Le reçu ACTIVITY_RESUMED est
fsync avant suppression de la barrière puis libération du verrou. Une politique
de rollback doit refuser une restauration ancienne après cette frontière.
Une lease fermée ou héritée dans un autre processus n'autorise aucune mutation.

## Frontière de confiance

L'API est privée à l'orchestrateur, sans endpoint ou commande libre. Le frontend
ne fournit jamais un chemin de prepend, de verrou, d'exécutable ou une lease.
Une requête SQL directe, un cron tiers ou un accès partagé qui contourne cette
barrière n'est pas couvert. Le futur adaptateur système doit établir la liste
fermée des producteurs et refuser une installation dont il ne maîtrise pas les
écritures. L'autorité système reste capable d'intervenir et doit respecter la
maintenance annoncée. Aucun mécanisme n'est présenté comme une exclusion de root.

## Recette à qualifier

Sept scénarios réels : login/session conservée après reprise, requête PHP en
cours et drainage borné, décès du contrôleur et 503 persistant, producteur non
HTTP, permissions/liens/dérive, consentement/annulation, lease périmée ou héritée.
Le banc injecte le même prepend dans un vrai serveur PHP sous l'identité Web.
Cela ne remplace pas la recette Apache/PHP-FPM/systemd Debian12/13 de 5D.

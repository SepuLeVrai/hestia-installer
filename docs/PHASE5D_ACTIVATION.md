# Phase 5D3 - Activation explicite et disponibilité locale

Ce lot prolonge la [préparation 5D2](PHASE5D_WIZARD_COMPOSITION.md) terminée sur
Debian 13 préparé. Les dix StepSpecs acquis, les empreintes approuvées, les
protocoles SQL et les sources Web restent inchangés. Les campagnes dédiées
5C4 et 5D1 restent acquises sans nouvelle exécution pour récupérer leur contexte.

## Plan et consentement distincts

Le wizard propose un plan d'activation seulement après une préparation DONE.
`activation/state.json`, privé en 0600, contient cinq étapes : sortie de
maintenance, démarrage du PHP-FPM dédié, de l'Apache dédié, du timer de collecte
des sessions, puis requête locale de disponibilité. Chaque StepSpec est lié au
SHA-256 de préparation. Registre tronqué, configuration modifiée et parent
incomplet sont refusés. Le verrou de préparation sérialise la création/exécution
du second journal, qui possède son propre verrou. Planifier ne démarre rien.

Les POST `/api/web/activation/plan`, `apply`, `resume`, `retry` et `check` utilisent
HTTPS/session/Origin/CSRF. Aucun chemin, unité, URL ou commande n'est fourni par
le navigateur. L'exécution exige le digest d'activation et une confirmation
explicite. Le rapport inclut les deux journaux lorsque l'activation existe.
La CLI historique concerne toujours la préparation ; le second journal se
reprend depuis le wizard dans ce lot.

## Effets et reprise

Le moteur persiste son checkpoint puis le contrôleur écrit une intention privée
exclusive liée à l'installation, au StepSpec et au parent. Sources, configuration,
unités, drop-ins, dépendances système, comptes et sceaux sont relus par les
contrôles natifs acquis. La reprise observe uniquement : le reçu natif reconnaît
l'admission déjà ouverte ; l'unité et son cgroup reconnaissent un service déjà
démarré, sans second start. Le PID principal appartient à l'unité exacte.

Les drop-ins acquis interdisent le démarrage pendant la maintenance. L'ordre
est donc sortie de maintenance puis démarrages ; il ne constitue pas une bascule
atomique. Dès la sortie, l'activité peut écrire. Aucun rollback SQL, arrêt,
redémarrage, fermeture d'admission ou réparation implicite n'est tenté. Une
intention sans effet reconnaissable, une dérive ou une nouvelle maintenance
reste manuelle. Une unité morte après DONE n'est pas relancée par consultation
ou reprise d'une autre étape. Le produit utilise start, jamais enable.

## Disponibilité actuelle séparée

La vérification émet un GET `/login.php` vers `127.0.0.1:9080` depuis le proxy
loopback fixé `127.0.0.2`, avec les en-têtes du contrat ingress acquis. Pas de
DNS réseau, redirection suivie, URL libre, cookie conservé ou mot de passe transmis.
Le résultat exige trois unités actives et une réponse HTML 200 avec formulaire
CSRF, de taille bornée. Une requête PHP peut créer une session : aucun GET du
wizard ni observateur de reprise ne l'effectue. Le bouton « Vérifier maintenant »
renouvelle explicitement la preuve et affiche son heure. Elle reste en mémoire,
perdue au redémarrage du contrôleur. Un échec actuel ne modifie pas le journal
DONE. Ce n'est pas une surveillance continue, un login administrateur ou une
preuve de disponibilité du frontal TLS public.

## Qualification et suite

Les contrats locaux utilisent seulement des fichiers et des doubles des effets
système. La recette `application_activation_systemd.py` utilise une CI jetable
avec vrais navigateur, SQL, Apache/PHP-FPM, systemd, Ext4 et sources épinglées.
Huit scénarios nouveaux : activation navigateur suivie d'un login administrateur
via TLS de fixture (backend démarré par le produit), trois réponses perdues,
intention avant start, dérive d'unité, indisponibilité après DONE et nouvelle
maintenance après interruption. Les pins et résultats mesurés sont au checkpoint.

Restent le frontal public/TLS, la persistance au boot, le wizard upgrade et le
serveur vierge (dépendances/MariaDB). `application_installed` et `phase5_complete`
restent faux. Phase 5 et #13 restent ouverts.

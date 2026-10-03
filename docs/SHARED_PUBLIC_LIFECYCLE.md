# 6B11 - cycle interne du frontal commun Web/Mobile

Base : 6B10 `14d7a8ca26e8c7f7a5c4f07acc5848fd452fd15f`. Le pré-plan reste
historique et sans autorisation d'exécuter. Le nouveau contrôleur interne
`SharedPublicLifecycle` produit son propre registre et exige sa confirmation.
Il n'est pas encore exposé dans le cockpit. Phase 6 ouverte.

## Admission et ressources

Le contrôleur lie la préparation 6B10, les journaux parents, la Gateway MAIN
terminée, sa configuration native reconstruite et toutes les sources exécutables
du nouveau bundle. Une autre sélection, un autre jeu de sources ou un parent
modifié invalide l'exécution. Le verrou du plan Web reste tenu ; le verrou partagé
d'activité empêche un drainage concurrent d'aboutir pendant le transfert.
Les observations de la Gateway vérifient son identité, son binaire, sa configuration,
son état privé et son listener local. Les entrées ne permettent aucun chemin,
commande, hook ou autorité ACME libre. Les lectures de statut n'observent pas
l'hôte et ne revendiquent aucune disponibilité courante.

Le parent conserve son profil, son code privé, son worker, ses configurations
NGINX, son include Apache, ses reçus et son magasin de certificat Web. Le successeur
copie les quatre fragments originaux et l'overlay Apache avant toute substitution.
Il conserve les noms des unités et les trois liens d'activation existants.
Les quatre fragments et le seul overlay `60-hestia-public.conf` sont remplacés
après comparaison des octets attendus. Le fragment Apache de base et les unités,
liens et sources boot ne sont pas modifiés.

Le nouveau garde Apache conserve exactement la commande Apache et son include.
Il vérifie le bundle successeur, les originaux conservés, les unités exactes, les
liens, les permissions et la maintenance. Le lecteur courant d'overlay admet ce
successeur uniquement après une intention et un reçu de propriété liés au profil.
Une transition endommagée ne retombe pas sur le garde ancien. L'ancien bundle
reste figé et son worker refuse normalement les fragments successeurs.

## Ordre des effets

1. Enrôler le code privé, les deux jeux de configurations et les copies originales.
2. Arrêter le timer, vérifier le renouvellement inactif, arrêter HTTPS puis HTTP,
   substituer les cinq fichiers attendus, recharger systemd, enregistrer la propriété
   puis démarrer HTTP et HTTPS. Le Web est servi ; Mobile ne dispose encore que
   du challenge HTTP-01 et répond 503 hors challenge.
3. Obtenir le certificat Mobile dans son magasin distinct par la commande Certbot
   fermée, puis vérifier sa configuration, sa chaîne, son domaine et sa clé.
4. Tester son renouvellement sur l'autorité de staging.
5. Revérifier les certificats et la Gateway, arrêter les deux listeners, enregistrer
   l'état prêt puis démarrer les configurations Web/Mobile composées.
6. Activer le timer commun, hors du verrou d'effet pour permettre une invocation
   immédiate de son calendrier persistant.
7. Vérifier les reçus, les services, les certificats et le raccordement signé MAIN.

Les deux bascules interrompent brièvement les listeners. Apache n'est pas arrêté
ou redémarré. Le timer est suspendu entre le transfert et son activation finale.
Une erreur de certificat pendant cette période nécessite une intervention avant
que le renouvellement Web puisse reprendre. Aucun état partiel n'est présenté
comme une installation publique Mobile réussie.

Les workers HTTP/HTTPS vérifient leur bundle avant de remplacer leur processus
par NGINX dans le même cgroup. Les chemins des configurations sont fixes et
sélectionnés uniquement par les reçus privés. Un début de publication sans son
reçu prêt interdit les démarrages. Le listener HTTP ne dépend pas du boot Web ;
HTTPS conserve les dépendances Web précédentes. Cela n'enrôle pas le boot Gateway.

## Renouvellement et reprise

Le worker de renouvellement utilise le verrou d'effet du successeur et les deux
commandes Certbot isolées. Il accepte des feuilles expirées avant renouvellement,
mais exige des certificats valides ensuite. Une erreur de commande Web n'empêche
pas de tenter le renouvellement Mobile. Toute erreur empêche le HUP. Un HUP unique
n'est envoyé qu'après vérification des deux magasins et de la configuration NGINX.
Un HTTPS arrêté n'est jamais démarré par le renouvellement.

Les intentions précèdent les effets. Un reçu complet dont la réponse a été perdue
est reconnu par observation, sans second démarrage ni nouvelle émission. Une
intention incomplète, une copie partielle ou une dérive reste manuelle ; la reprise
ne remplace, n'efface et ne rejoue rien automatiquement. Les étapes terminées sont
revérifiées avant que le moteur poursuive. Il n'existe pas de rollback automatique
qui réouvrirait un frontal ou restaurerait un ancien certificat.

## Qualification et limites

41 nouveaux contrats obligatoires couvrent les profils fermés, les sources figées,
les ressources et copies, le consentement distinct, l'ordre des effets, les refus
avant publication, les reprises, les permissions et les deux renouvellements.
Les 30 contrats 6B10 et les 10 contrats de composition restent conservés.

La recette additionnelle Debian 13 s'exécute uniquement dans le conteneur jetable
avec systemd PID 1 et réseau coupé. Elle utilise le code privé exact du parent
6B10, de vrais workers figés, unités, comptes, cgroups, NGINX et Certbot. Dix tests
couvrent le refus du worker historique après transfert, la conservation des
originaux, les deux domaines, les deux certificats tournés puis rechargés par HUP,
les drop-ins étrangers, la réponse perdue, la propriété partielle, le lecteur
d'overlay et les redémarrages des unités publiques.

Ses dépendances Apache/boot/Gateway sont des fixtures déclarées. L'émission et le
dry-run ACME sont interceptés et fournissent des certificats signés par une CA
locale jetable. Le renouvellement passe réellement par Certbot sur deux lignées
non échues. Ce scénario ne qualifie ni l'émission ACME publique, ni le démarrage
complet Web/Gateway, ni un véritable login applicatif après reboot. Les résultats
sont à rattacher au commit exact dans le checkpoint ; aucune qualification n'est
acquise par cette description seule.

Suite : recette composée avec Web/Gateway réels et ACME privé, reprise après
interruptions aux frontières natives, puis raccordement du cockpit. Le boot
Mobile, DEV/FCM, les APK et 6C restent ouverts. Aucun SQL, `schema.sql` ou
`install.php` modifié. Aucune promotion main/dev/dev-Bastien dans ce lot.

## Correction de la première fixture

Le premier gel `413c3de51d9829ee94f420aca2085c933a033afe` a échoué dans la
nouvelle recette système avant les effets du transfert : l'argument Docker
Debian, déclaré avant `FROM`, n'était pas disponible dans l'étape d'installation
conditionnelle de Certbot. L'image ne contenait donc pas `certbot.timer`.
L'argument est redéclaré dans le stage. Le nouveau scénario passe en début de
campagne pour rendre ses échecs rapides à diagnostiquer. Aucun garde produit ni
assertion n'est retiré ; une nouvelle qualification du gel corrigé est requise.

Le deuxième gel `9e7ef55e5b3c2f7b78d860392d30f09d409fea6b` a atteint l'enrôlement
et refusé la configuration composée de 63 routes (environ 95 Ko), trop grande
pour le helper historique de 16 Ko. Le successeur dispose maintenant d'une
écriture privée exclusive et d'une lecture bornées à 256 Ko, sans modifier la
limite des helpers antérieurs. Trois contrats sur les vrais fichiers couvrent
le round-trip, la borne, les chemins, les liens et les écritures partielles.

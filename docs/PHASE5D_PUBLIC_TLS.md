# Phase 5D — frontal HTTPS et fin du parcours serveur

Ce dernier plan fresh suit les journaux acquis de préparation, SQL, activation,
boot 5D6 et dépendances ACME 5D7a. Il ne réécrit aucun de ces plans, reçu, unité
native ou bundle de démarrage. Le statut DONE décrit une configuration acquise ;
la disponibilité actuelle reste une vérification explicite et datée.

## Périmètre

Debian 13, PHP 8.4, Web `2a27c7a1f9fe0a00289eb53278f75d5f230900b7`,
Apache local sur 9080, frontal NGINX IPv4 sur 80/443. Le domaine doit avoir des
adresses A dirigées vers ce serveur et aucun AAAA. Les services déjà présents sur
80/443 sont refusés, jamais arrêtés ni adoptés. Le profil permet l'accès public
IPv4 ou une liste fermée de CIDR IPv4. L'accès local de contrôle reste autorisé.

Le réseau complet (IPv6, pare-feu, frontal externe, domaine Mobile distinct)
reste dans la phase réseau dédiée. Les scénarios upgrade acquis conservent leur
périmètre scellé ; ce plan public ne prétend pas adopter un frontal legacy.

## Confirmation et effets

Le wizard demande l'adresse ACME et l'accès souhaité, montre le plan lié au
domaine, puis demande une confirmation distincte incluant les conditions de
Let's Encrypt. Neuf étapes : identité isolée, préparation, HTTP-01, certificat,
test de renouvellement, bascule Apache, HTTPS, boot/timer, contrôle final.

Le backend conserve son identité, ses données, ses unités, son Apache et son
PHP-FPM. Un seul drop-in Apache additionnel fermé charge la nouvelle autorisation
après la configuration native. La bascule prend le verrou de maintenance,
arrête Apache, valide la configuration puis confirme explicitement la reprise.
L'audit systemd et les barrières de maintenance/sauvegarde vérifient le profil,
le bundle et le reçu de cet overlay. Le frontal utilise un autre UID/GID sans
accès aux données Web. Les adresses et en-têtes canoniques sont transmis une
seule fois, conformément au contrat ProxyIngress acquis.

Le challenge HTTP-01 reste accessible hors allowlist et pendant la maintenance.
Les autres demandes HTTP sont redirigées vers le domaine HTTPS canonique.
Certbot utilise des chemins privés dédiés, un fichier de configuration fermé et
les autorités fixes production/staging de Let's Encrypt. Aucun hook opérateur,
chemin arbitraire, commande libre ou serveur ACME fourni par le navigateur.
Le certificat est contrôlé avec la chaîne de confiance système, le nom DNS,
l'échéance, les liens de lineage, la correspondance clé/certificat et fullchain.

Un timer dédié tente le renouvellement deux fois par jour avec délai aléatoire
et rattrapage. Le service valide les fichiers avant Certbot, puis le certificat
et NGINX avant d'envoyer HUP à l'unité HTTPS exacte. Le timer global Certbot et
NGINX global restent masqués. Une maintenance ne rouvre pas le backend au boot.
Après une longue interruption, l'expiration n'empêche pas Certbot de renouveler
un ancien certificat dont les autres contrôles restent valides. Le nouveau
certificat doit passer tous les contrôles avant rechargement. Un frontal déjà
arrêté reste arrêté ; sa remise en service demeure une action explicite.
Une preuve d'activation absente bloque les workers ; aucun worker ne retire une
maintenance, ne modifie SQL ni ne réinstalle des paquets.

## Reprise et exploitation

Chaque effet possède une intention privée durable et un reçu lié au plan. Une
réponse perdue après reçu complet est relue sans effet ; une émission ACME, une
bascule ou une activation incomplète exige une inspection manuelle. Aucun rejeu
aveugle d'émission, d'upgrade SQL ou de réouverture de maintenance.

Le rapport `public_tls.phase5_complete` devient vrai lorsque les neuf étapes
sont DONE. Il reste historique si une vérification ultérieure échoue ; le champ
`availability` rend alors cet échec explicite. Les GET et `--report` ne sondent
pas le réseau et ne démarrent aucun service. La vérification HTTPS du produit
contrôle la page de connexion, sans utiliser le mot de passe administrateur.

## Qualification

Les tests locaux restent limités aux fichiers, rendus et effets simulés. La
recette `public_tls_systemd.py` est exclusivement opt-in dans un serveur Debian
jetable avec PID 1 systemd, SQL réel, Ext4 et le bundle boot 5D6 d'origine.
Elle exécute la confirmation Chromium, la perte de réponse du certificat, HTTPS,
le login administrateur, le filtrage des chemins/en-têtes, une vraie seconde
émission ACME forcée par la fixture, puis le service de renouvellement installé
et son rechargement. Elle vérifie aussi le nouveau PID 1, la sauvegarde native
avec le backend public et le maintien de la maintenance après redémarrage.

L'autorité de cette recette est Pebble 2.10.1, dans un réseau Docker privé sans
sortie Internet. DNS et certificats de confiance sont propres au banc ; les URL
et commandes du produit sont inchangées. Cela qualifie le protocole HTTP-01 et
le cycle Certbot, **pas une émission par l'autorité publique Let's Encrypt**.
Les clés privées, comptes ACME et dumps SQL ne sont jamais livrés en artefacts.
Les preuves de qualification et le gel exact sont consignés dans l'issue #13.

Références : [Apache -c](https://httpd.apache.org/docs/2.4/programs/httpd.html),
[AuthMerging](https://httpd.apache.org/docs/2.4/mod/mod_authz_core.html#authmerging),
[Certbot](https://eff-certbot.readthedocs.io/en/stable/using.html),
[Pebble](https://github.com/letsencrypt/pebble/tree/v2.10.1).

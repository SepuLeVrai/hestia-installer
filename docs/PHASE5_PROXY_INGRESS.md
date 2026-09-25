# Phase 5 — interface du backend avec un frontal TLS

Base : checkpoint paquets `15057f62b732bbb46be018fe254ce26f3bfda010`,
Quality `36160960174`, système `36160960191`, paquets `36160960155`,
SQL/HTTP `36161088874`. Web inchangé et épinglé
`46c03060625d4d53c675474b11aaa33007d9aad7`.

## Contrat livré

`RuntimeSpec.ingress` accepte un `ProxyIngress` immuable explicite. La préparation
existante écrit ses fichiers exclusifs, vérifie Apache/FPM et les dépendances,
puis garde les deux services inactifs sous maintenance. Aucun nouveau chemin
HTTP public, aucune commande de démarrage et aucune modification d'un vhost
existant ne sont ajoutés. L'absence de profil conserve exactement le contrat
de staging historique ; elle ne constitue pas une exposition Web qualifiée.

Deux entrées indépendantes sont obligatoires : une adresse IPv4 loopback du
proxy, distincte de `127.0.0.1`, et une liste non vide de réseaux clients CIDR
canoniques IPv4/IPv6. Aucun réseau client ne devient implicitement un proxy.
Le backend reste `127.0.0.1:port_sélectionné`.

Apache `mod_remoteip` ne consomme `X-Forwarded-For` que depuis ce proxy exact.
L'autorisation exige simultanément le pair de connexion déclaré, un client
résolu distinct du pair, un client dans l'allowlist, le Host configuré et le
protocole transmis exactement `https`. Une enveloppe manquante ou invalide est
refusée. Les modules supplémentaires font partie du plan et de ses empreintes.
Un changement de proxy ou de réseaux invalide l'observation du staging.

Après l'autorisation, Apache retire les variables HTTP de forwarding à la
frontière FastCGI (sans priver les redirections internes DirectoryIndex de leurs
entrées d'autorisation) et fournit
`REMOTE_ADDR` canonique et `HTTPS=on` à FPM. La liste de proxies côté PHP est
vide : `clear_env=yes` et aucune déclaration FPM de cette variable (FPM refuse
une valeur `env[...]` vide). Il ne doit pas y avoir une seconde interprétation de la chaîne. Le
Bearer Authorization et les règles d'accès aux fichiers privés sont préservés.

`ProxyIngress.nginx_server(...)` rend un bloc serveur déterministe, sans aucune
écriture ni exécution. Il utilise le port backend choisi, le port TLS choisi,
les chemins de certificat/clé explicites et `proxy_bind` sur le pair déclaré.
Il remplace les valeurs client par `$remote_addr` et `https`, supprime les
autres en-têtes de forwarding, applique aussi l'allowlist et refuse un Host
étranger. TLS 1.2 et 1.3 uniquement. Le frontal IPv4 peut écouter l'adresse
choisie ; une écoute IPv6 du frontal n'est pas livrée par ce profil.

## Frontière de confiance et intégration

La confiance porte sur le serveur local administré et le proxy déclaré. Une
adresse source loopback n'authentifie pas un processus : un autre processus
local capable de choisir cette même adresse pourrait imiter ce proxy. Ce
profil ne prétend pas isoler des utilisateurs locaux hostiles ; une installation
multi-tenant demanderait une isolation réseau/processus supplémentaire.

Le bloc est destiné à un frontal dédié sans `real_ip`, réécriture de headers ou
includes hérités étrangers au contrat. Son intégrateur doit contrôler le contexte
global, les permissions et la chaîne des certificats, exécuter `nginx -t` avant
activation et assurer la récupération de son propre service. Le rendu seul ne
prouve aucun de ces points. Le module global NGINX/ACME, émission/renouvellement
de certificat et adoption/reload d'un frontal existant restent hors de ce lot.
Les deux fichiers de configuration Apache/FPM sont exclusifs, jamais écrasés.
Une interruption ou dérive suit le refus/reprise en lecture déjà documenté
dans [le runtime](PHASE5_HTTP_RUNTIME.md), sans redémarrage automatique.

Exemple privé : `ProxyIngress('127.0.0.2', ('192.0.2.0/24',))`, injecté dans
`RuntimeSpec` avant `HttpRuntime.create(confirmed=True)`. La préparation reste
sous maintenance. Ne pas retirer celle-ci pour une application tant que les
prérequis de stockage, finalisation, collecteur et orchestration ne sont pas
vérifiés. Les démarrages dans la recette sont propres aux fixtures jetables.

## Qualification requise sur les sources exactes

608 core par Debian 12/13, 16 DOM et 21 HTTPS historiques ; 47 recettes système
historiques et 14 nouvelles recettes proxy par Debian ; 13 recettes paquets par
Debian ; 118 SQL/HTTP historiques sur le Web épinglé. Une recette supplémentaire
de 14 cas sur Debian 13 charge le véritable `includes/security.php` épinglé
(SHA-256 `2abfe1185b81318c1e14b3a329eee78d0d0b69b30c7fdfb7cd4e703269690347`).

La recette démarre un vrai NGINX/Apache/FPM dans un conteneur systemd sans réseau
externe ni port publié. Son certificat auto-signé éphémère est explicitement
approuvé par le client du test ; un client sans cette confiance échoue. Elle
exerce TLS 1.2/1.3, cookies Secure/HttpOnly/SameSite, HTTPS et IP canonique, Bearer,
usurpation de headers, client/pair interdits, Host étranger, enveloppes invalides,
fichiers privés, maintenance et écoute loopback. Le helper Web ajoute les
assertions HSTS/IP/HTTPS réelles. Les fichiers globaux restent inchangés.

Ces tests d'interface ne sont pas une connexion Admin/Dashboard/GED complète.
Debian 12/PHP 8.2 reste incompatible avec le PHP minimal de l'application.
`application_installed`, `system_wiring_verified`, `complete_web_backup` et
`service_activation_delivered` restent faux. L'intermittence DOM historique
n'est pas déclarée résolue. Les résultats finaux et éventuels échecs se trouvent
dans le checkpoint après gel documentaire ; aucune PR/promotion avant tous
les contrôles requis verts. Phase 5 reste ouverte.

## Sources techniques

- [Apache mod_remoteip](https://httpd.apache.org/docs/2.4/mod/mod_remoteip.html) :
  adresse client pour Require ip, pair original CONN_REMOTE_ADDR, proxy interne.
- [Apache mod_proxy_fcgi](https://httpd.apache.org/docs/2.4/mod/mod_proxy_fcgi.html) :
  environnement FastCGI explicite.
- [NGINX proxy](https://nginx.org/en/docs/http/ngx_http_proxy_module.html) et
  [contrôle d'accès](https://nginx.org/en/docs/http/ngx_http_access_module.html).

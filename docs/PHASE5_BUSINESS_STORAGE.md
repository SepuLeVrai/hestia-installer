# Phase 5 : stockages métier externes sous maintenance commune

## Sources et portée du lot

Base Installer qualifiée `357d7164af7161aaa113dc50ca33d44dd46ba873`.
Le Web historique `46c03060625d4d53c675474b11aaa33007d9aad7` reste disponible.
Le nouveau Web `2a27c7a1f9fe0a00289eb53278f75d5f230900b7` a l'arbre
`783be5abdcd5e13addefe96d743eee3a97b7a6de`, 1843 fichiers et le digest runtime
`42c99a13f41b50a5263c69d14557dd088b5787b40ffdc51873b8d61ea1bc8edb`.
Sa Quality `36195113348` est verte (PHP 8.3/8.4, MariaDB fresh/replay/upgrade,
Apache interne). Aucun schéma ni marqueur de version n'a changé dans ce lot.

Le catalogue fermé accepte chaque identité explicitement et propage son digest
de finalisation jusqu'à la sauvegarde. Reconnaître deux sources n'autorise pas
leur transition. `UpgradePreflight` reste `IDENTICAL_RELEASE`, apply interdit.
Les adaptateurs SQL historiques sont inchangés et vérifient leurs moteurs par
empreinte indépendamment du catalogue runtime.

## Contrat des chemins et propriétaires

| Ressource | Propriétaire / mode | Accès |
| --- | --- | --- |
| Web et son `uploads` historique | root, non inscriptibles | Code scellé, `.htaccess` compris |
| `<runtime>/data` | root:groupe dédié 0750 | Parent protégé |
| `sessions`, `tmp`, `upload-tmp`, `imports`, `log`, `uploads` sous `data` | UID/GID dédiés 0700 | Racines créées exclusivement |
| `<slot configuration>/maintenance` | root:groupe dédié 0750 | Gate commun, fichiers protégés |
| GED, photos, imports créés par PHP | UID/GID dédiés, permissions propres au helper avec UMask 0077 | Données métier, jamais code activable |

`RuntimeSpec.external_uploads=True` requiert simultanément le chemin du gate,
Debian 13 et PHP officiel 8.4. Le gate est hors webroot et hors runtime, sous
`/var/lib`, se termine par `maintenance` et appartient au slot finalisé de la
même instance et du nouveau pin. Une racine runtime, une unité, un drop-in ou
un gate préexistant est refusé, même vide. Un échec partiel reste manuel.

`HESTIA_UPLOAD_STORAGE` pointe exclusivement vers `<runtime>/data/uploads`.
Le helper Web résout les chemins physiques sans modifier les valeurs SQL ni
les URL `uploads/...`. Il ne crée ni n'adopte une racine. Le profil Web historique
sans cette variable reste disponible. GED, photos profil/référentiels/références,
helpers PHP mobile interne, templates/export et portabilité utilisent ce resolver.
Aucun appareil, APK ou Gateway n'est nécessaire ni qualifié.

Apache ne publie que `/uploads/profiles/`, `constructeurs/`, `distributeurs/`
et `references/`, pour PNG/JPEG/WebP/GIF. Les contrôles Host/pair/clients du profil
proxy s'appliquent aussi à ces alias. `Options None`, `AllowOverride None` et
`SetHandler none` empêchent leur utilisation comme code. Les autres données
restent privées et la GED passe par son téléchargement authentifié. Aucun alias
générique de `uploads` n'est créé. Un nom `.php.png` est une image statique,
jamais du PHP ; les dotfiles et autres extensions restent refusés.

## Maintenance et sauvegarde

FPM, les conditions systemd, le collecteur et `CoordinatedBackup` partagent le
scope exact du slot SQL. Le collecteur conserve 43200 secondes et les sessions
fonctionnelles 1 h/4 h/8 h. Aucun fichier global PHP/Apache/NGINX ni le nettoyeur
Debian natif n'est modifié.

La sauvegarde coordonnée reçoit les six racines externes explicitement.
Elle restaure réellement SQL et données dans des cibles jetables. La recette
arrête au préalable Apache/FPM et le collecteur, contrôle les cgroups puis
remplace, dans son instance jetable seulement, les six racines par la restauration.
Le Web relit ensuite la photo, le document GED et la session restaurés.
Les imports sont conservés avec leurs références SQL et leur contenu exact ;
cela certifie la réception et le stockage, pas tous les formats/analyse métier.

Le guard PHP reste postérieur à la réception multipart. Ni son 503 ni les quatre
unités ne prouvent l'arrêt de tous les producteurs. `StorageInventory` exige
l'observation de la variable externe et conserve aussi les chemins historiques.
La recette n'en déduit pas un inventaire exhaustif hôte.

## Qualification requise après gel

Conserver toutes les campagnes historiques sur le nouvel Installer : 628 core
par Debian 12/13, 61 système et 13 paquets par Debian, 16 DOM, 21 HTTPS,
118 SQL/HTTP, 14 helper proxy, 10 Web historique. Ajouter neuf scénarios Debian 13
sans réseau, sous SQL fresh managed et vrais services Apache/FPM/TLS :

1. Photo uploadée, servie et remplacée avec suppression de l'ancienne image.
2. GED uploadée, téléchargée par la route authentifiée et refusée directement.
3. Import multipart réellement déplacé hors webroot et référencé en SQL.
4. Alias publics, refus des fichiers privés et absence d'exécution PHP.
5. Gate commun PHP/collecteur, conservation puis nettoyage de session expirée.
6. Arrêt/cgroups puis refus de redémarrage sous la même maintenance.
7. Sauvegarde SQL/fichiers et relecture des données/session réellement restaurées.
8. Conservation d'une session valide de huit heures sous ce profil managed.
9. Refus d’un slot SQL copié dont le pointeur actif vise toujours l’original.

Tous les manifests doivent désigner exactement le même arbre Installer gelé.
Le banc `verification/*` matérialise séparément les deux commits Web complets ;
son checkout technique n'est jamais une livraison produit. Les preuves finales
et incidents éventuels sont remis dans le checkpoint compagnon, sans commit
documentaire ultérieur maquillant l'arbre qualifié.

## Incident de qualification conservé

Le premier candidat `5dddcdcc` a passé les 628 core sous Debian 13, les recettes
système et paquets sous Debian 12/13. Le core Debian 12 a rencontré un
`SSLEOFError` lors de l’envoi du corps surdimensionné dans le test HTTP strict
(run `36196621490`). Le serveur refuse normalement dès Content-Length puis
ferme TLS ; le client envoyait encore le corps. Le test exige maintenant le
413 exact, son diagnostic, Connection: close et l’absence de journal avant
envoi du corps. Aucun retry, délai, exception avalée ou tolérance de statut
additionnelle. Le code serveur est inchangé. La campagne complète doit être
repassée sur l’arbre corrigé. L’intermittence DOM historique reste distincte.

La revue du raccordement a également ajouté la reconstruction du pointeur
d’activation exact, lié au chemin du slot, pour refuser une copie valide du
slot vers une autre racine. La recette négative exige ce refus spécifique.

Le premier banc métier `36196797191` a passé GED, import, gate commun,
drainage/restart et session huit heures. Trois cas se sont arrêtés sur une
attente incorrecte du banc : le helper profil réencode en WebP avec GD officiel,
pas en PNG/JPEG. La recette exige maintenant le suffixe WebP, le MIME exact
et la signature RIFF/WEBP en plus des octets servis. Aucun changement Web pour
contourner ce résultat ; la restauration doit encore atteindre ses assertions.

## Frontières toujours ouvertes

`application_installed`, `system_wiring_verified`, `writable_business_storage_ready`,
`storage_inventory_complete`, `complete_web_backup`, `service_activation_delivered`
et `phase5_complete` restent faux dans les reçus produit. Il reste à fermer
l'audit/contrôle des neuf groupes de producteurs, la sauvegarde exhaustive 5C2,
une vraie transition 5C3, observation/reprise/rollback 5C4, orchestration et
écrans/journal du wizard 5D. Les actions de démarrage et bascule du banc ne sont
pas une activation livrée. Aucun certificat public ou cycle global NGINX/ACME.

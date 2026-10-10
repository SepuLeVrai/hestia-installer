# HESTIA Installer - reprise Work du 10 octobre 2026

## Source de reprise

Le chantier courant est la chaîne de générations publiques successives,
implémentée mais pas encore qualifiée. Lire d'abord
[GATEWAY_SUCCESSIVE_GENERATIONS_20261010.md](GATEWAY_SUCCESSIVE_GENERATIONS_20261010.md).
Le dernier candidat testé nativement est `dd4d89af9ee6d5ff62621ba77aba36f194c53b5d`,
arbre `6739bfa15369c9ed18671da702f108dba70fe336`, 555 fichiers.

Branche technique Installer : `validation/phase6-public-generations-20261010`.
Relire son HEAD avant toute modification et préserver les changements ultérieurs.
L'appelant Web `verification/phase6-public-generations-20261010`, à
`c826a48bbe62e9e0121a13ba654496a1416e2599`, épingle ce candidat exact.
La base canonique des fichiers livrés reste
`quality/phase6-gateway-lifecycle-20261004` à `1028c05ce48d0f528f8b6f71837be51ee15710a0`.
Aucune intégration main ni intervention sur la production n'est revendiquée.

Le premier transfert public/boot est un acquis distinct à `24a210d`, documenté
dans [sa qualification](GATEWAY_PUBLIC_BOOT_QUALIFICATION_20261010.md).
Ne pas repartir du checkpoint `9dc7e3a`, de ce précédent lot ou du seul `main`
pour poursuivre les générations successives.

## Point de qualification du candidat

- Quality `38080884154` PASS : 2138 tests sur chacun de Debian 12 et 13,
  38 bridge et 50 HTTPS ; sources exactes, aucune erreur ni aucun saut.
- Protocole/systemd `38080884162` PASS : 109 et 10 tests respectivement.
- Recette native `38080901195` terminée en échec : cycle 1 + boot PASS, cycle 2
  refusé à la reprise sur le journal commun de réouverture ; cycle 3 non exécuté.
  Upgrade indépendant au watchdog HTTP, rollback indépendant refusé en préparation.
  Preuves authentifiées : `11681931422`, `11681289874`, `11680751576`.
- Le workflow présente six étapes sur le même hôte : upgrade puis redémarrage,
  rollback puis redémarrage, upgrade puis redémarrage.
- Aucune relance exécutée. Les corrections suivantes remplacent ce gel :
  journaux de réouverture distincts par bail et audit HTTP/nettoyeur composé.
  Le HEAD technique doit être qualifié à son propre SHA ; 2152 tests core attendus.

Le code corrigé lit les profils de génération complets avec leur lecteur privé
borné à 256 Kio. Le lecteur de petits reçus, limité à 64 Kio, refusait le profil
valide du deuxième cycle. Les empreintes, permissions, archives distinctes et
contrôles natifs avant nouveau bail restent obligatoires.

## Acquis et limites à préserver

- FCM initial : lot `b9a3997`, #15 fermé ; aucune réception Google/téléphone
  réelle déduite du compte de recette synthétique.
- DEV distinct initial : `eaa2faf`, #16 fermé ; cette qualification ne couvre
  pas une transition de version avec DEV/FCM.
- Cockpit privé : `1028c05`, cinq étapes, deux hôtes indépendants, première
  génération MAIN avant public/boot ; preuves historiques du 5 octobre.
- Public/boot : première génération MAIN, SharedPublic v1 et MobileBoot
  existants, deux paquets catalogue SQLite 6, sans DEV/FCM actif.
- Nouveau PID 1 dans un même noyau est distinct d'un reboot noyau.
- Deux directions sur des hôtes indépendants sont distinctes d'un aller-retour
  upgrade/rollback/upgrade sur le même hôte.

## Prochain travail

1. Qualifier le HEAD corrigé de la branche technique : journaux par bail et
   factorisation des audits HTTP. Conserver les erreurs de `dd4d89a` ; ne pas
   relancer ce gel dépassé. Les tests Ext4 exigent le véritable volume de CI.
2. Épingler l'appelant Web sur le nouveau SHA exact, puis recueillir les trois
   transferts, trois redémarrages et deux recettes indépendantes.
3. Authentifier les artefacts et comparer toutes les sources. Ne jamais attribuer
   un PASS d'une ancienne source à la nouvelle. SQL 180 s et watchdog 1800 s
   restent inchangés ; aucun cache d'observations natives.
4. Finaliser les documents et le ZIP de fichiers modifiés complets, vérifier
   avant/après extraction et par reconstruction depuis la base canonique.
5. Étendre seulement ensuite aux variantes DEV/FCM compatibles. Un retour
   0.12.3 vers 0.12.2 avec le profil FCM actuel reste incompatible.
6. Restauration originale, recette 6C et intégration #18 restent séparées.
   Ne pas clôturer #17 ni la phase 6 au seul succès de ce lot MAIN.

## Règles techniques

- Préserver UUID SQLite, comptes, clés P-256, chemins, parents et politiques.
  Un retour binaire ne rembobine jamais SQLite, sessions ou quotas.
- Les clés et credentials Firebase ne sont pas supposés inclus dans SQLite.
- Admissions SQL bornées à 180 secondes, audits natifs complets aux frontières
  et gardes vivantes entre les retraits de marqueurs.
- Conserver les anciens bundles ; produire un successeur explicite plutôt que
  réécrire leurs profils ou leurs empreintes.
- Aucun reçu historique, simple référence ou compilation n'accorde une autorité
  de démarrer, de supprimer un garde ou de restaurer.
- Session HTTPS, Origin, CSRF, imports authentifiés et bornés, permissions privées.
- Tester les parcours fresh et upgrade, les refus, interruptions, états vides,
  données atypiques et tailles d'écran adaptées aux écrans modifiés.
- Geler les sources, exécuter les gates utiles, vérifier les manifestes et
  livrer les fichiers complets exacts, sans modification après le gate final.
- Mettre à jour `/docs`. Pas de changement SQL dans ce bloc ; `schema.sql` et
  `install.php` n'ont pas besoin de modification.

## Banc et récupération

Le Work de reprise utilise Python 3.12, supervisord en PID 1 et un namespace
UID/GID limité à 0. Ne pas y simuler les propriétaires 65534 ni les audits
systemd pour obtenir un PASS. La CI native technique utilise Debian 13,
Ext4, PID 1, Web privé figé, vrais paquets Gateway et CA ACME jetable.

Le workflow réutilisable Installer `public-composed-validation.yml` est appelé
par la branche technique Web `verification/phase6-public-generations-20261010` avec
un SHA Installer exact. Son jeton lit le Web privé ; aucun nouveau credential
interdépôts ni source Web privée n'est publié dans le dépôt Installer public.

Conserver les échecs utiles. Une relance au même SHA reste une nouvelle tentative,
pas une requalification d'une ancienne source. Regrouper les changements et
limiter les runs ; ne relancer que le job nécessaire si le code est identique.
Les preuves déjà acquises ne doivent pas être refaites sans risque concret.

L'autorisation antérieure couvre les branches techniques du chantier.
Ne pas écrire sur une branche active sans autorisation explicite applicable,
ne pas force-push et ne pas déployer sur le serveur personnel pour tester.

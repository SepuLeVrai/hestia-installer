# HESTIA Installer - reprise Work du 10 octobre 2026

## Source de reprise

Lire d'abord [GATEWAY_PUBLIC_BOOT_QUALIFICATION_20261010.md](GATEWAY_PUBLIC_BOOT_QUALIFICATION_20261010.md)
pour le verdict public/boot et les éventuelles limites encore ouvertes.
Le dernier code produit est `24a210d3ffceda40c0adec4d5f505f0b2a3784b6`,
arbre `f503bc740e258e37e78e6f541d8878ebb0cd5f54`, 545 fichiers.

La branche technique est `validation/phase6-public-fragments-20261010` dans
`SepuLeVrai/hestia-installer`. Relire son HEAD avant toute modification et
préserver les changements ultérieurs. La base canonique du patch reste
`quality/phase6-gateway-lifecycle-20261004` à `1028c05ce48d0f528f8b6f71837be51ee15710a0`.
`main` est restée à `c0dcb902663130302599635b36c7fb8deab80a47` lors de la reprise.
Aucune intégration main ni intervention sur la production n'est revendiquée.

Le ZIP de checkpoint `9dc7e3a` est antérieur au raccordement complet.
Ne pas repartir de ce checkpoint ni du seul `main`. Les étapes suivantes sont
désormais implémentées : transfert natif des huit fragments, bundle successeur,
reload systemd, sélection explicite de l'overlay, admissions publiques,
activation locale et réouverture, cockpit à sept étapes et lecture après
nouveau PID 1. Leur verdict est distinct de leur existence dans le code.

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

Après clôture effective du bloc public/boot selon son document de qualification :

1. Définir et implémenter la chaîne de générations successives, avec parcours
   upgrade/rollback/upgrade sur le même hôte. Le refus actuel d'adopter une
   génération déjà publiée doit rester fermé jusqu'à ce nouveau contrat.
2. Étendre seulement aux variantes DEV/FCM compatibles et les qualifier.
   Un retour 0.12.3 vers 0.12.2 avec le profil FCM actuel reste incompatible.
3. Restauration sur l'instance originale : autorité distincte, époque
   d'authentification, révocations, données et services. Aucun reçu de backup
   historique ne l'autorise à lui seul.
4. Recette 6C et intégration #18, puis clôture justifiée de #17 et de la phase 6.
5. Les phases réseau général, APK et import/restauration guidés restent séparées.

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
par la branche technique Web `verification/phase6-public-boot-20261010` avec
un SHA Installer exact. Son jeton lit le Web privé ; aucun nouveau credential
interdépôts ni source Web privée n'est publié dans le dépôt Installer public.

Conserver les échecs utiles. Une relance au même SHA reste une nouvelle tentative,
pas une requalification d'une ancienne source. Regrouper les changements et
limiter les runs ; ne relancer que le job nécessaire si le code est identique.
Les preuves déjà acquises ne doivent pas être refaites sans risque concret.

L'autorisation antérieure couvre les branches techniques du chantier.
Ne pas écrire sur une branche active sans autorisation explicite applicable,
ne pas force-push et ne pas déployer sur le serveur personnel pour tester.

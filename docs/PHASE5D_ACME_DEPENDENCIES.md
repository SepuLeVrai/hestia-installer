# Phase 5D7a — Dépendances du frontal Let's Encrypt

Ce lot prépare la dernière étape publique de phase 5. Il ne constitue pas sa
clôture : aucun certificat n'est demandé, aucun port 80/443 n'est ouvert et
aucun renouvellement n'est configuré. Les indicateurs `public_tls_configured`,
`renewal_configured` et `phase5_complete` restent faux, même après DONE.

## Ajout explicite après les paquets acquis

Le profil `AcmePackages` ajoute uniquement Certbot et, si le profil initial ne
l'avait pas demandé, NGINX. Il réutilise les lecteurs APT authentifiés, les
archives scellées et les contrôles de versions du provisionneur acquis. Il ne
modifie ni ce provisionneur ni ses StepSpecs. Les dépôts système ne changent
pas, les paquets existants ne sont ni mis à jour ni supprimés, et les services
Certbot/timer/NGINX par défaut restent masqués. L'installation des archives se
fait sans téléchargement, sous la politique de non-démarrage existante.

Le profil lie le reçu d'installation initial, son inventaire dpkg exact, son
plan d'archives et ses masques. L'inventaire final doit être exactement égal à
l'ancien inventaire plus les archives consenties. Une dérive d'un paquet, un
ajout étranger, une sélection différente ou un reçu manquant bloque la reprise.
Le lecteur dpkg initial conserve son exigence d'égalité : il refuse désormais
les paquets ajoutés. Le nouveau contrôleur porte explicitement cette extension,
sans réécrire le reçu initial pour lui attribuer des effets ultérieurs.

## Consentements et reprise

Les journaux privés `acme-packages/acquire/state.json` et
`acme-packages/install/state.json` sont distincts. Les POST fermés
`/api/system/acme-packages/{acquire,install}/{plan,apply,resume,retry}` utilisent
le verrou parent et les protections HTTPS/session/Origin/CSRF existantes.
L'utilisateur ne fournit aucun paquet, dépôt, chemin ou commande arbitraire.
Le téléchargement peut être préparé après les paquets initiaux ; l'installation
exige les journaux fresh/SQL/activation/boot terminés et le boot natif vérifié.

Le wizard présente les versions exactes et deux confirmations. Consultation,
annulation, rechargement, rapport et GET ne font aucun effet système. Une réponse
perdue après installation complète est reconnue par lecture des reçus et de
l'inventaire, sans rappeler APT. Une installation partielle reste manuelle.

## Référence au boot 5D6 gelé

Un nouveau module change l'ensemble des sources de l'Installer. Le lecteur
`frozen_boot.reference` vérifie donc une référence explicite au profil 5D6
déjà installé : parents, registre complet, reçus, copie privée du code, unités,
liens et disponibilité. Il ne tente pas de reprendre ce plan avec un autre
ensemble de sources. Le contrôleur BootPlan acquis conserve son refus d'une
adoption avec du code différent. Aucun ancien fichier ou reçu n'est réécrit.

## Qualification et suite

Les tests locaux sont limités aux fichiers et doubles d'effets. La recette
dédiée s'exécute uniquement en CI Debian 13 jetable, avec deux profils parents
(NGINX initialement demandé ou absent). Le téléchargement authentifié est
séparé de la cible applicative sans réseau. La chaîne acquise est une fixture ;
ses campagnes historiques ne sont pas relancées. Le boot emploie la vraie copie
des sources 5D6, distincte du candidat. Les nouvelles assertions couvrent les
archives, le navigateur et une réponse perdue, les PID et reçus conservés,
un nouveau PID 1, puis le refus d'un reçu final manquant. Le noyau reste partagé.

Le raccordement public doit encore traiter ensemble l'adresse client canonique,
le backend Apache loopback, la maintenance et le périmètre des producteurs de
données. Ajouter un second Apache sans l'enrôler dans le contrat de drainage et
de sauvegarde ne suffit pas pour clore phase 5. La qualification finale devra
couvrir HTTP-01, certificat réel via ACME de recette, renouvellement, reprise,
maintenance et restauration des services. Le choix produit reste Let's Encrypt
avec renouvellement automatique. Le périmètre réseau complet, Mobile API et
firewall reste suivi dans #5 ; ce lot ne coche pas ces éléments.

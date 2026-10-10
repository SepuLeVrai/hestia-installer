# Transfert public/boot - protocole de fragments en développement

Base : `1028c05ce48d0f528f8b6f71837be51ee15710a0`, branche
`quality/phase6-gateway-lifecycle-20261004`, vérifiée le 10 octobre 2026.

## Portée de ce candidat

`installer/gateway_public_fragments.py` ajoute une primitive interne de
remplacement ordonné des huit fragments du frontal et des workers boot.
Elle lie chaque transaction à la maintenance vivante, aux générations Gateway
source/cible, à la publication, aux profils publics et boot, et au code successeur.
Les chemins dérivent exclusivement de l'instance et des noms autorisés.

Le journal conserve les inodes des sources, des parents et du répertoire privé.
Pour chaque remplacement : création exclusive du temporaire, journal de son
inode, écriture bornée, armement durable, renommage, reçu final.
Une réponse perdue après renommage se résout par lecture de l'inode cible.
Un fichier étranger de contenu identique est refusé. Un temporaire créé mais
sans reçu d'inode durable reste manuel. Aucun fichier étranger n'est effacé.

Les tests exercent de vrais fichiers, permissions, verrous de maintenance,
substitutions d'inodes et un SIGKILL après renommage. Les substitutions de
fonctions servent uniquement à injecter les interruptions ou interdire un effet.

## Limites explicites

Ce candidat n'est pas raccordé au cockpit. Il ne constitue ni un transfert
public opérationnel, ni une qualification native. Il ne recharge pas systemd,
ne lance aucun service, n'ouvre pas la maintenance, ne restaure pas SQLite
et ne modifie aucun bundle historique. Les refus existants restent actifs.

Le futur coordinateur doit conserver la barrière des workers publics pendant
les remplacements. Une maintenance Web seule ne prouve pas leur arrêt.
Les références fournies à cette primitive doivent être construites et vérifiées
par ce coordinateur ; leur format ne suffit pas à accorder une admission.

Restent à implémenter et qualifier ensemble :

- Construction et contrôle des bundles successeurs, boot Web compris.
- Autorité fermée pour les admissions fichiers, externes, données et reprise.
- Transfert systemd et observation après perte de réponse.
- Raccordement au cockpit et contrôles Web/Mobile, TLS et renouvellement.
- Nouvelle époque PID 1, installation initiale et upgrade natifs.
- Quality globale du gel final et packaging exact.

## Banc de vérification

Le Work du 10 octobre est Ubuntu 24.04, Python 3.12, supervisord en PID 1.
Les espaces UID et GID ne mappent que l'identité 0. La première exécution des
23 tests échoue donc dans la préparation de maintenance sur `fchown(0, 65534)`.
Aucun verdict PASS n'est déduit de cet environnement.

Le workflow technique `public-fragments-validation.yml` utilise Debian 13
jetable pour contrôler les vrais propriétaires et verrous. Son résultat porte
uniquement sur la primitive et les contrôles statiques, pas sur un service
systemd ni sur la clôture du bloc public/boot.

La publication de la branche technique a été refusée par la revue automatique
d'autorisation : elle considère la publication du code et du workflow vers ce
dépôt GitHub comme non autorisée. Aucun contournement ni nouvel essai de push.
Le candidat reste local ; aucun résultat CI n'est disponible. Les deux tests
de grammaire et de correspondance des noms avec les vrais générateurs passent
localement. Les 23 tests de fichiers restent bloqués par le GID non mappé.
Les 25 nouveaux contrats sont ajoutés à la baseline sans retrait historique.

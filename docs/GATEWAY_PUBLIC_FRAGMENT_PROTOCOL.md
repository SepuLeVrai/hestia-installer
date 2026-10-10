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

La publication technique a ensuite été autorisée par l'utilisateur. La recette
`38038107737` a réussi sur `86a65998432a7c3aa7b1bda2d266d08c0869e042` :
25 tests, statique PASS. Elle ne contient pas les ajouts suivants, dont les
résultats doivent être vérifiés sur leur propre gel.

## Génération successeur en développement

`gateway_public_generation.py` compile les huit fragments et conserve les
bundles parents à leurs chemins historiques. Le nouveau worker vérifie les
empreintes du profil et du code privé avant import. Le lecteur
`CompletedFragments` relit les inodes de la transaction terminée sans exposer
une méthode de mutation. Il refuse une transaction partielle ou une substitution
étrangère de contenu identique, même après fermeture du bail de maintenance.

Le worker exige une admission consommée et un propriétaire d'activation lié
aux fragments. Aucun producteur de ce propriétaire n'est encore raccordé.
Le dépôt ne dispose donc toujours pas d'un transfert public/boot opérationnel.
Le staging natif, les admissions, le rechargement systemd, la réouverture et
le cockpit restent à développer et qualifier. Aucun succès unitaire ne vaut
preuve de TLS courant, de renouvellement ACME ou de nouvelle époque PID 1.

## Coordinateur systemd en cours de qualification

`gateway_public_systemd.py` ajoute l'arrêt explicite du timer puis des listeners,
le refus d'un renouvellement actif, les remplacements sous le verrou public,
et le rechargement du manager. Le plan lie les inodes source et répertoires,
la génération, le bail et l'époque PID 1. Un changement d'époque pendant une
transaction incomplète est refusé ; aucun démarrage n'est exécuté ici.

L'adaptateur `prepare_systemd` / `transfer_systemd` dérive tous les chemins du
profil `Generation`, conserve les parents et stage le worker avant le transfert.
Le contrôle final reste sous maintenance et ne produit aucune admission.

Une recette Debian 13/PID 1 jetable est ajoutée au workflow ciblé : services de
fixture réels, arrêts, refus d'un inode remplacé et d'un renouvellement actif,
réponse perdue après stop, SIGKILL réel après daemon-reload. Ce n'est pas une
recette NGINX/Gateway/TLS ni une nouvelle époque après réouverture. Ses résultats
restent à lire sur le SHA publié. Les refus des admissions et du cockpit restent
inchangés à ce stade.

La première recette systemd (6 cas) passe sur `571fc31a04e543f6e409989c44211eaa9bf17d04`,
run `38040712005`. La relecture a ensuite identifié la composition Apache à
compléter : le garde de maintenance `50-hestia-maintenance.conf` doit être
vérifié et épinglé en plus de l'overlay public 60. Le candidat suivant ajoute
ce garde et l'inode du verrou au plan, ainsi que trois cas natifs : garde
étranger, invocation de listener remplacée, transfert partiel avec reload en
attente. Le résultat des 6 cas ne qualifie pas ces ajouts.

Le run `38040999507` échoue sur l'assertion de la recette selon laquelle le
premier renommage doit nécessairement produire `NeedDaemonReload=yes`.
L'unité inactive peut être déchargée puis relue par PID 1. Le candidat suivant
contrôle donc la frontière de reload par le journal (premier reload exécuté
explicitement), et vérifie aussi les Description/ExecStart/ExecStartPre chargés
par PID 1. L'échec n'est pas converti en skip ; le cas reste exécuté.

Le run `38041175181` révèle ensuite l'omission des propriétés de commande vides
par `systemctl show`. Le lecteur demande désormais `--all` en conservant son
schéma exact obligatoire. Les neuf contrats systemd sont ajoutés à la baseline
et la recette refuse les tests absents, skips et échecs attendus. Les commandes
chargées sont comparées au résultat des générateurs, pas déduites du seul bit
NeedDaemonReload. Référence de syntaxe : manuel officiel systemd/systemctl,
option --all (https://github.com/systemd/systemd/blob/main/man/systemctl.xml).

Le diagnostic réel (`38041418645`) confirme une exception de rendu des tableaux
Exec : ExecStartPre vide n'a aucune ligne, même avec --all. Le lecteur traduit
uniquement cette représentation des deux tableaux Exec en liste vide ; tous
les champs scalaires restent obligatoires. Une commande exigée et absente reste
refusée. Un dixième contrat natif vérifie ce refus et celui d'un garde différent,
avec les propriétés réelles de PID 1, sans substituer le lecteur systemd.

## Sélection explicite de l'overlay après transfert

`gateway_public_selection` publie `gateway-successor.json` dans le répertoire
privé SharedPublic source. Le producteur est appelé après `Manager.apply`, sous
le verrou public historique, avec un nouveau contrôle du bail, de l'époque PID 1,
des arrêts, du reload et des huit fragments. Le pointeur lie le bail, le profil
SharedPublic, la génération scellée et le plan des fragments. Il ne crée aucun
`activated.json` et n'accorde aucun droit de démarrer.

Le lecteur vérifie le bundle complet, la publication cible, les configurations
et les identités des huit fragments terminés. `shared_public_runtime.overlay`
retourne alors les octets réellement sélectionnés, y compris l'empreinte de la
génération. Un pointeur corrompu, incomplet, lié à un autre parent ou un lien
symbolique pendant refuse sans repli vers l'ancien worker. Un pointeur absent
conserve le contrat historique, dont la vérification des anciens fragments.
Le profil de drain historique n'est pas remappé par ce lecteur.

Neuf tests de contrats couvrent cette sélection et sa délégation d'overlay ;
ils utilisent des fichiers privés réels et isolent les contrôles natifs. Ils ne
prouvent pas une admission ou un boot. Le gel précédent `70a4ca6` a passé le
run natif `38041537966` (10 cas PID 1) et Quality `38041537919` ; ces preuves
restent attachées à cet ancien gel. L'admission publique, le producteur de
l'autorité d'activation, la réouverture, le cockpit et la nouvelle époque PID 1
complète restent à raccorder et qualifier avant toute clôture du bloc.

### Régression observée au gate navigateur

Le premier gate du gel `47b8903` (`38042358724`) échoue sur le rafraîchissement
du brouillon du wizard : le mode affiché revient à fresh pendant une sauvegarde
encore en cours. Les 51 contrats ciblés, les 10 cas PID 1 et les deux suites core
passent à ce gel ; cet échec navigateur n'est pas un PASS global.
L'indicateur `aria-busy` ne couvrait pas la file `saveChain`. Il couvre désormais
chaque sauvegarde en attente, jusqu'à sa réponse ou son erreur, tout en laissant
les champs modifiables. Un contrat à sauvegarde volontairement retardée vérifie
la file et le rafraîchissement dans les deux parcours navigateur, bridge et HTTPS.

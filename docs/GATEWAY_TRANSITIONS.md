# Gateway : changements de version, lot 3

## 3B2.2c1 — Références figées pour les nouveaux profils public/boot

Base : reprise locale 3B2.2b qualifiée sur
`875e9b3a15fd27f4840900e748d162337a5f6236`. Cette tranche prépare le transfert
public/boot en raccordant ses lecteurs au profil actif publié.

`gateway_frozen_reference.reference` lit l'enrôlement original, l'intention de
publication, son reçu et les preuves de bascule. Le lecteur partage la grammaire
fermée de `gateway_active_profile` et ne lance ni sonde native, ni recherche NSS,
ni commande système, ni écriture. Son résultat reste historique. Les deux fichiers
de publication absents désignent l'enrôlement original ; une publication partielle
refuse, sans retour implicite au profil original.

Un nouveau SharedPublic v2 enregistre `gateway_publication_sha256`, en plus du
profil cible. Sa référence MobileBoot conserve ce même digest dans son profil
figé. La construction des profils et des unités reste pure. À l'admission native,
la publication sélectionnée doit avoir exactement ce digest et ce profil :
`attach` ajoute seulement les preuves au lecteur déjà construit, puis effectue
l'audit natif complet. Le lecteur existant recontrôle la publication avant et
après chaque audit. Une dérive d'inode, de binaire ou de preuve n'est jamais
acceptée au seul motif que le numéro de version est identique.

Les profils SharedPublic v1 ne peuvent pas adopter une publication. Un profil v2
ne peut pas suivre une publication ultérieure. Les plans, reçus et bundles
originaux restent intacts ; le code figé d'un ancien bundle n'est pas remplacé.

Cette tranche ne revendique aucun transfert d'unités déjà enrôlées, aucune
nouvelle époque PID 1, aucun reboot noyau, ni un cycle montée/retour sur le même
hôte. Les 19 tests de contrat s'ajoutent à la baseline, sans suppression des tests
acquis. Les CI du commit doivent être contrôlées séparément ; les campagnes
natives de 3B2.2b ne qualifient pas ce nouveau code.

## 3A, matrice fermée et plan sans effet

Base acquise : lot DEV `eaa2faf3c90f993923b99c8fe6f3bca5dc94ee04`.
Cette sous-étape ajoute une évaluation de compatibilité et un plan privé lié au
service terminé. Elle ne déploie aucun binaire, ne lance aucun service et ne
restaure aucune donnée. Les reçus des parents restent inchangés. Les étapes 3B,
3C et 3D doivent qualifier les effets et la restauration sur l'origine.

| Source | Cible | Profil conservé | Compatibilité des fichiers |
| --- | --- | --- | --- |
| 0.12.2-installer.rc1 | 0.12.3-installer.rc1 | MAIN, sans FCM | Candidate |
| 0.12.2-installer.rc1 | 0.12.3-installer.rc1 | MAIN et DEV distinct, sans FCM | Candidate |
| 0.12.3-installer.rc1 | 0.12.2-installer.rc1 | MAIN, sans FCM | Candidate rollback |
| 0.12.3-installer.rc1 | 0.12.2-installer.rc1 | MAIN et DEV distinct, sans FCM | Candidate rollback |
| 0.12.3-installer.rc1 | 0.12.2-installer.rc1 | FCM, avec ou sans DEV | Refusée avant effet |
| Une version | Même version | Tout profil | Aucun changement de version |

Une candidate compatible n'est pas une transition exécutée ou qualifiée. Les
références admises sont exclusivement `e2c09f53593bf316906ccc4387f185e73e7f85a8`
et `33927821bbda57a2c10791d0523eaf3b254c8c9e`. Branches, tags et versions textuelles
ne sélectionnent jamais des octets. Une extension du catalogue fresh ne peut pas
étendre implicitement cette matrice.

Les deux versions partagent les six migrations SQLite identiques et le schéma 6.
Leurs empreintes Git sont dans `installer/gateway_transition.py`. En revanche,
0.12.2 refuse `project_push_project_id` et le chemin systemd privé du credential,
et ne fournit pas `--check-push-credential`. La compatibilité SQLite ne permet
donc pas un rollback FCM. Aucun projet n'est supprimé et aucun secret n'est déplacé
vers une configuration globale pour contourner ce refus.

## Contrat du plan

`POST /api/gateway/transition/plan` exige session, origine, CSRF et les trois champs
`source_plan_sha256`, `target_commit`, `direction`. Le service source doit avoir
un journal terminé et un profil exactement reconstructible. Le plan lie le SHA
du journal source, le SHA du profil, la direction, les deux paquets exacts, les
configurations, les identités MAIN/DEV et le reçu FCM public éventuel.

Le cockpit affiche l'examen de la mise à jour ou du retour arrière. Une action
explicite prépare le plan ; le refresh lit seulement les métadonnées. Le plan
reste immuable. Une sélection ou un parent modifié est refusé. Aucune route
`apply`, `resume`, `restore`, `rollback` ou `retry` n'est exposée pour ce plan 3A.
Son SHA ne vaut pas autorisation d'écriture sur l'hôte.

Les contextes, comptes, clés, chemins, origine, unité et configuration sont
conservés. L'ajout DEV et l'activation/désactivation FCM sur service existant ne
sont pas implicitement absorbés par un changement de binaire.

## 3B1, préparation durable des deux binaires

`installer/gateway_transition_stage.py` prépare physiquement les binaires source
et cible exacts. Son entrée exige un objet `GatewayBackup` lié à une fence native
vivante : maintenance, services arrêtés, verrou SQLite exclusif et inodes Ext4
immuables. Le manifeste composé Web/SQLite, les copies sauvegardées et les octets
SQLite courants sont revérifiés. Schéma 6 et empreinte UUID restent liés au reçu.
Les deux ZIP entiers sont authentifiés avant la première intention.

Une intention privée est écrite et synchronisée avant le répertoire de staging.
Les fichiers `source.bin` et `target.bin` restent root:root 0600 dans un dossier
0700 sous la racine privée de sauvegarde. Une copie interrompue ne reprend que si
ses octets sont un préfixe exact du binaire authentifié. L'effet de renommage puis
le reçu terminé sont durables. `recovery=True` exige l'intention exacte et une
nouvelle acquisition native de la même maintenance/fence ; un appel initial ne
réadopte jamais une préparation existante. Les fichiers inconnus, liens,
permissions élargies, dérives de sauvegarde et octets modifiés sont refusés.
Un reçu complet rend la reprise uniquement vérificatrice : aucune recopie ni
réparation d'un binaire absent ou altéré. Une intention ou un reçu déchiré exige
une inspection manuelle ; son préfixe JSON n'est jamais traité comme autorité.

Cette primitive n'est pas encore exposée au cockpit. Son reçu déclare
`active_profile_changed`, `apply_allowed`, `rollback_verified`, `boot_requalified`
et `restore_to_original_allowed` à false. Les binaires ne sont pas exécutables,
le Gateway actif et son `staged.json` restent inchangés ; aucun service n'est
lancé et aucun garde n'est retiré. Le reçu est historique et n'autorise aucune
future bascule. L'étape **3B2** doit réaliser le passage explicite des profils de
service, d'admission et de boot, puis le retour arrière sans rembobiner SQLite.

La recette `tests/integration/gateway_transition_stage_systemd.py` couvre deux
instances MAIN réelles : préparation 0.12.2→0.12.3 puis 0.12.3→0.12.2 sans FCM.
Chaque cas vérifie quatre SIGKILL (copie partielle, deux renommages, reçu), les
paquets officiels, SQLite/UUID, les clés, les parents, les unités et l'interdiction
de démarrage sous maintenance. Cette recette ne revendique ni DEV en transition,
ni changement du profil actif, ni réactivation, ni reboot. Les 16 nouveaux tests
core couvrent les reprises et refus ; la qualification native reste à obtenir
pour le gel candidat, sans réutiliser les PASS historiques comme nouveau verdict.

## 3B2.1, remplacement physique et retour au fichier original sous maintenance

`installer/gateway_transition_cutover.py` réalise un effet borné : remplacer le
fichier binaire par la cible 3B1, puis, sur demande explicite, réinstaller les
octets du binaire original. Le profil actif n'est pas publié dans ce sous-lot.
L'instance doit rester arrêtée sous la même maintenance et la même fence SQLite.
Les profils, clés, configurations, unités, sauvegardes et journaux parents ne
sont pas réécrits. Le rollback de fichiers ne restaure jamais SQLite.

L'entrée `apply` exige un `GatewayBackup` vivant et les deux paquets exacts.
La préparation 3B1 doit être complète ; son reçu, ses deux binaires, l'évaluation
de compatibilité, la sauvegarde composée et les octets SQLite sont revérifiés.
Un garde durable `gateway-cutover.attempt` précède la copie. Il interdit à la
fois la réouverture de maintenance et la levée de la fence Gateway, même après
le retour au binaire d'origine.

La copie temporaire reste dans un répertoire root:root 0700, sous le contrôle
privé du service et sur le même système de fichiers que le binaire final.
Une interruption ne reprend que depuis un préfixe exact. Les octets complets
reçoivent les droits natifs 0750/root:groupe-Gateway avant l'intention de
renommage. Cette intention lie les deux inodes exacts. La reprise distingue le
fichier précédent, le fichier temporaire armé et le fichier effectivement
renommé ; des octets identiques sur un inode étranger sont refusés. Le reçu
durable suit le renommage et la revérification de la fence et de SQLite.

`recover(..., action='resume'|'check'|'rollback')` réacquiert la maintenance et
le verrou exclusif de l'état, authentifie de nouveau les deux paquets et exige
la même intention. `rollback` ne commence qu'après un reçu cible complet ; une
fois son intention écrite, `resume` termine le retour au fichier source. Un
reçu terminé ne déclenche aucune recopie. Un journal déchiré, une dérive, un
lien, un inode remplacé, un fichier étranger ou une copie complète supprimée
exigent une inspection manuelle et ne sont jamais réparés implicitement.

Seul l'auditeur privé de cette opération reconnaît le binaire intermédiaire,
avec le garde et l'intention exacts. Le lecteur de service ordinaire reste lié
au profil d'origine et refuse la cible. Aucun service n'est démarré. Les anciens
bundles de boot restent inchangés et la maintenance interdit leur démarrage.
Les reçus déclarent `active_profile_changed`, `activity_resumed`,
`rollback_verified`, `boot_requalified` et `restore_to_original_allowed` à false.
Une copie ciblée réussie n'est donc pas un upgrade utilisable en production.

La recette dédiée teste les deux sens sur des hôtes indépendants, avec quatre
SIGKILL à l'installation de la cible et quatre au retour de fichier, soit
seize coupures pour la matrice complète. Elle doit prouver les octets et inodes
SQLite inchangés, les clés et parents conservés, le refus de démarrage, ainsi
que les reprises terminées sans réécriture. Les verdicts du gel restent à
obtenir. Les dix-sept contrats core sont obligatoires et ajoutés sans retrait.

**3B2.2 reste nécessaire** : publier explicitement le profil de service et
d'admission, transmettre le boot à un successeur qualifié, puis seulement
rouvrir les services et qualifier le rollback opérationnel. 3C et 3D suivent.

## 3B2.2a : publication durable du profil actif, activité toujours fermée

`gateway_active_profile.publish` exige une bascule cible 3B2.1 terminée,
les deux paquets authentifiés, la sauvegarde composée et les mêmes verrous,
inodes et octets SQLite. Les actions sont `apply`, `resume` et `check`.
L'intention de maintenance précède l'intention persistante dans `control`,
puis le reçu de publication. Toute reprise relit les sources ; aucun journal
partiel ou étranger n'est réparé. Les opérations terminées sont en lecture seule.

Le lecteur courant `gateway_service_drain.attached` sélectionne explicitement
ce successeur. À chaque inspection, il revérifie la chaîne de bascule, le
manifeste d'origine, l'inode du binaire et les identités d'état, puis les contrôles
natifs complets avec le SHA cible : configuration, clés, compte, unité et état
du service. Il relit la publication après l'audit pour refuser une dérive en
cours de lecture. Un reçu historique ne suffit donc jamais à prouver un service
vivant. Les lecteurs construits sur un ancien profil restent liés à ce profil.

`staged.json` n'est pas réécrit. Le manifeste courant porte la version cible ;
le manifeste d'installation reste son ancêtre. L'intention
`gateway-active-profile.attempt` est un bloqueur indépendant de maintenance et
de libération SQLite. Dès cette intention, les anciennes actions de bascule,
y compris le rollback de fichier, refusent d'agir. Le futur rollback opérationnel
devra produire son propre successeur et conserver l'état SQLite courant.

La recette native dédiée prévoit deux hôtes indépendants, une publication dans
chaque sens, trois SIGKILL chacun et un contrôle réel des lecteurs natifs. Elle
vérifie les octets/inodes SQLite, les parents, les clés, l'unité, le refus de
démarrage et les reprises sans réécriture. Les vingt contrats core sont ajoutés
sans suppression. Le gel de ce sous-lot reste à qualifier.

**Limite explicite :** profil actif publié, services arrêtés. Aucun successeur
SharedPublic/admission/boot n'est livré ici ; les anciens bundles restent figés.
Aucune route cockpit n'est ajoutée et le lot 3B2.2 n'est pas déclaré terminé.

## Contrats requis avant la réouverture 3B2.2 et la restauration 3C

La bascule doit authentifier les deux paquets et l'instance source réelle,
vérifier une sauvegarde composée courante, tenir le garde de maintenance,
arrêter le Gateway exact et contrôler schéma/UUID. Chaque effet doit avoir une
intention durable et une reprise par observation. Les vérifications du service,
des deux contextes et du boot devront suivre le profil actif après bascule.

Un rollback de fichiers conserve la base actuelle : sessions, révocations,
quotas et UUID ne sont jamais rembobinés. Une restauration SQLite sur l'origine
est une opération séparée, avec autorisation distincte et traitement explicite
de l'époque d'authentification et des révocations Web. Les sauvegardes existantes
restent `restore_to_original_allowed: false` tant que ce contrat n'est pas acquis.
Les clés P-256 et le credential Firebase ne font pas partie d'une sauvegarde
SQLite. Aucune restauration de ces secrets n'est supposée.

## Vérification

Les nouveaux contrats sont ajoutés à la baseline obligatoire sans retirer un
ancien identifiant. Les tests navigateur utilisent les routes HTTPS réelles.
`scripts/audit_gateway_transition.py --legacy PACKAGE_0122 --current PACKAGE_0123
--output REPORT` authentifie les deux ZIP complets puis exécute leurs vrais
binaires : version, schéma, configuration MAIN/DEV avec et sans FCM. Il ne lance
aucun serveur ni migration et n'ouvre pas SQLite. Ce contrôle des parseurs ne
qualifie pas une bascule réelle. Les résultats et SHA du gel se trouvent dans
les preuves du checkpoint ; aucun succès d'un gel antérieur n'est réattribué.


## 3B2.2b — Admission du profil successeur et reprise locale

Le coordinateur privé `gateway_transition_resume` prépare une autorité liée à
la publication complète, aux deux profils, au drain original et au répertoire
de sauvegarde. Il conserve les journaux d’origine. Sa projection de la liaison
historique est limitée au contexte explicite de reprise : les inspections du
binaire et le profil réel restent ceux de la cible. Le journal d’activation
contient les empreintes des deux profils et de cette nouvelle autorité.

Les six étapes de préparation qualifiées sont réutilisées : SQLite Gateway,
fichiers, chemins externes, données, plan de reprise et transfert des anciens
bloqueurs. Chaque étape achevée possède un checkpoint privé. Les enveloppes
SQL/fichiers incluent exactement les nouveaux marqueurs, sans exclusion de
sous-arbre. Les marqueurs cutover/publication/reprise restent fermés jusqu’à
la dernière fenêtre SQL réelle et au journal d’activation armé. Leur retrait
suit un préfixe journalisé ; une perte de réponse après unlink est conciliable,
une suppression étrangère ou hors ordre est refusée.

Le moteur d’activation conserve son verrou exclusif au-delà du retrait de la
maintenance. Le contrôle SQL doit se terminer normalement avant les cinq starts
ordonnés. Une intention de start sans invocation prouvée reste manuelle. Les
reprises après admission ne relisent pas la sauvegarde SQL ; check reste sans
start ni réécriture.

Périmètre : MAIN local avant enrôlement public/boot, dans les deux directions
0.12.2/0.12.3 compatibles. Ce lot n’autorise pas la reprise d’un ancien bundle
boot/public, ne déclare pas le boot persistant, et ne restaure jamais SQLite.
Les profils DEV/FCM et leur succession publique restent hors de cette admission.
Les façades cockpit ne sont pas modifiées par ce raccordement natif privé.

La recette `gateway_transition_resume_systemd.py` reprend la publication native
et ajoute les interruptions après unlink du premier verrou et après le start
PHP réel, puis vérifie la cible active, les cinq invocations, la page de login,
l’UUID/schema SQLite et l’intégrité des parents. Le verdict de ce candidat doit
être établi sur son commit exact ; les PASS antérieurs ne le qualifient pas.

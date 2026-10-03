# Phase 6, lot 2 : Web DEV distinct

## Contrat du candidat du 4 octobre 2026

Le lot ajoute un premier raccordement Foundation/Gateway à deux contextes.
MAIN reste l'autorité des appareils et de l'enrôlement. DEV utilise un autre
Web géré, ses propres comptes système, arborescences, base et utilisateur SQL.
Le Web sélectionné reste Mobile v2 `a21fc758fc4c1de9580a953ec07f459f54609874`.
Les clés P-256 MAIN/DEV préparées sont relues, jamais régénérées ni copiées.
Le Gateway FCM `33927821bbda57a2c10791d0523eaf3b254c8c9e` et le credential
FCM du lot 1 restent compatibles avec ce profil.

| Canal | MAIN | DEV |
| --- | --- | --- |
| Identifiant Mobile | `main` | `dev-bastien` |
| Backend Web | `127.0.0.1:9080` | `127.0.0.1:9084` |
| Foundation | `127.0.0.1:9082` | `127.0.0.1:9081` |
| Clé chargée par systemd | `main-key` | `dev-key` |
| Appareils et enrôlement | Autorité canonique | Aucun registre cloné |

Le service Gateway commun écoute toujours sur `127.0.0.1:9083`. Aucun port
arbitraire ni URL externe ne peut être soumis par le navigateur.

## Enregistrement puis consentement

Avant le premier plan Foundation et avant le service Gateway, l'administrateur
enregistre le descripteur JSON privé d'un Web DEV **déjà préparé et activé** :
`python3 -m installer --state-dir /chemin/etat-main --register-dev-web /chemin/prive/dev.json`.
Le fichier doit appartenir à root, être en mode 0600 dans un dossier privé et
ne pas dépasser 16 Kio. L'enregistrement ne crée pas le Web ni sa base.

Le format fermé comprend `version: 1`, `descriptor`, `configuration`,
`preparation_sha256`, `main_configuration_sha256` et `debug_subjects`.
Le descripteur géré est version 2 ; son HTTP contient `instance`, `root`,
`webroot`, `service_user`, `hostname`, `port` et `maintenance_directory`.
Le worker contient `user`, `run_root` et `state_root`. Les chemins et comptes
doivent être exactement ceux du profil fresh Mobile v2 pour cette instance.
La configuration publique provient des sceaux du Web existant : mode upgrade,
base locale existante, aucun administrateur à créer, assistant préservé.
Les deux empreintes lient la préparation DEV et la configuration publique MAIN.
Le contrôle natif relit aussi les unités, comptes, sources et sceaux attendus.

`debug_subjects` est une liste triée de 1 à 16 UUID v4 explicitement autorisés.
Les comptes correspondants doivent déjà exister dans chaque Web. Le produit ne
copie ni compte, ni appareil, ni session. Les appareils de distribution restent
limités à MAIN ; l'accès debug à DEV exige aussi les politiques Web courantes.

Le cockpit propose ensuite « Raccorder aussi le Web DEV enregistré ». La case
est décochée à chaque rechargement avant plan. Le plan sélectionne l'empreinte
du descripteur et présente six étapes : préparation, démarrage et contrôle de
MAIN, puis de DEV. L'application exige son consentement distinct. GET et refresh
relisent uniquement les métadonnées, sans sonde, réparation ni redémarrage.

## Compatibilité et reprise

Les profils MAIN historiques gardent leurs champs, fragments et étapes exacts.
La variante à deux contextes est versionnée explicitement ; une dérive de cible,
de clé, de configuration ou de parent est refusée. Un effet incertain n'est pas
rejoué implicitement. Les reçus et intentions de démarrage sont distincts.

La maintenance DEV ferme son canal sans détour vers MAIN. La maintenance MAIN
continue de fermer le Gateway commun. Au nouveau PID 1, le boot Mobile démarre
les unités DEV déjà scellées uniquement si le garde DEV autorise le service.
Un garde DEV fermé reste fermé et MAIN peut démarrer. Une intention déjà émise
ne permet pas de redémarrer un processus disparu durant le même démarrage.

La sauvegarde MAIN conserve son périmètre MAIN et Gateway. Sur le serveur SQL
partagé, le verrou global admet seulement la base DEV liée au descripteur natif
scellé, en plus de MAIN. Cette exception est enregistrée dans le manifeste de
sauvegarde ; elle n'inclut pas les données DEV dans cette sauvegarde. Un troisième
schéma étranger reste refusé et le profil historique à une base reste fermé.
Le consentement au verrou global s'applique aussi à la suspension temporaire des
écritures SQL DEV pendant la sauvegarde MAIN.

## Qualification et limites

Le candidat ajoute 32 contrats locaux et un parcours Chromium HTTPS de sélection,
annulation, application et refresh. La recette composée utilise deux véritables
bases SQL et le binaire Gateway exact ; elle conserve aussi sauvegarde,
préparation, activation, FCM, QR, ACME privé et nouveau PID 1 du lot précédent.
Elle vérifie les autorisations dans chaque contexte, les clés et sessions
croisées refusées, la politique de compte DEV et l'absence d'appareil copié.
Les verdicts natifs et les manifestes exacts restent requis avant livraison.

Le premier gel `eb8606a` passe les trois CI Installer : 4 134 tests et 32
manifestes concordants. La recette native `37159389283` a atteint les deux
Foundation et le Gateway FCM, puis refusé la sauvegarde MAIN car le garde SQL
historique exigeait une seule base. Le correctif conserve cette règle historique
et ajoute un protocole v2 limité au DEV effectivement scellé. La recette corrigée
doit prouver à la fois l'admission de cette paire et le refus d'un troisième
schéma réel ; aucune suppression du contrôle ni exemption générale n'est ajoutée.

Le gel `df4aaf1` passe ensuite les trois CI Installer (4 144 tests, 32 manifestes,
501 fichiers). Le run natif `37160502888` prouve l'admission des deux bases et le
refus réel d'une troisième, puis retourne `GATEWAY_BACKUP_WEB_INCOMPLETE`.
L'artefact `11288150453` est conservé ; un diagnostic séparé sur les mêmes sources
(`37161938912`) ne journalise que des codes fermés, sans variables ni secrets.

Le candidat suivant évite une vérification HTTP redondante dans les checkpoints
de sauvegarde avec DEV : le `WebFence` natif, lié par identité à la même barrière,
revérifie déjà cette barrière puis ses propres inodes. Ce contrôle complet est
exécuté à chaque checkpoint, sans cache ni observation persistée. Les autres
fences restent contrôlés et le parcours historique sans DEV reste inchangé.
Quatre contrats supplémentaires refusent une barrière étrangère, un wrapper,
une barrière HTTP altérée, des inodes modifiés et une fence fermée.
La recette enregistre chaque fenêtre SQL et exige une libération normale en
moins de 180 secondes ; ni le délai natif ni le nombre de contrôles SQL ne sont
augmentés. Ce candidat reste à qualifier sur les moteurs natifs.

Le banc provisionne sa cible DEV séparément au port fixe avec le moteur Web
qualifié. Ce montage de recette ne constitue pas un assistant fresh DEV produit.
L'ajout de DEV sur un Gateway déjà scellé, la modification d'une cible enregistrée,
l'upgrade/rollback et la restauration d'origine relèvent du lot 3 (#17).
La réouverture après le test de maintenance DEV est explicite et confinée au banc.
Aucune réouverture automatique n'est revendiquée. Aucun certificat public,
reboot noyau, accès Firebase réel ni réception sur téléphone n'est revendiqué.
La clôture globale de phase 6 reste liée au lot 4 (#18).

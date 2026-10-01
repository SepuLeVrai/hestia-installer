# 6B7b7a — préparation durable de la reprise mobile

Base : Installer `e56375fb8e76b73649a16da8e2fb047053f6a0fe`, arbre
`82600999c9e14b084d0332eb5f490586c907baab`. Les trois campagnes Installer
36852206372 / 36852206289 / 36852206202 sont PASS. Sa composition native
36863158334 reste en cours au gel. Parent natif qualifié : 6B7b5b `e1a65ce`,
run 36848220815, 36 PASS et neuf mesures SQL sous la limite native de 180 s.

`installer.mobile_resume_plan` prépare un dossier privé dans les sauvegardes,
sous une véritable `DataAdmissionWindow` courante, après libération complète
des données. Chaque begin, recover, prepare et check vérifie la fenêtre à
l'entrée et à la sortie. Le lecteur natif des parents, les verrous de configuration,
les inventaires, les archives, les schedulers et la clôture SQL restent ceux de 6B7b6b.

Le plan lie l'instance, la maintenance, l'inode des sauvegardes, les plans fichiers,
réservations et données, tous leurs parents, le profil drainé et les unités exactes.
Il conserve les quatre journaux originaux : maintenance, garde mobile, Gateway
libéré et drainage HTTP. Leur lecture respecte les modes natifs (notamment
root:groupe applicatif 0640 pour maintenance/HTTP). Les copies sont root:root 0600,
le dossier 0700. Les originaux restent inchangés, y compris leurs métadonnées.

L'ordre prévu est PHP, Apache, Foundation, Gateway, timer du nettoyeur de sessions.
Les fragments, manifestes et toute l'identité native Gateway sont liés au plan.
L'ordre futur des bloqueurs est mobile puis Gateway, maintenance en dernier.
Ce sont des liaisons préparatoires, pas des intentions de démarrage.

## Interruptions et observations

Le plan est créé exclusivement avant les copies. Chaque fichier est synchronisé,
puis son dossier. Recover est en lecture seule ; prepare exige le SHA exact et
ne complète que les fichiers absents d'une préparation non terminée. Un plan
absent/tronqué, une copie partielle, un fichier étranger, un lien, un mauvais mode
ou un reçu terminal auquel manque une copie sont refusés sans réparation.
Une réponse perdue après une écriture durable se récupère sans réécriture.

Chaque fenêtre ajoute au plus une observation, plafonnée à 64 pour ce plan.
Toutes les observations conservées doivent correspondre exactement au fichier
observed.json de leur fenêtre native d'origine. Aucune n'est une admission active.
Une révocation tardive peut laisser un reçu historique préparé mais l'opération
échoue. Toute nouvelle étape nécessite une admission SQL fraîche. L'objet plan
ne conserve pas la fenêtre et ne traverse ni sérialisation ni changement de PID.

## Validation et frontière du lot

40 contrats sont ajoutés : 14 purs et 26 sur fichiers/verrous/processus réels,
dont SIGKILL après première copie et après reçu préparé. Les tests fichiers sont
réservés aux conteneurs Debian jetables ; leur frontière SQL/runtime/données est
explicitement isolée. Ils qualifient la préparation des fichiers, pas une nouvelle
composition native. Les anciens tests et StepSpecs ne changent pas.

Les trois campagnes Installer doivent être obtenues sur ce gel ; les verdicts
et identités exactes sont conservés dans les issues et le checkpoint de livraison.
La recette native 6B7b6b doit être qualifiée séparément avant de raccorder ce plan.

Aucun bloqueur n'est consommé, aucune maintenance n'est levée, aucun service n'est
lancé. La phase 6 reste ouverte. La suite doit concevoir une admission courante
pendant la consommation des journaux, puis observer chaque invocation de service
sans rejouer un start ambigu. Après le premier start, le lecteur de drainage
historique ne doit pas être repris : il arrêterait l'activité. La promotion des
branches actives et la construction APK restent hors de ce lot.

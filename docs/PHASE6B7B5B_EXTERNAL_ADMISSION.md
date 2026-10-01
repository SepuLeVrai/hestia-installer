# Phase 6B7b5b - admission vivante pendant la libération externe

## Base et frontière

Base qualifiée 6B7b5a : `f75be3c2c26d2c8a2d720f9117a16fd26b89e1ab`,
arbre `1b9c031653b67c08492535702960b0550336f3ee`. Ce lot raccorde la primitive
externe à une nouvelle admission SQL/fichiers et à une véritable réacquisition de
configuration après retrait des réservations. Aucune réouverture données, aucun
start ni retrait de bloqueur n'est effectué. Le profil reste MAIN local managed,
sans boot/public ; les étapes et journaux du sous-plan fichiers ne changent pas.

## Séquence et propriété des verrous

L'appelant crée d'abord le plan externe qualifié depuis le check DONE fichiers,
puis ferme normalement l'ancien ConfigurationLease lié aux réservations. La
nouvelle entrée privée exige les vrais objets de maintenance, drain, données et
Gateway attachés à la même instance, les credentials d'autorité SQL distincts
et la confirmation exacte du plan externe.

La composition prend les deux verrous exclusifs existants de configuration et
vérifie leurs inodes/contenus. Elle relit les trois inventaires natifs déprotégés,
les services arrêtés, la Gateway courante, les archives complètes et l'enveloppe
Web/configuration. Pendant une reprise partielle, l'ancien ConfigurationLease
ne serait pas valide : un observateur privé relit alors l'inventaire d'inodes
configuration sous les verrous exclusifs réels, avec les lecteurs d'entrées acquis.
Aucun ancien bail ou ReopenFilesPlan vivant n'est fabriqué pour contourner un refus.

Un nouveau répertoire `external-admission-<aléatoire>` conserve l'intention puis
un nouvel export SQL. Le SqlReadFence qualifié de 180 secondes reste détenu pendant
cet export, les contrôles avant effet, la libération journalisée et la réacquisition
finale. Apply, resume et check refont tous l'export ; un reçu antérieur n'est jamais
un cache d'admission. L'empreinte logique complète doit correspondre à la sauvegarde
restaurée qualifiée ; un simple compte de lignes ne suffit pas.

Après l'export, les archives, fichiers, services, schedulers et verrou SQL sont
revérifiés avant l'effet. La primitive externe conserve exactement ses intentions,
reçus et lecteurs bas niveau. Son point d'entrée privé sous verrous déjà détenus
exige un objet de garde lié au plan, au processus et au contexte encore ouvert.
Son API habituelle acquiert cette même garde : les tests 6B7b5a sont conservés.
La garde ne se sérialise pas et ne peut pas être utilisée après fermeture ou fork.

La composition libère ensuite les verrous exclusifs et acquiert un véritable
ConfigurationLease sans réservations externes. Le verrou SQL reste vivant ; les
contrôles complets sont refaits après cette acquisition, avant toute observation
finale. Une dérive pendant cette frontière est refusée. Les écritures administratives
privilégiées hors protocole ne sont pas déclarées empêchées par cette observation.

## Enveloppe exacte et reprise

L'enveloppe archive exige toujours le marqueur externe original exact. Selon l'état
natif journalisé, ce seul marqueur est conservé ou retiré, et le seul RELEASE exact
peut être ajouté pendant une interruption. Les cinq originaux, les intentions et
reçus du plan fichiers et les parents mobile/Gateway sont toujours vérifiés. Aucun
sous-arbre maintenance n'est ignoré ; tout fichier supplémentaire reste une dérive.

Sans intention externe exacte, l'absence de journaux natifs est refusée. Avec cette
intention et les preuves natives exactes, la reprise peut terminer après disparition
du dernier RELEASE, mais elle doit d'abord refaire l'admission SQL actuelle. Les
essais interrompus, exports partiels et observations historiques restent conservés
et ne sont ni écrasés ni adoptés comme succès.

La fenêtre finale appartient à son processus et à ses baux encore ouverts. Chaque
assert_held et la sortie normale contrôlent à nouveau le SQL, les schedulers, les
services, les archives, les inodes, la configuration réacquise et l'absence des
réservations. Une perte de bail, expiration ou dérive révoque la fenêtre. Même un
reçu externe déjà écrit ne permet pas de rouvrir les données ou de démarrer.

## Qualification du gel

20 nouveaux tests cœur : 14 contrats purs et 6 tests de fichiers/Ext4 en CI jetable.
Ils couvrent les phases de l'enveloppe, les entrées, la perte du SQL, les données
historiques, les gardes fermées/étrangères/forkées, l'inventaire configuration sous
verrous et la réacquisition réelle après libération. Les tests antérieurs restent
obligatoires, y compris les 18 scénarios Ext4 6B7b5a.

La recette Gateway MAIN native est prolongée après les assertions 6B7b4 : elle
introduit une vraie dérive SQL avant l'effet, exige son refus avant intention
externe, puis effectue un SIGKILL réel après unlink du dernier RELEASE. La reprise
reprend le même bail, refait un nouvel export, termine le reçu externe et réacquiert
la configuration. Une dérive de données et un journal maintenance étranger révoquent
la fenêtre finale. Les parents, clés, fermeture 0700 et bloqueurs sont conservés.
Le rapport dédié est `mobile-external-admission-native.json` ; la suite native garde
ses 36 tests historiques, avec une recette étendue et aucune assertion supprimée.

Les trois campagnes Installer et cette recette native doivent viser le même commit
figé avant qualification. SQL, comptes, systemd et Ext4 réels sont exclusivement
exécutés en CI jetable. Documentation gelée avant CI : résultats, identités exactes,
preuves et éventuel état en attente sont consignés dans les issues et la livraison.

## Suite

Après qualification de cette frontière, traiter la réouverture récupérable de
DataAccessFence, puis l'admission/consommation des bloqueurs et les starts avec
intentions et reçus dédupliqués. Ne jamais rappeler le drain après un premier
start effectif. Boot composé, restauration originale, DEV/FCM et recette globale
6C restent ouverts. Aucun jalon phase 6 global n'est clos par cette fenêtre.

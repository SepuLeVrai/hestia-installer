# Phase 6B7b5b — correction du budget de la recette native

## Résultat observé et base conservée

La recette Web `e0893c7557449260455ce02394b22b3c154044ee`, run `36820888356`,
épinglée sur Installer `b7159802a0973d96f8f8a38662b288e932274a3e`, a terminé en
échec après 57 minutes : 36 tests exécutés, zéro échec d’assertion, une erreur,
zéro skip et sources stables. Le diagnostic non secret indique
`normal-released-window-exit`, puis `SQL_FENCE_TIMEOUT` au dernier contrôle natif.
La libération externe, le SIGKILL, la reprise avec nouvel export et les refus de
dérive avaient été franchis ; leur qualification globale n’est pas acquise.
Les preuves de cette exécution sont conservées avec taille et SHA-256 vérifiés.

Le travail part de 6B7b6a `bc1cc59e20d4436a6b2e30beaf80fbf5e43c5fa2`, arbre
`e9c32a2cc1cf477f51fbd1c044e74cd7bf9a0c3d`, 417 fichiers, dont les trois campagnes
Installer sont vertes. Sa primitive données n’est pas appelée dans cette recette.
Les modules de production, les lecteurs, StepSpecs et tests cœur demeurent inchangés.
Le raccordement SQL/données 6B7b6b attend toujours la qualification du parent.

## Scénarios indépendants et bornes inchangées

Le scénario natif ne cumule plus la reprise, le rapport, les deux altérations et
leurs contrôles de sortie dans la même fenêtre SQL. Il utilise trois demandes
indépendantes : resume avec rapport et sortie normale ; check avec journal de
maintenance étranger ; check avec altération des données actuelles. Chacune
réacquiert réellement le verrou SQL et refait l’export. Le test exige trois slots
et trois empreintes d’export distincts. Aucun reçu historique n’autorise une fenêtre.

Toutes les assertions antérieures sont conservées : refus SQL avant intention,
SIGKILL après le dernier RELEASE, export frais, configuration réellement réacquise,
refus des deux dérives, refus d’une fenêtre fermée, conservation des observations,
des clés et parents, maintenance et données 0700. Les vérifications de sortie
normale restent actives pour chacune des trois fenêtres. Aucun lecteur n’est
simulé, aucune vérification n’est supprimée ou mise en cache.

La limite native de 180 secondes et le protocole SQL restent inchangés. Le test
écrit des durées non secrètes à l’entrée, avant sortie du consommateur et après
libération normale, puis exige neuf mesures inférieures à 180 secondes. Il ne
renouvelle ni ne modifie le délai. Le fichier
`mobile-external-admission-timings.json` est conservé même si un contrôle ultérieur
échoue ; le rapport final contient les mesures et les trois demandes fraîches.

La recette CI globale dispose de 85 minutes, avec un job de 90 minutes incluant
la préparation et la récupération des preuves, pour les deux exports indépendants
supplémentaires. Ce budget global ne change aucune borne d’une opération native.
La CI reste isolée, sans réseau pendant les tests, avec systemd et Ext4 jetables.
Aucun test natif n’est exécuté dans Work, LAB ou PROD.

## Gel et point d’arrêt

Les trois campagnes Installer doivent qualifier ce nouveau gel, puis une seule
nouvelle recette Web épinglée sur son commit/arbre exact. Les 36 tests natifs et
tous les tests Installer historiques restent présents. Les résultats définitifs,
identités, preuves et reprises sont consignés dans les issues et la livraison ;
les documents du dépôt conservent le statut au gel. Phase 6 ouverte, aucun start,
aucune promotion main/dev/dev-Bastien et aucun build APK.

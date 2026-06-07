README

-------
UTILITÉ
-------

InjectionCFG permet d'injecter dans un projet GNS3 des fichiers .cfg dans les routeurs correspondant. 

	injecté
RX.cfg-----------> RX, avec X un entier

--------------
MODE D'EMPLOI 
--------------

1.
Déposer les RX.cfg dans le dossier CFG_FILES
Renseigner le chemin du projet GNS3 dans PROJET_DIR
ATTENTION : Les noms des routeurs doivent être identiques à ceux des .cfg

2.
Lancer : InjectionCFG\inject_cfgs.py

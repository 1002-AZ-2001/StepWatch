# StepWatch

StepWatch documente une intervention informatique à partir des pixels visibles sur le poste Windows du technicien. Les captures et les rapports restent sur ce poste. Aucun agent n'est installé chez le client.

**Le dépôt fourni était vide.** La version 0.1.0 a fourni la première implémentation du cahier des charges ; elle ne constituait pas une correction vérifiée du logiciel historique 1.0.1, dont les sources n'étaient pas présentes. La version actuelle **0.2.0** ajoute les correctifs et améliorations issus de l'audit Claude fourni par l'utilisateur : capture et interface plus robustes, regroupement des doubles-clics, rapport autonome et diagnostic local.

## Installation et lancement sous Windows

Prérequis : Windows 10/11, Python 3.11 ou ultérieur (3.12 recommandé), installation officielle incluant Tcl/Tk et pip. Une connexion Internet est nécessaire pour installer les dépendances, puis l'application fonctionne localement. Exécutez les commandes depuis le dossier du dépôt dans PowerShell :

```powershell
.\scripts\install.ps1
.\scripts\launch.ps1
```

L'installation crée `.venv`, installe les versions de `requirements.txt` et vérifie leurs dépendances. Elle peut être relancée. Pour choisir un interpréteur précis :

```powershell
.\scripts\install.ps1 -Python "C:\Python312\python.exe"
```

Sans `-Python`, le script essaie le lanceur `py` dans l'ordre 3.12, 3.13 puis 3.11, et enfin la commande `python`. Il vérifie la version et l'import de Tcl/Tk avant de créer `.venv`. Un environnement existant non conforme est signalé, sans suppression automatique. Les autres versions peuvent être sélectionnées explicitement, mais la compatibilité complète des dépendances avec Python 3.14 ou ultérieur n'est pas établie. Les scripts PowerShell doivent encore être exécutés et validés sur Windows PowerShell 5.1 et PowerShell 7.

Si la politique PowerShell bloque les scripts, utilisez les commandes Python ci-dessous ou une autorisation limitée au processus, selon la politique de votre organisation. Aucun script ne modifie la politique de sécurité de la machine.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m stepwatch
```

## Enregistrer une intervention

1. Saisissez un nom et choisissez le dossier de destination.
2. Sélectionnez une fenêtre ouverte, un moniteur ou un rectangle personnalisé. **Actualiser** recharge la liste des fenêtres et des écrans. Les coordonnées d'écran utilisent des pixels physiques ; un écran placé à gauche peut avoir une origine négative.
3. Choisissez un mode : **Hybride** (clics locaux et changements visuels), **Clics uniquement** ou **Changements visuels uniquement**.
4. Réglez le délai après clic, l'intervalle de comparaison et la sensibilité. Le seuil représente une proportion de pixels modifiés : une valeur plus faible conserve davantage d'étapes. Le mode conservateur privilégie la conservation des petits changements.
5. Définissez si nécessaire des masques sensibles, puis cliquez sur **Démarrer**. Rien n'est enregistré automatiquement au lancement de l'application.
6. Utilisez **Pause**, **Reprendre** et **Arrêter**. L'aperçu, les compteurs et le journal montrent les captures conservées, les comparaisons ignorées et les erreurs.

Un double-clic (ou un triple-clic) du même bouton, au même endroit et dans le délai de double-clic de Windows, donne une seule étape « Double-clic local », capturée après le dernier appui : les appuis suivants doivent survenir avant la capture du premier, c'est-à-dire pendant le délai après clic (0,35 s par défaut). `steps.json` indique le nombre d'appuis dans `click_count`.

En mode **Clics uniquement**, la première capture attend un clic local : aucune image de référence n'est enregistrée au démarrage. Les modes hybride et visuel peuvent conserver une image initiale pour établir le début de l'intervention.

Les masques sont des rectangles `x,y,largeur,hauteur`, un par ligne, relatifs au coin supérieur gauche de la capture. Ils sont appliqués avant comparaison et enregistrement. Ils restent fixes par rapport à la capture : vérifiez leur position si la disposition ou la taille de la fenêtre change. Ils ne détectent pas automatiquement les mots de passe. N'ouvrez pas de données sensibles hors des zones masquées pendant une session.

Pour AnyDesk, TeamViewer ou RDP, sélectionnez la fenêtre locale du logiciel de télémaintenance. Une action visible du client distant est libellée **changement visuel détecté** : StepWatch ne peut pas connaître la position de son clic. Les marqueurs représentent uniquement les clics locaux captés par le hook souris. Aucune frappe clavier n'est enregistrée.

## Captures et rapports

Chaque intervention reçoit un dossier unique contenant les captures PNG dans `captures/`, les étapes dans `steps.json` et le rapport dans `rapport.html`. Ouvrez `rapport.html` avec un navigateur, sans serveur ni accès Internet. Conservez le dossier `captures/` à côté du HTML pour garder ses liens relatifs valides. `steps.json` enregistre les horodatages en UTC ; les rapports HTML les affichent en heure locale du poste, avec le décalage par rapport à UTC.

**Rapport autonome** (à côté de **Ouvrir le dossier**) écrit `rapport-autonome.html` dans le dossier de l'intervention : un seul fichier, captures intégrées, à joindre à un ticket ou à transmettre sans le dossier `captures/`. Seules les images PNG du dossier `captures/` de la session sont intégrées. Ce fichier contient toutes les captures : protégez-le comme le dossier lui-même. Il est plus lourd que les PNG réunis (encodage base64, environ +33 %).

L'import **ShareX** lit les images du dossier choisi (sans parcourir les sous-dossiers), les classe par date de modification puis nom, et les copie dans une nouvelle intervention. Les fichiers sources ne sont jamais supprimés ni modifiés. La date de modification n'est pas toujours la date de prise de vue : vérifiez l'ordre après un transfert ayant changé ces dates. Si une image est illisible, l'import signale l'erreur et conserve les images déjà copiées dans le dossier de sortie indiqué. Les masques de capture ne s'appliquent pas à cet import : vérifiez au préalable que les images ShareX peuvent être exportées telles quelles.

Les captures peuvent contenir des données confidentielles. Choisissez un dossier adapté et gérez vous-même sa protection, sa sauvegarde et sa durée de conservation. StepWatch ne transmet aucune capture sur Internet et n'ajoute aucune télémétrie ; un dossier synchronisé (OneDrive, partage réseau sauvegardé dans le cloud…) enverrait en revanche les captures hors du poste.

Si un antivirus, l'indexeur ou un outil de synchronisation verrouille brièvement un fichier, l'écriture est réessayée. Si `steps.json` ou `rapport.html` ne peut toujours pas être réécrit, la capture PNG est conservée, le journal le signale et l'enregistrement continue : une nouvelle écriture est tentée à la capture suivante et à l'arrêt. Si le verrou persiste jusqu'à la fermeture, les rapports peuvent rester incomplets malgré la présence des PNG ; conservez le dossier et le journal pour récupérer les captures et reconstituer les étapes manquantes. Le programme ne garantit pas la récupération des métadonnées non écrites après la fin du processus.

## Journal de diagnostic

StepWatch tient un journal local pour le support : `%LOCALAPPDATA%\StepWatch\logs\stepwatch.log` sous Windows (`~/.local/state/stepwatch/` ailleurs), en rotation sur 4 fichiers de 1 Mo au plus. Le chemin est rappelé au démarrage dans le journal de l'interface. Il reprend les messages de ce journal (sauf les comparaisons ignorées, trop fréquentes) et ajoute la pile d'appels des erreurs, y compris celles des rappels de l'interface et des threads, qui seraient sinon perdues dans l'exécutable sans console. `crash.log`, dans le même dossier, recueille les plantages natifs.

Ce journal ne contient ni image ni frappe clavier, mais il peut contenir des noms d'intervention, des titres de fenêtres et des chemins de dossiers. Il reste sur le poste ; relisez-le avant de le transmettre.

La rotation concerne `stepwatch.log` et ses trois sauvegardes ; `crash.log` est un fichier séparé ajouté à chaque plantage natif. L'interface ne garde qu'un aperçu en attente lorsque son affichage prend du retard : les PNG et les messages de chaque étape sont conservés.

## Limites de la capture Windows

- La capture utilise les pixels du bureau visible. Gardez la fenêtre choisie visible et dégagée : une fenêtre superposée peut apparaître dans l'image. Le suivi de fenêtre recalcule sa position et sa taille.
- Un clic local n'est rattaché à la fenêtre choisie que s'il tombe dans sa zone, sur elle-même ou sur une fenêtre qu'elle possède (boîte de dialogue, message d'erreur). Un clic sur une autre fenêtre superposée est ignoré.
- Une fenêtre fermée, réduite ou invisible produit une erreur explicite. Consultez le journal et sélectionnez une cible valide avant de reprendre une session.
- Le bureau sécurisé UAC, certains contenus protégés, les fenêtres élevées et les changements de session peuvent empêcher la capture ou la réception des clics. Ne présumez pas que ces écrans ont été documentés.
- La comparaison visuelle atténue les petites variations et les mouvements isolés, sans garantir l'élimination de toute animation ou la conservation de toute modification très petite. Ajustez le seuil et l'intervalle pour votre intervention.
- Le mode conservateur augmente la sensibilité et peut conserver davantage de captures. Une modification plus brève que l'intervalle peut ne pas être observée.
- L'arrêt attend la fin des opérations en cours pour finaliser les fichiers et libérer les hooks. Un appel natif bloqué peut retarder la fermeture : un thread Python ne peut pas être interrompu de force sans risque pour les ressources et les écritures. Une interruption forcée du processus peut laisser un rapport incomplet.

## Tests automatisés

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Les tests portent sur les rectangles et formats `mss`, les fenêtres simulées, les comparaisons, les déclencheurs, le cycle de vie des ressources, les rapports et la préservation des images importées. Ils peuvent être exécutés sous Linux pour valider la logique indépendante du système :

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

Les tests de l'interface sont optionnels et ignorés par défaut. Avec Tcl/Tk et un serveur X de test déjà démarré (par exemple `Xorg` ou `Xvfb` sur `:99`), activez-les ainsi :

```bash
DISPLAY=:99 STEPWATCH_GUI_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

Ces tests utilisent une vraie interface Tk et simulent le backend Windows ; ils ne valident pas les hooks Windows. `test_live_capture.py` vérifie aussi une vraie capture MSS d'une fenêtre de test sur l'affichage Linux, son masquage et son export PNG/JSON/HTML.

Vérifiez le bilan du runner : les tests graphiques ignorés faute d'affichage ne comptent pas comme réussis. Complétez les tests par `.venv/bin/python -m pip check` sous Linux, ou `.\.venv\Scripts\python.exe -m pip check` sous Windows. Le nombre de tests évolue avec le code ; utilisez les résultats de votre exécution pour valider la version installée.

Les tests avec mocks ne constituent pas une validation des API, hooks ou captures sur Windows réel. L'enregistrement nécessite Windows et une session graphique interactive. Aucune capture Windows réelle ni compilation Windows n'est attestée par les seuls tests exécutés dans l'environnement Linux de développement.

### Validation manuelle sur Windows

Avant de diffuser l'application :

1. Lancez-la et vérifiez qu'aucune capture ne commence avant **Démarrer**.
2. Enregistrez une fenêtre Bloc-notes : clics gauche/droit, double-clic, déplacement, redimensionnement, pause et reprise. Contrôlez les marqueurs, les compteurs et l'ordre des étapes.
3. Réduisez puis fermez la cible et vérifiez que les erreurs sont visibles, sans plantage ni perte silencieuse de capture.
4. Testez chaque moniteur, des origines négatives et plusieurs mises à l'échelle DPI. Vérifiez les coordonnées des clics et des masques.
5. Testez un changement distant dans AnyDesk, TeamViewer ou RDP sans clic local : le libellé doit rester un changement visuel.
6. Testez une zone masquée : aucun pixel sensible ne doit apparaître dans les PNG, l'aperçu ou le rapport. Vérifiez de nouveau après redimensionnement.
7. Arrêtez puis relancez une session et fermez l'application pendant un enregistrement. Vérifiez le rapport, la libération des hooks et l'absence de capture après l'arrêt.
8. Importez des images ShareX, ouvrez le rapport hors ligne et comparez les sources avant/après.

## Construire un exécutable

La compilation doit s'effectuer sur Windows, avec le même type d'architecture que les postes ciblés :

```powershell
.\scripts\install.ps1
.\scripts\build.ps1
```

Le script installe `requirements-dev.txt`, exécute les tests puis appelle PyInstaller. Il embarque explicitement `scripts/StepWatch.manifest` pour déclarer la gestion DPI par moniteur (`PerMonitorV2`, puis `PerMonitor`) dès le lancement de l'exécutable, avant l'initialisation de Python et de Tk. Le résultat se trouve dans `dist\StepWatch\StepWatch.exe`. Distribuez **tout le dossier** `dist\StepWatch`, pas seulement le fichier `.exe`. Aucun Python n'est requis sur le poste de destination pour cette distribution.

Cette méthode prépare une application portable ; elle ne fournit pas d'installeur MSI, de signature de code ni de validation antivirus. Testez le résultat sur un poste Windows distinct. PyInstaller ne produit pas un `.exe` Windows depuis Linux.

## Architecture

- `models.py` : cibles, rectangles et paramètres validés.
- `platform.py` : énumération et capture Windows, gestion des moniteurs et du DPI.
- `imaging.py` : masquage, marqueurs et comparaison des images.
- `engine.py` : capture dans un thread, déclencheurs et événements à destination de l'interface.
- `storage.py` : images, JSON, rapport hors ligne et import ShareX.
- `ui.py` : interface Tkinter/ttk en français ; le moteur prépare les miniatures avant leur affichage par Tk.
- `diagnostics.py` : journal de diagnostic local en rotation et capture des exceptions non gérées.

Tkinter est fourni avec Python sous Windows et évite d'ajouter un framework lourd à ce dépôt initialement vide. Les dépendances de capture sont Pillow, `mss` et `pynput` (sur Windows). Aucun service réseau, serveur ou compte externe n'est nécessaire pour enregistrer.

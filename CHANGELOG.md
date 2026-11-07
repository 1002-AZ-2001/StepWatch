# Journal des modifications

## Non publié

- GitHub Actions prépare une distribution portable et un exécutable unique pour Windows, ainsi qu'un paquet `.deb` amd64 pour Linux. Les builds sont déclenchés par les push vers `main`, les pull requests, les tags `v*` ou manuellement ; un tag égal à la version du projet permet une publication dans les Releases après réussite.
- Le paquet Linux embarque Python, ajoute un raccourci et déclare les dépendances système ; l'import ShareX et les rapports sont disponibles, tandis que l'enregistrement reste réservé à Windows. Le minimum glibc tient compte du système de compilation, avec un plancher à 2.35.
- Tests de structure du paquet Debian, des permissions, des dépendances et du rejet des liens sortants ; documentation du téléchargement des artefacts et de l'installation.

## 0.2.0 — 2026-11-07

Intègre les correctifs et améliorations issus de l'audit Claude fourni par l'utilisateur, puis revus dans le dépôt. Les appels natifs, les scripts PowerShell et l'exécutable restent à valider sur Windows réel.

### Corrections

- Comparaison visuelle sous-échantillonnée sur les grandes cibles en mode normal pour limiter le coût des filtres ; le mode conservateur reste en pleine résolution. La comparaison n’est plus calculée pour les captures déclenchées par un clic ; la capture MSS est décodée directement depuis le tampon BGRA. Le temps réel dépend de la cible, du mode et du matériel.
- Les clics sont localisés au moment du clic, dans le hook souris, et non plus quand le thread de capture se libère : un clic droit ou un bouton ouvrant un menu, une liste ou une boîte de dialogue sous le curseur n’est plus perdu silencieusement, et le marqueur reste juste si la fenêtre a bougé entre-temps.
- Les clics sur une boîte de dialogue ou un message appartenant à la fenêtre cible (fenêtre possédée) sont conservés ; ils étaient écartés sans trace.
- Une erreur pendant le traitement d’un événement n’arrête plus la boucle de suivi de l’interface (elle figeait l’affichage et laissait grossir sans fin la file d’images du moteur). L’aperçu ne provoque plus de redessins en cascade ; le moteur envoie une miniature bornée (1600 × 1000) au lieu de l’image pleine résolution. Les derniers messages d’une session (erreur de rapport à l’arrêt) ne sont plus perdus quand une nouvelle session démarre aussitôt.
- Sous Windows, un fichier brièvement verrouillé (antivirus, indexeur, OneDrive) n’interrompt plus l’enregistrement : le remplacement atomique est réessayé, et un échec d’écriture du seul rapport après l’enregistrement du PNG n’est plus fatal. Une image ShareX trop grande (protection contre les bombes de décompression de Pillow) est signalée avec le dossier partiel comme les autres erreurs.
- Validation des entrées : des masques fournis par un générateur n’étaient plus appliqués (zones sensibles visibles) ; un booléen était accepté comme identifiant de fenêtre ; les erreurs de saisie numérique s’affichaient en anglais sans nommer le champ ni la ligne de masque ; un dossier de rapports relatif dépendait du dossier courant du raccourci de lancement.
- La fenêtre s’adapte à l’écran et au DPI : 1190 × 820 pixels dépassaient un écran 1366 × 768 (journal hors écran) et, à 125–150 %, la colonne de réglages restait à 420 pixels physiques avec des polices agrandies, ce qui tronquait les libellés.
- `install.ps1` vérifie la version de Python et la présence de Tcl/Tk avant de créer `.venv` (Python 3.12, 3.13 puis 3.11 via le lanceur `py`, puis `python`) ; un `.venv` existant non conforme est signalé avec la marche à suivre au lieu de faire échouer chaque relance. Un autre interpréteur peut être indiqué avec `-Python`. Les scripts PowerShell sont enregistrés en UTF-8 avec BOM pour Windows PowerShell 5.1. La sélection de l'interpréteur ne constitue pas une validation de toutes ses dépendances ; les scripts n'ont pas été exécutés sous Windows dans l'environnement Linux de développement.
- Si la boucle Tk se termine sans passer par la fermeture normale (Ctrl+C dans la console de `launch.ps1`, exception), l’enregistrement est arrêté : le thread de capture continuait d’enregistrer sans interface, hook souris compris, et empêchait le processus de se terminer.
- Revue d'intégration : un clic commencé avant une pause ne réapparaît plus après la reprise ; seules les séquences consécutives peuvent former un double-clic. La file ne conserve qu'un aperçu en attente, sans perdre les événements ni les erreurs.
- L'export autonome refuse aussi les chemins absolus et les liens du répertoire `captures/` ; il retente la finalisation de la session courante avant de lire ses métadonnées. Une erreur de fermeture est signalée et n'interrompt plus la procédure de suivi de l'arrêt.
- Les hooks de diagnostic conservent leur chaîne initiale après nettoyage, préservent les remplacements externes et libèrent leurs fichiers à la sortie de l'application.

### Améliorations

- Un double-clic ou un triple-clic produit une seule étape « Double-clic local », capturée après le dernier appui, au lieu de deux ou trois étapes quasi identiques ; le délai et la zone de double-clic de Windows sont respectés. Les annotations nomment le bouton en français (« Clic droit local. ») et `steps.json` ajoute `click_count`.
- Export d’un rapport autonome (`rapport-autonome.html`) : un seul fichier, captures PNG intégrées, à joindre à un ticket ; seules les images du dossier `captures/` de la session sont acceptées. Les rapports affichent l’heure locale du poste avec son décalage UTC ; `steps.json` reste en UTC.
- Journal de diagnostic local en rotation (`%LOCALAPPDATA%\StepWatch\logs`) : messages du journal de l’interface et pile d’appels des erreurs du moteur, des rappels Tk et des threads, auparavant perdues dans l’exécutable sans console ; `crash.log` pour les plantages natifs. Aucune image ni frappe clavier.

## 0.1.0 — 2026-11-03

Première implémentation dans le dépôt StepWatch fourni, initialement vide. Aucun code de la version historique 1.0.1 n'était disponible ; cette livraison ne prétend donc pas en être une correction vérifiée.

- Interface française avec choix de fenêtre, de moniteur ou de zone, paramètres de détection, aperçu et journal.
- Capture déclenchée par les clics locaux, les changements visuels ou les deux.
- Comparaison d'images, gestion des masques sensibles, marqueurs de clic et limitation des doublons.
- Sessions locales avec images PNG, métadonnées JSON et rapport HTML hors ligne.
- Import de captures ShareX sans suppression des sources.
- Adaptation des rectangles de moniteurs fournis sous forme de dictionnaires ou d'objets.
- Tests automatisés avec simulations du système Windows ; scripts PowerShell d'installation, de lancement et de compilation PyInstaller.
- Manifeste Windows explicite pour le DPI par moniteur dès le démarrage de l'exécutable ; tests optionnels de l'interface Tk sous affichage X.

La validation sur un poste Windows réel, notamment DPI, multi-écrans, hooks et télémaintenance, reste nécessaire avant une utilisation en production.

# Journal des modifications

## Non publié

Rien pour l'instant.

## 1.2.0 — 2026-11-27

Moins de raisons pour les antivirus de se méfier de StepWatch.

- **Clics lus par Raw Input sous Windows**, l'API documentée par Microsoft pour lire un périphérique, à la place d'un hook système bas niveau (`SetWindowsHookEx`, bibliothèque pynput). L'exécutable ne contient plus aucun code d'écoute du clavier, motif typique des logiciels espions. StepWatch ne s'abonne qu'à la souris. Autre bénéfice : Windows ne peut plus retirer silencieusement l'écoute quand une application ralentit le système.
- **CI Windows** : un vrai clic (SendInput) dans une fenêtre de test doit devenir une étape, avec la nouvelle écoute.
- **Guide [Antivirus et SmartScreen](docs/antivirus.md)** :
  - différence entre programme inconnu (SmartScreen, classification WatchGuard EPDR) et détection nommée ;
  - marche à suivre dans la console WatchGuard EPDR (déblocage, exclusion par signature) ;
  - texte prêt à copier pour déclarer un faux positif à Microsoft ou à WatchGuard.
- Dépendance retirée : pynput.

## 1.1.0 — 2026-11-27

Synchronisation vers un serveur, installateur Windows, moins de faux positifs antivirus et corrections.

- **Synchronisation des guides** (Paramètres › Synchronisation, Fichier › Synchroniser le guide, Ctrl+Maj+S) :
  - destinations : Nextcloud ou WebDAV (HTTPS obligatoire, certificat vérifié), dossier réseau (NAS, `\\nas\partage`), serveur SFTP (mot de passe ou clé SSH) ;
  - envoi automatique à l'arrêt de l'enregistrement, désactivable ; test de la connexion dans les Paramètres ;
  - captures puis rapports, chaque fichier sous un nom temporaire puis renommé ; captures inchangées non renvoyées (reprise après coupure) ; un seul listage par dossier distant ;
  - captures des étapes supprimées jamais envoyées et retirées du serveur ;
  - SFTP : empreinte d'un serveur inconnu à confirmer, empreinte modifiée refusée ;
  - mots de passe dans le coffre du système (keyring), jamais dans settings.json.
  Validé contre Nextcloud 35 et un serveur OpenSSH réels.
- **Installateur Windows** `StepWatch-VERSION-windows-x64-setup.exe` (Inno Setup) : installation dans le profil sans droits administrateur, menu Démarrer, désinstallation ; testé par la CI (installation silencieuse, auto-test, désinstallation). La mise à jour automatique le reconnaît et relance l'installateur en mode silencieux.
- **Moins de faux positifs antivirus** :
  - chargeur PyInstaller recompilé par la CI ;
  - pas de compression UPX ;
  - informations de version et icône dans les exécutables ;
  - CI prête pour la signature gratuite de la SignPath Foundation (activée dès l'acceptation du projet) ;
  - [politique de signature](CODE_SIGNING_POLICY.md) et guide [Antivirus et SmartScreen](docs/antivirus.md).
- **Corrections** :
  - en mode barre flottante, fermer la fenêtre « Ajouter une note » faisait quitter StepWatch en plein enregistrement (1.0.0) ;
  - une fenêtre maximisée ne l'était plus après la barre flottante ;
  - SFTP : l'empreinte d'un serveur sur un port autre que 22 était mal enregistrée (constaté sur un vrai serveur avant publication).
- **Auto-test** : vérifie aussi la synchronisation et la présence des modules WebDAV, SFTP et du coffre.

## 1.0.0 — 2026-11-22

Nouvelle interface Qt.

- **Interface réécrite avec Qt (PySide6 6.12, licence LGPL v3)**, en remplacement de Tkinter. Mêmes fonctions, mêmes raccourcis et mêmes préférences : `settings.json` est repris tel quel.
- **Wayland natif** : sans XWayland, StepWatch s'ouvre maintenant au lieu de refuser de démarrer. Avec XWayland, il passe par lui pour que la barre flottante reste au premier plan, à la position choisie. `STEPWATCH_WAYLAND=natif` force le Wayland natif.
- **Thème clair, sombre ou comme le système** (Affichage › Thème, Paramètres › Interface). Le thème et la taille du texte s'appliquent sans redémarrer.
- **Icône dans la zone de notification** : démarrer, pause/reprise, note, arrêt, quitter. Désactivable dans Paramètres › Interface.
- **Mise à l'échelle par Qt** sur les écrans haute résolution. Les zones de StepWatch exclues des captures sont converties en pixels physiques ; le cas de plusieurs écrans à des échelles différentes reste à vérifier sur un vrai poste (étape 16 de la validation).
- **Dialogues standard en français** (traduction Qt), coches dessinées dans les cases, menus et listes plus lisibles.
- **Éditeur de guide** : une modification reste possible pendant le chargement d'un aperçu.
- **Paquets Linux** :
  - greffons Qt inutiles retirés (GTK, eglfs, VNC…), ainsi que les bibliothèques devenues orphelines ;
  - bibliothèques X11, Wayland, OpenGL, GLib, D-Bus et de polices fournies par le système ;
  - paquet `.deb` de 39 Mo au lieu de 27 Mo avec Tk.
- **Tests** : les tests de l'interface tournent hors écran (Qt « offscreen ») ; la CI vérifie l'auto-test sous X11, sous Wayland avec XWayland et en Wayland natif.

## 0.10.0 — 2026-11-22

Mises à jour automatiques.

- **Recherche des mises à jour** au démarrage, au plus une fois par jour, et à la demande dans **Aide › Rechercher des mises à jour…**. Désactivable dans **Paramètres › Général**. Une version trouvée pendant un enregistrement est proposée à l'arrêt.
- **Fenêtre « Mise à jour disponible »** : notes de version, **Installer et redémarrer**, **Plus tard**, **Ignorer cette version**, **Voir sur GitHub**, avec la progression du téléchargement.
- **Installation selon la forme de StepWatch** :
  - exécutable Windows : remplacé en place ; l'ancien fichier est effacé au démarrage suivant ;
  - dossier portable : fichiers copiés par un script PowerShell après la fermeture, puis redémarrage ;
  - paquets Debian/Ubuntu et Arch Linux : `pkexec apt-get install` ou `pkexec pacman -U` ;
  - depuis les sources : lien vers la page de la version.
- **Vérification SHA-256 obligatoire** avant toute installation : empreinte GitHub du fichier et fichier `SHA256SUMS` de la version, qui doivent concorder. HTTPS uniquement. Les certificats du système sont utilisés si Python embarqué ne trouve pas les siens (paquets Linux).
- Après une mise à jour, la nouvelle version attend la fermeture de l'ancienne avant de démarrer (instance unique).

## 0.9.0 — 2026-11-21

Barre de menus et fenêtre Paramètres.

- **Barre de menus**, inspirée de MusicScope :
  - **Fichier** : ouvrir un guide, guides récents, import ShareX, exports, Paramètres, quitter ;
  - **Enregistrement** : démarrer, pause, note, arrêt, actualiser les cibles, barre flottante ;
  - **Guide** : annuler, titre, monter, descendre, masquer, supprimer ;
  - **Affichage** : onglets, réglages avancés ;
  - **Aide** : raccourcis, documentation en ligne, signaler un problème, dossiers du journal et des paramètres, vérifier l'installation, à propos.
  Les entrées indisponibles sont grisées. Nouveaux raccourcis : Ctrl+O, Ctrl+I, Ctrl+E, Ctrl+R, Ctrl+1/2/3, F5, F1, Ctrl+, et Ctrl+Q ; ils sont ignorés dans une zone de texte, où ils ont un sens d'édition.
- **Paramètres…** (Ctrl+,), en quatre onglets :
  - nom proposé pour un nouveau guide, avec `{date}` et `{heure}` ;
  - position de la barre flottante (en bas ou en haut) ;
  - raccourcis globaux activables ;
  - signal sonore à chaque étape ;
  - onglet Guide et rapport autonome automatiques à l'arrêt ;
  - confirmation de suppression ;
  - conservation ou effacement de la capture d'une étape supprimée : l'effacement est toujours confirmé et ne s'annule pas ;
  - taille du texte de 90 à 130 % ;
  - réglages avancés ouverts au démarrage.
  **Réinitialiser** garde la liste des guides récents.
- **Vérifier l'installation** (menu Aide) lance l'auto-test des images, rapports et import, sans réseau.

## 0.8.0 — 2026-11-21

Confort d'utilisation : StepWatch se souvient de vos réglages, se pilote au clavier et permet de revenir sur une modification du guide.

- **Préférences retenues** entre deux lancements : dossier des guides, cible, mode, réglages avancés et leur ouverture, masques, barre flottante, taille de la fenêtre. Fichier local `settings.json` (`%APPDATA%\StepWatch` ou `~/.config/stepwatch`) ; un fichier absent, invalide ou non inscriptible n'empêche jamais le lancement.
- **Raccourcis d'enregistrement** : Ctrl+Maj+F9 pause/reprise, Ctrl+Maj+F10 note, Ctrl+Maj+F11 arrêt. Sous Windows, ils fonctionnent dans toutes les applications grâce à `RegisterHotKey`, qui ne signale que ces combinaisons : aucune autre frappe n'est observée. Une combinaison déjà prise par une autre application est signalée dans le journal. Sous Linux, ils fonctionnent dans les fenêtres de StepWatch.
- **Raccourcis de l'éditeur** : Suppr, Ctrl+↑/↓, Ctrl+Z, F2. Ils n'agissent pas pendant la saisie d'un texte ni quand l'onglet Guide est masqué.
- **Annuler** : titre, description, nom du guide, ordre et suppression d'une étape (la capture revient de `captures/retirees/`). Le masquage reste définitif.
- **Guides récents** dans **Ouvrir un guide ▾** : dossiers déjà ouverts et interventions du dossier des guides, les plus récents d'abord.
- **Instance unique** : un second lancement affiche un message et s'arrête (code 3), au lieu d'installer un second hook souris. L'auto-test reste possible pendant que StepWatch est ouvert.

## 0.7.0 — 2026-11-19

Nouvelle interface et éditeur de guide, inspirés d'OpenSteps pour l'usage : StepWatch se fait discret pendant l'enregistrement, et l'intervention se met en forme avant d'être partagée.

- **Interface** : thème clair (surfaces blanches, accent bleu), en-tête avec icône et pastille d'état colorée, bouton **● Enregistrer**, onglets **Enregistrer**, **Guide** et **Journal**. Les réglages de détection et les masques sont regroupés dans **Réglages avancés**, replié par défaut.
- **Barre flottante** pendant l'enregistrement : la fenêtre principale se retire au profit d'une petite barre (état, nombre d'étapes, Pause/Reprendre, Note, Arrêter, retour à la fenêtre), déplaçable. Les clics sur la barre ou sur la fenêtre principale ne deviennent jamais des étapes. Sous Windows 10 2004+ et 11, la barre est exclue des captures (`SetWindowDisplayAffinity`) ; ailleurs, les parties de StepWatch qui recouvrent la cible sont noircies dans les captures. Option pour garder la fenêtre principale.
- **Éditeur de guide** (onglet **Guide**) : titre du guide, titre et description de chaque étape, ordre (↑ ↓), suppression et **Masquer une zone…** à la souris. Chaque modification réécrit `steps.json`, `rapport.html` et `rapport.md`. La capture d'une étape supprimée est déplacée dans `captures/retirees/`. Un rapport autonome devenu obsolète est supprimé, pour ne jamais partager une version qui contient une zone non masquée ou une étape retirée. **Exporter le guide** regroupe le rapport autonome, le Markdown et l'ouverture du dossier.
- **Rapports** : un titre d'étape saisi remplace le type d'étape dans les titres HTML et Markdown ; le type reste affiché comme une pastille. `steps.json` gagne un champ facultatif `title` (format 1 inchangé, compatible avec les versions précédentes).
- Icône de fenêtre et de dock dessinée comme `packaging/linux/stepwatch.svg`.

Testé sous Linux (Xvfb, Python 3.12) : suite complète, nouveaux tests de l'éditeur, de la barre flottante et de l'exclusion des clics, et un enregistrement X11 réel d'un moniteur entier où la barre est noircie dans la capture. Sous Windows, les tests d'interface de la CI appellent `SetWindowDisplayAffinity` sur la barre ; son effet sur les captures reste à vérifier sur un poste réel (liste de validation).

## 0.6.0 — 2026-11-18

StepWatch enregistre désormais les interventions sous Linux, en plus de Windows. Les captures et les rapports restent sur le poste du technicien.

- Enregistrement sous **X11** avec sélection d'une fenêtre, d'un moniteur ou d'une zone et détection des clics et des changements visuels.
- Enregistrement des changements visuels sous **Wayland** avec sélection de la source dans le portail du bureau et réception des images via PipeWire. La fenêtre StepWatch utilise XWayland.
- L'actualisation des cibles conserve l'identité de la fenêtre sélectionnée même quand plusieurs fenêtres portent le même titre. Une cible disparue doit être resélectionnée.
- Le décodage MSS en RGB évite une copie de 31,6 Mio par image UHD. La file de clics est bornée et signale les clics écartés quand cette limite est atteinte.
- Un échec temporaire d'écriture des rapports lors d'un import ShareX est réessayé. L'export autonome vérifie l'intégrité des PNG avant de remplacer le rapport existant.
- L'import ShareX traite l'orientation en place. StepWatch dessine l'aperçu de capture quand son onglet est visible.
- La fermeture de la vue Étapes attend la fin du travail de lecture même lorsqu'une lecture a échoué avec une erreur de délai.

Sur trois PNG 4K synthétiques, le pic mémoire d'import passe de 141 à 47 Mio. La capture Wayland a été testée de bout en bout sous Sway virtuel. Le guide de distribution détaille les composants du bureau nécessaires.

## 0.5.0 — 2026-11-18

Cette version permet de rouvrir et relire les interventions dans StepWatch. Elle réduit aussi le travail de comparaison des captures et répartit la capture et l'encodage PNG entre deux threads.

- Barre de capture compacte et onglets **Capture**, **Étapes** et **Journal**. L'onglet Étapes rouvre les interventions existantes et charge l'aperçu de la sélection sur demande.
- La capture et l'encodage PNG peuvent avancer sur des cœurs distincts. L'écriture reste ordonnée, avec une seule image soumise à la fois.
- Les images identiques évitent le filtrage. Le mode normal réutilise une référence réduite et le mode conservateur limite ses allocations temporaires en comparant par bandes. L'export autonome encode le PNG par blocs.
- Libération explicite des objets Tk sur le thread graphique après arrêt des traitements, afin d'éviter un abandon natif de Tk 8.6 lors d'un ramasse-miettes déclenché depuis un autre thread.
- Test de régression pour la fermeture de Tk. En cas d'échec dans la CI Linux, les traces natives sont conservées avec le journal.

Sur 20 captures 4K synthétiques sous Linux, le temps CPU passe de 5,62 à 3,98 s et le pic RAM de 260 à 156 Mio. Les détails figurent dans le document de validation.

## 0.4.0 — 2026-11-09

Linux : paquet Arch Linux, Debian 12 vérifié, et fonctionnement sous Wayland. Sous Linux, StepWatch propose toujours l'import ShareX et les rapports ; l'enregistrement reste réservé à Windows.

- **Paquet Arch Linux** `stepwatch-VERSION-1-x86_64.pkg.tar.zst`, construit par `makepkg` sans privilèges dans un conteneur Arch officiel (`scripts/build_arch.py` prépare le PKGBUILD). La CI l'installe, le teste sous X11 et sous Wayland, puis le retire. Il est publié dans les Releases avec sa somme SHA-256.
- **Debian 12** : la CI installe le `.deb` dans un conteneur `debian:12-slim`, exécute l'auto-test de l'interface, puis retire le paquet.
- **Wayland** : Tk n'a pas de backend Wayland natif et s'affiche par XWayland. StepWatch le vérifie dans un vrai compositeur (weston en mode headless, avec XWayland) sous Ubuntu 22.04 et Arch Linux. Sans XWayland, un message explique quel paquet installer (`xwayland` ou `xorg-xwayland`), au lieu d'une erreur Tk ; le code de sortie est 2. Les paquets le suggèrent (`Suggests` Debian, `optdepends` Arch).
- **Icône dans les docks** : la fenêtre déclare la classe X11 `Stepwatch` (au lieu de `Tk`), et l'entrée de menu déclare `StartupWMClass=Stepwatch` : GNOME, KDE et les autres bureaux associent la fenêtre à l'icône StepWatch, y compris sous XWayland.
- **Polices sous Arch Linux** : les paquets n'embarquent plus les bibliothèques X11 et de polices du système de compilation (fontconfig, freetype, Xft, X11…). Le fontconfig d'Ubuntu 22.04 ne savait pas lire la configuration d'Arch, et Tk s'arrêtait sur « failed to allocate font ». Ces bibliothèques viennent désormais du système cible ; elles sont déclarées en dépendances (`libxft2`/`libxft` ajoutées), ainsi qu'une police (`ttf-font`) pour Arch.
- **`stepwatch --self-test`** vérifie une installation sur une session synthétique : images, masques, rapports HTML et Markdown, note, rapport autonome, import ShareX, puis ouverture et fermeture de la vraie fenêtre si un affichage est disponible. `stepwatch --version` affiche la version.

## 0.3.0 — 2026-11-09

Première version publiée dans les Releases GitHub, pour Windows (exécutable unique et archive portable) et Linux (paquet `.deb`). Comme pour la 0.2.0, les hooks, la capture et les exécutables Windows sont vérifiés par la CI Windows, mais restent à valider sur un poste réel.

- **Une Release par version** : quand une nouvelle version de `pyproject.toml` arrive sur `main`, la CI publie la Release `vX.Y.Z` après tous les tests et le contrôle des paquets. Les notes viennent de la section correspondante de ce journal ; une version déjà publiée n'est jamais remplacée depuis `main`. Un tag `v*` poussé à la main reste possible.
- README réorganisé (présentation, téléchargement, parcours, liens vers la documentation) ; version anglaise dans `README.en.md`. Guide de distribution, validation et architecture déplacés dans `docs/`.
- Dépendances mises à jour par Dependabot : Pillow 13.0.0, mss 10.2.0, pynput 1.8.2 et actions GitHub ; la suite complète passe avec ces versions.
- `stepwatch.__version__` est vérifié par un test contre `pyproject.toml`.
- GitHub Actions prépare une distribution portable et un exécutable unique pour Windows, ainsi qu'un paquet `.deb` amd64 pour Linux. Les builds sont déclenchés par les push vers `main`, les pull requests, les tags `v*` ou manuellement ; un tag égal à la version du projet permet une publication dans les Releases après réussite.
- Le paquet Linux embarque Python, ajoute un raccourci et déclare les dépendances système ; l'import ShareX et les rapports sont disponibles, tandis que l'enregistrement reste réservé à Windows. Le minimum glibc tient compte du système de compilation, avec un plancher à 2.35.
- Tests de structure du paquet Debian, des permissions, des dépendances et du rejet des liens sortants ; documentation du téléchargement des artefacts et de l'installation.
- **CI alignée sur la documentation** : le workflow ne produisait que l'exécutable Windows unique alors que le README annonçait aussi l'archive portable, le paquet `.deb` et les fichiers `SHA256SUMS-*`. Il construit désormais l'archive portable (testée au lancement comme l'exécutable unique), le paquet Debian sur Ubuntu 22.04 (tests Tk sous Xvfb, installation par `apt` puis lancement contrôlé) et les sommes SHA256, et publie l'ensemble dans les Releases.
- Lint `ruff` en CI avec un jeu de règles explicite ; Dependabot mensuel pour les dépendances pip et les actions GitHub.
- **Surveillance du hook souris** : Windows 7 et ultérieur retire sans notification un hook bas niveau qui dépasse `LowLevelHooksTimeout`, alors que son thread reste vivant ; les clics étaient alors perdus en silence. Le moteur compare l'état physique des boutons (`GetAsyncKeyState`) aux notifications du hook ; un appui non signalé dans la seconde provoque la réinstallation du hook et un message dans le journal, avec un délai croissant jusqu'à 30 s si la situation se répète. Une localisation de clic de plus de 100 ms dans le hook est consignée dans le journal de diagnostic.
- **Notes du technicien** : le bouton « Ajouter une note » ajoute un texte libre à la dernière étape, pendant l'enregistrement ou la pause. La note passe par le thread de capture, sans concurrence avec l'écriture des images.
- **Rapport Markdown** `rapport.md`, écrit à l'arrêt et régénéré par l'export du rapport autonome ; le texte est échappé et les images restent limitées au dossier `captures/`.
- Encodage PNG au niveau zlib 3 au lieu de 6 (valeur par défaut de Pillow) : sur des images 1080p de test, 23 à 63 % plus rapide et 5 à 20 % plus léger. La réécriture complète de `steps.json` et `rapport.html` à chaque capture a été mesurée à 33 ms pour 2 000 étapes sous Linux et conservée.
- Documentation : historique déplacé dans `docs/HISTORIQUE.md`, feuille de route dans `docs/FEUILLE_DE_ROUTE.md`, vérification des sommes SHA256.

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

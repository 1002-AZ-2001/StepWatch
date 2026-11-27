# Validation de StepWatch

Ce document décrit les tests automatiques et les vérifications à faire sur un poste Windows ou Linux.

## Ce que vérifie la CI à chaque modification

Le workflow [Tests et paquets Windows / Debian](https://github.com/1002-AZ-2001/StepWatch/actions/workflows/build.yml) s'exécute pour chaque pull request et chaque fusion dans `main`.

| Job | Vérifications |
| --- | --- |
| Windows x64 | Installation par `install.ps1` ; suite complète, interface Qt comprise (hors écran) ; construction PyInstaller ; essai natif (`smoke_windows.py`) : énumération des fenêtres, capture MSS, déplacement d'une fenêtre, état des boutons de la souris, clic réel (SendInput) enregistré par l'écoute Raw Input ; ouverture et fermeture de l'exécutable unique et de l'exécutable portable ; `StepWatch.exe --self-test` ; sommes SHA-256. |
| Linux amd64 (Ubuntu 22.04) | Vérifications `ruff` et `pip check`, tests de l'interface Qt (hors écran) et capture X11 sous Xvfb. Construction et installation du `.deb`, contrôle de la présence du script ScreenCast, lancement et capture X11 depuis l'exécutable installé. Auto-test de l'interface sous Weston, avec XWayland puis en Wayland natif. Sommes SHA-256. |
| Debian 12 (conteneur) | Installation du `.deb` par `apt`, auto-test avec capture X11, désinstallation complète. |
| Arch Linux (conteneurs) | Paquet construit par `makepkg` non root ; installation par `pacman`, auto-test X11 et Wayland (weston, avec et sans `xorg-xwayland`), désinstallation complète. |
| Publier la version | Notes tirées de `CHANGELOG.md`, tag et Release, uniquement pour une version pas encore publiée (voir [distribution](distribution.md)). |

Les tests simulent le backend Windows pour les cas difficiles à provoquer : fenêtre fermée, verrou d'antivirus, hook retiré par Windows, pause pendant une capture, etc. **Ces simulations ne remplacent pas un essai sur un poste réel** : hooks, DPI mixte, télémaintenance et bureau sécurisé UAC ne sont pas reproduits par la CI.

## Exécuter les tests localement

Sous Windows :

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

Sous Linux, pour la logique indépendante du système :

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check stepwatch tests scripts
```

Les tests de l'interface Qt tournent hors écran (`QT_QPA_PLATFORM=offscreen`), sans serveur graphique. Les tests de capture réelle demandent un serveur X de test déjà démarré, par exemple `Xvfb :99`.

```bash
DISPLAY=:99 XDG_SESSION_TYPE=x11 STEPWATCH_GUI_TESTS=1 STEPWATCH_X11_TESTS=1 \
  .venv/bin/python -m unittest discover -s tests -v
```

Vérifiez le bilan du runner : un test graphique ignoré faute d'affichage ne compte pas comme réussi.

Avec les dépendances Wayland système installées, le test suivant vérifie la conversion NV12 en RGB et le rejet des trames corrompues ou vides.

```bash
STEPWATCH_GSTREAMER_TESTS=1 .venv/bin/python -m unittest discover -s tests -p 'test_wayland.py' -v
```

## Validation manuelle sur Windows

À faire avant une diffusion opérationnelle, sur un poste distinct de celui de compilation :

1. Lancez l'application et vérifiez qu'aucune capture ne commence avant **● Enregistrer**.
2. Enregistrez une fenêtre Bloc-notes : clics gauche et droit, double-clic, déplacement, redimensionnement, pause et reprise. Contrôlez les marqueurs, les compteurs et l'ordre des étapes.
3. Pendant un enregistrement, ajoutez une note, puis vérifiez sa présence dans `rapport.html` et `rapport.md`.
4. Réduisez puis fermez la cible : les erreurs doivent être visibles, sans plantage ni perte silencieuse de capture.
5. Testez chaque moniteur, des origines négatives et plusieurs mises à l'échelle DPI. Vérifiez les coordonnées des clics et des masques.
6. Testez un changement distant dans AnyDesk, TeamViewer ou RDP sans clic local : le libellé doit rester « changement visuel ».
7. Testez une zone masquée : aucun pixel sensible ne doit apparaître dans les PNG, l'aperçu ou les rapports. Vérifiez de nouveau après un redimensionnement.
8. Arrêtez puis relancez une session, et fermez l'application pendant un enregistrement. Vérifiez le rapport, la libération des hooks et l'absence de capture après l'arrêt.
9. Importez des images ShareX, ouvrez le rapport hors ligne et comparez les sources avant et après.
10. Cliquez dans une fenêtre lancée en administrateur pendant l'enregistrement et consultez le journal pour détecter une éventuelle réinstallation du hook.
11. Enregistrez un moniteur entier avec la barre flottante. Cliquez sur ses boutons : aucune étape ne doit apparaître. Sous Windows 10 2004+ et 11, la barre ne doit pas figurer dans les PNG (`SetWindowDisplayAffinity`). Déplacez-la au-dessus de la cible et vérifiez de nouveau.
12. Dans l'onglet **Guide**, modifiez un titre, une description, l'ordre, supprimez une étape et masquez une zone. Vérifiez `rapport.html`, `rapport.md`, `captures/retirees/` et la suppression d'un rapport autonome existant.
13. Pendant un enregistrement, utilisez Ctrl+Maj+F9, F10 et F11 depuis une autre application (Bloc-notes) : pause, note et arrêt doivent répondre, et aucune autre frappe ne doit apparaître dans le journal ni dans les rapports. Lancez StepWatch une seconde fois : un message doit l'empêcher.
14. Parcourez chaque menu pendant et hors enregistrement (entrées grisées ou actives), puis modifiez chaque paramètre dans **Paramètres…**, relancez StepWatch et vérifiez qu'il est conservé.
15. Avec la version précédente installée (exécutable, dossier portable, paquet Debian ou Arch), ouvrez **Aide › Rechercher des mises à jour…** : la nouvelle version doit être proposée, téléchargée, vérifiée, installée, puis StepWatch doit redémarrer dans la nouvelle version (**Aide › À propos**). Sous Windows, vérifiez que le fichier `.old` a disparu après le redémarrage. `stepwatch --check-updates` affiche la dernière version publiée.
16. Sous Windows, puis sous Linux X11 et Wayland (GNOME ou KDE), vérifiez le thème sombre, l'icône de la zone de notification (démarrer, pause, arrêt) et la netteté à 150 %. Sous Wayland sans XWayland, vérifiez que la fenêtre s'ouvre et que la barre flottante reste utilisable.
17. Synchronisez un guide vers Nextcloud (mot de passe d'application), un partage réseau et un serveur SFTP : contenu identique sur le serveur, reprise après coupure du réseau, captures d'une étape supprimée retirées, mot de passe absent de `settings.json`.
18. Sous Windows, installez avec `StepWatch-VERSION-windows-x64-setup.exe`, lancez depuis le menu Démarrer, puis désinstallez depuis les Paramètres de Windows ; vérifiez les propriétés de l'exécutable (version, éditeur) et, si la signature est active, l'onglet Signatures numériques.

## Validation sur un bureau Linux

1. En session X11, capturez une fenêtre de test en mode **Hybride**. Déplacez et redimensionnez la fenêtre. Testez un clic dans la cible et un clic dans une autre fenêtre qui la recouvre, puis vérifiez les étapes et leurs marqueurs.
2. En session X11 avec plusieurs moniteurs, vérifiez la capture de chaque moniteur et d'une zone personnalisée. Contrôlez les pixels de la zone capturée et la position des masques dans le PNG.
3. En session Wayland, ouvrez StepWatch depuis le menu, lancez un enregistrement et annulez le partage dans le portail. Vérifiez le retour de l'interface à un état permettant de démarrer une nouvelle session.
4. Autorisez une capture depuis **● Enregistrer**, puis modifiez la source partagée. Vérifiez les changements enregistrés et les masques dans les PNG. Testez la pause et la reprise.
5. Arrêtez le partage depuis le bureau et vérifiez l'état de StepWatch et la disponibilité du rapport. Recommencez en utilisant **Arrêter** dans StepWatch.
6. Sous X11, enregistrez un moniteur entier avec la barre flottante au-dessus : elle doit être noircie dans les PNG, et ses clics ignorés. Sous Wayland, vérifiez qu'elle reste hors de la zone partagée.

Un portail de test sur D-Bus a transmis à StepWatch un flux PipeWire animé de 162 × 94 à 120 images par seconde. Sur Debian 13, le backend a reçu 70 images distinctes sur 70 demandes, y compris après deux pauses de lecture de 1,25 s. Le test reproduisait l'épuisement des 16 buffers du producteur avant le correctif de recyclage. Une variante à deux couleurs a vérifié le contenu des pixels et la révocation par le portail.

Un essai de bout en bout sur Sway 1.12, XWayland et le portail wlr 0.8.5 a enregistré deux états d'un même écran. Les PNG contenaient les couleurs attendues et le masque. Le moteur a respecté la pause et la reprise, finalisé les rapports et fermé le partage. Le banc utilisait une sortie virtuelle à haute fréquence pour réduire une course du portail wlr observée à 60 Hz. Les essais sur GNOME et KDE restent à faire.

## Mesures

### Audit des performances depuis la version 0.5.0

Les mesures utilisent des données synthétiques sous Linux. Le pic RAM d'import correspond au processus de test. La mesure de la file de clics couvre les allocations Python de la rafale.

| Opération | Avant | Après |
| --- | ---: | ---: |
| Import ShareX de trois PNG 4K RGB | 140,7 Mio de pic RAM et 1,40 s de CPU | 46,6 Mio et 0,74 s |
| Décodage en RGB d'une image MSS 4K | 8,69 ms en médiane | 5,57 ms et une copie de 31,6 Mio évitée |
| 20 000 clics pendant une capture bloquée | 20 000 entrées et 3,35 Mio d'allocations | 256 entrées et 43 Kio |
| 80 aperçus de 1 600 × 1 000 reçus pendant que l'onglet Capture est masqué | 80 rendus et 1,32 s de CPU | Aucun rendu avant le retour à l'onglet, puis un seul |
| 1 000 captures de 16 × 16, écritures de JSON et HTML | 291,7 Mio écrits et 9,65 s de CPU | 252,5 Mio et 7,10 s |

La génération des rapports réécrit les fichiers JSON et HTML complets à chaque étape. Le volume cumulé des écritures continue donc de croître au carré du nombre d'étapes.

### Enregistrement 4K et traitement en parallèle

Comparaison avec la version 0.4.0 (`5f1c912`) dans le même environnement Linux, sur un bureau synthétique de 3 840 × 2 160 pixels. Le moteur fonctionne en mode visuel avec un intervalle de 100 ms, un backend simulé et de vraies écritures PNG, JSON et HTML. Chaque scénario est mesuré dans un processus séparé. Le pic mémoire inclut le processus entier et son image de test.

| Scénario | Temps CPU avant → après | Pic mémoire avant → après | Durée avant → après |
| --- | ---: | ---: | ---: |
| 20 images identiques, 1 capture conservée | 2,42 → 0,58 s | 228 → 118 Mio | 3,28 → 1,95 s |
| 20 captures conservées avec changement de zone | 5,62 → 3,98 s | 260 → 156 Mio | 5,68 → 2,88 s |

Dans le second scénario, le temps CPU dépasse la durée réelle après modification. L'analyse et l'écriture ont avancé en parallèle. Le nouveau moteur a également préparé une image supplémentaire qu'il a écartée à l'arrêt. Les mesures concernent ce scénario de test et varient selon les images, le processeur et le disque du poste.

### Encodage PNG (version 0.3.0)

Pillow utilise par défaut le niveau zlib 6. Sur des images 1080p de test, sous Linux :

| Image | Niveau 6 | Niveau 3 (retenu) | Niveau 1 |
| --- | ---: | ---: | ---: |
| Bureau synthétique (fenêtres, texte, zone bruitée) | 87 ms, 628 Ko | 67 ms, 494 Ko | 75 ms, 505 Ko |
| Bruit RGB | 566 ms, 3 232 Ko | 208 ms, 2 613 Ko | 156 ms, 2 767 Ko |

La réécriture complète de `steps.json` et `rapport.html` après chaque capture a été mesurée à 33 ms pour une session de 2 000 étapes. Elle a donc été conservée.

### Comparaison visuelle (version 0.2.0)

Voir [AUDIT_INTEGRATION.md](AUDIT_INTEGRATION.md) : la comparaison sous-échantillonnée passe de 537 ms à 72 ms sur une image 1080p de bureau synthétique.

Ces mesures sont indicatives : elles isolent un traitement et ne garantissent pas une cadence sur un poste Windows donné.

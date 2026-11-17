# Validation de StepWatch

Ce document distingue ce qui est vérifié automatiquement de ce qui reste à vérifier sur un poste Windows réel.

## Ce que vérifie la CI à chaque modification

Le workflow [Tests et paquets Windows / Debian](https://github.com/1002-AZ-2001/StepWatch/actions/workflows/build.yml) s'exécute pour chaque pull request et chaque fusion dans `main`.

| Job | Vérifications |
| --- | --- |
| Windows x64 | Installation par `install.ps1` ; suite complète, interface Tk comprise ; construction PyInstaller ; essai natif (`smoke_windows.py`) : énumération des fenêtres, capture MSS, déplacement d'une fenêtre, état des boutons de la souris ; ouverture et fermeture de l'exécutable unique et de l'exécutable portable ; `StepWatch.exe --self-test` ; sommes SHA-256. |
| Linux amd64 (Ubuntu 22.04) | Lint `ruff` ; `pip check` ; suite complète sous Xvfb, interface Tk et vraie capture MSS comprises ; construction du `.deb`, installation par `apt`, lancement contrôlé (`smoke_linux_bundle.py`) et `stepwatch --self-test` sous X11 ; auto-test dans une session **Wayland** réelle (weston headless + XWayland) et message explicite sans XWayland (`smoke_wayland.py`) ; sommes SHA-256. |
| Debian 12 (conteneur) | Installation du `.deb` par `apt`, `stepwatch --self-test` avec l'interface, désinstallation complète. |
| Arch Linux (conteneurs) | Paquet construit par `makepkg` non root ; installation par `pacman`, auto-test X11 et Wayland (weston + `xorg-xwayland`), désinstallation complète. |
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

Les tests de l'interface sont ignorés par défaut. Avec Tcl/Tk et un serveur X de test déjà démarré (par exemple `Xvfb :99`) :

```bash
DISPLAY=:99 STEPWATCH_GUI_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

Vérifiez le bilan du runner : un test graphique ignoré faute d'affichage ne compte pas comme réussi.

## Validation manuelle sur Windows

À faire avant une diffusion opérationnelle, sur un poste distinct de celui de compilation :

1. Lancez l'application et vérifiez qu'aucune capture ne commence avant **Démarrer**.
2. Enregistrez une fenêtre Bloc-notes : clics gauche et droit, double-clic, déplacement, redimensionnement, pause et reprise. Contrôlez les marqueurs, les compteurs et l'ordre des étapes.
3. Pendant un enregistrement, ajoutez une note, puis vérifiez sa présence dans `rapport.html` et `rapport.md`.
4. Réduisez puis fermez la cible : les erreurs doivent être visibles, sans plantage ni perte silencieuse de capture.
5. Testez chaque moniteur, des origines négatives et plusieurs mises à l'échelle DPI. Vérifiez les coordonnées des clics et des masques.
6. Testez un changement distant dans AnyDesk, TeamViewer ou RDP sans clic local : le libellé doit rester « changement visuel ».
7. Testez une zone masquée : aucun pixel sensible ne doit apparaître dans les PNG, l'aperçu ou les rapports. Vérifiez de nouveau après un redimensionnement.
8. Arrêtez puis relancez une session, et fermez l'application pendant un enregistrement. Vérifiez le rapport, la libération des hooks et l'absence de capture après l'arrêt.
9. Importez des images ShareX, ouvrez le rapport hors ligne et comparez les sources avant et après.
10. Sous Linux (GNOME ou KDE, en session Wayland), lancez StepWatch depuis le menu : la fenêtre doit s'ouvrir avec l'icône StepWatch dans le dock. Importez des images ShareX et ouvrez le rapport.
11. Cliquez dans une fenêtre lancée en administrateur pendant l'enregistrement : notez si le journal signale une réinstallation du hook.

## Mesures

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

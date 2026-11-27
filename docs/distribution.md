# Distribution de StepWatch

Chaque version publiée se trouve dans les [Releases GitHub](https://github.com/1002-AZ-2001/StepWatch/releases). Les fichiers sont construits par le workflow [Tests et paquets Windows / Debian](https://github.com/1002-AZ-2001/StepWatch/actions/workflows/build.yml) sur leur système cible, après la suite de tests et un essai de lancement.

| Fichier | Usage |
| --- | --- |
| `StepWatch-VERSION-windows-x64-setup.exe` | Windows 10/11 x64, installateur (Inno Setup) recommandé : installation dans `%LOCALAPPDATA%\Programs\StepWatch` sans droits administrateur, menu Démarrer, désinstallation depuis les Paramètres de Windows. Installation silencieuse : `/VERYSILENT /SUPPRESSMSGBOXES`. |
| `StepWatch-VERSION-windows-x64.exe` | Windows 10/11 x64, un seul fichier. Il extrait son environnement dans un dossier temporaire au lancement : le premier démarrage peut être plus lent. |
| `StepWatch-VERSION-windows-x64-portable.zip` | Windows, dossier portable : extraire puis lancer `StepWatch\StepWatch.exe`. Conserver le dossier entier. |
| `stepwatch_VERSION_amd64.deb` | Debian 12+ ou Ubuntu 22.04+, amd64. |
| `stepwatch-VERSION-1-x86_64.pkg.tar.zst` | Arch Linux x86_64. |
| `SHA256SUMS-windows.txt`, `SHA256SUMS-linux.txt` | Sommes SHA-256 des fichiers ci-dessus. |

Python est inclus dans tous les fichiers. Aucun ne contient d'identifiant ni de télémétrie. Le seul accès réseau est la recherche de mises à jour sur GitHub, désactivable (voir le [README](../README.md#mises-à-jour-automatiques)). Les mises à jour automatiques fonctionnent à partir de la version 0.10.0 : installez une fois la 0.10.0 manuellement depuis une version antérieure.

## Vérifier un téléchargement

Sous Linux, depuis le dossier des fichiers téléchargés :

```bash
sha256sum -c SHA256SUMS-linux.txt --ignore-missing
```

Sous PowerShell :

```powershell
Get-FileHash .\StepWatch-VERSION-windows-x64.exe   # SHA256 par défaut
Get-Content .\SHA256SUMS-windows.txt
```

Les deux empreintes doivent être identiques (la casse ne compte pas).

Les exécutables Windows **ne sont pas signés** : SmartScreen peut afficher « Windows a protégé votre ordinateur ». Vérifiez d'abord le SHA-256, puis choisissez **Informations complémentaires → Exécuter quand même** si la politique de votre organisation l'autorise. La signature de code est prévue dans la [feuille de route](FEUILLE_DE_ROUTE.md).

## Windows

Aucune installation n'est nécessaire. Les interventions sont enregistrées dans le dossier choisi dans l'interface, et le journal de diagnostic dans `%LOCALAPPDATA%\StepWatch\logs`. Pour retirer l'application, supprimez l'exécutable ou le dossier portable ; vos interventions ne sont pas supprimées.

## Linux : Debian, Ubuntu et Arch Linux

Les deux paquets installent l'application dans `/opt/stepwatch`, la commande `stepwatch` et une entrée dans le menu. Les interventions et les journaux restent dans les dossiers de l'utilisateur.

Après l'installation, `stepwatch --self-test` vérifie les rapports sur des données synthétiques, puis ouvre et ferme l'interface. En session X11, il vérifie aussi le moteur de capture.

### Debian et Ubuntu

```bash
sudo apt install ./stepwatch_VERSION_amd64.deb
stepwatch
```

Pour la retirer : `sudo apt remove stepwatch`. La CI installe et teste le paquet sous Ubuntu 22.04 et dans un conteneur Debian 12.

Le paquet déclare `libc6 (>= 2.35)`, ou la version de glibc du système de compilation si elle est plus récente. Il est construit sur Ubuntu 22.04 par la CI. Un paquet construit localement sur une distribution plus récente peut donc exiger une glibc plus récente : vérifiez-le avec `dpkg-deb -f FICHIER.deb Depends` avant de le redistribuer.

### Arch Linux

```bash
sudo pacman -U ./stepwatch-VERSION-1-x86_64.pkg.tar.zst
stepwatch
```

`pacman` installe les dépendances depuis les dépôts Arch, y compris une police si aucune n'est présente (`ttf-font` : `pacman` propose alors un choix). Pour retirer l'application : `sudo pacman -R stepwatch`. Le paquet est construit par `makepkg`, sans privilèges, dans l'image officielle `archlinux:base-devel` ; la CI l'installe, le teste sous X11 et sous Wayland, puis le retire dans un conteneur `archlinux:base`. Il n'est pas signé avec une clé de mainteneur Arch : comparez son SHA-256 à celui de `SHA256SUMS-linux.txt`.

### X11

Dans une session X11, sélectionnez une fenêtre visible, un moniteur ou une zone en pixels et démarrez l'enregistrement. Les modes clics, changements visuels et hybride utilisent la même interface que sous Windows. La détection des clics nécessite l'extension X11 RECORD, présente sur la plupart des serveurs Xorg. Le mode changements visuels reste utilisable sans cette extension.

### Wayland

Dans une session Wayland, **● Enregistrer** ouvre le sélecteur de partage du bureau. Choisissez l'écran ou la fenêtre à partager selon les sources proposées par le bureau. StepWatch reçoit les images via PipeWire et enregistre les changements visuels. Le bureau peut mettre fin au partage à tout moment. Les réglages de fréquence, de sensibilité et de masquage restent disponibles.

Installez les composants de capture manquants et le backend ScreenCast de votre bureau. Par exemple, `xdg-desktop-portal-gnome` pour GNOME, `xdg-desktop-portal-kde` pour KDE Plasma ou `xdg-desktop-portal-wlr` pour Sway. PipeWire doit être actif dans la session utilisateur.

```bash
# Debian et Ubuntu
sudo apt install xwayland pipewire xdg-desktop-portal python3-gi \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-plugins-base gstreamer1.0-pipewire

# Arch Linux
sudo pacman -S --needed xorg-xwayland pipewire xdg-desktop-portal \
  python-gobject gstreamer gst-plugins-base gst-plugin-pipewire
```

L'interface Qt passe par XWayland quand il est installé : la barre flottante peut alors rester au premier plan, à la position choisie. Sans XWayland, elle s'affiche en Wayland natif (le compositeur place alors la barre). `STEPWATCH_WAYLAND=natif` force le Wayland natif ; `QT_QPA_PLATFORM` reste prioritaire. Le sélecteur du bureau et le flux de capture utilisent la session Wayland. Wayland réserve l'observation des clics globaux au compositeur, donc StepWatch propose le mode changements visuels sans marqueur de clic. L'import ShareX et les rapports fonctionnent avec les seules dépendances d'affichage.

## Construire depuis les sources

### Exécutables Windows

La compilation doit se faire sur Windows, avec la même architecture que les postes ciblés. PyInstaller ne produit pas d'`.exe` Windows depuis Linux.

```powershell
.\scripts\install.ps1
.\scripts\build.ps1 -SingleFile
```

Le script installe `requirements-dev.txt`, exécute les tests, puis appelle PyInstaller. Il embarque `scripts/StepWatch.manifest` pour déclarer la gestion DPI par moniteur (`PerMonitorV2`, puis `PerMonitor`) dès le lancement, avant l'initialisation de Python et de Qt. Il produit :

- `dist\StepWatch\` : distribution portable, à distribuer **en entier** ;
- `dist\StepWatch-singlefile.exe` : exécutable unique, seulement avec `-SingleFile`.

Ce script ne fournit ni installeur MSI, ni signature de code, ni validation antivirus. Testez le résultat sur un poste Windows distinct.

### Paquets Debian et Arch Linux

Utilisez Ubuntu 22.04 amd64 pour retrouver la compatibilité de la CI. Il faut Python 3.11 ou ultérieur, `dpkg-deb`, `readelf` (paquet `binutils`) et les dépendances de `requirements-dev.txt`. Le script retire du paquet les greffons Qt inutiles (GTK, eglfs, VNC…) et les bibliothèques devenues orphelines ; les bibliothèques X11, Wayland, OpenGL, GLib, D-Bus et de polices viennent du système (dépendances du paquet) :

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/build_deb.py
```

Le script crée `dist/stepwatch_VERSION_amd64.deb` sans privilège root. Les fichiers appartiennent à root dans l'archive, et aucun script de maintenance n'est exécuté à l'installation. Pour réutiliser un dossier PyInstaller déjà construit sur le même système :

```bash
.venv/bin/python scripts/build_deb.py --bundle dist/StepWatch --output dist
```

Le bundle réutilisé doit être construit avec la même version de StepWatch, y compris le script `stepwatch/_wayland_portal.py` fourni à PyInstaller comme fichier de données. Le script s'exécute avec le Python système et les bibliothèques du bureau. Les liens symboliques qui sortent du bundle sont refusés. Le paquet utilise les bibliothèques X11 et de polices de la distribution cible, afin de lire sa configuration locale.

Pour le paquet Arch, conservez le bundle, puis préparez le PKGBUILD et construisez-le avec `makepkg` en utilisateur non root, par exemple dans un conteneur Arch :

```bash
.venv/bin/python scripts/build_deb.py --output dist --keep-bundle build/StepWatch
.venv/bin/python scripts/build_arch.py --bundle build/StepWatch --output build/arch
docker run --rm -v "$PWD/build/arch:/build" -w /build archlinux:base-devel bash -euc \
  'useradd -m packager && chown -R packager:packager /build && runuser -u packager -- makepkg --noconfirm --nodeps'
```

## Publier une nouvelle version

L'arrivée d'une nouvelle version sur `main` déclenche sa Release GitHub.

1. Choisissez le numéro `MAJEUR.MINEUR.CORRECTIF` et reportez-le dans `pyproject.toml` et `stepwatch/__init__.py`. Le manifeste Windows `scripts/StepWatch.manifest` reprend le même numéro suivi de `.0`.
2. Dans `CHANGELOG.md`, déplacez le contenu de « Non publié » sous un nouveau titre `## VERSION — AAAA-MM-JJ`. Cette section fournit le texte de la Release.
3. Faites arriver ces changements sur `main`. Le workflow construit et teste les exécutables Windows et les paquets Linux. Il vérifie leurs sommes SHA-256, puis crée la Release et son tag `vVERSION`.

Dans GitHub Actions, **Run workflow** permet aussi de lancer la publication de la version de `main` avec l'option **Publier la version de main après les tests**. Une version déjà publiée est ignorée. Le lien **Dernière version** du README pointe automatiquement vers la Release courante.

Pousser à la main un tag `vVERSION`, égal à la version de `pyproject.toml`, reste possible. Pour ce tag, le workflow crée la Release, ou remplace les fichiers d'une Release existante. Un tag qui ne correspond pas à la version est refusé.

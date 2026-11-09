# Distribution de StepWatch

Chaque version publiée se trouve dans les [Releases GitHub](https://github.com/1002-AZ-2001/StepWatch/releases). Les fichiers sont construits par le workflow [Tests et paquets Windows / Debian](https://github.com/1002-AZ-2001/StepWatch/actions/workflows/build.yml) sur leur système cible, après la suite de tests et un essai de lancement.

| Fichier | Usage |
| --- | --- |
| `StepWatch-VERSION-windows-x64.exe` | Windows 10/11 x64, un seul fichier. Il extrait son environnement dans un dossier temporaire au lancement : le premier démarrage peut être plus lent. |
| `StepWatch-VERSION-windows-x64-portable.zip` | Windows, dossier portable : extraire puis lancer `StepWatch\StepWatch.exe`. Conserver le dossier entier. |
| `stepwatch_VERSION_amd64.deb` | Debian 12+ ou Ubuntu 22.04+, amd64 : import ShareX et rapports uniquement. |
| `stepwatch-VERSION-1-x86_64.pkg.tar.zst` | Arch Linux x86_64 : import ShareX et rapports uniquement. |
| `SHA256SUMS-windows.txt`, `SHA256SUMS-linux.txt` | Sommes SHA-256 des fichiers ci-dessus. |

Python est inclus dans tous les fichiers. Aucun ne contient d'identifiant, de télémétrie ni de service réseau.

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

**Sous Linux, seuls l'import ShareX et les rapports sont disponibles.** L'interface refuse l'enregistrement : les hooks et la capture de fenêtre sont réservés à Windows. Les deux paquets installent les mêmes fichiers : l'application dans `/opt/stepwatch`, la commande `stepwatch`, une entrée dans le menu et une icône. Les interventions et les journaux restent dans les dossiers de l'utilisateur.

Après l'installation, `stepwatch --self-test` vérifie l'application sur une session synthétique et ouvre puis ferme la fenêtre ; il n'utilise aucune donnée personnelle.

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

### Wayland

Tk, la bibliothèque graphique de StepWatch, n'a pas de backend Wayland natif. Dans une session Wayland (GNOME, KDE Plasma, Sway, Hyprland…), il s'affiche par **XWayland**, présent par défaut sur la plupart des bureaux. Si XWayland manque, StepWatch s'arrête avec un message qui indique le paquet à installer :

```bash
sudo apt install xwayland        # Debian, Ubuntu
sudo pacman -S xorg-xwayland     # Arch Linux
```

La fenêtre déclare la classe `Stepwatch`, ce qui permet au dock d'afficher l'icône de StepWatch. Avec une mise à l'échelle fractionnaire, certains bureaux affichent les applications XWayland légèrement floues ; c'est une limite de XWayland, pas de StepWatch.

## Construire depuis les sources

### Exécutables Windows

La compilation doit se faire sur Windows, avec la même architecture que les postes ciblés. PyInstaller ne produit pas d'`.exe` Windows depuis Linux.

```powershell
.\scripts\install.ps1
.\scripts\build.ps1 -SingleFile
```

Le script installe `requirements-dev.txt`, exécute les tests, puis appelle PyInstaller. Il embarque `scripts/StepWatch.manifest` pour déclarer la gestion DPI par moniteur (`PerMonitorV2`, puis `PerMonitor`) dès le lancement, avant l'initialisation de Python et de Tk. Il produit :

- `dist\StepWatch\` : distribution portable, à distribuer **en entier** ;
- `dist\StepWatch-singlefile.exe` : exécutable unique, seulement avec `-SingleFile`.

Ce script ne fournit ni installeur MSI, ni signature de code, ni validation antivirus. Testez le résultat sur un poste Windows distinct.

### Paquets Debian et Arch Linux

Utilisez Ubuntu 22.04 amd64 pour retrouver la compatibilité de la CI. Il faut Python 3.11 ou ultérieur avec Tkinter, `dpkg-deb` et les dépendances de `requirements-dev.txt` :

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/build_deb.py
```

Le script crée `dist/stepwatch_VERSION_amd64.deb` sans privilège root. Les fichiers appartiennent à root dans l'archive, et aucun script de maintenance n'est exécuté à l'installation. Pour réutiliser un dossier PyInstaller déjà construit sur le même système :

```bash
.venv/bin/python scripts/build_deb.py --bundle dist/StepWatch --output dist
```

Les liens symboliques qui sortent du bundle sont refusés. Les bibliothèques X11 et de polices du système de compilation (fontconfig, freetype, Xft, X11…) ne sont pas embarquées : elles viennent du système cible, qui les déclare en dépendances. Sinon, un fontconfig ancien ne sait pas lire la configuration d'une distribution plus récente.

Pour le paquet Arch, conservez le bundle, puis préparez le PKGBUILD et construisez-le avec `makepkg` en utilisateur non root, par exemple dans un conteneur Arch :

```bash
.venv/bin/python scripts/build_deb.py --output dist --keep-bundle build/StepWatch
.venv/bin/python scripts/build_arch.py --bundle build/StepWatch --output build/arch
docker run --rm -v "$PWD/build/arch:/build" -w /build archlinux:base-devel bash -euc \
  'useradd -m packager && chown -R packager:packager /build && runuser -u packager -- makepkg --noconfirm --nodeps'
```

## Publier une nouvelle version

Chaque version a sa Release GitHub. La publication est automatique :

1. Choisissez le numéro `MAJEUR.MINEUR.CORRECTIF` et reportez-le dans `pyproject.toml` (`version`) et dans `stepwatch/__init__.py` (`__version__`). Un test vérifie que ces deux valeurs concordent.
2. Dans `CHANGELOG.md`, déplacez le contenu de « Non publié » sous un nouveau titre `## VERSION — AAAA-MM-JJ`. Cette section devient le texte de la Release : sans elle, la publication est refusée.
3. Fusionnez ces changements dans `main` par une pull request.

Après la fusion, le workflow construit et teste les fichiers Windows et Linux, puis le job **Publier la version** crée le tag `vVERSION` et la Release avec tous les fichiers. Si la version est déjà publiée, `main` ne la modifie jamais : les fusions ordinaires ne touchent pas les Releases existantes.

Pousser à la main un tag `vVERSION`, égal à la version de `pyproject.toml`, reste possible. Pour ce tag, le workflow crée la Release, ou remplace les fichiers d'une Release existante. Un tag qui ne correspond pas à la version est refusé.

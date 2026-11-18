# Feuille de route

Ce document liste les versions livrées, puis les versions proposées, avec la valeur attendue et les limites de chaque chantier. Les priorités suivent deux critères : la protection des données des clients, et la possibilité de vérifier le travail sans poste Windows réel. Aucune date n'est promise.

## Version 0.6 — enregistrement sous Linux

StepWatch capture les clics et les changements visuels sous X11. Sous Wayland, le partage passe par le portail du bureau et PipeWire pour suivre les changements visuels. Cette version réduit aussi les copies d'images et la mémoire utilisée par l'import ShareX. [Validation et mesures](validation.md)

## Version 0.5 — relecture et performances

La vue **Étapes** rouvre une session et charge seulement l'image sélectionnée. La capture et l'écriture PNG s'exécutent en parallèle. Le correctif de fermeture Tk évite que le nettoyage des objets graphiques soit effectué par un thread de travail.

## Version 0.4 — Linux : Arch, Debian 12 et Wayland (livrée)

| Chantier | Fonction livrée | Limite à connaître |
| --- | --- | --- |
| Paquet Arch Linux | `.pkg.tar.zst` construit par `makepkg`, installé, testé et retiré par la CI. | Non signé avec une clé de mainteneur Arch ; vérifier le SHA-256. |
| Debian 12 | `.deb` installé, testé et retiré dans un conteneur Debian 12. | Paquet construit sur Ubuntu 22.04 (glibc 2.35 minimum). |
| Wayland | Fonctionnement par XWayland vérifié dans weston ; message explicite sans XWayland ; icône associée dans les docks. | Tk n'a pas de backend Wayland natif : XWayland reste nécessaire. |
| Auto-test | `stepwatch --self-test` vérifie une installation sans données personnelles. | — |

Cette version ouvrait l'interface sous Linux pour l'import ShareX et la consultation des rapports.

## Version 0.3 — publication et fiabilité du hook (livrée)

| Chantier | Fonction livrée | Limite à connaître |
| --- | --- | --- |
| Releases | Une Release GitHub par version, publiée automatiquement après les tests, avec notes tirées du CHANGELOG, exécutables Windows, `.deb` et SHA-256. | Exécutables non signés : avertissement SmartScreen possible. |
| CI complète | Windows (exe unique et portable, essai natif) et Linux (lint, tests Tk sous Xvfb, `.deb` installé et lancé). | Les hooks et le DPI réels ne sont pas reproduits par la CI. |
| Surveillance du hook | Réinstallation du hook souris retiré en silence par Windows, détectée par l'état physique des boutons. | Des clics peuvent manquer avant la détection (1 s au plus, puis délai croissant). |
| Notes et Markdown | Notes du technicien sur la dernière étape ; `rapport.md` à l'arrêt et à l'export. | Une note ne peut viser que la dernière étape. |
| Encodage PNG | Niveau zlib 3 : plus rapide et plus léger que le niveau 6 sur les images de test. | Mesuré sous Linux, sur des images synthétiques. |

## Versions 0.1 et 0.2 (livrées)

| Version | Contenu |
| --- | --- |
| 0.1.0 | Première implémentation : capture par clics et changements visuels, masques, rapports HTML hors ligne, import ShareX. |
| 0.2.0 | Corrections de l'audit : clics localisés au moment du clic, dialogues possédés, comparaison accélérée, écritures robustes, double-clic regroupé, rapport autonome, journal de diagnostic. Voir [AUDIT_INTEGRATION.md](AUDIT_INTEGRATION.md). |

## À venir — rapports prêts à partager

Un rapport est fait pour être transmis. Ce chantier traite le risque principal de l'outil, une donnée sensible oubliée dans une capture, et rend le rapport présentable sans retouche externe. Tout s'y vérifie sur des fichiers, donc sous Linux et en CI, sans poste Windows.

| Chantier | Valeur | Effort | Risque et limite |
| --- | --- | --- | --- |
| Éditeur de session après l'arrêt | Supprimer une étape inutile, corriger ou compléter une annotation, ajouter une note à n'importe quelle étape. Rapports régénérés. | Moyen : nouvelle fenêtre Tk, réécriture de `steps.json`. | Une étape supprimée doit-elle garder son PNG d'origine ? À décider ; proposition : déplacement dans `captures/retirees/`, exclu des rapports. |
| Masquage a posteriori | Noircir à la souris une zone oubliée sur une capture déjà enregistrée, avant partage. | Moyen : dessin sur l'image dans l'éditeur. | Écrase définitivement les pixels du PNG ; le rapport autonome existant doit être régénéré. |
| Masques dessinés avant l'enregistrement | Dessiner les zones sur un aperçu de la cible au lieu de saisir `x,y,largeur,hauteur`. | Moyen. | L'aperçu nécessite l'accès à la cible avant l'enregistrement. |

## À venir — capture plus fiable

Ces chantiers touchent les API Windows : la CI les compile et les simule, mais seul un essai sur un poste réel les valide.

| Chantier | Valeur | Effort | Risque et limite |
| --- | --- | --- | --- |
| Capture d'une fenêtre recouverte | `PrintWindow` avec `PW_RENDERFULLCONTENT` (Windows 8.1+) pour ne plus capturer une fenêtre superposée, avec repli sur la copie d'écran actuelle. | Moyen. | Option non documentée : image noire possible pour certains contenus (DirectX, protégés). L'API documentée `Windows.Graphics.Capture` est plus robuste, mais plus lourde à intégrer depuis Python. |
| Masques ancrés | Un masque suit un bord ou un coin de la fenêtre quand elle est redimensionnée. | Faible à moyen. | Ne suit pas un champ qui se déplace à l'intérieur de la fenêtre. |

## Version 1.0 proposée — diffusion

| Chantier | Valeur | Prérequis |
| --- | --- | --- |
| Validation sur postes réels | Exécuter la [liste de validation](validation.md) sur Windows 10 et 11, avec DPI mixte et télémaintenance. | Accès à des postes Windows. |
| Signature de code | Moins d'avertissements SmartScreen et de faux positifs antivirus. La SignPath Foundation signe gratuitement les projets sous licence OSI (AGPL v3 incluse). | Candidature du propriétaire du dépôt ; authentification multifacteur pour tous les mainteneurs. |
| Installeur | Paquet MSI ou `winget`, avec raccourci et désinstallation propre. | Signature recommandée au préalable. |
| Interface en anglais | Le README existe déjà en anglais ; l'interface reste en français. | Extraction des textes de `ui.py`, `engine.py` et `storage.py`. |

## Enregistrement sous Linux

StepWatch utilise le moteur de capture de la session de bureau.

- **X11** utilise `mss` pour les images et `python-xlib` pour les cibles et les clics via l'extension RECORD.
- **Wayland** utilise le portail ScreenCast et PipeWire pour les changements visuels de la source partagée par l'utilisateur. La fenêtre Tk s'affiche via XWayland.

Les sources proposées par le portail dépendent du bureau. [Dépendances Linux et mise en route](distribution.md#wayland)

## Idées non retenues pour l'instant

- **Détection automatique des données sensibles (OCR)** : dépendances lourdes, et risque de donner une fausse impression de sécurité. Le masquage a posteriori prévu couvre le besoin avec un contrôle humain.
- **Export PDF natif** : l'impression du rapport HTML en PDF depuis le navigateur suffit, grâce à la feuille de style d'impression.
- **Images WebP ou JPEG** : gain de taille réel pour le rapport autonome, mais perte de netteté sur le texte en JPEG. À réexaminer avec l'édition des rapports.

## Références

- Hooks bas niveau retirés sans notification : <https://learn.microsoft.com/windows/win32/winmsg/lowlevelmouseproc>
- `PW_RENDERFULLCONTENT` dans WebRTC : <https://webrtc.googlesource.com/src/+/f89110d67902e787f6745ad2b52f7f09fc808512>
- SignPath Foundation : <https://signpath.org/>
- Tk et Wayland : <https://wiki.tcl-lang.org/page/GSoC+Idea%3A+Tk+Backend+for+the+Wayland+Display+Protocol>
- Portail InputCapture : <https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.InputCapture.html>
- Portails XDG (Arch Wiki) : <https://wiki.archlinux.org/title/Xdg-desktop-portal>

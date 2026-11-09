# Feuille de route

Ce document liste les versions livrées, puis les versions proposées, avec la valeur attendue et les limites de chaque chantier. Les priorités suivent deux critères : la protection des données des clients, et la possibilité de vérifier le travail sans poste Windows réel. Aucune date n'est promise.

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

## Version 0.4 proposée — rapports prêts à partager

Un rapport est fait pour être transmis. Ce chantier traite le risque principal de l'outil, une donnée sensible oubliée dans une capture, et rend le rapport présentable sans retouche externe. Tout s'y vérifie sur des fichiers, donc sous Linux et en CI, sans poste Windows.

| Chantier | Valeur | Effort | Risque et limite |
| --- | --- | --- | --- |
| Éditeur de session après l'arrêt | Supprimer une étape inutile, corriger ou compléter une annotation, ajouter une note à n'importe quelle étape. Rapports régénérés. | Moyen : nouvelle fenêtre Tk, réécriture de `steps.json`. | Une étape supprimée doit-elle garder son PNG d'origine ? À décider ; proposition : déplacement dans `captures/retirees/`, exclu des rapports. |
| Masquage a posteriori | Noircir à la souris une zone oubliée sur une capture déjà enregistrée, avant partage. | Moyen : dessin sur l'image dans l'éditeur. | Écrase définitivement les pixels du PNG ; le rapport autonome existant doit être régénéré. |
| Masques dessinés avant l'enregistrement | Dessiner les zones sur un aperçu de la cible au lieu de saisir `x,y,largeur,hauteur`. | Moyen. | L'aperçu nécessite une capture de la cible, donc Windows pour l'essai réel. |
| Correctif d'interface | Le chemin de la session est tronqué sous les boutons « Ouvrir le dossier » et « Rapport autonome ». | Faible. | — |

## Version 0.5 proposée — capture plus fiable

Ces chantiers touchent les API Windows : la CI les compile et les simule, mais seul un essai sur un poste réel les valide.

| Chantier | Valeur | Effort | Risque et limite |
| --- | --- | --- | --- |
| Capture d'une fenêtre recouverte | `PrintWindow` avec `PW_RENDERFULLCONTENT` (Windows 8.1+) pour ne plus capturer une fenêtre superposée, avec repli sur la copie d'écran actuelle. | Moyen. | Option non documentée : image noire possible pour certains contenus (DirectX, protégés). L'API documentée `Windows.Graphics.Capture` est plus robuste, mais plus lourde à intégrer depuis Python. |
| Masques ancrés | Un masque suit un bord ou un coin de la fenêtre quand elle est redimensionnée. | Faible à moyen. | Ne suit pas un champ qui se déplace à l'intérieur de la fenêtre. |
| Écriture dans un thread dédié | L'encodage PNG ne retarde plus la boucle de capture sur les très grandes cibles. | Moyen. | Ordre des étapes et remontée des erreurs à préserver ; utile surtout en 4K. |

## Version 1.0 proposée — diffusion

| Chantier | Valeur | Prérequis |
| --- | --- | --- |
| Validation sur postes réels | Exécuter la [liste de validation](validation.md) sur Windows 10 et 11, avec DPI mixte et télémaintenance. | Accès à des postes Windows. |
| Signature de code | Moins d'avertissements SmartScreen et de faux positifs antivirus. La SignPath Foundation signe gratuitement les projets sous licence OSI (AGPL v3 incluse). | Candidature du propriétaire du dépôt ; authentification multifacteur pour tous les mainteneurs. |
| Installeur | Paquet MSI ou `winget`, avec raccourci et désinstallation propre. | Signature recommandée au préalable. |
| Interface en anglais | Le README existe déjà en anglais ; l'interface reste en français. | Extraction des textes de `ui.py`, `engine.py` et `storage.py`. |

## Idées non retenues pour l'instant

- **Détection automatique des données sensibles (OCR)** : dépendances lourdes, et risque de donner une fausse impression de sécurité. Le masquage a posteriori de la version 0.4 couvre le besoin avec un contrôle humain.
- **Export PDF natif** : l'impression du rapport HTML en PDF depuis le navigateur suffit, grâce à la feuille de style d'impression.
- **Images WebP ou JPEG** : gain de taille réel pour le rapport autonome, mais perte de netteté sur le texte en JPEG. À réexaminer après la version 0.4.

## Références

- Hooks bas niveau retirés sans notification : <https://learn.microsoft.com/windows/win32/winmsg/lowlevelmouseproc>
- `PW_RENDERFULLCONTENT` dans WebRTC : <https://webrtc.googlesource.com/src/+/f89110d67902e787f6745ad2b52f7f09fc808512>
- SignPath Foundation : <https://signpath.org/>

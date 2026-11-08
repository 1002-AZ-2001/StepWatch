# Feuille de route

Pistes d'évolution identifiées lors de la revue du 8 novembre 2026, non encore réalisées. Elles sont classées par intérêt pour l'utilisateur ; aucune date n'est promise.

## Rapports

- **Éditeur d'étapes après l'arrêt** : supprimer, réordonner, renommer ou annoter une étape avant de partager le rapport. Aujourd'hui, seules les notes pendant l'enregistrement sont possibles.
- **Export PDF natif** : en attendant, `rapport.html` s'imprime en PDF depuis le navigateur.
- **Formats d'image plus légers** (WebP ou JPEG optionnels) pour le rapport autonome, qui encode les PNG en base64 (+33 % environ).

## Masquage

- Dessiner les masques à la souris sur l'aperçu au lieu de saisir `x,y,largeur,hauteur`.
- Ancrer un masque à un bord ou à un coin de la fenêtre pour qu'il suive un redimensionnement.
- Détection locale optionnelle de champs sensibles (OCR hors ligne), sans jamais remplacer la vérification humaine.

## Capture Windows

- Capturer une fenêtre partiellement recouverte : `PrintWindow` avec `PW_RENDERFULLCONTENT` (Windows 8.1+, non documenté, peut produire une image noire pour certains contenus), ou l'API documentée `Windows.Graphics.Capture`. À valider sur Windows réel avant toute adoption.
- Encodage PNG dans un thread d'écriture dédié, pour ne plus retarder la boucle de capture sur les très grandes cibles.

## Distribution

- **Signature de code** des exécutables Windows pour limiter les avertissements SmartScreen et les faux positifs antivirus. La SignPath Foundation propose une signature gratuite aux projets sous licence approuvée par l'OSI (l'AGPL v3 l'est) ; la candidature et l'activation de l'authentification multifacteur par tous les mainteneurs relèvent du propriétaire du dépôt.
- Installeur MSI ou paquet `winget`.

## Références

- Hooks bas niveau retirés sans notification : <https://learn.microsoft.com/windows/win32/winmsg/lowlevelmouseproc>
- `PW_RENDERFULLCONTENT` dans WebRTC : <https://webrtc.googlesource.com/src/+/f89110d67902e787f6745ad2b52f7f09fc808512>
- SignPath Foundation : <https://signpath.org/>

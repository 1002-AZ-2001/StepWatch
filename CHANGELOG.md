# Journal des modifications

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

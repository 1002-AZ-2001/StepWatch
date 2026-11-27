# Architecture de StepWatch

StepWatch est une application Python 3.11+ avec une interface Qt (PySide6) en français. La capture utilise Pillow pour les traitements et `mss` pour les images sous Windows et X11. Les clics passent par Raw Input sous Windows (sans hook système) et par Xlib sous X11. La capture Wayland utilise le portail de partage du bureau, PipeWire et GStreamer. [Dépendances par distribution](distribution.md)

## Modules

| Module | Responsabilité |
| --- | --- |
| `models.py` | Cibles, rectangles et réglages validés. Les coordonnées de capture et de masquage utilisent les pixels de l'image. |
| `platform.py` | Sélection du moteur selon le système et la session. Sur Windows, sélection des fenêtres, DPI par moniteur, capture MSS, localisation des clics et surveillance du hook. |
| `x11.py` | Géométrie des cibles via Xlib, réception des clics via RECORD et capture MSS des pixels visibles. |
| `wayland.py`, `_wayland_portal.py` | Demande de partage via ScreenCast, lecture du flux PipeWire avec GStreamer et retour des images au moteur. Le script utilise le Python et les bibliothèques du bureau. |
| `imaging.py` | Masquage, marqueurs de clic et comparaison visuelle (sous-échantillonnée en mode normal, pleine résolution en mode conservateur). |
| `engine.py` | Capture, comparaison, suivi du hook et mise en ordre des écritures sur un second thread. |
| `events.py` | File d'événements qui ne garde qu'un aperçu en attente, sans perdre les messages ni les erreurs. |
| `storage.py` | PNG, `steps.json`, `rapport.html`, `rapport.md`, rapport autonome et import ShareX ; écritures atomiques réessayées sous Windows. |
| `ui.py` | Fenêtre principale Qt ; tous les appels Qt restent sur le thread principal, une minuterie lit les files des traitements. |
| `qtcompat.py` | Application Qt unique, choix X11 / XWayland / Wayland natif, valeurs observables liées aux widgets, boîtes de dialogue en français, conversion Pillow vers Qt. |
| `review.py` | Éditeur de guide : liste paginée des étapes, lecture de la seule image sélectionnée, titres et descriptions modifiables, ordre, suppression, fenêtre de masquage a posteriori. |
| `guide.py` | Modifications d'une session terminée : `steps.json`, `rapport.html` et `rapport.md` réécrits atomiquement ; capture d'une étape supprimée déplacée dans `captures/retirees/` ; masquage définitif d'une zone ; suppression d'un rapport autonome devenu obsolète. |
| `floating.py` | Barre flottante pendant l'enregistrement : état, nombre d'étapes, pause, note, arrêt, retour à la fenêtre. |
| `theme.py` | Palettes claire et sombre, feuille de style Qt (style Fusion) et icône StepWatch dessinée avec Pillow. |
| `settings.py` | Préférences locales (`settings.json`) : lues au lancement, écrites au démarrage d'un enregistrement et à la fermeture ; un fichier absent ou invalide donne les valeurs par défaut. |
| `sync.py` | Synchronisation d'un guide : WebDAV/Nextcloud (urllib, HTTPS vérifié), dossier réseau, SFTP (paramiko, empreintes de serveur) ; envoi atomique, reprise, retrait des captures d'étapes supprimées ; mots de passe dans le coffre du système (keyring). |
| `rawinput.py` | Clics de la souris sous Windows par Raw Input (`RegisterRawInputDevices`, fenêtre réservée aux messages), souris uniquement, sans hook ni code d'écoute du clavier. |
| `updater.py` | Mises à jour : dernière version publiée sur GitHub, téléchargement vérifié par SHA-256, remplacement de l'exécutable, script de la version portable, `pkexec` pour les paquets Linux. |
| `update_dialog.py` | Fenêtre « Mise à jour disponible » : notes de version, progression, installation et redémarrage. |
| `settings_dialog.py` | Fenêtre Paramètres : onglets Général, Enregistrement, Guide et rapports, Interface ; travaille sur une copie des préférences. |
| `library.py` | Guides récents : dossiers déjà ouverts et interventions du dossier des guides, les plus récents d'abord. |
| `hotkeys.py` | Raccourcis d'enregistrement Windows (`RegisterHotKey`) sur un thread doté de sa propre boucle de messages ; les actions passent à l'interface par une file. |
| `instance.py` | Instance unique : mutex nommé sous Windows, `flock` sous Linux ; libérés par le système à la fin du processus. |
| `display.py` | Type de session (X11, Wayland) et message explicite sans affichage ; classe de fenêtre et identifiant Wayland `stepwatch` pour les docks. |
| `selftest.py` | `stepwatch --self-test` vérifie une session synthétique, les rapports, un import ShareX et l'interface. Sous X11, il vérifie aussi une capture réelle de la fenêtre d'auto-test. |
| `diagnostics.py` | Journal local en rotation et capture des exceptions non gérées (threads, rappels Qt, plantages natifs). |

## Threads et flux d'une capture

1. Sous Windows et X11, le **thread souris** localise le clic par rapport à la cible dès sa réception, puis le met dans une file bornée. Sous Wayland, le moteur commence ses comparaisons à partir du flux autorisé par l'utilisateur.
2. Le **thread de capture** (`StepWatch-capture`) dépile les clics, regroupe les doubles-clics, attend le délai après clic et capture la zone. Il masque l'image, compare si nécessaire puis ajoute le marqueur. La comparaison normale réutilise la référence en basse résolution et saute les filtres pour des images identiques. Le mode conservateur traite les différences par bandes à pleine résolution.
3. Le **thread d'écriture** encode le PNG, écrit les rapports et prépare la miniature. Le traitement de la capture suivante peut avancer sur un autre cœur. Une seule écriture est soumise à la fois, dans l'ordre des captures. Un rapport verrouillé est réessayé à la capture suivante et à l'arrêt.
4. L'**interface** traite jusqu'à 100 événements par passage. Elle actualise les commandes quand leur état change, et lit les événements moins souvent au repos. En relecture, une page contient au plus 48 étapes et les aperçus sont chargés un par un sur demande.

Le moteur attend la fin de l'écriture en cours avant d'ajouter une note ou de finaliser la session. L'arrêt laisse se terminer les écritures déjà soumises et écarte toute image encore en préparation. La fermeture libère les fenêtres Qt sur le thread graphique après l'arrêt des traitements.

## Garanties

- **Local par défaut** : aucune capture, aucun journal ni aucune télémétrie n'est envoyé sur le réseau. Seuls la recherche de mises à jour sur l'API publique de GitHub (désactivable) et la synchronisation des guides vers un serveur que l'utilisateur configure lui-même utilisent le réseau.
- **Aucune frappe clavier** n'est enregistrée. Les seuls textes saisis conservés sont le nom de l'intervention et les notes.
- **Masques avant écriture** : les zones masquées sont noircies avant la comparaison, l'aperçu et l'écriture, puis de nouveau après le marqueur de clic.
- **Rien au lancement** : aucun hook ni aucune capture avant **● Enregistrer**.
- **Sources ShareX intactes** : l'import copie les images, sans jamais modifier ni supprimer les originaux.
- **Rapport autonome restreint** : seuls les PNG du dossier `captures/` de la session sont intégrés. Les chemins absolus, les liens symboliques et les jonctions sont refusés.

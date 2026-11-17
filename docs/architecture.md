# Architecture de StepWatch

StepWatch est une application Python 3.11+ avec une interface Tkinter/ttk en français. Les dépendances d'exécution sont Pillow, `mss` et `pynput` (Windows uniquement). Aucun service réseau, serveur ou compte externe n'est nécessaire.

## Modules

| Module | Responsabilité |
| --- | --- |
| `models.py` | Cibles (fenêtre, moniteur, zone), rectangles et paramètres validés. Toutes les coordonnées sont en pixels physiques. |
| `platform.py` | Énumération des fenêtres Win32, DPI par moniteur, capture MSS, rattachement d'un clic à la fenêtre cible ou à ses dialogues, état physique des boutons pour la surveillance du hook. |
| `imaging.py` | Masquage, marqueurs de clic et comparaison visuelle (sous-échantillonnée en mode normal, pleine résolution en mode conservateur). |
| `engine.py` | Capture, comparaison, suivi du hook et mise en ordre des écritures sur un second thread. |
| `events.py` | File d'événements qui ne garde qu'un aperçu en attente, sans perdre les messages ni les erreurs. |
| `storage.py` | PNG, `steps.json`, `rapport.html`, `rapport.md`, rapport autonome et import ShareX ; écritures atomiques réessayées sous Windows. |
| `ui.py` | Fenêtre principale ; tous les appels Tk restent sur le thread principal. |
| `review.py` | Liste paginée des étapes d'une session et lecture de la seule image sélectionnée. |
| `display.py` | Type de session (X11, Wayland) et message explicite quand Tk ne peut pas s'afficher, par exemple sous Wayland sans XWayland ; classe de fenêtre `Stepwatch` pour les docks. |
| `selftest.py` | `stepwatch --self-test` : session synthétique, rapports, import ShareX, puis ouverture et fermeture de la vraie fenêtre. Sans réseau ni données personnelles. |
| `diagnostics.py` | Journal local en rotation et capture des exceptions non gérées (threads, rappels Tk, plantages natifs). |

## Threads et flux d'une capture

1. Le **hook souris** (thread `pynput`) reçoit un clic. Il localise immédiatement le clic par rapport à la cible, avant que l'application ne réagisse (menu, dialogue), puis le met en file. Il n'écrit rien sur disque.
2. Le **thread de capture** (`StepWatch-capture`) dépile les clics, regroupe les doubles-clics, attend le délai après clic et capture la zone. Il masque l'image, compare si nécessaire puis ajoute le marqueur. La comparaison normale réutilise la référence en basse résolution et saute les filtres pour des images identiques. Le mode conservateur traite les différences par bandes à pleine résolution.
3. Le **thread d'écriture** encode le PNG, écrit les rapports et prépare la miniature. Le traitement de la capture suivante peut avancer sur un autre cœur. Une seule écriture est soumise à la fois, dans l'ordre des captures. Un rapport verrouillé est réessayé à la capture suivante et à l'arrêt.
4. L'**interface** traite jusqu'à 100 événements par passage. Elle actualise les commandes quand leur état change, et lit les événements moins souvent au repos. En relecture, une page contient au plus 48 étapes et les aperçus sont chargés un par un sur demande.

Le moteur attend la fin de l'écriture en cours avant d'ajouter une note ou de finaliser la session. L'arrêt laisse se terminer les écritures déjà soumises et écarte toute image encore en préparation. La fermeture libère les objets Tcl/Tk sur le thread graphique après l'arrêt des traitements.

## Garanties

- **Local uniquement** : aucune capture, aucun journal ni aucune télémétrie n'est envoyé sur le réseau.
- **Aucune frappe clavier** n'est enregistrée. Les seuls textes saisis conservés sont le nom de l'intervention et les notes.
- **Masques avant écriture** : les zones masquées sont noircies avant la comparaison, l'aperçu et l'écriture, puis de nouveau après le marqueur de clic.
- **Rien au lancement** : aucun hook ni aucune capture avant **Démarrer**.
- **Sources ShareX intactes** : l'import copie les images, sans jamais modifier ni supprimer les originaux.
- **Rapport autonome restreint** : seuls les PNG du dossier `captures/` de la session sont intégrés. Les chemins absolus, les liens symboliques et les jonctions sont refusés.

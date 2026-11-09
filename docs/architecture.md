# Architecture de StepWatch

StepWatch est une application Python 3.11+ avec une interface Tkinter/ttk en français. Les dépendances d'exécution sont Pillow, `mss` et `pynput` (Windows uniquement). Aucun service réseau, serveur ou compte externe n'est nécessaire.

## Modules

| Module | Responsabilité |
| --- | --- |
| `models.py` | Cibles (fenêtre, moniteur, zone), rectangles et paramètres validés. Toutes les coordonnées sont en pixels physiques. |
| `platform.py` | Énumération des fenêtres Win32, DPI par moniteur, capture MSS, rattachement d'un clic à la fenêtre cible ou à ses dialogues, état physique des boutons pour la surveillance du hook. |
| `imaging.py` | Masquage, marqueurs de clic et comparaison visuelle (sous-échantillonnée en mode normal, pleine résolution en mode conservateur). |
| `engine.py` | Thread de capture : hook souris, regroupement des doubles-clics, déclencheurs, pause, notes, surveillance et réinstallation du hook, événements vers l'interface. |
| `events.py` | File d'événements qui ne garde qu'un aperçu en attente, sans perdre les messages ni les erreurs. |
| `storage.py` | PNG, `steps.json`, `rapport.html`, `rapport.md`, rapport autonome et import ShareX ; écritures atomiques réessayées sous Windows. |
| `ui.py` | Fenêtre principale ; tous les appels Tk restent sur le thread principal. |
| `diagnostics.py` | Journal local en rotation et capture des exceptions non gérées (threads, rappels Tk, plantages natifs). |

## Threads et flux d'une capture

1. Le **hook souris** (thread `pynput`) reçoit un clic. Il localise immédiatement le clic par rapport à la cible, avant que l'application ne réagisse (menu, dialogue), puis le met en file. Il n'écrit rien sur disque.
2. Le **thread de capture** (`StepWatch-capture`) dépile les clics, regroupe les doubles-clics, attend le délai après clic, capture la zone, applique les masques, compare si nécessaire, ajoute le marqueur, puis applique de nouveau les masques avant d'écrire.
3. Le **stockage** écrit le PNG, puis réécrit `steps.json` et `rapport.html`. Un rapport verrouillé n'interrompt pas l'enregistrement : il est réécrit à la capture suivante et à l'arrêt.
4. L'**interface** lit les événements toutes les 100 ms, au plus 100 par passage, et affiche une miniature préparée par le thread de capture.

Les notes saisies dans l'interface passent par le thread de capture : seul ce thread modifie la session, ce qui évite toute écriture concurrente.

## Garanties

- **Local uniquement** : aucune capture, aucun journal ni aucune télémétrie n'est envoyé sur le réseau.
- **Aucune frappe clavier** n'est enregistrée. Les seuls textes saisis conservés sont le nom de l'intervention et les notes.
- **Masques avant écriture** : les zones masquées sont noircies avant la comparaison, l'aperçu et l'écriture, puis de nouveau après le marqueur de clic.
- **Rien au lancement** : aucun hook ni aucune capture avant **Démarrer**.
- **Sources ShareX intactes** : l'import copie les images, sans jamais modifier ni supprimer les originaux.
- **Rapport autonome restreint** : seuls les PNG du dossier `captures/` de la session sont intégrés. Les chemins absolus, les liens symboliques et les jonctions sont refusés.

# Antivirus et SmartScreen

StepWatch observe les clics de la souris et capture l'écran : ce sont aussi des comportements de logiciels espions. Depuis la version 1.2, les clics sont lus par **Raw Input**, l'API documentée par Microsoft pour lire un périphérique, et non plus par un hook système (`SetWindowsHookEx`) : l'exécutable ne contient plus aucun code d'écoute du clavier. Ajouté au format des exécutables Python (PyInstaller), cela peut déclencher des **faux positifs** d'antivirus ou l'avertissement **« Windows a protégé votre ordinateur »** de SmartScreen.

## Ce que fait StepWatch pour limiter les faux positifs

| Mesure | Effet |
| --- | --- |
| **Installateur** `StepWatch-VERSION-windows-x64-setup.exe` (recommandé) | Installation classique dans le profil, sans extraction dans un dossier temporaire à chaque lancement, comportement que les antivirus surveillent. |
| **Chargeur PyInstaller recompilé** par la CI | Le chargeur précompilé est partagé par d'innombrables programmes, malveillants compris ; StepWatch a le sien. |
| **Pas de compression UPX** | Les exécutables compressés par UPX sont souvent bloqués par principe. |
| **Informations de version et icône** | Éditeur, produit, version et description visibles dans les propriétés du fichier. |
| **Signature de code** (SignPath Foundation, dès acceptation du projet) | La signature relie le fichier au dépôt public et construit une réputation SmartScreen d'une version à l'autre. Voir la [politique de signature](../CODE_SIGNING_POLICY.md). |
| **Aucun accès réseau caché** | Seules la recherche de mises à jour (désactivable) et la synchronisation que vous configurez utilisent le réseau. |

Aucune de ces mesures ne garantit l'absence d'alerte : Microsoft indique que la réputation SmartScreen se construit par la signature et l'usage, fichier par fichier ; même un certificat EV ne la donne plus d'emblée depuis 2024.

## Programme « inconnu » ou « malveillant » : quelle différence ?

| Message | Signification | Que faire |
| --- | --- | --- |
| SmartScreen : « Windows a protégé votre ordinateur » | Fichier **sans réputation** (trop récent, pas encore signé). Ce n'est pas une détection. | Vérifier la somme SHA-256, puis « Informations complémentaires › Exécuter quand même ». La signature et le nombre d'installations construisent la réputation. |
| WatchGuard EPDR : programme **bloqué en cours de classification** | Le service *Zero-Trust Application* bloque tout fichier inconnu jusqu'à ce que le laboratoire de WatchGuard l'ait classé. Chaque nouvelle version non signée est inconnue. | Voir [WatchGuard EPDR](#watchguard-epdr) ci-dessous. |
| Defender : menace nommée (`Trojan:Win32/…!ml`, `Wacatac`…) | Détection, souvent par apprentissage automatique (`!ml`) pour les exécutables Python. | [Déclarer le faux positif](#déclarer-un-faux-positif-à-microsoft). |

## WatchGuard EPDR

Pour l'administrateur de la console WatchGuard Cloud (Endpoint Security) :

1. **Débloquer la version en cours** : *Statut › Programmes bloqués en cours de classification* (*Currently Blocked Programs Being Classified*), sélectionnez `StepWatch.exe` ou l'installateur, puis **Débloquer**. Le fichier s'exécute alors sur les postes gérés, et la classification se poursuit : elle peut prendre quatre heures ou plus.
2. **Éviter le blocage à chaque version** : une fois les exécutables signés, créez une **exclusion par signature numérique** (éditeur « SignPath Foundation »), ou envoyez un fichier signé au **support WatchGuard** pour qu'il l'ajoute à sa liste d'autorisation. Tant qu'ils ne sont pas signés, l'exclusion par empreinte (hash) doit être refaite à chaque version.
3. **Fichier resté longtemps « en classification »** : WatchGuard recommande l'outil *PSInfo Sample Submit* ou le support pour envoyer l'échantillon à son laboratoire.

N'utilisez pas l'option « Conserver dans la liste des programmes autorisés par l'administrateur » pour toute une famille de fichiers : WatchGuard indique qu'elle peut ouvrir une brèche. Sources : [classification et reclassification](https://www.watchguard.com/help/docs/help-center/en-US/Content/en-us/Endpoint-Security/monitor-threats/file-classification-reclassification.html), [autoriser un élément bloqué](https://www.watchguard.com/help/docs/help-center/en-US/Content/en-us/Endpoint-Security/monitor-threats/allow-blocked-items.html), [programmes bloqués](https://www.watchguard.com/help/docs/help-center/en-US/Content/en-us/Endpoint-Security/troubleshooting/mal-rel-issues.html).

## Déclarer un faux positif à Microsoft

Sur le [portail d'analyse de Microsoft](https://www.microsoft.com/wdsi/filesubmission), choisissez **Software developer** si vous publiez StepWatch, sinon **Home customer** ou **Enterprise customer**, joignez le fichier téléchargé depuis GitHub et indiquez :

```text
Produit : StepWatch (logiciel libre, GNU AGPL v3) — https://github.com/1002-AZ-2001/StepWatch
Fichier : <nom du fichier>, version <X.Y.Z>, SHA-256 <somme publiée dans SHA256SUMS-windows.txt>
Détection : <nom exact affiché par Defender, ou « avertissement SmartScreen »>
Description : outil de documentation des interventions informatiques. Il capture l'écran et
lit les clics de la souris (API Raw Input) uniquement pendant un enregistrement démarré par
l'utilisateur, et enregistre les rapports en local. Aucune frappe clavier n'est lue.
Construit par GitHub Actions à partir du code public :
https://github.com/1002-AZ-2001/StepWatch/actions/workflows/build.yml
```

Le même texte convient pour le support WatchGuard ou un autre éditeur d'antivirus. Microsoft ne tient pas de liste blanche préventive : chaque version est analysée sur déclaration, et la signature reste le moyen durable de construire la réputation.

## Vérifier un fichier avant de l'ouvrir

1. Comparez sa somme SHA-256 à celle de `SHA256SUMS-windows.txt`, publiée avec la version :

   ```powershell
   Get-FileHash .\StepWatch-VERSION-windows-x64-setup.exe -Algorithm SHA256
   ```

2. Si le fichier est signé : Propriétés › Signatures numériques › « SignPath Foundation ».

## En cas de détection

- **SmartScreen** : « Informations complémentaires » › « Exécuter quand même », seulement si la somme SHA-256 correspond.
- **Microsoft Defender** : signalez le faux positif sur le [portail d'analyse de Microsoft](https://www.microsoft.com/wdsi/filesubmission) (« Software developer » ou « Home customer »), en joignant le fichier et le lien de la version.
- **Autre antivirus** : chaque éditeur propose un formulaire de faux positif ; indiquez le lien vers la version GitHub.
- Ouvrez un [ticket](https://github.com/1002-AZ-2001/StepWatch/issues) avec le nom de l'antivirus et le nom de la détection : le projet peut alors signaler la version concernée.

N'ajoutez une exclusion dans l'antivirus qu'en dernier recours, et seulement pour un fichier dont la somme SHA-256 est vérifiée.
